"""刮削服务：把"扫描 → 匹配 → 抓取 → 落库 → 下图 → 写 NFO"串成一条流水线。

为什么要有这一层
================
* 界面（海报墙/设置页）只该说"刮这个目录"，**不该知道 TMDB 的接口细节**；
* 刮削是**长任务**：要能报进度、能取消、能只跑一批（限流友好），失败了下次接着来；
* 本地已有的 NFO / 图片要**优先**（用户整理过的东西不能被在线数据盖掉）。

流水线（每个单元）
==================
1. **本地优先**：目录里有 NFO → 先读它当底稿；有 `poster/fanart` → 直接登记成图片；
2. **拿 ID**：底稿/文件名里有 `tmdbid` → 直接取详情；否则搜索 + 打分：
   分数够高且领先第二名够多 → 自动采纳；否则记成"需要确认"（界面里让人点一下）；
3. **取详情**：电影一次拿全；剧集拿剧 + 逐季（**只为补集标题，可选，可关**）；
4. **挂文件**：把扫描到的视频按 `(季, 集)` 挂到对应的集上（绝对集号的番剧按顺序兜底）；
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
        return 结果

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

        # ---- 2) 没有 ID → 搜索 + 打分 ----
        需要确认 = False
        说明 = ""
        候选们: list[匹配候选] = []
        if 在线 is None:
            if self.客户端 is None or not self.客户端.可用():
                说明 = "没有配置 TMDB（只能写本地 NFO/图片）"
            else:
                候选们, 需确认, 理由 = self._搜索决策(单元, 条目)
                if 候选们 is None:
                    结果.失败 += 1
                    self.库.记任务(str(路径), "失败", None, 理由)
                    self._日志(f"[刮削] ✗ {单元.标题}：{理由}")
                    return
                if 需确认 or not 候选们:
                    需要确认 = True
                    说明 = 理由
                else:
                    采纳 = 候选们[0]
                    self._日志(f"[刮削] 自动匹配：{采纳.可读()}｜{理由}")
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
        """搜索 + 打分 + 决策；返回 (候选们 或 None, 需要确认, 说明)。"""
        from .匹配打分 import 从文件名猜查询词, 排序候选, 自动决策
        from .命名解析 import 解析
        目标 = 解析((单元.主视频.name if 单元.主视频 else 单元.路径.name),
                  父目录名=单元.路径.name)
        if 单元.标题 and not 目标.标题:
            目标.标题 = 单元.标题
        目标.年份 = 目标.年份 or 单元.年份
        候选们: list[匹配候选] = []
        查询词们 = 从文件名猜查询词(单元.主视频 or 单元.路径) or [单元.标题 or 单元.路径.name]
        for 词 in 查询词们[:2]:
            try:
                if 单元.类型 is 媒体类型.剧集:
                    候选们 += self.客户端.搜剧集(词, 目标.年份)
                else:
                    候选们 += self.客户端.搜电影(词, 目标.年份)
            except Exception as 错:  # noqa: BLE001
                return None, False, f"搜索失败：{错}"
            if 候选们:
                break
        if not 候选们:
            return [], True, "没搜到任何候选"
        排序候选(候选们, 目标)
        采纳, 需确认, 说明 = 自动决策(候选们, 目标, self.设置.自动采纳阈值,
                                self.设置.领先阈值)
        if 采纳 is not None and not 需确认:
            return [采纳] + [c for c in 候选们 if c is not 采纳], False, 说明
        return 候选们, True, 说明

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
        """重扫这个剧的目录，拿到**本地实际有的每一个 (季,集)**。

        为什么不是直接用传进来的单元：一个"任务"是**按剧文件夹**归并的
        （一集一个任务会让 20 集的剧搜 20 次，白撞限流），所以手上这个单元只是
        "这部剧的某一集"；要挂文件得知道整部剧在本地有哪些集。
        """
        目录 = Path(单元.路径)
        扫的目录 = 目录.parent if 目录.name.lower().startswith("season") else 目录
        try:
            结果 = 扫描媒体库(扫的目录, 最小文件字节=self.设置.最小文件字节)
        except Exception as 错:  # noqa: BLE001 - 扫不动也不该让整次刮削失败
            self._日志(f"[刮削] 重扫 {扫的目录} 失败：{错}")
            return []
        return [x for x in 结果.条目们
                if x.类型 is 媒体类型.剧集 and x.集号 is not None]

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
