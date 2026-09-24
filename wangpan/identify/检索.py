"""④ 检索：多查询 × 双接口（电影 / 剧集）→ 合并去重 + 缓存（P2 的右半边）。

为什么不是"一次搜索定生死"
==========================
旧实现是"一个查询词 + 一个接口 + 一次请求"。真机上必然翻车的两种情形：

* **类型未知**：``沙丘.2021…`` 到底是电影还是剧集？只搜电影接口会漏掉同名剧集，
  只搜剧集接口会漏掉电影 —— 所以类型未知时**两个接口都要搜**（一次查询两个端点）；
* **改名规避限制**：``146.SDR.8bit…mp4`` 这类文件的作品名只在目录里，
  所以 ③ 会给出多个查询词，这里要把它们**都搜一遍**，再把结果合并。

合并时保留"这条候选是被哪几个查询词搜出来的"（:attr:`检索候选.来源`）——
这是 ⑤ 打分**最便宜也最有效**的证据：一部剧被"仙逆"和"Renegade Immortal"两个查询词
同时搜到，比只被一个模糊词搜到的候选可信得多。丢掉这个信息，多查询就白做了。

为什么要缓存
============
真机场景是"一个目录几十个文件，每个文件的目录链一模一样"（``仙逆.4K高码…`` 下 16 集）。
不缓存的话，同一部剧的同一批查询词会被重复搜 16 遍：慢、白挨限流（TMDB 现行约 40 请求/秒
且随时会变，见 ``wangpan/scrape/tmdb.py``），而且没必要 —— 同一目录的同一天，
答案不会变。所以：
* **进程内缓存**：键 = ``(查询词, 接口类型, 年份, 命名空间)``，同一进程里第二次直接命中；
* **可选落盘缓存**（``数据/识别检索缓存.json``）：跨次启动也省接口，**键里带日期**，
  跨天自动失效（剧集在更新、新剧在播，昨天的"查不到"今天可能就有了）。
  落盘是**可选**的：库函数默认不写用户的数据目录，要省接口的场景（真网探测 / 管线）
  显式打开它。

失败要软
========
单个查询词报错（网络抖、429、代理断）**不许让整轮失败**：真机实测同一批查询里
"剧集接口通、电影接口 TLS 握手偶发失败"是常态。失败的记进 :attr:`检索结果.错误们`，
继续跑其它查询词 —— 召回是"只要有路走通就不算丢"，不是"一次不通全盘皆输"。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .候选 import 查询词 as 候选查询词
from .候选 import 查询集合, 归一类型, 去重键

__all__ = [
    "检索候选", "查询命中", "检索错误", "检索结果", "检索", "检索缓存",
    "默认落盘缓存路径", "类型_电影", "类型_剧集", "归一查询词们", "归一类型偏好",
]

类型_电影 = "movie"
类型_剧集 = "tv"

日志 = logging.getLogger("wangpan.identify.检索")

#: 落盘缓存的格式版本：形状改了要能认出来并**当没有**（旧格式当成新格式解析会出错数据）。
缓存版本 = 1


def 默认落盘缓存路径() -> Path:
    """``数据/识别检索缓存.json``（``Path(__file__).parents[2]`` = 项目根，同 tmdb.py 的算法）。

    ⚠️ 这个路径是**对外契约**（``tests/test_识别检索.py`` 钉着"必须在数据目录"），
    所以"测试要落到临时文件"那件事不走这里 —— 见 ``服务.py`` 的
    ``_识别检索落盘路径()``。
    """
    return Path(__file__).resolve().parents[2] / "数据" / "识别检索缓存.json"


def 今天(现在=None) -> str:
    当前 = float(现在() if callable(现在) else time.time())
    return time.strftime("%Y-%m-%d", time.localtime(当前))


# ============================ 数据结构 ============================


@dataclass
class 检索候选:
    """合并去重后的一条候选（同一 tmdb id + 同一类型只算一条）。

    ``来源`` 是**证据**：``{查询词文本: 权重}``。⑤ 打分靠它知道"这条候选是怎么被找到的"，
    真网探测脚本靠它把"哪个查询词命中了什么"打给人看。
    """

    标识: str = ""                       # tmdb id
    类型: str = 类型_电影                # movie / tv
    标题: str = ""
    原名: str = ""
    年份: Optional[int] = None
    简介: str = ""
    海报远端: str = ""
    热度: float = 0.0
    票数: int = 0
    评分: float = 0.0
    原始语言: str = ""
    来源: dict = field(default_factory=dict)      # 查询词文本 → 权重
    原始: Any = None                              # 客户端返回的原对象（不落盘）

    def 来源查询们(self) -> list[str]:
        """按权重从高到低排的命中查询词（⑤ 打分与人工核对都按这个顺序看）。"""
        return [x for x, _ in sorted(self.来源.items(), key=lambda kv: -float(kv[1]))]

    def 最高权重(self) -> float:
        return max((float(x) for x in self.来源.values()), default=0.0)

    def 可读(self) -> str:
        年 = f"（{self.年份}）" if self.年份 else ""
        证据 = "、".join(self.来源查询们())
        return (f"[{self.类型}#{self.标识}] {self.标题}{年}｜热度 {self.热度:.1f}"
                f"｜被 {len(self.来源)} 个查询词搜到：{证据}")

    # ---- 序列化（落盘缓存要能把它存下来再读回来）----

    def 到字典(self) -> dict:
        return {"标识": self.标识, "类型": self.类型, "标题": self.标题, "原名": self.原名,
                "年份": self.年份, "简介": self.简介, "海报远端": self.海报远端,
                "热度": self.热度, "票数": self.票数, "评分": self.评分,
                "原始语言": self.原始语言, "来源": dict(self.来源)}

    @classmethod
    def 从字典(cls, 数据: Mapping) -> "检索候选":
        数据 = 数据 if isinstance(数据, Mapping) else {}

        def _整数(值):
            try:
                return int(值)
            except (TypeError, ValueError):
                return 0

        def _浮点(值):
            try:
                return float(值)
            except (TypeError, ValueError):
                return 0.0

        来源 = 数据.get("来源")
        return cls(标识=str(数据.get("标识") or ""), 类型=str(数据.get("类型") or 类型_电影),
                   标题=str(数据.get("标题") or ""), 原名=str(数据.get("原名") or ""),
                   年份=_可选整数(数据.get("年份")),
                   简介=str(数据.get("简介") or ""), 海报远端=str(数据.get("海报远端") or ""),
                   热度=_浮点(数据.get("热度")), 票数=_整数(数据.get("票数")),
                   评分=_浮点(数据.get("评分")), 原始语言=str(数据.get("原始语言") or ""),
                   来源={str(k): _浮点(v) for k, v in (来源 or {}).items()}
                   if isinstance(来源, Mapping) else {})


@dataclass
class 查询命中:
    """**一次**（查询词 × 接口）的原始结果 —— 合并前的证据，真网探测要一条条打出来。"""

    查询词: str
    类型: str
    候选们: list = field(default_factory=list)
    查询词对象: Any = None
    错误: str = ""
    来自缓存: bool = False
    年份回退: bool = False               # 带年份搜不到 → 退回不带年份重搜过

    def 前几条(self, 数: int = 5) -> str:
        if self.错误:
            return f"× {self.错误}"
        if not self.候选们:
            return "（0 条）"
        部分 = [f"{x.标题}{f'（{x.年份}）' if x.年份 else ''}"
                f" [{'电影' if x.类型 == 类型_电影 else '剧集'}#{x.标识}]"
                for x in self.候选们[:数]]
        尾巴 = f" …共 {len(self.候选们)} 条" if len(self.候选们) > 数 else ""
        return "；".join(部分) + 尾巴


@dataclass
class 检索错误:
    查询词: str
    类型: str
    原因: str

    def 可读(self) -> str:
        return f"{self.查询词 or '（整体）'}[{self.类型 or '-'}]：{self.原因}"


@dataclass
class 检索结果:
    候选们: list = field(default_factory=list)          # 合并去重后的候选（已排序）
    命中们: list = field(default_factory=list)          # 每个 (查询词 × 接口) 的原始命中
    错误们: list = field(default_factory=list)
    请求次数: int = 0                                   # 真打到客户端的次数（不含缓存命中）
    缓存命中: int = 0
    年份回退数: int = 0
    类型们: list = field(default_factory=list)
    查询词们: list = field(default_factory=list)

    def 有错(self) -> bool:
        return bool(self.错误们)

    def 汇总(self) -> str:
        return (f"查询词 {len(self.查询词们)} 个 × 接口 {len(self.类型们)} 个 → "
                f"候选 {len(self.候选们)} 条（请求 {self.请求次数} 次，缓存命中 "
                f"{self.缓存命中} 次，错误 {len(self.错误们)} 个）")


# ============================ 入参归一 ============================


def 归一类型偏好(偏好: Any) -> list[str]:
    """``tv`` / ``movie`` / ``unknown`` → 要搜的接口列表（unknown = 两个都搜）。

    ``unknown`` 是**故意**保留的一档：宁可多打一次接口，也不要在信息不足时赌错方向。
    """
    文本 = str(getattr(偏好, "value", 偏好) or "").strip().lower()
    归一 = 归一类型(文本)
    if 归一 == 类型_剧集:
        return [类型_剧集]
    if 归一 == 类型_电影:
        return [类型_电影]
    return [类型_剧集, 类型_电影]


def 归一查询词们(入参: Any) -> list[候选查询词]:
    """接受 ③ 的 :class:`查询集合` / ``list[查询词]`` / ``list[str]`` / 单个字符串。

    为什么这么宽容：真网探测、单测、将来的管线会拿不同形状来调它，
    让调用方为了"凑一个类型"而写包装层不值得；分不清的（比如空串）直接丢掉。
    """
    if 入参 is None:
        return []
    词们: list[候选查询词] = []
    if isinstance(入参, 查询集合):
        词们 = list(入参.查询词们)
    elif isinstance(入参, 候选查询词):
        词们 = [入参]
    elif isinstance(入参, str):
        词们 = [候选查询词(文本=入参, 来源="文件标题", 权重=1.0)]
    elif isinstance(入参, Iterable):
        for 项 in 入参:
            if isinstance(项, 候选查询词):
                词们.append(项)
            elif isinstance(项, str) and 项.strip():
                词们.append(候选查询词(文本=项.strip(), 来源="文件标题", 权重=1.0))
    去重: dict[str, 候选查询词] = {}
    for 词 in 词们:
        文本 = (词.文本 or "").strip()
        if not 文本:
            continue
        键 = 去重键(文本)
        if 键 and (键 not in 去重 or 词.权重 > 去重[键].权重):
            去重[键] = 词
    return sorted(去重.values(), key=lambda x: -float(x.权重))


# ============================ 缓存 ============================


class 检索缓存:
    """进程内 + 可选落盘的两级缓存，键 = ``(命名空间, 接口类型, 年份, 查询词)``。

    **命名空间为什么必须进键**：同一份缓存会被"官方 API / 自建代理 / 环回假服务"先后用过
    （真机验证就是这么跑的，见 ``tests/假TMDB服务.py``），而语言不同搜出来的标题也不同。
    不带命名空间的话，假服务的数据会被当成 TMDB 的真数据喂给打分/海报墙 —— 这种错很难看出来
    （同样的教训在 ``wangpan/scrape/tmdb.py`` 的 ``_缓存路径`` 里已经吃过一次）。

    **日期为什么进键**：见 ``数据/识别`` 的需求 —— 剧集在更新，跨天该重搜；
    只保留"今天"的条目，既保证过期语义，又天然限制文件不会无限长大。
    """

    #: 键分隔符用 US（\x1f）：查询词里可能出现 ``|`` ``:`` 等任何可见字符，用它才不会撞车。
    _分隔 = "\x1f"

    def __init__(self, 落盘: Optional[Path] = None, 现在=None) -> None:
        self._内存: dict[str, list[dict]] = {}
        self._落盘路径 = Path(落盘) if 落盘 else None
        self._落盘表: Optional[dict] = None      # 懒加载
        self._现在 = 现在
        self.内存命中 = 0
        self.落盘命中 = 0
        self.未命中 = 0
        self.写入数 = 0
        self.错误 = ""

    # ---- 键 ----

    def 建键(self, 命名空间: str, 类型: str, 年份: Optional[int], 查询词: str) -> str:
        return self._分隔.join([命名空间 or "", 类型 or "", "" if not 年份 else str(年份),
                              (查询词 or "").strip()])

    # ---- 读写 ----

    def 取(self, 键: str) -> Optional[list[dict]]:
        if 键 in self._内存:
            self.内存命中 += 1
            return self._内存[键]
        落盘 = self._读落盘()
        if 落盘 is not None and 键 in 落盘:
            self.落盘命中 += 1
            self._内存[键] = 落盘[键]           # 顺便提到内存，第二次不再读盘
            return self._内存[键]
        self.未命中 += 1
        return None

    def 存(self, 键: str, 候选们: Sequence[Mapping]) -> None:
        条目 = [dict(x) for x in 候选们 if isinstance(x, Mapping)]
        self._内存[键] = 条目
        self.写入数 += 1
        if self._落盘路径 is not None:
            落盘 = self._读落盘()
            if 落盘 is not None:
                落盘[键] = 条目

    def 设落盘(self, 路径: Optional[Path]) -> None:
        """给已存在的缓存补一个落盘路径（管线先建缓存、后来才决定要落盘时用）。"""
        if 路径 and self._落盘路径 is None:
            self._落盘路径 = Path(路径)
            self._落盘表 = None

    @property
    def 落盘路径(self) -> Optional[Path]:
        """当前生效的落盘文件（``None`` = 只在内存里缓存）。"""
        return self._落盘路径

    # ---- 落盘 ----

    def _读落盘(self) -> Optional[dict]:
        """读一次落盘文件（懒加载）。**坏文件当没有**：缓存绝不能让检索失败。"""
        if self._落盘路径 is None:
            return None
        if self._落盘表 is not None:
            return self._落盘表
        self._落盘表 = {}
        今天文本 = 今天(self._现在)
        try:
            原文 = json.loads(self._落盘路径.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._落盘表
        if not isinstance(原文, Mapping) or int(原文.get("版本") or 0) != 缓存版本:
            return self._落盘表
        条目 = 原文.get("条目")
        if not isinstance(条目, Mapping):
            return self._落盘表
        for 键, 值 in 条目.items():
            记录 = 值 if isinstance(值, Mapping) else {}
            # 日期不一致 = 过期：剧集在更新，昨天的"查不到"今天可能就有。
            if str(记录.get("日期") or "") != 今天文本:
                continue
            候选们 = 记录.get("候选们")
            if isinstance(候选们, list):
                self._落盘表[str(键)] = [dict(x) for x in 候选们 if isinstance(x, Mapping)]
        return self._落盘表

    def 保存(self) -> bool:
        """把"今天"的条目原子写回落盘文件（返回是否真的写了）。

        ``_落盘表`` 也要一起写回去：那些是**本次启动读过但没用到的**条目
        （比如同时识别 16 集，这次只用到其中几条），只写 ``_内存`` 会把它们抹掉 ——
        下次启动就又得重新打接口，缓存等于被自己毁掉一半。
        """
        if self._落盘路径 is None:
            return False
        try:
            self._落盘路径.parent.mkdir(parents=True, exist_ok=True)
            今天文本 = 今天(self._现在)
            全部 = dict(self._落盘表 or {})
            全部.update(self._内存)
            条目 = {键: {"日期": 今天文本, "候选们": 值} for 键, 值 in 全部.items()}
            临时 = self._落盘路径.with_name(self._落盘路径.name + ".part")
            临时.write_text(json.dumps({"版本": 缓存版本, "日期": 今天文本, "条目": 条目},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
            临时.replace(self._落盘路径)      # 原子替换：半截 JSON 比没有缓存更糟
            return True
        except OSError as 异常:
            self.错误 = f"检索缓存落盘失败：{异常}"
            日志.debug("%s", self.错误)
            return False

    def 统计(self) -> str:
        return (f"内存命中 {self.内存命中}｜落盘命中 {self.落盘命中}｜未命中 {self.未命中}"
                f"｜写入 {self.写入数}" + (f"｜{self.错误}" if self.错误 else ""))


#: 进程内共享的默认缓存。为什么要共享：真机是"同一目录几十个文件"逐个识别，
#: 每个文件都新建一个缓存对象就等于没有缓存。命名空间（语言/基地址）已经进了键，
#: 所以不同客户端之间不会串味。
_默认缓存: Optional[检索缓存] = None


def 默认缓存() -> 检索缓存:
    global _默认缓存
    if _默认缓存 is None:
        _默认缓存 = 检索缓存()
    return _默认缓存


# ============================ 候选归一（鸭子类型） ============================


def _取(对象: Any, 名字: str, 默认: Any = None) -> Any:
    if 对象 is None:
        return 默认
    if isinstance(对象, Mapping):
        值 = 对象.get(名字, 默认)
    else:
        值 = getattr(对象, 名字, 默认)
    return 默认 if 值 is None else 值


def _浮点(值, 默认: float = 0.0) -> float:
    try:
        return float(值)
    except (TypeError, ValueError):
        return 默认


def _整数(值, 默认: int = 0) -> int:
    try:
        return int(值)
    except (TypeError, ValueError):
        return 默认


def _可选整数(值) -> Optional[int]:
    """年份这类"可以没有"的整数：解析不出来就是 ``None``，**不许悄悄变成 0**。

    0 是个合法的年份位（特别是从缓存/脏 JSON 里读回来时），拿 0 顶替 None 会让
    ⑤ 打分把"年份未知"当成"公元前 0 年"，判成严重不符。
    """
    if 值 in (None, ""):
        return None
    try:
        return int(值)
    except (TypeError, ValueError):
        try:
            return int(float(值))
        except (TypeError, ValueError):
            return None


def _规范候选(原: Any, 类型: str) -> 检索候选:
    """把客户端返回的一条（``匹配候选`` 或等价的 dict/对象）摊成 :class:`检索候选`。

    为什么要摊平而不是直接抱着原对象：**缓存要能序列化**。抱着原对象就没法落盘，
    而"同一目录 16 集不重复打接口"正是靠缓存。原对象仍然留在 :attr:`检索候选.原始`
    里供同进程使用（⑤ 打分可能要它的 ``额外`` 字段）。
    """
    额外 = _取(原, "额外", {})
    额外 = 额外 if isinstance(额外, Mapping) else {}
    类型值 = str(getattr(_取(原, "类型", 类型), "value", _取(原, "类型", 类型)) or 类型)
    return 检索候选(
        标识=str(_取(原, "来源标识", "") or ""),
        类型=类型 if 类型值 not in (类型_电影, 类型_剧集) else 类型值,
        标题=str(_取(原, "标题", "") or ""),
        原名=str(_取(原, "原名", "") or ""),
        年份=_可选整数(_取(原, "年份")),
        简介=str(_取(原, "简介", "") or ""),
        海报远端=str(_取(原, "海报远端", "") or ""),
        热度=_浮点(_取(原, "热度")),
        票数=_整数(_取(原, "票数", 额外.get("票数"))),
        评分=_浮点(_取(原, "评分", 额外.get("评分"))),
        原始语言=str(_取(原, "原始语言", 额外.get("原始语言")) or ""),
        原始=原,
    )


def _候选键(候选: 检索候选) -> tuple:
    """合并去重的键：**同一 tmdb id + 同一类型算一条**。

    id 缺失时退回 ``(标题, 年份, 类型)``：拿不到 id 的响应（自建代理、异常数据）里，
    同名同年同类型的条目本来就应该算一条，否则候选表里会出现一堆重复行。
    """
    if 候选.标识:
        return (候选.类型, "id", 候选.标识)
    return (候选.类型, "文字", 去重键(候选.标题), 候选.年份 or 0)


def 合并候选(条目们: Iterable[tuple[检索候选, str, float]]) -> list[检索候选]:
    """``[(候选, 查询词, 权重)]`` → 去重后的候选表（来源证据合并进 ``来源``）。"""
    表: dict[tuple, 检索候选] = {}
    for 候选, 查询词, 权重 in 条目们:
        键 = _候选键(候选)
        已有 = 表.get(键)
        if 已有 is None:
            候选.来源 = {查询词: float(权重)}
            表[键] = 候选
            continue
        已有.来源[查询词] = max(float(权重), float(已有.来源.get(查询词, 0.0)))
        # 补空：同一个 id 在剧集接口返回的字段可能比电影接口全（反之亦然），
        # 后到的非空字段把空字段补上，别让"先搜到的那个接口"决定信息量。
        for 字段 in ("标题", "原名", "简介", "海报远端", "原始语言"):
            if not getattr(已有, 字段) and getattr(候选, 字段):
                setattr(已有, 字段, getattr(候选, 字段))
        if not 已有.年份 and 候选.年份:
            已有.年份 = 候选.年份
        已有.热度 = max(已有.热度, 候选.热度)
        已有.票数 = max(已有.票数, 候选.票数)
        已有.评分 = max(已有.评分, 候选.评分)
    return sorted(表.values(),
                  key=lambda x: (-x.最高权重(), -x.热度, -x.票数, x.标识))


# ============================ 主函数 ============================


def _命名空间(客户端: Any) -> str:
    """缓存分区：语言 + 接口基地址（拿不到就退回类名）。

    语言决定 TMDB 返回的标题语言；基地址决定"对面是官方还是环回假服务"。
    这两个只要有一个不同，缓存就不能共用。
    """
    配置 = getattr(客户端, "配置", None)
    语言 = str(getattr(配置, "language", "") or "")
    基 = str(getattr(配置, "接口基地址", "") or "")
    if not 语言 and not 基:
        return f"类:{type(客户端).__name__}"
    return f"{语言}|{基 or '官方'}"


def _脱敏(客户端: Any, 文本: str) -> str:
    收尾 = getattr(客户端, "_脱敏", None)
    if callable(收尾):
        try:
            return str(收尾(文本))
        except Exception:                     # noqa: BLE001 - 脱敏失败不影响主流程
            pass
    return re.sub(r"(api_key=)[^&\s]+", r"\1***", str(文本))


def _错误文本(客户端: Any, 异常: BaseException) -> str:
    """异常 → 一句能贴进报告的原因（**先把 HTTP 状态码捞出来**）。

    为什么专门捞状态码：真机上"429 限流"、"401 凭据不对"、"连不上"三种情况的处置完全不同，
    而不少异常类型的 ``str()`` 里根本没有码（``tmdb.请求失败`` 就是把码放在 ``.状态码``
    属性上），不捞它等于把最有用的信息丢掉。
    """
    码 = ""
    for 属性 in ("状态码", "status", "status_code", "code"):
        值 = getattr(异常, 属性, None)
        if isinstance(值, int) and 值:
            码 = f"HTTP {值} "
            break
    return _脱敏(客户端, f"{码}{type(异常).__name__}: {异常}")


#: 类型 → 客户端方法名（真 ``TMDB客户端`` 就是这两个名字，见 wangpan/scrape/tmdb.py）
方法名 = {类型_剧集: "搜剧集", 类型_电影: "搜电影"}


def 检索(客户端: Any, 查询词们: Any, 类型偏好: Any = "unknown", *,
         过滤年份: Optional[int] = None, 缓存: Optional[检索缓存] = None,
         落盘: Any = None, 重试不带年份: bool = True) -> 检索结果:
    """④ 的主函数：多查询 × 双接口 → 合并去重 + 缓存。

    参数
    ----
    客户端
        **注入**的 TMDB 客户端（``wangpan/scrape/tmdb.py`` 的 ``TMDB客户端``，或任何有
        ``搜电影(标题, 年份=None)`` / ``搜剧集(标题, 年份=None)`` 的对象）。注入是为了
        单测能换成假客户端，也能在真机验证时指向环回假服务。
    查询词们
        ③ 的 :class:`~wangpan.identify.候选.查询集合`（``list[查询词]`` / ``list[str]`` 也认）。
    类型偏好
        ``tv`` / ``movie`` / ``unknown``（unknown = 电影与剧集**两个接口都搜**）。
    过滤年份
        ③ 返回的独立年份过滤条件。**它是软过滤**：带上年份一条都没搜到时会自动退回
        不带年份再搜一次（``first_air_date_year`` 是"首播年"，长播剧的集用别的年份命名
        是常事，硬过滤会把它们全杀掉）。退回过的事实记在
        :attr:`查询命中.年份回退` 与 :attr:`检索结果.年份回退数` 里。
    缓存 / 落盘
        见 :class:`检索缓存`。``缓存=None`` 时用进程内共享的 :func:`默认缓存`
        （这样同一目录的多个文件天然只搜一遍）；``落盘=`` 打开跨次启动的缓存
        （``落盘=True`` = :func:`默认落盘缓存路径`，也可以直接给一个 :class:`~pathlib.Path`）。
    """
    落盘路径 = 默认落盘缓存路径() if 落盘 is True else (Path(落盘) if 落盘 else None)
    词们 = 归一查询词们(查询词们)
    类型们 = 归一类型偏好(类型偏好)
    结果 = 检索结果(类型们=list(类型们), 查询词们=[x.文本 for x in 词们])

    if not 词们:
        结果.错误们.append(检索错误("", "", "没有查询词（③ 候选生成为空）"))
        return 结果
    if hasattr(客户端, "可用") and not 客户端.可用():
        结果.错误们.append(检索错误("", "", "TMDB 客户端没配鉴权（token / api_key），不发请求"))
        return 结果

    if 缓存 is None:
        缓存 = 检索缓存(落盘=落盘路径)
    else:
        缓存.设落盘(落盘路径)
    命名空间 = _命名空间(客户端)

    年份 = _可选整数(过滤年份)
    条目们: list[tuple[检索候选, str, float]] = []

    for 词 in 词们:
        for 种类 in 类型们:
            方法名_ = 方法名[种类]
            方法 = getattr(客户端, 方法名_, None)
            if not callable(方法):
                结果.错误们.append(检索错误(词.文本, 种类,
                                        f"客户端没有 {方法名_}(标题, 年份) 方法"))
                continue
            键 = 缓存.建键(命名空间, 种类, 年份, 词.文本)
            命中缓存 = 缓存.取(键)
            命中 = 查询命中(查询词=词.文本, 类型=种类, 查询词对象=词)
            if 命中缓存 is not None:
                命中.候选们 = [检索候选.从字典(x) for x in 命中缓存]
                命中.来自缓存 = True
                结果.缓存命中 += 1
            else:
                候选们: list[Any] = []
                带年份失败 = ""
                try:
                    # 计数放在调用**之前**：失败的请求也一样打了网络/挨了限流，
                    # "这次省了多少次接口"要按真实调用次数算，不能只算成功的。
                    结果.请求次数 += 1
                    if 年份 is not None:
                        候选们 = list(方法(词.文本, 年份) or [])
                    else:
                        候选们 = list(方法(词.文本) or [])
                except Exception as 异常:              # noqa: BLE001 - 单个查询词失败不许毁掉整轮
                    带年份失败 = _错误文本(客户端, 异常)
                    if not (年份 is not None and 重试不带年份):
                        命中.错误 = 带年份失败
                        结果.错误们.append(检索错误(词.文本, 种类, 带年份失败))
                        结果.命中们.append(命中)
                        continue
                    候选们 = []
                # 年份是软过滤：带上年份一条都没有（或者直接报错）→ 退回不带年份再搜一次。
                if 年份 is not None and 重试不带年份 and not 候选们:
                    try:
                        结果.请求次数 += 1
                        候选们 = list(方法(词.文本) or [])
                        命中.年份回退 = True
                        结果.年份回退数 += 1
                        if 带年份失败:
                            结果.错误们.append(检索错误(
                                词.文本, 种类, f"带年份 {年份} 搜索失败已退回不带年份：{带年份失败}"))
                    except Exception as 异常:          # noqa: BLE001
                        命中.错误 = _错误文本(客户端, 异常)
                        结果.错误们.append(检索错误(词.文本, 种类, 命中.错误))
                        结果.命中们.append(命中)
                        continue
                规范们 = [_规范候选(x, 种类) for x in 候选们]
                命中.候选们 = 规范们
                缓存.存(键, [x.到字典() for x in 规范们])
                if 命中.年份回退:
                    # 不带年份的结果也存一份：同一目录的其它文件往往没有年份，
                    # 它们能直接命中这份"无年份"的缓存，省掉一次多余的往返。
                    缓存.存(缓存.建键(命名空间, 种类, None, 词.文本),
                           [x.到字典() for x in 规范们])
            for 候选 in 命中.候选们:
                条目们.append((候选, 词.文本, float(词.权重)))
            结果.命中们.append(命中)

    结果.候选们 = 合并候选(条目们)
    if 缓存.落盘路径 is not None:
        # 每轮结束就落盘：真机是"一个文件一次识别"，等进程退出再存等于没存
        # （崩溃/被杀就没有了），而这份文件只有几十 KB。
        缓存.保存()
    return 结果
