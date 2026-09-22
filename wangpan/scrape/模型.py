"""刮削的数据模型（V2 自己定义）。

为什么单独一份"内存模型"而不是直接写库：
* 扫描/匹配/抓取/落库是四个阶段，中间要有**纯数据**在流动，方便单测与重放；
* 库表结构会随功能变，内存模型相对稳定；两者之间只在 :mod:`~wangpan.scrape.库` 里转换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

__all__ = ["媒体类型", "人物工种", "图片类型", "媒体条目", "季", "集", "人物",
           "参演", "图片", "分级", "匹配候选", "刮削结果"]


class 媒体类型(str, Enum):
    电影 = "movie"
    剧集 = "tv"
    未知 = "unknown"


class 人物工种(str, Enum):
    演员 = "actor"
    导演 = "director"
    编剧 = "writer"
    制片 = "producer"
    作曲 = "composer"
    其它 = "other"


class 图片类型(str, Enum):
    海报 = "poster"          # 竖版主图
    背景 = "backdrop"        # 横版背景
    标志 = "logo"            # 透明 logo
    剧照 = "still"           # 剧集单集剧照
    人物 = "profile"         # 演员头像


@dataclass
class 图片:
    """一张图片的元信息（文件本身下载到缓存目录，这里只记路径与来源）。"""

    类型: 图片类型
    远端路径: str = ""             # TMDB 的 file_path（形如 /abc.jpg）
    本地路径: Optional[Path] = None
    宽: int = 0
    高: int = 0
    语言: str = ""                 # zh / en / ""（无语言 = 通用图）
    评分: float = 0.0              # 源的 vote_average（选图用）
    票数: int = 0
    来源: str = "tmdb"

    def 可用(self) -> bool:
        return bool(self.本地路径 and Path(self.本地路径).is_file())


@dataclass
class 人物:
    标识: str = ""                 # 源里的 person id
    名字: str = ""
    原名: str = ""
    头像: Optional[图片] = None
    来源: str = "tmdb"


@dataclass
class 参演:
    """一条"人物—作品"关系（演员/导演/编剧…）。"""

    人物: 人物
    工种: 人物工种 = 人物工种.演员
    角色: str = ""                 # 演的是谁
    排序: int = 999                # 越小越靠前（源里的 order 字段）
    集数: int = 0                  # 剧集里该演员出演了几集（aggregate_credits 才有）


@dataclass
class 分级:
    """一条分级（国家 + 值 + 来源）。"""

    国家: str = ""                 # US / HK / JP …（TMDB 没有 CN，见研究文档）
    值: str = ""
    来源: str = "tmdb"

    def 可读(self) -> str:
        return f"{self.国家}:{self.值}" if self.国家 else self.值


@dataclass
class 集:
    季号: int = 1
    集号: int = 1
    标题: str = ""
    简介: str = ""
    播出日期: str = ""
    时长分钟: int = 0
    评分: float = 0.0
    剧照: Optional[图片] = None
    文件路径: Optional[Path] = None
    外部ID: str = ""               # 源的 episode id
    集号到: int = 0                # 一文件多集（S01E01-E02）时的结束集号

    @property
    def 编号(self) -> str:
        return f"S{self.季号:02d}E{self.集号:02d}"


@dataclass
class 季:
    季号: int = 1
    标题: str = ""
    简介: str = ""
    集数: int = 0
    播出日期: str = ""
    海报: Optional[图片] = None
    集们: list[集] = field(default_factory=list)


@dataclass
class 媒体条目:
    """一部电影或一部剧（剧集时 季们 里有季/集）。"""

    类型: 媒体类型 = 媒体类型.未知
    标题: str = ""
    原名: str = ""
    年份: Optional[int] = None
    简介: str = ""
    标签: list[str] = field(default_factory=list)      # 类型（动作/科幻…）
    时长分钟: int = 0
    评分: float = 0.0                                   # 0–10
    评分票数: int = 0
    影评人评分: float = 0.0                             # 0–100（量纲与上面不同！）
    分级们: list[分级] = field(default_factory=list)
    海报: Optional[图片] = None
    背景: Optional[图片] = None
    标志: Optional[图片] = None
    图片们: list[图片] = field(default_factory=list)
    参演们: list[参演] = field(default_factory=list)
    季们: list[季] = field(default_factory=list)
    外部ID: dict[str, str] = field(default_factory=dict)   # {"tmdb": "123", "imdb": "tt…"}
    来源: str = "tmdb"
    文件路径: Optional[Path] = None                     # 电影的主文件；剧集为空
    #: 同一部作品的其它文件（多版本 1080p/4K、多段 cd1/cd2）——
    #: 只记主文件会把"我明明有两个版本"这件事丢掉（海报墙上就看不到版本选择）
    额外文件: list[Path] = field(default_factory=list)
    状态: str = ""                                      # 源里的 status（Released/Returning…）
    原始语言: str = ""
    制片公司: list[str] = field(default_factory=list)

    # ---- 便捷 ----
    @property
    def 是剧集(self) -> bool:
        return self.类型 is 媒体类型.剧集

    def 排序用标题(self) -> str:
        return (self.标题 or self.原名 or "").strip()

    def 一句话(self) -> str:
        年份 = f"（{self.年份}）" if self.年份 else ""
        return f"{self.标题 or '未命名'}{年份}｜{self.类型.value}｜{self.评分:.1f}分"

    def 取分级(self, 优先国家=("CN", "HK", "TW", "US", "JP")) -> Optional[分级]:
        """按优先国家取分级（TMDB 没有 CN，所以要能给回退顺序）。"""
        表 = {g.国家: g for g in self.分级们}
        for 国家 in 优先国家:
            if 国家 in 表 and 表[国家].值:
                return 表[国家]
        return next((g for g in self.分级们 if g.值), None)

    def 汇总集数(self) -> int:
        return sum(len(s.集们) for s in self.季们)

    def 演员(self, 上限: int = 20) -> list[参演]:
        return [x for x in self.参演们 if x.工种 is 人物工种.演员][:上限]

    def 导演(self) -> list[参演]:
        return [x for x in self.参演们 if x.工种 is 人物工种.导演]


@dataclass
class 匹配候选:
    """搜索/匹配返回的一个候选（给用户点选，或按分数自动采纳）。"""

    来源标识: str = ""             # tmdb id
    标题: str = ""
    原名: str = ""
    年份: Optional[int] = None
    类型: 媒体类型 = 媒体类型.未知
    简介: str = ""
    海报远端: str = ""
    热度: float = 0.0
    分数: float = 0.0              # 我们自己的匹配打分（见 匹配打分.py）
    理由: str = ""                 # 为什么给这个分（调试与界面提示）
    额外: dict = field(default_factory=dict)

    def 可读(self) -> str:
        年份 = f"（{self.年份}）" if self.年份 else ""
        return f"{self.分数:5.1f} 分｜{self.标题}{年份}｜{self.类型.value}｜{self.理由}"


@dataclass
class 刮削结果:
    """一次刮削（一个媒体条目）的结果与统计。"""

    条目: Optional[媒体条目] = None
    候选们: list[匹配候选] = field(default_factory=list)
    采用候选: Optional[匹配候选] = None
    需要人工确认: bool = False
    说明: str = ""
    耗时秒: float = 0.0
    下载图片数: int = 0
    错误: str = ""
