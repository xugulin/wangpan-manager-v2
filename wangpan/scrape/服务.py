"""刮削服务：把"扫描 → 匹配 → 抓取 → 落库 → 下图 → 写 NFO"串成一条流水线。

为什么要有这一层
================
* 界面（海报墙/设置页）只该说"刮这个目录"，**不该知道 TMDB 的接口细节**；
* 刮削是**长任务**：要能报进度、能取消、能只跑一批（限流友好），失败了下次接着来；
* 本地已有的 NFO / 图片要**优先**（用户整理过的东西不能被在线数据盖掉）。

流水线（每个单元）
==================
1. **本地优先**：目录里有 NFO → 先读它当底稿；有 `poster/fanart` → 直接登记成图片；
2. **拿 ID**：底稿/文件名里有 `tmdbid` → 直接取详情；否则走**新识别管线**
   `结构(P1) → 候选(P2) → 检索(P2) → 打分(P3) → 决策(P3)`
   （`identify.管线.全链`，与评测脚本**同一份代码**）：
   * **自动入库** → 采纳那条 id，继续往下走；
   * **待确认** → 记成"需要确认"并留在队列里（候选一起存下来）；
   * **丢弃** → 只记日志，任务记成"跳过"（不进队列、不再重试）；
3. **取详情**：电影一次拿全；剧集拿剧 + 逐季（**只为补集标题，可选，可关**）；
4. **挂文件**：把扫描到的视频按 `(季, 集)` 挂到对应的集上；**网盘目录走 `远端单元` 表**
   （没有本地路径可 walk），所以 16 集的网盘剧能真的挂上 16 个文件；
5. **下图**：海报/背景/标志/演员头像（按尺寸档，异步线程池，失败不影响入库）；
6. **落库 + 写 NFO**（可关）+ 记任务状态。

"拿不准"不进资料库
==================
分数不够高 / 领先不够多 / 压根没搜到 → 记成 **需要确认** 并**留在队列里**，
由 :mod:`wangpan.ui.待确认` 让人点一下（:meth:`刮削服务.采纳候选` /
:meth:`刮削服务.忽略待确认`）。**没有 TMDB 身份的条目不会进海报墙** ——
否则墙上会出现一张"标题就是文件名"的空卡，而且历史上还会和其他空身份条目互相顶掉
（``UNIQUE(来源, tmdb_id)`` 里的空串也会去重，见 :mod:`~wangpan.scrape.库`）。

限流友好
========
* 每次调用只处理 `本批上限` 个单元（默认 20），处理完把控制权还给界面；
* TMDB 客户端的缓存/TTL/429 退避在 :mod:`~wangpan.scrape.tmdb` 里，这一层不重复实现；
* 成功/失败/待确认都写进"刮削任务"表，下次 `续跑()` 只挑没成功的。
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from .NFO import 写NFO, 写单集NFO, 写季NFO, 读NFO, 找NFO
from .图片 import 图片缓存
from .库 import 资料库
from .扫描 import 发现条目, 扫描媒体库
from .模型 import (人物, 人物工种, 图片, 图片类型, 媒体条目, 媒体类型, 匹配候选,
                参演, 分级, 季, 集, 待确认项)

__all__ = ["刮削服务", "刮削进度", "刮削设置", "服务结果"]


@dataclass
class 刮削设置:
    """一次刮削行为的可调项。"""

    语言: str = "zh-CN"
    自动采纳阈值: float = 78.0
    领先阈值: float = 12.0
    抓季集详情: bool = True               # 剧集是否逐季抓（费请求，但集标题更全）
    下载图片: bool = True
    下载演员头像: bool = True
    头像上限: int = 12
    写NFO: bool = False                  # 默认不写（别擅自改用户的目录）
    覆盖已有NFO: bool = False
    本批上限: int = 20
    最小文件字节: int = 20 * 1024 * 1024
    只处理这些后缀: tuple[str, ...] = ()


@dataclass
class 刮削进度:
    总数: int = 0
    已完成: int = 0
    成功: int = 0
    需要确认: int = 0
    失败: int = 0
    当前: str = ""
    说明: str = ""
    开始时刻: float = field(default_factory=time.time)

    @property
    def 百分比(self) -> float:
        return (self.已完成 / self.总数 * 100.0) if self.总数 else 0.0

    @property
    def 用时秒(self) -> float:
        return time.time() - self.开始时刻

    def 摘要(self) -> str:
        return (f"{self.已完成}/{self.总数}（{self.百分比:.0f}%）"
                f"｜成功 {self.成功}｜待确认 {self.需要确认}｜失败 {self.失败}"
                f"｜用时 {self.用时秒:.1f}s" + (f"｜{self.当前}" if self.当前 else ""))


@dataclass
class 服务结果:
    成功: int = 0
    需要确认: int = 0
    失败: int = 0
    跳过: int = 0
    下载图片: int = 0
    处理: list[tuple[str, str]] = field(default_factory=list)   # (路径, 结果说明)
    错误们: list[str] = field(default_factory=list)

    def 摘要(self) -> str:
        return (f"成功 {self.成功}｜待确认 {self.需要确认}｜失败 {self.失败}"
                f"｜跳过 {self.跳过}｜下载图片 {self.下载图片}")


class 刮削服务:
    """一条龙刮削（线程内运行；界面把它放进 QThread 并连进度回调）。"""

    def __init__(self, 库: 资料库, 客户端=None, 缓存: Optional[图片缓存] = None,
                 设置: Optional[刮削设置] = None,
                 进度回调: Optional[Callable[[刮削进度], None]] = None,
                 日志回调: Optional[Callable[[str], None]] = None,
                 远端单元: Optional[dict] = None) -> None:
        #: ``{库里的路径: 发现条目}`` —— **网盘里的**文件用这个喂进来。
        #: 为什么需要：`_造单元()` 走的是本地文件系统（目录列举、读 nfo/海报），
        #: 网盘文件夹没有本地路径可走；而刮削真正依赖的只有文件名（TMDB 按名字搜）。
        #: 界面先把远端目录列出来、造成单元（`扫描.造远端单元`），这里直接用现成的，
        #: 不再去碰本地磁盘。
        self.远端单元 = dict(远端单元 or {})
        self.库 = 库
        self.客户端 = 客户端
        self.缓存 = 缓存 or 图片缓存()
        self.设置 = 设置 or 刮削设置()
        self.进度 = 刮削进度()
        self._进度回调 = 进度回调 or (lambda _p: None)
        self._日志 = 日志回调 or (lambda _t: None)
        self._取消 = threading.Event()
        #: 手动匹配选中的 (tmdb id, 类型)；非空时跳过搜索直接用
        self._强制: tuple[str, 媒体类型 | None] = ("", None)
        # ---- 新识别管线的两个共用件（**整个服务实例共用**，见各自的类注释）----
        # 检索缓存：进程内共享 + 落盘（同一目录几十个文件只搜一遍，跨次启动也有缓存）；
        # 候选详情：头部候选的"季集范围 + 别名"，进程内缓存 + TMDB 客户端的 7 天磁盘缓存。
        # 为什么挂在服务上而不是每次识别新建：真机是"一部剧几十集连着识别"，
        # 每集新建一个缓存对象 == 没有缓存（P2 的检索缓存注释里记过这个坑）。
        from ..identify.检索 import 默认缓存 as _默认检索缓存
        from ..identify.详情 import 候选详情
        self.识别缓存 = _默认检索缓存()
        self.识别详情 = 候选详情(客户端, 上限=3) if 客户端 is not None else None
        # 图片 CDN 与 API 是两个域名：客户端配了代理就同步给图片缓存
        # （真机实测 API 直连不通、图片 CDN 通；反过来也有）
        代理文本 = str(getattr(getattr(客户端, "配置", None), "代理", "") or "")
        if 代理文本 and hasattr(self.缓存, "设置代理"):
            self.缓存.设置代理(代理文本)
        self._线程池 = ThreadPoolExecutor(max_workers=4,
                                    thread_name_prefix="V2-刮削图")

    # ---------------- 对外 ----------------

    def 取消(self) -> None:
        self._取消.set()

    def 被取消(self) -> bool:
        return self._取消.is_set()

    def 关闭(self) -> None:
        self._线程池.shutdown(wait=False, cancel_futures=True)

    # ---------------- 扫描 ----------------

    def 扫库(self, 根目录们: Iterable[Path | str]) -> tuple[int, str]:
        """扫描目录并把发现的单元登记成"待处理"任务（不联网）。"""
        全部: list[发现条目] = []
        摘要们: list[str] = []
        for 目录 in 根目录们:
            结果 = 扫描媒体库(目录, 最小文件字节=self.设置.最小文件字节)
            全部.extend(结果.条目们)
            摘要们.append(f"{目录}：{结果.摘要()}")
            self._日志(f"[刮削] {结果.摘要()}")
            for 错 in 结果.错误们:
                self._日志(f"[刮削] {错}")
        from .扫描 import 合并远端单元
        表 = self._按路径归并(全部)
        for 路径, 组 in 表.items():
            单元 = 合并远端单元(组) or 组[0]
            self.库.记任务(路径, "待处理", None, 单元.一句话())
            self._记单元底稿(单元)      # 先让它出现在海报墙上（用户真机反馈）
        return len(全部), "；".join(摘要们)

    def _记单元底稿(self, 单元) -> None:
        """给发现的单元先落一条**没有 TMDB 信息**的媒体底稿，让海报墙立刻能看到。

        用户真机两次反馈"加了文件夹扫描后媒体库什么都不显示" —— 原设计是
        "拿不准的只进待确认队列、不进海报墙"，但对用户来说就是"扫了跟没扫一样"。
        现在两者都要：**底稿先进墙**（标题/年份来自文件名解析），同时仍然进待确认
        队列；之后联网刮到会用**同一条**记录补全（去重靠 唯一键 / 文件路径，
        剧集也带上集的文件路径，不会留两条）。
        """
        from .模型 import 集, 季, 媒体条目, 媒体类型
        主 = 单元.主视频
        if 主 is None:
            return
        try:
            条目 = 媒体条目(类型=单元.类型, 标题=单元.标题 or Path(str(主)).stem,
                         年份=单元.年份, 来源="扫描")
            if 单元.类型 is 媒体类型.剧集:
                # 把这一部剧**所有集**都建出来（不是只建第一集）：用户要在海报里
                # 直接看到并选集，确认之前也得能列出来（真机反馈"剧集信息不显示"）。
                from .命名解析 import 解析
                季表: dict = {}
                for 视频 in (单元.视频们 or [主]):
                    名字 = Path(str(视频)).name
                    解 = 解析(名字, 父目录名=Path(str(单元.路径)).name)
                    季号 = int((解.季 if 解.季 is not None else 单元.季号) or 1)
                    集号 = int((解.集 if 解.集 is not None else 单元.集号) or 1)
                    季表.setdefault(季号, []).append(
                        集(季号=季号, 集号=集号,
                          集号到=int(解.集到 or 0), 文件路径=Path(str(视频))))
                条目.季们 = [季(季号=号, 集数=len(集们), 集们=sorted(
                    集们, key=lambda c: c.集号)) for 号, 集们 in sorted(季表.items())]
            else:
                条目.文件路径 = Path(str(主))
            self.库.存条目(条目)
        except Exception as 错:  # noqa: BLE001
            self._日志(f"[刮削] 落底稿失败（{Path(str(主)).name}）：{错}")

    @staticmethod
    def _按路径归并(单元们) -> dict:
        """把"一集一个"的单元按路径（= 一部剧的文件夹）归并成 {路径: [单元…]}。"""
        表: dict = {}
        for 单元 in 单元们 or []:
            if 单元 is None or not 单元.可刮削():
                continue
            表.setdefault(str(单元.路径), []).append(单元)
        return 表

    def 记远端单元(self, 单元们) -> tuple[int, str]:
        """把**网盘**里的待刮削单元登记成任务（不联网、不碰本地文件系统）。

        对应的本地版本是 :meth:`扫库`（它内部 `os.walk` 本地目录）。

        ⚠️ 必须**按路径归并后再记**：远端扫描是"一集一个单元"，直接记会让一部剧
        在库里冒出十几张卡片（用户真机反馈"同一部剧要一张海报"，以及"剧集信息不显示"）。
        """
        from .扫描 import 合并远端单元
        表 = self._按路径归并(单元们)
        登记 = 0
        for 路径, 组 in 表.items():
            单元 = 合并远端单元(组) or 组[0]
            self.库.记任务(路径, "待处理", None, 单元.一句话())
            self._记单元底稿(单元)
            登记 += 1
        self._日志(f"[刮削] 网盘新增 {登记} 个待刮削单元（{len(单元们 or [])} 个视频）")
        return 登记, f"网盘 {登记} 个"

    # ---------------- 刮削 ----------------

    def 续跑(self, 批上限: Optional[int] = None) -> 服务结果:
        """处理"待处理 + 需要确认(重试) + 失败(重试)"的任务，最多一批。"""
        上限 = int(批上限 or self.设置.本批上限)
        任务们 = []
        for 状态 in ("待处理", "失败"):
            任务们 += [r for r in self.库.取任务(状态, 上限) if int(r["尝试次数"] or 0) < 3]
        任务们 = 任务们[:上限]
        结果 = 服务结果()
        self.进度 = 刮削进度(总数=len(任务们))
        self._进度回调(self.进度)
        for 行 in 任务们:
            if self._取消.is_set():
                self.进度.说明 = "已取消"
                break
            路径 = Path(行["路径"])
            self.进度.当前 = 路径.name
            try:
                self._刮一个(路径, 结果)
            except Exception as 错:  # noqa: BLE001 - 单个失败不能带崩整批
                结果.失败 += 1
                结果.错误们.append(f"{路径.name}: {错}")
                self.库.记任务(str(路径), "失败", None, str(错)[:200])
                self._日志(f"[刮削] ✗ {路径.name}：{错}")
            self.进度.已完成 += 1
            self._进度回调(self.进度)
        self._线程池.shutdown(wait=True)
        self._日志(f"[刮削] 本批完成：{结果.摘要()}")
        return 结果

    def 刮路径(self, 路径: Path | str, 强制标识: str = "",
             强制类型: 媒体类型 | None = None) -> 服务结果:
        """刮单个路径（右键"重新刮削"、或"手动匹配选中某个 id"用）。

        :param 强制标识: 非空时**跳过搜索**，直接按这个 TMDB id 取详情（手动匹配的落点）。
        :param 强制类型: 配合 强制标识 决定走电影还是剧集接口。
        """
        结果 = 服务结果()
        self.进度 = 刮削进度(总数=1)
        self._强制 = (str(强制标识 or ""), 强制类型)
        try:
            self._刮一个(Path(路径), 结果)
        finally:
            self._强制 = ("", None)
        self.进度.已完成 = 1
        self._进度回调(self.进度)
        # ---- P4 闭环：**人工指定的 id** = 用户点过确认（待确认队列采纳 / 手动匹配）----
        # 成功之后把这次确认写成学习样本（目录 → 作品、别名 → 作品），同一个目录/名字
        # 第二次识别就直接命中（架构文档 ⑦ 与 P4 任务的落点）。
        # 为什么挂在 刮路径 而不是 采纳候选：界面上的"采纳"走的是
        # 媒体库页.刮削路径(强制标识=…)（见 wangpan/ui/媒体库页.py 的开待确认队列），
        # 两条路都经过本函数，挂这里才**一处覆盖全部人工确认**。
        # 为什么必须软失败：记不住只是"下次还得点一次"，绝不能让写学习库把入库搞失败。
        if 强制标识 and 结果.成功 and not 结果.失败:
            self._记学习样本(路径, 强制标识, 强制类型)
        return 结果

    def _记学习样本(self, 路径, 标识: str, 类型=None) -> None:
        """把一次人工确认写成 P4 学习样本（**软失败**：只记日志，不打扰调用方）。"""
        try:
            from ..identify.学习 import 记确认
            记下 = 记确认(样本=str(路径), 选中=str(标识), 类型=类型)
            self._日志(f"[识别学习] {记下.get('摘要', '')}")
        except Exception as 错:  # noqa: BLE001 - 学习库写不动不许影响入库
            self._日志(f"[识别学习] 记录失败（不影响入库）：{type(错).__name__}: {错}")

    # ---------------- 待确认队列 ----------------
    #
    # 这一组是给"待确认队列"界面（wangpan/ui/待确认.py）用的**唯一入口**：
    # 界面不认识 TMDB id 该走电影还是剧集接口，也不该自己去拼 刮路径 的参数。

    def 待确认队列(self, 条数: int = 500) -> list[待确认项]:
        return self.库.待确认(条数)

    def 采纳候选(self, 路径: Path | str, 候选: 匹配候选 | str,
                 类型: Optional[媒体类型] = None) -> 服务结果:
        """按人选定的一条候选落库（= 强制 ID 刮一次，跳过搜索与打分）。"""
        if isinstance(候选, 匹配候选):
            标识, 猜类型 = 候选.来源标识, 候选.类型
        else:
            标识, 猜类型 = str(候选), 媒体类型.未知
        型号 = 类型 if 类型 is not None else (猜类型 if 猜类型 is not 媒体类型.未知 else None)
        if not 标识:
            raise ValueError("采纳候选需要非空的 TMDB id")
        结果 = self.刮路径(路径, 强制标识=标识, 强制类型=型号)
        if 结果.成功 and not 结果.失败:
            self._日志(f"[刮削] ✅ 人工确认：{Path(str(路径)).name} → TMDB {标识}")
        return 结果

    def 忽略待确认(self, 路径: Path | str, 原因: str = "人工跳过") -> None:
        """把一条待确认从队列里划掉（**不写资料库**，也不再自动重试）。"""
        self.库.记任务(str(路径), "跳过", None, 原因)

    def 重搜待确认(self, 路径: Path | str) -> 服务结果:
        """重新搜一次（换了关键词/年份，或上次搜的时候网络不好）。"""
        return self.刮路径(路径)

    # ---------------- 单个单元 ----------------
    def _取远端单元(self, 路径) -> Optional[发现条目]:
        """从远端单元表里取出这一部剧的单元（表里可能是"一集一个"的列表）。"""
        值 = self.远端单元.get(str(路径))
        if not 值:
            return None
        if isinstance(值, (list, tuple)):
            from .扫描 import 合并远端单元
            return 合并远端单元(list(值))
        return 值

    def _刮一个(self, 路径: Path, 结果: 服务结果) -> None:
        单元 = self._取远端单元(路径) or self._造单元(路径)
        if 单元 is None or not 单元.视频们:
            结果.跳过 += 1
            self.库.记任务(str(路径), "成功", None, "没有可刮削的视频")
            return
        条目 = self._本地底稿(单元) or 媒体条目()
        if 条目.类型 is 媒体类型.未知:
            条目.类型 = 单元.类型
        条目.标题 = 条目.标题 or 单元.标题
        条目.年份 = 条目.年份 or 单元.年份
        self._日志(f"[刮削] {单元.一句话()}")

        # ---- 1) 已有 ID（或手动指定的 ID）直接取详情 ----
        在线 = None
        强制标识, 强制类型 = self._强制
        id文本 = str(强制标识 or 单元.数据源ID or 条目.外部ID.get("tmdb", ""))
        if 强制类型 is not None:
            单元.类型 = 强制类型
        if self.客户端 is not None and self.客户端.可用() and id文本:
            try:
                在线 = (self.客户端.取剧集(id文本) if 单元.类型 is 媒体类型.剧集
                      else self.客户端.取电影(id文本))
            except Exception as 错:  # noqa: BLE001
                self._日志(f"[刮削] 按 ID 取详情失败：{错}")
                self.库.记任务(str(路径), "失败", None, str(错)[:200])
                结果.失败 += 1
                return

        # ---- 2) 没有 ID → 走新识别管线（结构 → 候选 → 检索 → 打分 → 决策）----
        需要确认 = False
        说明 = ""
        候选们: list[匹配候选] = []
        if 在线 is None:
            if self.客户端 is None or not self.客户端.可用():
                说明 = "没有配置 TMDB（只能写本地 NFO/图片）"
            else:
                from ..identify.决策 import 档_自动, 档_丢弃
                档位, 候选们, 说明, _链 = self._搜索决策(单元, 条目)
                if 档位 == 档_丢弃:
                    # 丢弃：**连人工都不值得看**（零候选，或首选分数低于丢弃门槛）。
                    # 处置：日志 + 任务记成"跳过"（与"忽略待确认"同一个状态）——
                    # 既不进待确认队列、也不会被 续跑 反复重试；留一行是为了能回答
                    # "这条为什么没入库"（日志会滚动，任务表不会）。
                    结果.跳过 += 1
                    self.库.记任务(str(路径), "跳过", None, f"识别丢弃：{说明}")
                    self._日志(f"[识别] 🚮 {单元.标题 or 单元.路径.name}：{说明}")
                    结果.处理.append((str(路径), f"丢弃：{说明}"))
                    return
                if 档位 != 档_自动:
                    需要确认 = True
                else:
                    采纳 = 候选们[0]
                    self._日志(f"[识别] 自动匹配：{采纳.可读()}｜{说明}")
                    try:
                        在线 = (self.客户端.取剧集(采纳.来源标识)
                              if 采纳.类型 is 媒体类型.剧集
                              else self.客户端.取电影(采纳.来源标识))
                    except Exception as 错:  # noqa: BLE001
                        结果.失败 += 1
                        self.库.记任务(str(路径), "失败", None, str(错)[:200])
                        return

        # ---- 3) 拿不准 → 只进"待确认队列"，**不落库** ----
        # 为什么不像以前那样先存进去：没有 TMDB 身份的条目在海报墙上就是一张
        # "文件名当标题"的空卡（而且历史上还会和别的空身份条目互相顶掉）。
        # 人确认之后走 采纳候选() → 强制 ID 刮一次，那时才真正入库。
        if 需要确认:
            结果.需要确认 += 1
            self.库.记任务(str(路径), "需要确认", None, 说明, 候选=候选们,
                        标题=单元.标题 or 条目.标题 or 单元.路径.name,
                        年份=单元.年份 or 条目.年份, 类型=单元.类型)
            self._日志(f"[刮削] ⏳ {单元.标题}：{说明}（{len(候选们)} 个候选等人确认）")
            结果.处理.append((str(路径), f"待确认：{说明}"))
            return

        # ---- 3) 合并（本地优先，在线补空）----
        最终 = self._合并(条目, 在线) if 在线 is not None else 条目
        if 在线 is not None:
            最终.类型 = 在线.类型
            if 单元.类型 is 媒体类型.电影 and not 最终.季们:
                最终.文件路径 = 单元.主视频

        # ---- 4) 季集对齐（剧集必须有"集"这个落点，否则文件挂不上去）----
        # ⚠️ 官方 `/tv/{id}` 的 seasons[] **只有集数，没有集列表**；不逐季取的话
        # 条目里一个"集"都没有，第 5 步就无从挂文件 —— 真机测真 API 时踩到过：
        # 剧集入库了、海报也下了，但**一集文件都没挂上**（海报墙点进去播不了）。
        # 单测没抓到是因为假客户端顺手把集塞进了 seasons[]，真 TMDB 不会。
        if 最终.类型 is 媒体类型.剧集:
            补了 = self._补季集(最终, 单元)
            if 补了:
                self._日志(f"[刮削] 季集对齐：{补了} 季 / "
                         f"{最终.汇总集数()} 集（本地文件与在线季集详情已对上）")

        # ---- 5) 挂文件 ----
        self._挂文件(最终, 单元)

        # ---- 6) 下图 ----
        if self.设置.下载图片:
            结果.下载图片 += self._下图(最终)

        # ---- 7) 落库 + NFO ----
        媒体id = self.库.存条目(最终)
        if self.设置.写NFO:
            self._写NFO(最终, 单元)
        结果.成功 += 1
        self.库.记任务(str(路径), "成功", 媒体id, 说明 or 最终.一句话())
        结果.处理.append((str(路径), 最终.一句话()))

    # ---------------- 各阶段 ----------------

    def _造单元(self, 路径: Path) -> Optional[发现条目]:
        """把一个路径变成扫描单元（支持"直接右键一个文件/文件夹"）。"""
        路径 = Path(路径)
        if 路径.is_file():
            单元们 = 扫描媒体库(路径.parent, 最小文件字节=0).条目们
            命中 = [x for x in 单元们 if 路径 in x.视频们]
            return 命中[0] if 命中 else None
        结果 = 扫描媒体库(路径, 最小文件字节=self.设置.最小文件字节)
        return 结果.条目们[0] if 结果.条目们 else None

    def _本地底稿(self, 单元: 发现条目) -> Optional[媒体条目]:
        """本地 NFO / 图片优先。"""
        nfo = 找NFO(单元.路径, 单元.主视频.stem if 单元.主视频 else "")
        if nfo is None:
            return None
        条目 = 读NFO(nfo)
        if 条目 is not None:
            self._日志(f"[刮削] 用本地 NFO 打底：{nfo.name}")
        return 条目

    def _搜索决策(self, 单元: 发现条目, 条目: 媒体条目):
        """**新识别管线**：结构(P1) → 候选(P2) → 检索(P2) → 打分(P3) → 决策(P3)。

        返回 ``(档位, 候选们[匹配候选], 说明, 链结果)``。三档的语义：

        * ``自动入库``：调用方按这个 id 取详情、走完补季集/挂文件/落库/记任务；
        * ``待确认``：候选一起落进"刮削任务"表的 ``候选`` 列（**数据结构与以前一模一样**，
          ``wangpan/ui/待确认.py`` 与 ``媒体库页.开待确认队列`` 都按 ``匹配候选`` 读它），
          采纳走 ``采纳候选`` → ``刮路径(强制标识=…)``，P4 的学习钩子就挂在 ``刮路径``；
        * ``丢弃``：调用方只记日志（见 ``_刮一个`` 里那一段），不进队列、不重试。

        为什么这一层要叫"搜索决策"这个名字：它是 ``_刮一个`` 里"没有 ID 时"那一段的
        原名字，界面/测试没有引用它，但保留名字能让 diff 一眼看出**换的是里面那条链**。

        为什么要 ``范围表``（候选取详情的成本）：⑤ 打分的"(季,集) 范围校验"需要候选的
        季数与集数，而 TMDB 搜索接口不返回它 —— 所以先预打一遍分出头部候选，再取头部
        的详情重排一次。取多少条是成本决策，``候选详情.上限`` 默认 3（同 P3 评测口径）。
        """
        from ..identify.管线 import 全链
        from ..identify.决策 import 档_丢弃, 档_自动
        from ..identify.学习 import 读库 as 读学习库
        from ..identify.检索 import 默认落盘缓存路径

        主 = 单元.主视频
        样本 = str(主) if 主 is not None else str(单元.路径)
        # 父目录名给"这部作品的目录"，**整条路径也一起给**：季目录（`Season 01`）只在
        # 文件路径里，少了它季号就丢了（`_本地单元们` 也靠同一份路径找回每一集）。
        目录名 = Path(str(单元.路径)).name
        # 本地 NFO 里的标题/年份也当成"目录级线索"喂进去（老实现是直接拿它当搜索目标的）：
        # 这样"文件名被改坏、但旁边有 NFO"的目录照样搜得对；而它排在目录链最后，
        # **不会覆盖**路径给出的更好标题（结构层的标题优先级是 文件名 > 目录名）。
        线索们: list[str] = []
        if 条目 is not None and 条目.标题:
            线索们.append(f"{条目.标题} ({条目.年份})" if 条目.年份 else str(条目.标题))
        try:
            学习库对象 = 读学习库()
        except Exception as 错:  # noqa: BLE001 - 学习库读不动只该少学一点，不该让识别停摆
            self._日志(f"[识别] 学习库读不动（当空库继续）：{错}")
            学习库对象 = None
        链 = 全链(样本, self.客户端, 父目录名=目录名, 目录链=tuple(线索们),
                 缓存=self.识别缓存, 落盘=self._识别检索落盘路径(),
                 详情=self.识别详情, 学习库=学习库对象)
        候选们 = [self._转匹配候选(项) for 项 in 链.打分结果.分数们]
        说明 = 链.决策结果.原因 or "没搜到任何候选"
        return 链.决策结果.档位, 候选们, 说明, 链

    @staticmethod
    def _识别检索落盘路径() -> Path:
        """识别检索缓存的落盘文件：默认 ``数据/识别检索缓存.json``。

        环境变量 ``V2_识别检索缓存`` 可以覆盖（**测试专用**）：单测会真的走
        "扫描 → 识别 → 落库"这条链、于是会写这份缓存，而"测试不许往用户的数据目录里
        写东西"是这套东西的纪律。为什么覆盖只在这一层认、不去改
        ``检索.默认落盘缓存路径()``：那条路径是 P2 的**对外契约**
        （``tests/test_识别检索.py`` 钉着"必须在数据目录"），这里只是"服务层这次
        落到哪"。
        """
        覆盖 = (os.environ.get("V2_识别检索缓存") or "").strip()
        if 覆盖:
            return Path(覆盖)
        from ..identify.检索 import 默认落盘缓存路径
        return 默认落盘缓存路径()

    @staticmethod
    def _转匹配候选(项) -> 匹配候选:
        """⑤ 的一条候选分 → 队列/界面认识的 :class:`匹配候选`。

        为什么要转：``identify`` 的候选（``检索候选``）是新管线自己的形状，而
        "待确认队列"这条路（``库.记任务`` 的 JSON 列 + ``ui/待确认.py`` 的表格 +
        ``采纳候选`` 读的 ``来源标识/类型``）是既有契约 —— **那条路的数据结构不许变**。
        分数与理由照搬打分的结论，界面上的排序与提示因此保持不变。
        """
        候选 = 项.候选
        类型 = {"tv": 媒体类型.剧集, "movie": 媒体类型.电影}.get(
            str(getattr(候选, "类型", "") or ""), 媒体类型.未知)
        理由 = "；".join(x.说明 for x in 项.关键信号们(2) if getattr(x, "说明", ""))
        return 匹配候选(
            来源标识=str(getattr(候选, "标识", "") or ""),
            标题=str(getattr(候选, "标题", "") or ""),
            原名=str(getattr(候选, "原名", "") or ""),
            年份=getattr(候选, "年份", None) or None,
            类型=类型,
            简介=str(getattr(候选, "简介", "") or ""),
            海报远端=str(getattr(候选, "海报远端", "") or ""),
            热度=float(getattr(候选, "热度", 0.0) or 0.0),
            分数=float(项.总分 or 0.0), 理由=理由)

    def _合并(self, 本地: 媒体条目, 在线: Optional[媒体条目]) -> 媒体条目:
        """本地优先、在线补空（本地有值的字段不覆盖）。"""
        if 在线 is None:
            return 本地
        结果 = 在线
        if 本地.标题:
            结果.标题 = 本地.标题
        if 本地.原名 and not 结果.原名:
            结果.原名 = 本地.原名
        if 本地.年份:
            结果.年份 = 本地.年份
        if 本地.简介 and not 结果.简介:
            结果.简介 = 本地.简介
        if 本地.评分 and not 结果.评分:
            结果.评分 = 本地.评分
        for 级 in 本地.分级们:
            结果.分级们.append(级)
        for 名 in 本地.标签:
            if 名 not in 结果.标签:
                结果.标签.append(名)
        for 键, 值 in 本地.外部ID.items():
            结果.外部ID.setdefault(键, 值)
        return 结果

    # ---------------- 季集对齐（剧集的核心）----------------

    def _本地单元们(self, 单元: 发现条目) -> list[发现条目]:
        """这个剧目录里**本地/网盘实际有的每一个 (季,集)** 单元。

        为什么不是直接用传进来的单元：一个"任务"是**按剧文件夹**归并的
        （一集一个任务会让 20 集的剧搜 20 次，白撞限流），所以手上这个单元只是
        "这部剧的某一集"；要挂文件得知道整部剧有哪些集。

        两条路（P5 补上第二条）：
        * **本地**：重扫这个剧的目录（`os.walk`），一步就能拿到每一集；
        * **网盘**：没有本地路径可走 —— 用调用方喂进来的 `远端单元` 表还原成
          "一集一个"的子单元。缺这条路时，一部 16 集的网盘剧只会挂上"第一个视频"
          （真机复验时暴露的：海报墙点进去只有一集能播）。
        """
        远端 = self.远端单元.get(str(单元.路径))
        if 远端:
            return [x for x in self._远端子单元们(远端, str(单元.路径))
                    if x.类型 is 媒体类型.剧集 and x.集号 is not None]
        目录 = Path(str(单元.路径))
        扫的目录 = 目录.parent if 目录.name.lower().startswith("season") else 目录
        try:
            结果 = 扫描媒体库(扫的目录, 最小文件字节=self.设置.最小文件字节)
        except Exception as 错:  # noqa: BLE001 - 扫不动也不该让整次刮削失败
            self._日志(f"[刮削] 重扫 {扫的目录} 失败：{错}")
            return []
        return [x for x in 结果.条目们
                if x.类型 is 媒体类型.剧集 and x.集号 is not None]

    @staticmethod
    def _远端子单元们(值, 路径文本: str) -> list[发现条目]:
        """`远端单元` 表里的一项 → "一集一个"的子单元列表。

        表里两种形状都认（`:paramref:`刮削服务.远端单元` 的注释里写了）：
        * **列表** = 界面喂进来的原样（`扫描.造远端单元` 的产物，一集一个）；
        * **单个单元** = 调用方只给了一个（测试与"直接刮一个远端文件"的场景）——
          如果它是**合并过**的（一个单元里带着整部剧的视频），它的集号只是"首集的"，
          必须按每个视频重新解析一遍，否则 15 集会被丢掉。
        """
        from .扫描 import 造远端单元
        单元们 = list(值) if isinstance(值, (list, tuple)) else [值]
        出: list[发现条目] = []
        前缀 = str(路径文本).split(":", 1)[0] if ":" in str(路径文本) else ""
        for 子 in 单元们:
            if 子 is None:
                continue
            # "重建"的两种情形：
            # * 一个单元里带着**多个视频** = 合并过的（它的集号只是"首集的"，直接拿来用会丢集）；
            # * 没有季集信息 = 电影单元或没抽到集号的，按文件名再解析一遍更保险。
            视频们 = list(子.视频们 or [])
            重建 = (len(单元们) == 1 and len(视频们) > 1) or (子.集号 is None and 子.季号 is None)
            if not 重建:
                出.append(子)
                continue
            for 视频 in 视频们:
                出.extend(造远端单元(前缀, str(子.路径), [str(视频)]))
        return 出

    def _本地文件表(self, 单元: 发现条目) -> dict[tuple[int, int], Path]:
        """本地 (季,集) → 主视频路径（多版本只留主视频，其余进"额外文件"）。"""
        表: dict[tuple[int, int], Path] = {}
        for 子单元 in self._本地单元们(单元):
            键 = (int(子单元.季号 if 子单元.季号 is not None else 1), int(子单元.集号))
            主 = 子单元.主视频
            if 主 is not None and 键 not in 表:
                表[键] = 主
        return 表

    def _补季集(self, 条目: 媒体条目, 单元: 发现条目) -> int:
        """让"本地有的 (季,集)"在条目里都有对应的 :class:`集` 对象。

        两件事（缺一个，文件就挂不上去）：
        1. **在线逐季取集**：官方 ``/tv/{id}`` 只给 ``seasons[].episode_count``，
           集标题/简介/剧照要另外打 ``/tv/{id}/season/{n}``。**只抓本地有文件的季**
           （20 季的剧全抓是 20 次请求，白挨限流）；
        2. **本地兜底**：在线没有的集（还没播、特别篇、自购版）也要建一个"集"，
           标题先用文件名解析出来的，否则本地文件就成了孤儿。

        返回对齐过的季数。
        """
        本地 = self._本地文件表(单元)
        if not 本地:
            return 0
        已有季 = {s.季号: s for s in 条目.季们}
        for 号 in sorted({季 for 季, _ in 本地}):
            if 号 not in 已有季:
                季对象 = 季(季号=号, 标题="特别篇" if 号 == 0 else f"第 {号} 季")
                条目.季们.append(季对象)
                已有季[号] = 季对象
        条目.季们.sort(key=lambda s: s.季号)

        # ---- 1) 在线逐季补集（只补本地有文件、且在线还没有集的季）----
        剧id = str(条目.外部ID.get("tmdb") or "")
        if self.设置.抓季集详情 and 剧id and self.客户端 is not None:
            for 号 in sorted({季 for 季, _ in 本地}):
                季对象 = 已有季[号]
                需要 = {集 for 季, 集 in 本地 if 季 == 号}
                if 需要 and 需要 <= {c.集号 for c in 季对象.集们}:
                    continue                       # 在线已经有了（或本地全没有）
                try:
                    在线季 = self.客户端.取季(剧id, 号)
                except Exception as 错:  # noqa: BLE001 - 补不到就用本地标题，别毁掉入库
                    self._日志(f"[刮削] 第 {号} 季详情没取到（用本地文件名兜底）：{错}")
                    continue
                有 = {c.集号: c for c in 季对象.集们}
                for 在线集 in 在线季.集们:
                    if 在线集.集号 in 有:
                        继续 = 有[在线集.集号]
                        for 字段 in ("标题", "简介", "播出日期", "时长分钟", "评分",
                                   "外部ID", "集号到"):
                            if not getattr(继续, 字段) and getattr(在线集, 字段):
                                setattr(继续, 字段, getattr(在线集, 字段))
                        if 继续.剧照 is None:
                            继续.剧照 = 在线集.剧照
                    else:
                        季对象.集们.append(在线集)
                if 季对象.标题 in ("", f"第 {号} 季") and 在线季.标题:
                    季对象.标题 = 在线季.标题
                if 季对象.海报 is None:
                    季对象.海报 = 在线季.海报
                季对象.集数 = max(int(季对象.集数 or 0), len(季对象.集们))
                季对象.集们.sort(key=lambda c: c.集号)

        # ---- 2) 本地兜底：在线没有的集也要有落点 ----
        标题表: dict[tuple[int, int], str] = {}
        for 子单元 in self._本地单元们(单元):
            键 = (int(子单元.季号 if 子单元.季号 is not None else 1), int(子单元.集号))
            标题表.setdefault(键, 子单元.标题 or "")
        for (季号, 集号), 标题 in sorted(标题表.items()):
            季对象 = 已有季[季号]
            if any(c.集号 == 集号 for c in 季对象.集们):
                continue
            季对象.集们.append(集(季号=季号, 集号=集号, 标题=标题))
            季对象.集们.sort(key=lambda c: c.集号)
        for 季对象 in 条目.季们:
            季对象.集数 = max(int(季对象.集数 or 0), len(季对象.集们))
        return len({季 for 季, _ in 本地})

    def _挂文件(self, 条目: 媒体条目, 单元: 发现条目) -> None:
        """把扫描到的视频挂到资料上（电影挂主文件；剧集按 (季,集) 挂到集）。"""
        if 条目.类型 is 媒体类型.电影 or not 条目.季们:
            条目.文件路径 = 条目.文件路径 or 单元.主视频
            # 多版本/多段：除了主文件，其余也记下来（海报墙里能看到"1080p / 2160p"）
            已有 = {条目.文件路径}
            for 视频 in 单元.视频们:
                if 视频 not in 已有:
                    条目.额外文件.append(视频)
                    已有.add(视频)
            return
        表: dict[tuple[int, int], 集] = {}
        for 季对象 in 条目.季们:
            for 集对象 in 季对象.集们:
                表[(季对象.季号, 集对象.集号)] = 集对象
        # 逐集挂：按 (季,集) 精确对上（多版本已在扫描单元里合并）
        for (季号, 集号), 视频 in sorted(self._本地文件表(单元).items()):
            目标 = 表.get((季号, 集号))
            if 目标 is not None and 目标.文件路径 is None:
                目标.文件路径 = 视频
        # 同一集的多版本：主文件之外都记进"额外文件"
        已挂 = {c.文件路径 for 季对象 in 条目.季们 for c in 季对象.集们 if c.文件路径}
        for 子单元 in self._本地单元们(单元):
            for 视频 in 子单元.视频们:
                if 视频 not in 已挂:
                    条目.额外文件.append(视频)
                    已挂.add(视频)
        # 绝对集号的番剧（扫描不到季集信息）：把手上这一集的视频兜底挂到第一季
        if 单元.集号 is not None:
            目标 = 表.get((int(单元.季号 or 1), int(单元.集号)))
            for 视频 in 单元.视频们:
                if 目标 is None:
                    break
                if 目标.文件路径 is None:
                    目标.文件路径 = 视频
                elif 视频 != 目标.文件路径 and 视频 not in 条目.额外文件:
                    条目.额外文件.append(视频)

    def _下图(self, 条目: 媒体条目) -> int:
        """下载海报/背景/标志/演员头像（在线图片走缓存，失败不影响入库）。"""
        if self.缓存 is None:
            return 0
        下载数 = 0
        基地址 = "https://image.tmdb.org/t/p/"
        if self.客户端 is not None and 本地配置可用(self.客户端):
            try:
                信息 = self.客户端.配置信息()
                基地址 = (信息 or {}).get("images", {}).get("secure_base_url") or 基地址
            except Exception:  # noqa: BLE001
                pass

        def 处理(图: Optional[图片], 尺寸: str) -> int:
            if 图 is None or 图.本地路径 is not None:
                return 0
            if not 图.远端路径:
                return 0
            本地 = self.缓存.确保(图.远端路径, 尺寸, 基地址=基地址)
            if 本地 is not None:
                图.本地路径 = 本地
                return 1
            return 0

        下载数 += 处理(条目.海报, "w500")
        下载数 += 处理(条目.背景, "w1280")
        下载数 += 处理(条目.标志, "w500")
        for 季对象 in 条目.季们:
            下载数 += 处理(季对象.海报, "w342")
            for 集对象 in 季对象.集们:
                下载数 += 处理(集对象.剧照, "w300")
        if self.设置.下载演员头像:
            数 = 0
            for 关系 in 条目.参演们:
                if 数 >= self.设置.头像上限:
                    break
                if 关系.人物.头像 is None:
                    continue
                数 += 处理(关系.人物.头像, "w185")
            下载数 += 数
        return 下载数

    def _写NFO(self, 条目: 媒体条目, 单元: 发现条目) -> None:
        try:
            if 条目.类型 is 媒体类型.电影:
                目标 = 单元.路径 / f"{单元.路径.name}.nfo"
                if 目标.is_file() and not self.设置.覆盖已有NFO:
                    return
                写NFO(条目, 目标)
                return
            目标 = 单元.路径 / "tvshow.nfo"
            if not 目标.is_file() or self.设置.覆盖已有NFO:
                写NFO(条目, 目标)
            for 季对象 in 条目.季们:
                写季NFO(季对象, 单元.路径 / f"Season {季对象.季号:02d}" / "season.nfo")
                for 集对象 in 季对象.集们:
                    if 集对象.文件路径 is None:
                        continue
                    写单集NFO(集对象, 条目.标题,
                            集对象.文件路径.with_suffix(".nfo"))
        except Exception as 错:  # noqa: BLE001
            self._日志(f"[刮削] 写 NFO 失败（不影响入库）：{错}")


def 本地配置可用(客户端) -> bool:
    """客户端能不能给图片基地址（拿不到也没关系，用默认 CDN）。"""
    return hasattr(客户端, "配置信息")
