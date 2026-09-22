"""媒体库扫描：把目录树变成"待刮削的条目"。

它只做**发现与归并**，不联网、不写库（那两件事在 :mod:`~wangpan.scrape.服务` 里）——
这样扫描可以单独测，也可以在没网时先跑完。

归并规则（决定了刮削次数，直接影响 API 用量）
==============================================
* 一部电影 = 一个**文件夹**（多版本/多段/番外都归到同一个作品）；
  但如果视频直接散在库根目录（没建文件夹），每个文件各算一部；
* 一部剧 = 一个剧文件夹；季目录归到季；剧集文件归到集；
* **跳过**：隐藏目录、`@eaDir`/`.DS_Store` 之类系统目录、
  体积过小的文件（默认 < 20MB 的当样片/花絮，可配）、
  明显是番外/花絮的文件（单独成"番外"条，不污染正片）；
* 同名的 NFO/图片/字幕被登记进条目的"附属文件"，刮削时优先用它们（本地优先）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .模型 import 媒体类型
from .命名解析 import (解析, 解析季目录, 提取发布标签, 猜剧集路径, 是同一集的不同版本)

__all__ = ["发现条目", "扫描结果", "扫描媒体库", "默认跳过目录", "视频扩展名"]

#: 视频扩展名（**只认这些**，别的都不当视频 —— 否则海报图会被当成影片）
视频扩展名 = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".mts",
         ".flv", ".wmv", ".m4v", ".mpg", ".mpeg", ".iso", ".rmvb", ".rm",
         ".vob", ".strm", ".mp3", ".flac", ".aac", ".m4a", ".wav", ".ogg"}

#: 默认跳过的目录（系统垃圾 + 我们自己的缓存）
默认跳过目录 = {".git", ".svn", "__pycache__", "@eadir", "#recycle", ".trash",
           "$recycle.bin", "system volume information", ".ds_store",
           "lost+found", "数据", "运行环境", ".venv", "venv", "node_modules",
           "extras", "behind the scenes", "deleted scenes", "interviews",
           "trailers", "featurettes", "clips", "samples", "shorts",
           "theme-music", "backdrops", "other"}

#: 附属文件（刮削时优先用本地的）
附图后缀 = {".jpg", ".jpeg", ".png", ".webp"}
附文后缀 = {".nfo"}
附字幕后缀 = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup"}


@dataclass
class 发现条目:
    """扫描发现的一个"待刮削单元"。"""

    类型: 媒体类型 = 媒体类型.未知
    路径: Path = field(default_factory=Path)        # 电影文件夹 / 剧文件夹 / 单个文件
    标题: str = ""
    年份: Optional[int] = None
    季号: Optional[int] = None
    集号: Optional[int] = None
    集到: Optional[int] = None
    数据源: str = ""
    数据源ID: str = ""
    视频们: list[Path] = field(default_factory=list)   # 该单元下的视频（多版本/多段）
    附属: dict[str, list[Path]] = field(default_factory=dict)  # {"nfo":[…], "poster":[…], "字幕":[…]}
    是番外: bool = False
    特别篇: bool = False
    版本: str = ""
    发布标签: dict = field(default_factory=dict)
    备注: str = ""

    @property
    def 主视频(self) -> Optional[Path]:
        return self.视频们[0] if self.视频们 else None

    def 一句话(self) -> str:
        季集 = ""
        if self.季号 is not None:
            季集 = f" S{self.季号:02d}"
        if self.集号 is not None:
            季集 += f"E{self.集号:02d}" + (f"-E{self.集到:02d}" if self.集到 else "")
        年份 = f"（{self.年份}）" if self.年份 else ""
        return (f"{self.标题 or '(未识别)'}{年份}{季集}｜{self.类型.value}"
                f"｜{len(self.视频们)} 个视频"
                + ("｜番外" if self.是番外 else "")
                + ("｜特别篇" if self.特别篇 else ""))

    def 可刮削(self) -> bool:
        return bool(self.视频们) and not self.是番外


@dataclass
class 扫描结果:
    条目们: list[发现条目] = field(default_factory=list)
    跳过文件数: int = 0
    跳过目录数: int = 0
    太大跳过: int = 0
    错误们: list[str] = field(default_factory=list)
    扫描目录数: int = 0

    @property
    def 电影数(self) -> int:
        return sum(1 for x in self.条目们 if x.类型 is 媒体类型.电影)

    @property
    def 剧集数(self) -> int:
        return len({x.路径 for x in self.条目们 if x.类型 is 媒体类型.剧集
                    and x.集号 is None})

    def 摘要(self) -> str:
        return (f"扫描 {self.扫描目录数} 个目录：发现 {len(self.条目们)} 个单元"
                f"（电影 {self.电影数}｜剧集 {self.剧集数}）"
                f"｜跳过文件 {self.跳过文件数}｜跳过目录 {self.跳过目录数}")


def _是视频(路径: Path) -> bool:
    return 路径.suffix.lower() in 视频扩展名


def _收集附属(目录: Path, 主干: str = "") -> dict[str, list[Path]]:
    结果: dict[str, list[Path]] = {"nfo": [], "图片": [], "字幕": []}
    try:
        文件们 = [p for p in 目录.iterdir() if p.is_file()]
    except OSError:
        return 结果
    for 文件 in 文件们:
        后缀 = 文件.suffix.lower()
        名 = 文件.name.lower()
        if 后缀 in 附文后缀:
            结果["nfo"].append(文件)
        elif 后缀 in 附图后缀:
            结果["图片"].append(文件)
        elif 后缀 in 附字幕后缀:
            结果["字幕"].append(文件)
        elif 名.endswith("-thumb.jpg") or 名.endswith("-poster.jpg"):
            pass
    return 结果


def _归并电影目录(目录: Path, 视频们: list[Path]) -> 发现条目:
    """把一个文件夹当成"一部电影"（多版本/多段合并）。"""
    名 = 目录.name
    解析结果 = 解析(视频们[0].name, 父目录名=名)
    标题 = 解析结果.标题 or 名
    年份 = 解析结果.年份
    if 年份 is None:
        # 文件夹名里通常带年份（"沙丘 (2021)"）
        父解析 = 解析(名 + ".mkv")
        标题 = 父解析.标题 or 标题
        年份 = 父解析.年份
    条目 = 发现条目(类型=媒体类型.电影, 路径=目录, 标题=标题, 年份=年份,
                数据源=解析结果.数据源, 数据源ID=解析结果.数据源ID,
                视频们=sorted(视频们), 附属=_收集附属(目录),
                发布标签=提取发布标签(视频们[0].name))
    if 解析结果.版本:
        条目.版本 = 解析结果.版本
    if 解析结果.是番外:
        条目.是番外 = True
    return 条目


def 扫描媒体库(根目录: Path | str, 最小文件字节: int = 20 * 1024 * 1024,
           上限文件数: int = 200000, 跳过目录: Iterable[str] = ()) -> 扫描结果:
    """扫描一个媒体库根目录。

    :param 最小文件字节: 小于它的视频当"样片/花絮"跳过（0 = 不跳）。
    """
    根目录 = Path(根目录).expanduser()
    结果 = 扫描结果()
    if not 根目录.is_dir():
        结果.错误们.append(f"不是目录：{根目录}")
        return 结果
    跳过 = {x.lower() for x in 默认跳过目录} | {x.lower() for x in 跳过目录}
    见过文件 = 0
    for 当前, 子目录们, 文件们 in os.walk(根目录):
        目录 = Path(当前)
        结果.扫描目录数 += 1
        # 过滤子目录（原地改，os.walk 认这个）
        保留 = []
        for 子 in 子目录们:
            if 子.lower() in 跳过 or 子.startswith("."):
                结果.跳过目录数 += 1
                continue
            保留.append(子)
        子目录们[:] = 保留
        视频们: list[Path] = []
        for 文件名 in 文件们:
            路径 = 目录 / 文件名
            if not _是视频(路径):
                结果.跳过文件数 += 1
                continue
            见过文件 += 1
            if 见过文件 > 上限文件数:
                结果.错误们.append(f"文件数超过上限 {上限文件数}，已停止")
                return 结果
            try:
                大小 = 路径.stat().st_size
            except OSError:
                大小 = 0
            if 最小文件字节 and 0 < 大小 < 最小文件字节:
                结果.太大跳过 += 1
                continue
            视频们.append(路径)
        if 视频们:
            结果.条目们.extend(扫描一个目录(目录, 视频们))
    return 结果


def 扫描一个目录(目录: Path, 视频们: list[Path]) -> list[发现条目]:
    """对"一个目录里的这些视频"做归并（公开出来便于单测直接喂构造数据）。"""
    视频们 = sorted(视频们)
    解析们 = [(v, 解析(v.name, 父目录名=目录.name)) for v in 视频们]
    剧集们 = [(v, r) for v, r in 解析们 if r.季 is not None or r.集 is not None]
    if not 剧集们:
        # 没有季集信息 → 若目录名像剧、视频名带集号再判，否则当电影
        return [_归并电影目录(目录, 视频们)]
    # 剧集：按 (季, 集) 归并多版本
    分组: dict[tuple[int, int], list[tuple[Path, object]]] = {}
    for 视频, 解析结果 in 剧集们:
        季 = int(解析结果.季 if 解析结果.季 is not None else 1)
        集 = int(解析结果.集 if 解析结果.集 is not None else 1)
        分组.setdefault((季, 集), []).append((视频, 解析结果))
    结果: list[发现条目] = []
    for (季, 集), 项们 in sorted(分组.items()):
        视频, 解析结果 = 项们[0]
        条目 = 发现条目(
            类型=媒体类型.剧集, 路径=目录.parent if 目录.name.lower().startswith("season")
            else 目录, 标题=解析结果.标题 or 目录.name, 年份=解析结果.年份,
            季号=季, 集号=集, 集到=解析结果.集到,
            数据源=解析结果.数据源, 数据源ID=解析结果.数据源ID,
            视频们=[v for v, _ in 项们], 附属=_收集附属(目录),
            是番外=解析结果.是番外,
            发布标签=提取发布标签(视频.name))
        条目.特别篇 = (季 == 0)
        条目.版本 = 解析结果.版本
        条目.备注 = f"{len(项们)} 个版本" if len(项们) > 1 else ""
        结果.append(条目)
    return 结果
