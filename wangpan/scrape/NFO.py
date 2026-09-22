"""NFO 读写（Kodi 风格的 XML 元数据文件）。

为什么要支持它（虽然我们有自己的 SQLite 库）：
* 用户换播放器/换软件时，**NFO 是唯一被大家共同认的"资料可携带格式"**
  （Kodi / Jellyfin / Emby / Plex 都读它）；
* 本地已有 NFO 的老库，扫描时应当**尊重它**（而不是把用户整理好的资料覆盖掉）；
* 导出 NFO 也是"把资料交还给用户"，避免被我们的数据库锁死。

实现取舍
========
* 只实现"最大公约数"字段：标题/原名/年份/评分/分级/简介/标签/时长/演职员/唯一 ID/季集；
  Kodi 的 NFO 字段有上百个，我们只写自己有的，**不造空标签**；
* 读的时候**容错优先**：编码依次试 UTF-8/UTF-8-BOM/GB18030；坏 XML 直接返回 None，不抛异常；
* 剧集用 `tvshow.nfo`（剧级）与 `season.nfo`（季级）、单集用 `<集文件名>.nfo`。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from .模型 import (人物, 人物工种, 参演, 分级, 媒体条目, 媒体类型, 季, 集)

__all__ = ["读NFO", "写NFO", "写剧集NFO", "写季NFO", "写单集NFO", "找NFO"]

#: 工种的中文 ↔ NFO 标签
工种标签 = {
    人物工种.演员: "actor",
    人物工种.导演: "director",
    人物工种.编剧: "credits",
    人物工种.制片: "producer",
    人物工种.作曲: "composer",
}


def _读文本(路径: Path) -> Optional[str]:
    for 编码 in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return 路径.read_text(encoding=编码)
        except (UnicodeDecodeError, LookupError):
            continue
        except OSError:
            return None
    try:
        return 路径.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _文本(根: ET.Element, 标签: str) -> str:
    节点 = 根.find(标签)
    return (节点.text or "").strip() if 节点 is not None and 节点.text else ""


def _整数(根: ET.Element, 标签: str) -> int:
    文本 = _文本(根, 标签)
    数字 = re.findall(r"\d+", 文本)
    return int(数字[0]) if 数字 else 0


def 读NFO(路径: Path | str) -> Optional[媒体条目]:
    """读一个 NFO（电影或剧集）；坏文件返回 None。"""
    路径 = Path(路径)
    if not 路径.is_file():
        return None
    文本 = _读文本(路径)
    if not 文本:
        return None
    try:
        根 = ET.fromstring(文本.strip())
    except ET.ParseError:
        return None
    标题 = _文本(根, "title")
    原名 = _文本(根, "originaltitle")
    if not 标题 and not 原名:
        return None
    类型 = 媒体类型.剧集 if 根.tag.lower() in ("tvshow", "season", "episodedetails") \
        else 媒体类型.电影
    条目 = 媒体条目(类型=类型, 标题=标题 or 原名, 原名=原名, 年份=None,
                简介=_文本(根, "plot") or _文本(根, "outline"),
                来源="nfo")
    年份 = _整数(根, "year") or _整数(根, "premiered")
    if not 年份:
        日期 = _文本(根, "premiered") or _文本(根, "releasedate")
        m = re.match(r"(\d{4})", 日期)
        年份 = int(m.group(1)) if m else 0
    条目.年份 = 年份 or None
    评分文本 = _文本(根, "rating") or _文本(根, "vote_average")
    try:
        分 = float(re.findall(r"\d+(?:\.\d+)?", 评分文本)[0]) if 评分文本 else 0.0
    except (IndexError, ValueError):
        分 = 0.0
    # Kodi 的 rating 可能是 0–10 也可能是 0–100（百分制），按量级判断
    if 分 > 10:
        条目.影评人评分 = 分
    else:
        条目.评分 = 分
    条目.时长分钟 = _整数(根, "runtime")
    for 标签节点 in 根.findall("genre"):
        if 标签节点.text:
            条目.标签.append(标签节点.text.strip())
    for 标签节点 in 根.findall("tag"):
        if 标签节点.text and 标签节点.text.strip() not in 条目.标签:
            条目.标签.append(标签节点.text.strip())
    分级值 = _文本(根, "mpaa") or _文本(根, "certification")
    if 分级值:
        条目.分级们.append(分级(国家="US", 值=分级值, 来源="nfo"))
    for 键, 名 in (("tmdbid", "tmdb"), ("imdbid", "imdb"), ("tvdbid", "tvdb")):
        值 = _文本(根, 键) or _文本(根, f"uniqueid_{名}")
        if 值:
            条目.外部ID[名] = 值
    for 唯一 in 根.findall("uniqueid"):
        种类 = (唯一.get("type") or "").lower()
        值 = (唯一.text or "").strip()
        if 种类 and 值:
            条目.外部ID[种类] = 值
    # 演职员
    for 节点 in 根.findall("actor"):
        人 = 人物(名字=_文本(节点, "name"), 角色=None, 来源="nfo")  # type: ignore[arg-type]
        人 = 人物(名字=_文本(节点, "name"), 来源="nfo")
        角色 = _文本(节点, "role")
        条目.参演们.append(参演(人物=人, 工种=人物工种.演员, 角色=角色,
                            排序=len(条目.参演们)))
    for 标签, 工种 in (("director", 人物工种.导演), ("credits", 人物工种.编剧),
                    ("writer", 人物工种.编剧), ("producer", 人物工种.制片)):
        for 节点 in 根.findall(标签):
            if 节点.text and 节点.text.strip():
                条目.参演们.append(参演(人物=人物(名字=节点.text.strip(), 来源="nfo"),
                                    工种=工种, 排序=900))
    return 条目


def 写NFO(条目: 媒体条目, 路径: Path | str) -> bool:
    """写电影/剧集级 NFO（剧集写 tvshow 根节点）。"""
    路径 = Path(路径)
    try:
        根 = ET.Element("tvshow" if 条目.类型 is 媒体类型.剧集 else "movie")
        _加(根, "title", 条目.标题)
        _加(根, "originaltitle", 条目.原名)
        if 条目.年份:
            _加(根, "year", str(条目.年份))
            _加(根, "premiered", str(条目.年份))
        if 条目.评分:
            _加(根, "rating", f"{条目.评分:.1f}")
            _加(根, "votes", str(条目.评分票数))
        if 条目.影评人评分:                      # 百分制，单独放一个我们自己的标签，别污染 rating
            _加(根, "criticrating", f"{条目.影评人评分:.0f}")
        if 条目.时长分钟:
            _加(根, "runtime", str(条目.时长分钟))
        _加(根, "plot", 条目.简介)
        _加(根, "outline", 条目.简介)
        if 条目.状态:
            _加(根, "status", 条目.状态)
        for 名 in 条目.标签:
            _加(根, "genre", 名)
        级 = 条目.取分级()
        if 级 is not None:
            _加(根, "mpaa", 级.值)
            _加(根, "certification", 级.值)
        for 键, 名 in (("tmdb", "tmdbid"), ("imdb", "imdbid"), ("tvdb", "tvdbid")):
            值 = 条目.外部ID.get(键)
            if 值:
                _加(根, 名, 值)
                唯一 = ET.SubElement(根, "uniqueid", {"type": 键, "default": "true"})
                唯一.text = 值
        for 关系 in 条目.参演们:
            标签 = 工种标签.get(关系.工种, "actor")
            if 标签 == "actor":
                节点 = ET.SubElement(根, "actor")
                _加(节点, "name", 关系.人物.名字)
                if 关系.角色:
                    _加(节点, "role", 关系.角色)
                if 关系.排序 < 900:
                    _加(节点, "order", str(关系.排序))
            else:
                _加(根, 标签, 关系.人物.名字)
        _缩进(根)
        临时 = 路径.with_suffix(路径.suffix + ".部分")
        临时.write_text('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                     + ET.tostring(根, encoding="unicode"), encoding="utf-8")
        临时.replace(路径)
        return True
    except OSError:
        return False


def 写季NFO(季对象: 季, 路径: Path | str) -> bool:
    路径 = Path(路径)
    try:
        根 = ET.Element("season")
        _加(根, "seasonnumber", str(季对象.季号))
        _加(根, "title", 季对象.标题)
        _加(根, "plot", 季对象.简介)
        if 季对象.播出日期:
            _加(根, "premiered", 季对象.播出日期)
        _缩进(根)
        路径.parent.mkdir(parents=True, exist_ok=True)
        路径.write_text('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    + ET.tostring(根, encoding="unicode"), encoding="utf-8")
        return True
    except OSError:
        return False


def 写单集NFO(集对象: 集, 剧名: str, 路径: Path | str) -> bool:
    路径 = Path(路径)
    try:
        根 = ET.Element("episodedetails")
        _加(根, "title", 集对象.标题 or f"{集对象.编号}")
        _加(根, "season", str(集对象.季号))
        _加(根, "episode", str(集对象.集号))
        if 集对象.集号到:
            _加(根, "episodenumberend", str(集对象.集号到))
        _加(根, "plot", 集对象.简介)
        if 集对象.播出日期:
            _加(根, "aired", 集对象.播出日期)
        if 集对象.评分:
            _加(根, "rating", f"{集对象.评分:.1f}")
        if 集对象.时长分钟:
            _加(根, "runtime", str(集对象.时长分钟))
        _加(根, "showtitle", 剧名)
        if 集对象.外部ID:
            唯一 = ET.SubElement(根, "uniqueid", {"type": "tmdb", "default": "true"})
            唯一.text = str(集对象.外部ID)
        _缩进(根)
        路径.parent.mkdir(parents=True, exist_ok=True)
        路径.write_text('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    + ET.tostring(根, encoding="unicode"), encoding="utf-8")
        return True
    except OSError:
        return False


def 找NFO(媒体目录: Path | str, 文件名主干: str = "") -> Optional[Path]:
    """在一个媒体目录里找 NFO（优先"同名 NFO"，再 `movie.nfo`/`tvshow.nfo`）。"""
    目录 = Path(媒体目录)
    if not 目录.is_dir():
        return None
    候选 = []
    if 文件名主干:
        候选 += [目录 / f"{文件名主干}.nfo"]
    候选 += [目录 / "movie.nfo", 目录 / "tvshow.nfo", 目录 / "season.nfo"]
    # 目录里唯一的 .nfo 也算（很多老库就这么放）
    其他 = sorted(p for p in 目录.glob("*.nfo"))
    候选 += 其他
    for 路径 in 候选:
        if 路径.is_file():
            return 路径
    return None


# ---------------- 小工具 ----------------

def _加(根: ET.Element, 标签: str, 文本: str) -> None:
    if not 文本:
        return
    节点 = ET.SubElement(根, 标签)
    节点.text = str(文本)


def _缩进(根: ET.Element) -> None:
    尝试 = getattr(ET, "indent", None)
    if 尝试 is not None:
        尝试(根, space="  ")
