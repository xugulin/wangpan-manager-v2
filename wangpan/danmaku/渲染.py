"""弹幕渲染（QPainter 实现，从零写）。

三个关键设计
============
1. **位置在绘制时算**（`:meth:`弹幕渲染器.绘制`` 里按"现在毫秒"纯函数算），
   轨道分配只在"弹幕第一次进入屏幕"时做一次 —— 所以暂停时画面完全静止、
   跳转后只要重填一次就正确，不需要"每帧更新状态"；
2. **每条弹幕预渲染成一张位图**（带描边的文字只画一次，之后每帧只 drawImage）。
   弹幕密集时这一步是数量级差距：QPainter 画文字（尤其带描边）远比贴图贵；
   位图缓存有**条数上限**与**总像素上限**（防内存爆），超了按 LRU 淘汰；
3. **帧时间平滑**：桌面端拿到的时间戳会抖（合成器节流、GC、解码抖动），
   直接用会让弹幕"一顿一顿"。这里做的是：EMA 估计帧间隔 + 相位误差回授
   （每帧最多修正一个间隔的一部分，超过阈值就直接透传，避免追帧时疯跑）。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath,
                          QPen, QPixmap)

from .模型 import 弹幕, 弹幕池, 弹幕配置, 弹幕模式, 颜色工具
from .轨道 import 屏幕布局, 轨道分配器

__all__ = ["帧时间平滑器", "弹幕渲染器", "渲染统计"]


class 帧时间平滑器:
    """把抖动的帧时间戳平滑成"看起来匀速"的时间轴。

    做法（自己设计，参数都是可调的）：
    * 用 EMA 估计帧间隔（``平滑系数``）；
    * 维护一个"我们以为现在的时间"：每帧按估计间隔前进，再按实际时间做**相位修正**，
      修正量每帧不超过 ``单帧最大修正比例 × 间隔``（避免卡顿后猛追）；
    * 帧间隔超过 ``透传阈值毫秒``（比如切场景、拖进度条）→ 直接跳到实际时间，
      不做平滑（否则会慢慢"漂"过去，看起来像卡了）。
    """

    def __init__(self, 平滑系数: float = 0.12, 单帧最大修正比例: float = 0.22,
                 透传阈值毫秒: float = 250.0) -> None:
        self.平滑系数 = max(0.01, min(0.9, 平滑系数))
        self.单帧最大修正比例 = max(0.0, min(0.9, 单帧最大修正比例))
        self.透传阈值毫秒 = max(50.0, 透传阈值毫秒)
        self._间隔 = 16.7
        self._上次实际 = 0.0
        self._现在 = 0.0
        self._初始化 = False
        self.透传次数 = 0

    def 重置(self, 现在毫秒: float) -> None:
        self._现在 = float(现在毫秒)
        self._上次实际 = time.monotonic() * 1000.0
        self._初始化 = True

    def 推进(self, 实际毫秒: float) -> float:
        """喂入播放器的实际时间（毫秒），返回**平滑后**用于绘制的时间。"""
        单调现在 = time.monotonic() * 1000.0
        if not self._初始化:
            self.重置(实际毫秒)
            return self._现在
        帧间隔 = max(1.0, 单调现在 - self._上次实际)
        self._上次实际 = 单调现在
        self._间隔 += (帧间隔 - self._间隔) * self.平滑系数
        if abs(实际毫秒 - self._现在) > self.透传阈值毫秒:
            # 跳转/卡顿很久：直接对齐，不平滑
            self._现在 = float(实际毫秒)
            self.透传次数 += 1
            return self._现在
        # 先按估计间隔前进，再按实际时间做有限幅度的相位修正
        预期 = self._现在 + self._间隔
        误差 = float(实际毫秒) - 预期
        上限 = self._间隔 * self.单帧最大修正比例
        预期 += max(-上限, min(上限, 误差))
        # 结果必须夹在"实际时间"附近，避免长时间漂移
        预期 = max(实际毫秒 - self.间隔 * 3, min(实际毫秒 + self.间隔 * 3, 预期))
        self._现在 = 预期
        return self._现在

    @property
    def 间隔(self) -> float:
        return self._间隔

    def 统计(self) -> dict:
        return {"估计帧间隔": round(self._间隔, 2), "透传次数": self.透传次数}


@dataclass
class 渲染统计:
    绘制条数: int = 0
    位图命中: int = 0
    位图新建: int = 0
    位图淘汰: int = 0
    缓存条数: int = 0
    缓存像素: int = 0

    def 摘要(self) -> str:
        return (f"绘制 {self.绘制条数} 条｜缓存 {self.缓存条数} 条"
                f"（{self.缓存像素 / 1048576:.1f} MPx）｜命中 {self.位图命中}"
                f"｜新建 {self.位图新建}｜淘汰 {self.位图淘汰}")


class 弹幕渲染器:
    """把弹幕池画到 QPainter 上（自绘，逐帧只贴图）。"""

    def __init__(self, 配置: Optional[弹幕配置] = None,
                 最大缓存条数: int = 3000, 最大缓存像素: int = 48 * 1024 * 1024) -> None:
        self.配置 = 配置 or 弹幕配置()
        self.分配器 = 轨道分配器(self.配置)
        self.平滑器 = 帧时间平滑器()
        self.统计 = 渲染统计()
        self._位图们: "OrderedDict[tuple, QPixmap]" = OrderedDict()
        self._宽度们: "OrderedDict[tuple, int]" = OrderedDict()
        self._缓存像素 = 0
        self._最大条数 = max(50, 最大缓存条数)
        self._最大像素 = max(2 * 1024 * 1024, 最大缓存像素)
        self._池: Optional[弹幕池] = None
        #: 当前在屏的弹幕（"装载表"）—— **必须在构造时就初始化**：
        #: 界面可能先接上控制器、第一帧就 paintEvent，此时还没设置弹幕池 →
        #: 之前这里会 AttributeError（真机第一次打开就崩了，单测没覆盖"先画后装"）
        self._装载表: list[弹幕] = []
        self._布局: Optional[屏幕布局] = None
        self._上次时间 = 0.0
        self._窗口游标 = 0            # 池里"下一个待装入"的下标
        self._字体key = ""
        self._字体: Optional[QFont] = None
        self._装入数 = 0

    # ---------------- 生命周期 ----------------

    def 设置弹幕池(self, 池: Optional[弹幕池], 现在毫秒: float = 0.0) -> None:
        """换一份弹幕（换集/换源），并重建状态。"""
        self._池 = 池
        self.清空()
        if 池 is not None:
            self._窗口游标 = max(0, 池._下界(int(现在毫秒 - self.配置.回溯装填毫秒)))
            self._上次时间 = float(现在毫秒)

    def 清空(self) -> None:
        self.分配器.清空()
        self._装载表: list[弹幕] = []
        self._装入数 = 0

    @property
    def 池(self) -> Optional[弹幕池]:
        return self._池

    def 设置配置(self, 配置: 弹幕配置) -> None:
        观感变了 = (配置.字号比例 != self.配置.字号比例
                 or 配置.描边宽度比例 != self.配置.描边宽度比例
                 or 配置.显示彩色 != self.配置.显示彩色
                 or 配置.不透明度 != self.配置.不透明度)
        self.配置 = 配置
        self.分配器.配置 = 配置
        if 观感变了:
            self._位图们.clear()
            self._宽度们.clear()
            self._缓存像素 = 0
            self._布局 = None

    # ---------------- 装入与推进 ----------------

    def _确保布局(self, 宽: int, 高: int) -> 屏幕布局:
        if (self._布局 is None or self._布局.宽 != max(1, int(宽))
                or self._布局.高 != max(1, int(高))):
            self._布局 = 屏幕布局.由尺寸与配置(宽, 高, self.配置)
            self.分配器.清空()          # 尺寸变了，之前的轨道位置全部作废
            self._窗口游标 = 0
            self._上次时间 = 0.0
        return self._布局

    def 推进(self, 实际毫秒: float, 宽: int, 高: int) -> float:
        """按播放器时间推进；返回用于绘制的**平滑后**时间（毫秒）。"""
        布局 = self._确保布局(宽, 高)
        平滑 = self.平滑器.推进(实际毫秒)
        跳变 = abs(实际毫秒 - self._上次时间)
        if 跳变 > self.配置.跳转重填阈值毫秒:
            # 跳转：清掉当屏、从目标点往回装一点（避免"跳过去一片空白"）
            self.分配器.清空()
            self._装载表 = []
            if self._池 is not None:
                self._窗口游标 = max(0, self._池._下界(int(实际毫秒 - self.配置.回溯装填毫秒)))
        self._上次时间 = float(实际毫秒)
        self._装入(平滑, 布局)
        self.分配器.过期清理(int(平滑), 布局)
        return 平滑

    def _装入(self, 现在毫秒: float, 布局: 屏幕布局) -> None:
        """把"当前应当可见"的弹幕装进轨道（每条只装一次）。

        只看一小段窗口：当前时间往前一小步（新弹幕）到"最长可能停留时间"之间。
        """
        if self._池 is None or not self.配置.显示:
            return
        最长停留 = max(
            self.配置.固定停留毫秒,
            int((布局.宽 + 布局.宽) / max(1.0, 布局.基准速度) * 1000.0) + 1000)
        起 = int(现在毫秒) - 200
        止 = int(现在毫秒) + 400          # 稍微提前装，避免"到点才出现"的一帧延迟
        窗口 = self._池.取窗口(max(0, 起), max(1, 止))
        for 条 in 窗口:
            self._分配一条(条, 布局)
        # 清理装载表里已经过期的（只留可能还可见的）
        self._装载表 = [d for d in self._装载表 if d.毫秒 >= 现在毫秒 - 最长停留]

    def _分配一条(self, 条: 弹幕, 布局: 屏幕布局) -> None:
        文本 = 条.文本
        if not 文本.strip():
            return
        字号 = self._字号(条, 布局)
        宽 = self._文本宽度(文本, 字号)
        定位 = self.分配器.分配(条, 文本宽度=宽, 布局=布局, 文本字号=字号)
        # ⚠️ 只有"这次真的排进轨道"的才进在屏表：分配() 对**早就排过**的弹幕也会
        # 返回位置（绘制要用），照着返回值加就会同一帧里加几十遍 —— 真机实测
        # 在屏表 113 条、去重后只有 3 条（同一条画几十遍，白烧 CPU、统计也失真）。
        if 定位 is not None and 定位.新增:
            self._装载表.append(条)
            self._装入数 += 1

    def _字号(self, 条: 弹幕, 布局: 屏幕布局) -> int:
        # 源里的"字号"字段大多是 18/25/36 三档（25 为基准）
        比例 = 1.0 if not 条.字号 else max(0.5, min(2.0, 条.字号 / 25.0))
        return max(self.配置.最小字号像素,
                   min(self.配置.最大字号像素, int(round(布局.字号 * 比例))))

    # ---------------- 位图缓存 ----------------

    def 字体(self, 布局: 屏幕布局) -> QFont:
        键 = f"{布局.字号}"
        if self._字体 is None or self._字体key != 键:
            字 = QFont()
            字.setPixelSize(布局.字号)
            字.setBold(True)                     # 弹幕要"粗"才看得清
            字.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
            self._字体, self._字体key = 字, 键
        return self._字体

    def _文本宽度(self, 文本: str, 字号: int) -> int:
        键 = (文本, 字号)
        宽 = self._宽度们.get(键)
        if 宽 is None:
            宽 = QFontMetrics(self.字体(屏幕布局.由尺寸与配置(1, 字号 / max(0.001, self.配置.字号比例), self.配置))).horizontalAdvance(文本)
            self._宽度们[键] = 宽
            if len(self._宽度们) > self._最大条数 * 2:
                self._宽度们.popitem(last=False)
        return int(宽)

    def _取位图(self, 文本: str, 字号: int, 颜色: int) -> tuple[QPixmap, int]:
        色 = 0xFFFFFF if not self.配置.显示彩色 else (int(颜色) & 0xFFFFFF)
        键 = (文本, 字号, 色)
        命中 = self._位图们.get(键)
        if 命中 is not None:
            self._位图们.move_to_end(键)
            self.统计.位图命中 += 1
            return 命中, self._宽度们.get(键, 命中.width())

        字体 = QFont()
        字体.setPixelSize(字号)
        字体.setBold(True)
        度量 = QFontMetrics(字体)
        文字宽 = 度量.horizontalAdvance(文本)
        描边 = max(1.0, 字号 * max(0.0, self.配置.描边宽度比例))
        边距 = int(描边 * 2 + 2)
        图宽 = 文字宽 + 边距 * 2
        图高 = 度量.height() + 边距 * 2
        图 = QImage(max(1, 图宽), max(1, 图高), QImage.Format.Format_ARGB32_Premultiplied)
        图.fill(Qt.GlobalColor.transparent)
        画 = QPainter(图)
        画.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        画.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        画.setFont(字体)
        # 描边：先按路径粗描一圈黑边，再填字（比逐方向偏移画多次快得多，也没有叠影）
        路径 = QPainterPath()
        路径.addText(边距, 边距 + 度量.ascent(), 字体, 文本)
        画.setPen(QPen(QColor(0, 0, 0, 235), 描边 * 2,
                     Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                     Qt.PenJoinStyle.RoundJoin))
        画.setBrush(Qt.BrushStyle.NoBrush)
        画.drawPath(路径)
        红, 绿, 蓝 = 颜色工具.分量(色)
        画.setPen(Qt.PenStyle.NoPen)
        画.setBrush(QColor(红, 绿, 蓝))
        画.drawPath(路径)
        画.end()
        位图 = QPixmap.fromImage(图)
        self._位图们[键] = 位图
        self._宽度们[键] = 文字宽
        self._缓存像素 += 图宽 * 图高
        self.统计.位图新建 += 1
        self._淘汰()
        return 位图, 文字宽

    def _淘汰(self) -> None:
        while self._位图们 and (len(self._位图们) > self._最大条数
                            or self._缓存像素 > self._最大像素):
            键, 位图 = self._位图们.popitem(last=False)
            self._缓存像素 -= 位图.width() * 位图.height()
            self.统计.位图淘汰 += 1

    def 清缓存(self) -> None:
        self._位图们.clear()
        self._宽度们.clear()
        self._缓存像素 = 0

    # ---------------- 绘制 ----------------

    def 绘制(self, 画: QPainter, 宽: int, 高: int, 现在毫秒: float) -> int:
        """把当前该显示的弹幕画出来；返回画了几条。"""
        self.统计.绘制条数 = 0
        if not self.配置.显示:
            return 0
        布局 = self._确保布局(宽, 高)
        计数 = 0
        限制 = self.配置.同屏上限
        画.setOpacity(max(0.05, min(1.0, self.配置.不透明度)))
        for 条 in self._装载表:
            if 限制 and 计数 >= 限制:
                break
            定位 = self.分配器.已排定位(条, int(现在毫秒), 布局)
            if 定位 is None:
                continue
            x, y = 定位.x, 定位.y
            if 定位.模式 is 弹幕模式.滚动:
                if x > 宽 or x + 定位.宽度 < 0:
                    continue
            else:
                if 现在毫秒 < 条.毫秒 or 现在毫秒 > 条.毫秒 + self.配置.固定停留毫秒:
                    continue
            位图, 文字宽 = self._取位图(条.文本, 定位.字号, 条.颜色)
            边距 = (位图.width() - 文字宽) // 2
            画.drawPixmap(int(x - 边距), int(y), 位图)
            计数 += 1
        self.统计.绘制条数 = 计数
        self.统计.缓存条数 = len(self._位图们)
        self.统计.缓存像素 = self._缓存像素
        画.setOpacity(1.0)
        return 计数

    # ---------------- 统计 ----------------

    def 详细统计(self) -> dict:
        结果 = dict(self.统计.__dict__)
        结果["轨道"] = self.分配器.统计()
        结果["平滑"] = self.平滑器.统计()
        结果["装入"] = self._装入数
        return 结果
