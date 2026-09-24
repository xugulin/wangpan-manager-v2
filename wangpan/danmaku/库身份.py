"""用**资料库里的识别结果**去取弹幕（而不是文件名）。

为什么要有这一层（用户原话）
============================
「获取弹幕应该通过刮削来获取准确的信息来获取对应的弹幕（因为用户会乱放文件夹乱改名，
所以文件名不可信）」。

真机踩过的现象：光鸭上那一集被改成 ``146.SDR.8bit.2160p.60fps.AAC2.0.WEB-DL.H265.mp4``
（目录名也不带剧名），拿这个"名字"去弹幕源搜，搜到的自然和片子一点关系都没有。
而刮削之后，资料库里那条记录上明明有：TMDB id / 正式标题 / 原名 / 年份 / 类型 / 季 / 集。

所以顺序必须是：

1. **先查资料库**（``资料库.按路径找媒体`` + ``按路径找集``）→ 拿身份与**库里的季集**；
2. 库里没有（还没刮削）→ 才退回文件名解析，并且**日志里明说这是兜底、可能不准**。

这一层的输出是 :class:`~wangpan.danmaku.源.接口.素材信息`（源只认它）：
``来自资料库=True`` + 标题/原名/季/集/标识，源据此走"按作品名搜索"而不是"按文件名 match"。

为什么放在 ``danmaku`` 包里而不是界面里：候选匹配、源、以及将来的自动取弹幕都要走同一条
路；放在界面里就会变成"只有播放页那一处是对的"。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .源.接口 import 素材信息

__all__ = ["库身份", "取库身份", "造素材", "库路径们", "打开资料库"]

日志 = logging.getLogger(__name__)


@dataclass
class 库身份:
    """资料库里这条文件属于谁（刮削结果）。"""

    媒体id: int = 0
    类型: str = ""                  # "movie" / "tv"
    标题: str = ""                  # 正式标题（中文名优先）
    原名: str = ""
    年份: Optional[int] = None
    标识: str = ""                  # TMDB id
    季号: Optional[int] = None
    集号: Optional[int] = None
    集标题: str = ""
    库路径: str = ""                # 命中的那条库路径（诊断用）
    文件路径: Optional[Path] = None

    def 搜索词们(self) -> list[str]:
        出: list[str] = []
        for 词 in (self.标题, self.原名):
            词 = str(词 or "").strip()
            if 词 and 词 not in 出:
                出.append(词)
        return 出

    def 一句话(self) -> str:
        季集 = ""
        if self.季号 is not None and self.集号 is not None:
            季集 = f" S{int(self.季号):02d}E{int(self.集号):02d}"
        elif self.集号 is not None:
            季集 = f" 第{int(self.集号)}集"
        年份 = f"（{self.年份}）" if self.年份 else ""
        标识 = f"｜TMDB {self.标识}" if self.标识 else ""
        return f"{self.标题 or self.原名 or '(无标题)'}{年份}{季集}{标识}"


# ---------------------------------------------------------------------------
# 路径 → 资料库
# ---------------------------------------------------------------------------

def 库路径们(路径: Optional[str | Path] = None, *, 文件名: str = "",
           远端路径: str = "", 网盘标识: str = "") -> list[str]:
    """可能的**库里路径**写法（按可信度排序，第一个命中就算命中）。

    库里对网盘文件的写法是 ``标识:远端路径``（见 ``媒体库来源.库路径``），
    本地文件就是它自己的绝对路径。播放时手上通常同时有"直链/本地路径"与
    "网盘标识 + 远端路径"，所以这里把几种写法都列出来。
    """
    出: list[str] = []

    def 加(文本: str) -> None:
        文本 = str(文本 or "").strip()
        if 文本 and 文本 not in 出:
            出.append(文本)

    标识 = str(网盘标识 or "").strip()
    本地 = str(路径 or "").strip()
    远端 = str(远端路径 or "").strip()
    名字 = str(文件名 or "").strip() or (Path(本地).name if 本地 else "")

    def 加带前缀(文本: str) -> None:
        加(文本)
        if 标识 and 标识 not in ("本地", "local"):
            加(f"{标识}:{文本}")

    if 本地:
        加(本地)
    加带前缀(远端)
    if 远端 and 名字 and not 远端.endswith(名字):
        加带前缀(f"{远端.rstrip('/')}/{名字}")
    return 出


def 打开资料库(路径=None):
    """打开资料库；**打不开就返回 None**（弹幕不该因为资料库坏了就用不了）。

    为什么软失败：弹幕是播放的附加功能，资料库文件被占用/损坏时"退回文件名"仍然能看，
    不能反过来把播放页搞崩。
    """
    try:
        from ..scrape.库 import 资料库
        return 资料库(路径) if 路径 else 资料库()
    except Exception as 错:  # noqa: BLE001
        日志.warning("资料库打不开（弹幕退回文件名兜底）：%s", 错)
        return None


def 取库身份(路径们: Iterable[str | Path], 资料库=None) -> Optional[库身份]:
    """在这些"库路径"里找第一个能认出来的文件 → :class:`库身份`。

    ``资料库=None`` 时自己打开一份（调用方通常传进来，省一次连接）。
    """
    库 = 资料库 if 资料库 is not None else 打开资料库()
    if 库 is None:
        return None
    for 文本 in 路径们 or ():
        文本 = str(文本 or "").strip()
        if not 文本:
            continue
        try:
            集行 = 库.按路径找集(文本)
            媒体id = int(集行["媒体id"]) if 集行 is not None else None
            if 媒体id is None:
                媒体id = 库.按路径找媒体(文本)
                if 媒体id is None:
                    continue
            条目 = 库.取媒体(int(媒体id))
            if 条目 is None:
                continue
            身份 = 库身份(
                媒体id=int(媒体id), 类型=条目.类型.value, 标题=条目.标题,
                原名=条目.原名, 年份=条目.年份,
                标识=str(条目.外部ID.get("tmdb") or ""),
                库路径=文本, 文件路径=Path(文本) if ":" not in 文本 else None)
            if 集行 is not None:
                身份.季号 = int(集行["季号"]) if 集行["季号"] is not None else None
                身份.集号 = int(集行["集号"]) if 集行["集号"] is not None else None
                身份.集标题 = str(集行["标题"] or "")
            else:
                # 文件表命中但没有"集"行（电影）→ 看条目自己的主文件/剧集季集兜一次
                身份.季号, 身份.集号, 身份.集标题 = _从条目找季集(条目, 文本)
            return 身份
        except Exception as 错:  # noqa: BLE001 - 单条路径查不动不该毁掉整次匹配
            日志.warning("按路径 %s 取库身份失败：%s", 文本, 错)
            continue
    return None


def _从条目找季集(条目, 路径文本: str) -> tuple[Optional[int], Optional[int], str]:
    """条目里"哪一集的文件路径是它" → ``(季号, 集号, 集标题)``；找不到给 ``(None, None, "")``。"""
    for 季对象 in getattr(条目, "季们", []) or []:
        for 集对象 in getattr(季对象, "集们", []) or []:
            if 集对象.文件路径 is not None and str(集对象.文件路径) == str(路径文本):
                return (int(季对象.季号), int(集对象.集号),
                       str(getattr(集对象, "标题", "") or ""))
    return (None, None, "")


# ---------------------------------------------------------------------------
# 素材：库身份优先，文件名兜底
# ---------------------------------------------------------------------------

def 造素材(路径: Optional[str | Path] = None, *, 文件名: str = "",
          远端路径: str = "", 网盘标识: str = "", 时长秒: float = 0.0,
          资料库=None, 日志回调=None) -> tuple[素材信息, str]:
    """造一份 :class:`素材信息`：**先资料库身份，库里没有才退回文件名**。

    返回 ``(素材, 说明)``：``说明`` 是要打进日志/提示的一行话（"用的是库身份" 还是
    "文件名兜底，可能不准"），调用方必须把它显示出来 —— 用户有权知道这次匹配依据是什么。
    """
    路径对象 = Path(路径) if 路径 else None
    名字 = str(文件名 or "").strip() or (路径对象.name if 路径对象 is not None else "")
    候选 = 库路径们(路径对象, 文件名=名字, 远端路径=远端路径, 网盘标识=网盘标识)
    身份 = 取库身份(候选, 资料库) if 候选 else None

    def 说(文本: str) -> None:
        if 日志回调 is not None:
            try:
                日志回调(str(文本))
            except Exception:  # noqa: BLE001 - 记日志不该影响匹配
                pass

    if 身份 is not None:
        素材 = 素材信息(
            路径=路径对象, 文件名=名字, 时长秒=float(时长秒 or 0.0),
            标题=身份.标题 or 身份.原名, 原名=身份.原名, 年份=身份.年份,
            标识=身份.标识, 季号=身份.季号, 集号=身份.集号,
            来自资料库=True,
            额外={"集标题": 身份.集标题, "库路径": 身份.库路径,
                "媒体id": 身份.媒体id, "类型": 身份.类型})
        说明 = (f"用资料库身份：{身份.一句话()}"
              + (f"（库路径 {身份.库路径}）" if 身份.库路径 else ""))
        说(f"[弹幕] {说明}")
        return 素材, 说明

    # ---- 兜底：库里没有（还没刮削）→ 用文件名解析（**明确告诉用户这可能不准**）----
    标题, 季号, 集号, 集到 = "", None, None, None
    try:
        from ..scrape.命名解析 import 解析 as 解析文件名
        父目录名 = 路径对象.parent.name if 路径对象 is not None else Path(远端路径 or "").parent.name
        解 = 解析文件名(名字, 父目录名=父目录名, 季目录名=父目录名)
        标题, 季号, 集号, 集到 = 解.标题 or "", 解.季, 解.集, 解.集到
    except Exception as 错:  # noqa: BLE001 - 解析失败就只剩文件名，照旧能搜
        说(f"[弹幕] 文件名解析失败（不影响继续找）：{错}")
    素材 = 素材信息(
        路径=路径对象, 文件名=名字, 时长秒=float(时长秒 or 0.0), 标题=标题,
        季号=季号, 集号=集号, 来自资料库=False,
        额外={"集标题": "", "集到": 集到, "库路径候选": 候选})
    说明 = (f"资料库里没有这条识别结果，**退回用文件名**（{名字}）"
          f"—— 名字被改过的话可能匹配到别的片子")
    说(f"[弹幕] {说明}")
    return 素材, 说明
