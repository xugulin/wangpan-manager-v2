"""弹幕引擎的**数据模型与配置**（V2 自己定义，从零实现）。

设计原则（来自研究结论，不搬任何项目的代码）
============================================
* **只支持三种模式**：滚动（NORMAL）/ 顶部（TOP）/ 底部（BOTTOM）。别的模式
  （逆向、定位、代码弹幕…）在源侧就丢弃 —— 它们要么只在特定播放器里有意义，
  要么会带来一堆安全与排版问题，而占比极低；
* **时间一律用毫秒**（源的精度就是毫秒；用秒会到处 float 比较，容易出边界 bug）；
* **颜色一律是 0xRRGGBB**（源里的 `R*65536+G*256+B` 就是这个格式，直接对齐，
  不做来回转换）；
* 弹幕**不可变**：过滤/偏移生成"视图"，不改原对象 —— 这样切换过滤条件不用重下弹幕。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable, Iterator, Sequence

__all__ = ["弹幕模式", "弹幕", "弹幕池", "弹幕配置", "默认配置", "来源标识", "颜色工具"]


class 弹幕模式(str, Enum):
    """三种模式（值用字符串，方便 JSON 与日志阅读）。"""

    滚动 = "滚动"
    顶部 = "顶部"
    底部 = "底部"

    @property
    def 固定(self) -> bool:
        """固定弹幕（顶部/底部）不横向移动。"""
        return self is not 弹幕模式.滚动


class 来源标识(str, Enum):
    """弹幕来自哪个源（用于界面上"来源"筛选与去重排序）。"""

    弹弹play = "dandanplay"
    本地 = "local"
    其它 = "other"


class 颜色工具:
    """0xRRGGBB ↔ 分量互转（源的算法就是 R*65536+G*256+B，这里只在需要时拆开）。"""

    @staticmethod
    def 分量(颜色: int) -> tuple[int, int, int]:
        颜色 = int(颜色) & 0xFFFFFF
        return (颜色 >> 16) & 0xFF, (颜色 >> 8) & 0xFF, 颜色 & 0xFF

    @staticmethod
    def 合成(红: int, 绿: int, 蓝: int) -> int:
        return ((int(红) & 0xFF) << 16) | ((int(绿) & 0xFF) << 8) | (int(蓝) & 0xFF)

    @staticmethod
    def 是彩色(颜色: int) -> bool:
        红, 绿, 蓝 = 颜色工具.分量(颜色)
        return not (红 == 绿 == 蓝)


@dataclass(frozen=True, slots=True)
class 弹幕:
    """一条弹幕（不可变）。"""

    毫秒: int                       # 出现时间（毫秒，相对视频开头）
    文本: str
    模式: 弹幕模式 = 弹幕模式.滚动
    颜色: int = 0xFFFFFF            # 0xRRGGBB
    字号: int = 25                  # 相对字号（源里通常是 18/25/36；我们按比例用）
    发送者: str = ""                # 源里的用户标识（用于"屏蔽此人"与"是我发的"）
    来源: 来源标识 = 来源标识.弹弹play
    标识: str = ""                  # 源里的唯一 id（用于去重）
    池: str = ""                    # 源里的弹幕池（保留字段，界面暂不暴露）

    # ---- 便捷 ----
    @property
    def 秒(self) -> float:
        return self.毫秒 / 1000.0

    def 偏移(self, 毫秒: int) -> "弹幕":
        """时间轴偏移（用户调 ±秒时用）。"""
        return replace(self, 毫秒=max(0, self.毫秒 + int(毫秒)))

    def 去重键(self) -> tuple:
        """去重键：同一条弹幕在不同源里会重复（时间+文本+模式足够）。"""
        return (self.毫秒, self.文本, self.模式)

    def 可读(self) -> str:
        return (f"[{self.毫秒 / 1000:8.3f}s] {self.模式.value} "
                f"#{self.颜色:06X} {self.文本[:40]}")


@dataclass
class 弹幕池:
    """一堆弹幕 + 按时间检索（**二分**，因为界面每 16ms 会问一次"现在该显示哪些"）。"""

    条目们: list[弹幕] = field(default_factory=list)
    来源说明: str = ""

    def __post_init__(self) -> None:
        self.排序()

    # ---- 构建 ----
    def 加(self, 弹幕们: Iterable[弹幕]) -> "弹幕池":
        self.条目们.extend(弹幕们)
        self.排序()
        return self

    def 排序(self) -> None:
        self.条目们.sort(key=lambda d: (d.毫秒, d.模式.value, d.文本))

    def 去重(self) -> int:
        """多源合并后去重（同一句在多个源里各有一份的情况很常见）。"""
        见过: set[tuple] = set()
        保留: list[弹幕] = []
        for 条 in self.条目们:
            键 = 条.去重键()
            if 键 in 见过:
                continue
            见过.add(键)
            保留.append(条)
        去掉 = len(self.条目们) - len(保留)
        self.条目们 = 保留
        return 去掉

    def 偏移(self, 毫秒: int) -> "弹幕池":
        """返回**新的**池（时间轴整体平移）；池本身不变，方便撤销。"""
        return 弹幕池([条.偏移(毫秒) for 条 in self.条目们], self.来源说明)

    # ---- 查询 ----
    def __len__(self) -> int:
        return len(self.条目们)

    def __iter__(self) -> Iterator[弹幕]:
        return iter(self.条目们)

    def 取窗口(self, 起毫秒: int, 止毫秒: int) -> list[弹幕]:
        """取 [起, 止) 时间窗内的弹幕（二分定位，O(log n + k)）。"""
        if 止毫秒 <= 起毫秒:
            return []
        左 = self._下界(起毫秒)
        if 左 >= len(self.条目们):
            return []
        if self.条目们[左].毫秒 >= 止毫秒:
            return []
        右 = self._下界(止毫秒)
        return self.条目们[左:右]

    def _下界(self, 毫秒: int) -> int:
        """第一个"时间 ≥ 毫秒"的下标（手写二分，别用库函数省得依赖）。"""
        低, 高 = 0, len(self.条目们)
        while 低 < 高:
            中 = (低 + 高) // 2
            if self.条目们[中].毫秒 < 毫秒:
                低 = 中 + 1
            else:
                高 = 中
        return 低

    def 时间范围(self) -> tuple[int, int]:
        if not self.条目们:
            return (0, 0)
        return (self.条目们[0].毫秒, self.条目们[-1].毫秒)

    def 统计(self) -> dict:
        模式数: dict[str, int] = {}
        彩色 = 0
        for 条 in self.条目们:
            模式数[条.模式.value] = 模式数.get(条.模式.value, 0) + 1
            if 颜色工具.是彩色(条.颜色):
                彩色 += 1
        起, 止 = self.时间范围()
        return {"总数": len(self.条目们), "按模式": 模式数, "彩色": 彩色,
                "首条毫秒": 起, "末条毫秒": 止}

    @classmethod
    def 合并(cls, 池们: Sequence["弹幕池"], 去重: bool = True) -> "弹幕池":
        """多源合并（默认去重）。"""
        新 = cls()
        for 池 in 池们:
            新.条目们.extend(池.条目们)
        新.排序()
        if 去重:
            新.去重()
        return 新


@dataclass
class 弹幕配置:
    """观感与行为配置（字段都是**用户可调**的，界面直接绑）。

    默认值的取法：研究里看到的那套观感是经过大量用户验证的（速度 88dp/s、
    安全间距 36dp、显示区 25%、字号 18sp、描边 4f、固定弹幕停留 5s、回溯装填 20s），
    这里按同样的**量级**取值，但单位换成像素/比例，并且全部可调、可持久化。
    """

    # ---- 观感 ----
    显示: bool = True
    # ⚠️ 这几个默认值是**量出来的**，不是拍的：见 docs/研究 与下面的容量估算。
    #    单轨道吞吐 ≈ 速度 / (平均文本宽 + 安全间距)（新弹幕要等前一条尾部让出这段距离），
    #    总吞吐 = 吞吐 × 轨道数。要求"每 150ms 来一条"（≈6.7 条/秒）时不丢太多：
    #    v=280、平均宽 300、间距 14 → 单轨 ≈0.89 条/秒 × 10 轨 ≈ 8.9 条/秒 ✓
    速度像素每秒: float = 280.0        # 滚动弹幕横穿速度（按 1080p 高归一；1920 宽屏约 8 秒穿完）
    安全间距像素: float = 14.0         # 同轨道两条弹幕的最小间距
    显示区比例: float = 0.45           # 弹幕最多占控件高度的比例（顶部起算；固定弹幕另算）
    字号比例: float = 0.037            # 字号 = 控件高度 × 该比例（0.037 × 1080 ≈ 40px，主流观感）
    最小字号像素: int = 12
    最大字号像素: int = 72
    不透明度: float = 0.92
    描边宽度比例: float = 0.10         # 相对字号
    显示彩色: bool = True
    允许顶部: bool = True
    允许底部: bool = False             # 与主流播放器一致：底部默认关（挡住字幕）
    固定停留毫秒: int = 5000           # 顶部/底部弹幕停留时间
    轨道间距比例: float = 1.15         # 轨道高 = 字号 × 该比例（留出行距）
    长弹幕加速: bool = True            # 长弹幕加速，避免长时间遮挡
    加速指数: float = 0.28             # 速度倍率 = (宽度/基准宽度)^指数（上限见下）
    加速上限倍率: float = 1.8
    抖动比例: float = 0.06             # 同屏速度的随机抖动（避免整齐划一）

    # ---- 行为 ----
    时间轴偏移毫秒: int = 0
    同屏上限: int = 0                  # 0 = 不限制；>0 时超出直接丢弃（防卡顿）
    回溯装填毫秒: int = 20000          # 跳转后往回多装一点，避免"跳过去一片空白"
    跳转重填阈值毫秒: int = 3000       # 时间跳变超过这个值就重建当屏弹幕

    # ---- 过滤（由 wangpan.danmaku.过滤 消费）----
    启用正则过滤: bool = True

    def 复制(self, **改动) -> "弹幕配置":
        from dataclasses import replace as _r
        return _r(self, **改动)

    def 摘要(self) -> str:
        return (f"速度 {self.速度像素每秒:.0f}px/s｜间距 {self.安全间距像素:.0f}px"
                f"｜显示区 {self.显示区比例 * 100:.0f}%｜字号 {self.字号比例 * 100:.1f}%"
                f"｜偏移 {self.时间轴偏移毫秒}ms")


#: 一份默认配置（各处用它当基准，避免到处都是魔法数）
默认配置 = 弹幕配置()
