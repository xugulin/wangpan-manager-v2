"""弹幕控制器：把"池 + 过滤 + 轨道 + 渲染 + 源"拼成一个能直接接播放器的对象。

职责边界
========
* 播放器只管每帧调 :meth:`弹幕控制器.推进` 与 :meth:`弹幕控制器.绘制`
  —— 它不知道弹幕从哪来、被过滤了什么；
* 界面管配置（开关/字号/透明度/速度/偏移/过滤规则），通过 :meth:`设置配置` 生效，
  **不直接碰渲染器内部**；
* 源（弹弹play/本地文件）在 :meth:`装载` 里被调用，结果进池子并落缓存。

时间轴偏移的语义
================
用户调的是"**弹幕相对视频整体平移**"（比如 23.5 集片头多了 2 秒，就该 -2000ms）。
实现上**不改池子**，而是渲染时用 `(视频时间 - 偏移)` 去取弹幕 ——
这样调偏移不需要重排轨道、也不用重下弹幕，拖滑块能实时看到效果。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtGui import QPainter

from .模型 import 弹幕, 弹幕池, 弹幕配置, 默认配置, 来源标识
from .渲染 import 弹幕渲染器

__all__ = ["弹幕控制器", "装载结果"]


@dataclass
class 装载结果:
    """一次"给这一集找弹幕"的结果。"""

    成功: bool = False
    条数: int = 0
    来源: str = ""
    说明: str = ""
    需要人工确认: bool = False
    候选们: list = field(default_factory=list)
    缓存命中: bool = False

    def 摘要(self) -> str:
        if self.成功:
            return f"{self.来源}：{self.条数} 条" + ("（缓存）" if self.缓存命中 else "")
        return self.说明 or "没有弹幕"


class 弹幕控制器:
    """弹幕的"大脑"：装载、过滤、偏移、开关、统计。"""

    def __init__(self, 配置: Optional[弹幕配置] = None, 日志回调=None) -> None:
        self.配置 = 配置 or 默认配置
        self.渲染器 = 弹幕渲染器(self.配置)
        self._日志 = 日志回调 or (lambda _t: None)
        self._原始池: Optional[弹幕池] = None      # 源给的原始弹幕（不含偏移）
        self._显示池: Optional[弹幕池] = None      # 过滤后的（渲染器用这个）
        self._过滤器 = None
        self._偏移毫秒 = int(self.配置.时间轴偏移毫秒)
        self._当前视频毫秒 = 0
        self._上次平滑毫秒 = 0.0
        self.装载结果 = 装载结果()
        self.统计 = {"装载次数": 0, "过滤前": 0, "过滤后": 0, "偏移调整次数": 0}

    # ---------------- 装载 ----------------

    def 装载本地(self, 池: 弹幕池, 来源说明: str = "本地") -> 装载结果:
        self._原始池 = 池
        self.装载结果 = 装载结果(成功=len(池) > 0, 条数=len(池), 来源=来源说明,
                            说明="" if len(池) else "本地没有弹幕")
        self.统计["装载次数"] += 1
        self._重算显示池()
        return self.装载结果

    def 装载(self, 素材, 源们=None, 自动阈值: float = 82.0) -> 装载结果:
        """按素材（文件/直链）找弹幕：调各源匹配 → 打分决策 → 取弹幕。"""
        from .匹配 import 找弹幕
        from .源 import 建默认源
        源们 = list(源们) if 源们 else 建默认源()
        try:
            决策 = 找弹幕(素材, 源们)
        except Exception as 错:  # noqa: BLE001 - 找弹幕失败不能影响播放
            self._日志(f"[弹幕] 匹配失败：{错}")
            决策 = None
        候选们 = list(getattr(决策, "候选们", []) or [])
        采纳 = getattr(决策, "采纳", None)
        if 决策 is None:
            self.装载结果 = 装载结果(说明="匹配失败")
            return self.装载结果
        if 采纳 is None:
            self.装载结果 = 装载结果(需要人工确认=True, 候选们=候选们,
                                说明=getattr(决策, "说明", "") or "需要人工确认")
            self._日志(f"[弹幕] {self.装载结果.说明}（候选 {len(候选们)} 个）")
            return self.装载结果
        池 = None
        for 源 in 源们:
            try:
                if not 源.能匹配():
                    continue
                池 = 源.取弹幕(采纳.标识)
                if 池 is not None and len(池):
                    break
            except Exception as 错:  # noqa: BLE001
                self._日志(f"[弹幕] {源.名字} 取弹幕失败：{错}")
                池 = None
        if 池 is None or not len(池):
            self.装载结果 = 装载结果(需要人工确认=True, 候选们=候选们,
                                说明="匹配到节目但没取到弹幕")
            return self.装载结果
        # 本地同名弹幕也一起加进来（用户自己下的弹幕往往比在线的更合口味）
        本地 = self._找本地(素材)
        if 本地 is not None and len(本地):
            池 = 弹幕池.合并([池, 本地])
        self._原始池 = 池
        self.装载结果 = 装载结果(成功=True, 条数=len(池), 来源=采纳.标题 or "在线")
        self.统计["装载次数"] += 1
        self._日志(f"[弹幕] 已装载 {len(池)} 条（{采纳.标题} {采纳.集标题}）")
        self._重算显示池()
        return self.装载结果

    def _找本地(self, 素材) -> Optional[弹幕池]:
        try:
            from .源.本地 import 读同名字幕弹幕
            池 = 读同名字幕弹幕(getattr(素材, "路径", None))
            if 池 is not None and len(池):
                self._日志(f"[弹幕] 本地同名弹幕 {len(池)} 条")
            return 池
        except Exception:  # noqa: BLE001
            return None

    # ---------------- 过滤与偏移 ----------------

    def 设置过滤器(self, 过滤器) -> None:
        """外部（设置界面）传一个带 `应用(池) -> (池, 统计)` 的过滤器。"""
        self._过滤器 = 过滤器
        self._重算显示池()

    def _重算显示池(self) -> None:
        池 = self._原始池
        if 池 is None:
            self.渲染器.设置弹幕池(None, self._当前视频毫秒)
            return
        self.统计["过滤前"] = len(池)
        if self._过滤器 is not None:
            try:
                池, _统计 = self._过滤器.应用(池)
            except Exception as 错:  # noqa: BLE001
                self._日志(f"[弹幕] 过滤出错（已忽略，显示全部）：{错}")
                池 = self._原始池
        self.统计["过滤后"] = len(池)
        self._显示池 = 池
        self.渲染器.设置弹幕池(池, self._当前视频毫秒)

    def 设置时间轴偏移(self, 毫秒: int) -> None:
        """用户调 ±偏移；只影响"用哪个时间去取弹幕"，不重排、不重下。"""
        self._偏移毫秒 = int(毫秒)
        self.统计["偏移调整次数"] += 1
        # 偏移变了等于时间跳变：让渲染器重新装填
        self.渲染器._上次时间 = -1e9

    @property
    def 时间轴偏移(self) -> int:
        return self._偏移毫秒

    def 微调偏移(self, 增量毫秒: int) -> int:
        self.设置时间轴偏移(self._偏移毫秒 + int(增量毫秒))
        return self._偏移毫秒

    # ---------------- 每帧 ----------------

    def 推进(self, 视频毫秒: float, 宽: int, 高: int) -> float:
        self._当前视频毫秒 = int(视频毫秒)
        self._上次平滑毫秒 = self.渲染器.推进(视频毫秒 - self._偏移毫秒, 宽, 高)
        return self._上次平滑毫秒

    def 绘制(self, 画: QPainter, 宽: int, 高: int) -> int:
        return self.渲染器.绘制(画, 宽, 高, self._上次平滑毫秒)

    # ---------------- 开关与手动 ----------------

    def 设置显示(self, 显示: bool) -> None:
        self.配置 = self.配置.复制(显示=bool(显示))
        self.渲染器.设置配置(self.配置)
        if not 显示:
            self.渲染器.清空()

    @property
    def 显示中(self) -> bool:
        return bool(self.配置.显示)

    @property
    def 有弹幕(self) -> bool:
        return bool(self._原始池 and len(self._原始池))

    def 加一条(self, 文本: str, 视频毫秒: float, 颜色: int = 0xFFFFFF,
             模式=None) -> bool:
        """手动发一条（只存在本地池里，用于"自己发的弹幕"与测试）。"""
        from .模型 import 弹幕模式 as _模式
        if self._原始池 is None:
            self._原始池 = 弹幕池()
        条 = 弹幕(int(视频毫秒), 文本, 模式 or _模式.滚动, 颜色,
                 发送者="我", 来源=来源标识.本地, 标识=f"local-{time.time_ns()}")
        self._原始池.加([条])
        self._重算显示池()
        return True

    def 导出弹幕(self) -> str:
        from .源.本地 import 导出为JSON
        return 导出为JSON(self._原始池 or 弹幕池(), )

    # ---------------- 统计 ----------------

    def 摘要(self) -> str:
        if not self.有弹幕:
            return self.装载结果.说明 or "无弹幕"
        池 = self._显示池 or self._原始池
        统计 = 池.统计() if 池 is not None else {}
        偏移 = f"｜偏移 {self._偏移毫秒:+d}ms" if self._偏移毫秒 else ""
        return (f"弹幕 {统计.get('总数', 0)} 条"
                f"（滚动 {统计.get('按模式', {}).get('滚动', 0)}"
                f"｜顶部 {统计.get('按模式', {}).get('顶部', 0)}"
                f"｜底部 {统计.get('按模式', {}).get('底部', 0)}）{偏移}")

    def 详细统计(self) -> dict:
        return {**self.统计, **self.渲染器.详细统计(),
                "有弹幕": self.有弹幕, "偏移毫秒": self._偏移毫秒}
