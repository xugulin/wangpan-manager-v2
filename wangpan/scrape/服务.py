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
                 日志回调: Optional[Callable[[str], None]] = None) -> None:
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
        for 单元 in 全部:
            if not 单元.可刮削():
                continue
            self.库.记任务(str(单元.路径), "待处理", None,
                       单元.一句话())
        return len(全部), "；".join(摘要们)

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
    def _刮一个(self, 路径: Path, 结果: 服务结果) -> None:
        单元 = self._造单元(路径)
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

        # ---- 4) 挂文件 ----
        self._挂文件(最终, 单元)

        # ---- 5) 下图 ----
        if self.设置.下载图片:
            结果.下载图片 += self._下图(最终)

        # ---- 6) 落库 + NFO ----
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
        # 逐单元重扫：剧集的每个单元只对应一集（多版本已在单元里合并）
        目录 = 单元.路径
        结果 = 扫描媒体库(目录.parent if 目录.name.lower().startswith("season") else 目录,
                     最小文件字节=self.设置.最小文件字节)
        for 子单元 in 结果.条目们:
            if 子单元.类型 is not 媒体类型.剧集 or 子单元.集号 is None:
                continue
            键 = (int(子单元.季号 or 1), int(子单元.集号))
            目标 = 表.get(键)
            if 目标 is not None and 目标.文件路径 is None:
                目标.文件路径 = 子单元.主视频
        # 绝对集号的番剧（扫描不到季集）→ 按顺序兜底挂到第一季
        未挂 = [c for 季对象 in 条目.季们 for c in 季对象.集们 if c.文件路径 is None]
        待挂 = [v for v in 单元.视频们 if v not in
              {c.文件路径 for 季对象 in 条目.季们 for c in 季对象.集们}]
        if 单元.集号 is not None:
            目标 = 表.get((int(单元.季号 or 1), int(单元.集号)))
            for 视频 in 单元.视频们:
                if 目标 is None:
                    break
                if 目标.文件路径 is None:
                    目标.文件路径 = 视频
                elif 视频 != 目标.文件路径:
                    条目.额外文件.append(视频)      # 同一集的多版本

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
