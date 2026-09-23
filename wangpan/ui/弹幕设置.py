"""弹幕观感/过滤设置面板（一个可单独构造的 QWidget）。

为什么要做成"面板"而不是对话框
================================
同一套设置有三个入口：播放时临时调、设置页里长期调、以后可能嵌到调试页里看效果。
做成非模态面板后，三处只是"放在哪儿"的区别，行为完全一致；做成对话框就得为
"播放中调完不打断画面"再写一遍。

信号为什么是"立刻发一次 + 60ms 合并一次"
========================================
* 只做 60ms 防抖 → 点一下滑块要等 60ms 才有反应，拖完还要再等一次，手感发黏；
* 每个 valueChanged 都发 → 拖一次滑块能发几百个信号，宿主若每次都重建弹幕视图
  就会卡成幻灯片。
所以：**第一次立刻发**（点了就有反应），随后的连续变化由 60ms 的定时器合并成
尾部一次（拖动过程中最多惊动宿主两次）。定时器宿主是面板自己（``QTimer(self)``）——
裸用 ``QTimer.singleShot`` 在面板被销毁后仍会回调，这个项目已经踩过这个坑。

为什么"取配置/取规则"永远是最新的
================================
控件一变就**同步**写进内部的配置/规则对象（发信号是另一回事），所以宿主不需要
"等信号到了再读"，任何时刻 ``取配置()`` 都是用户当前看到的设置。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QScrollArea, QSlider, QSpinBox,
                               QVBoxLayout, QWidget, QFrame)

from ..danmaku.模型 import 弹幕配置
from ..danmaku.过滤 import 过滤规则, 过滤器, 默认规则

__all__ = ["弹幕设置面板", "颜色提示", "颜色警告", "防抖毫秒"]

#: 连续拖动时的合并窗口（60ms ≈ 4 帧，人眼已看不出延迟，宿主却少收几百个信号）
防抖毫秒 = 60

颜色提示 = "#8fa2bb"
颜色好 = "#7ddc8a"
颜色警告 = "#ffb454"
颜色错 = "#ff7b72"

#: 字号比例滑块的刻度：值是"千分之几"（15 → 1.5%），QSlider 只认整数，
#: 用千分比就不用引入浮点滑块（也没必要）
字号千分下限 = 15
字号千分上限 = 80

#: 换算文案的基准分辨率（弹幕字号 = 控件高度 × 比例，1080p 是最常见的高度）
基准高度 = 1080

#: 时间轴偏移范围（毫秒）：±30 秒足够覆盖"源整段偏了"这种常见情况
偏移下限 = -30000
偏移上限 = 30000

#: 规则文件对话框的过滤串
规则文件过滤 = "规则文件 (*.json);;所有文件 (*)"


def _解析多行(文本: str) -> list[str]:
    """多行文本 → 列表：空行忽略、首尾空白去掉、**保留用户写的顺序**。

    不去重也不排序：规则的顺序会影响"谁先命中"（见 :mod:`wangpan.danmaku.过滤`
    的判定顺序），界面不该偷偷替用户重排。
    """
    return [行.strip() for 行 in str(文本 or "").splitlines() if 行.strip()]


def _合并行(表: list[str]) -> str:
    return "\n".join(str(一个) for 一个 in (表 or []))


def _坏正则(正则们: list[str]) -> list[str]:
    """挑出编译不了的正则（界面上标红用）。

    这里**只是提前告诉用户**：真跑过滤时 :class:`~wangpan.danmaku.过滤.过滤器`
    本来就会跳过坏正则（手写正则写错一个括号是常事），所以坏正则绝不阻止别的设置保存。
    """
    坏: list[str] = []
    for 一条 in 正则们 or ():
        文本 = str(一条 or "").strip()
        if not 文本:
            continue
        try:
            re.compile(文本, re.I)
        except re.error:
            坏.append(文本)
    return 坏


class 弹幕设置面板(QWidget):
    """弹幕观感 + 时间轴 + 过滤规则 + 规则导入导出。

    单独构造就能用（``弹幕设置面板()``），不依赖播放器/引擎 ——
    设置界面不该因为"还没打开视频"就用不了。
    """

    配置变化 = Signal(object)        # 参数：新的 弹幕配置（**已复制**，不会和面板共享）
    规则变化 = Signal(object)        # 参数：新的 过滤规则

    def __init__(self, 配置=None, 规则=None, 父=None) -> None:
        super().__init__(父)
        # 存**自己的一份副本**：宿主手里那份要等它自己调 取配置()/收信号 才更新，
        # 面板里的改动不会悄悄改到正在播放中的配置对象
        self._配置 = 配置.复制() if isinstance(配置, 弹幕配置) else 弹幕配置()
        self._规则 = 规则.复制() if isinstance(规则, 过滤规则) else 默认规则()
        self._回填中 = False        # 回填控件时挡住"变化"，否则装载会反过来发信号

        self._建界面()
        self.装载配置(self._配置)
        self.装载规则(self._规则)

    # ---------------- 对外 ----------------

    def 取配置(self) -> 弹幕配置:
        """当前配置（任何时候都是用户眼前这一份，不用等信号）。"""
        return self._配置

    def 取规则(self) -> 过滤规则:
        """当前过滤规则。"""
        return self._规则

    def 装载配置(self, 配置, 发信号: bool = False) -> None:
        """把一份配置铺到控件上（默认**不发**信号：装载不是用户操作）。"""
        if not isinstance(配置, 弹幕配置):
            return
        self._配置 = 配置.复制()
        self._回填配置(self._配置)
        if 发信号:
            self.配置变化.emit(self._配置)

    def 装载规则(self, 规则, 发信号: bool = False) -> None:
        if not isinstance(规则, 过滤规则):
            return
        self._规则 = 规则.复制()
        self._回填规则(self._规则)
        if 发信号:
            self.规则变化.emit(self._规则)

    def 装载(self, 配置=None, 规则=None) -> None:
        """一次装两样（宿主切视频/切用户配置时省事）。"""
        if 配置 is not None:
            self.装载配置(配置)
        if 规则 is not None:
            self.装载规则(规则)

    def 导出规则(self) -> str:
        """导出成 JSON 文本（可直接写文件）。

        走 :meth:`过滤器.导出`：格式与"规则版本"由那边统一管，
        面板自己拼 JSON 迟早会和导入端对不上。
        """
        return 过滤器(self._规则).导出()

    def 导入规则(self, 文本: str) -> bool:
        """从 JSON 文本导入；坏内容返回 False 并保持原规则不变。

        为什么要自己先 json.loads 一次：:meth:`过滤器.导入` 的契约是"坏 JSON 也用默认
        规则、绝不抛"，那是给播放路径用的（宁可没过滤也不能起不来）；但界面必须能
        告诉用户"这个文件不对"，而不是悄悄把规则清空成默认值。
        """
        try:
            数据 = json.loads(str(文本 or ""))
        except ValueError as 错:
            self._规则提示(f"❌ 导入失败：不是合法 JSON（{错}）；当前规则没有改动",
                        颜色错)
            return False
        if not isinstance(数据, dict):
            self._规则提示("❌ 导入失败：文件顶层应该是一个 JSON 对象；当前规则没有改动",
                        颜色错)
            return False
        内层 = 数据.get("规则", 数据)
        if not isinstance(内层, dict):
            self._规则提示("❌ 导入失败：里面的「规则」不是对象；当前规则没有改动",
                        颜色错)
            return False
        新 = 过滤器.导入(str(文本 or "")).规则
        self._规则 = 新.复制()
        self._回填规则(self._规则)
        self._规则提示("✅ 已导入规则：" + self._规则摘要(), 颜色好)
        self.规则变化.emit(self._规则)
        return True

    def 导出到文件(self, 路径: Path | str) -> bool:
        """导出到文件（按钮走这里；测试也走这里，免得去戳文件对话框）。"""
        目标 = Path(路径)
        try:
            目标.parent.mkdir(parents=True, exist_ok=True)
            目标.write_text(self.导出规则(), encoding="utf-8")
        except OSError as 错:
            self._规则提示(f"❌ 写不了文件：{错}", 颜色错)
            return False
        self._规则提示(f"✅ 规则已导出：{目标.name}", 颜色好)
        return True

    def 从文件导入(self, 路径: Path | str) -> bool:
        来源 = Path(路径)
        try:
            文本 = 来源.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as 错:
            self._规则提示(f"❌ 读不了文件：{错}", 颜色错)
            return False
        return self.导入规则(文本)

    # ---------------- 时间轴 ----------------

    def 调偏移(self, 毫秒: int) -> int:
        """在当前偏移上增减（自动夹在滑块范围内）；返回夹住之后的值。"""
        目标 = int(self._配置.时间轴偏移毫秒) + int(毫秒)
        self.偏移滑块.setValue(max(偏移下限, min(偏移上限, 目标)))
        return int(self.偏移滑块.value())

    def 归零偏移(self) -> None:
        self.偏移滑块.setValue(0)

    # ---------------- 界面 ----------------

    def _建界面(self) -> None:
        # 两个防抖定时器：**宿主是面板自己**（面板销毁时定时器一起没了，
        # 不会出现"面板没了回调还在"的崩溃）
        self._配置防抖 = QTimer(self)
        self._配置防抖.setSingleShot(True)
        self._配置防抖.setInterval(防抖毫秒)
        self._配置防抖.timeout.connect(self._发配置)
        self._规则防抖 = QTimer(self)
        self._规则防抖.setSingleShot(True)
        self._规则防抖.setInterval(防抖毫秒)
        self._规则防抖.timeout.connect(self._发规则)

        外 = QVBoxLayout(self)
        外.setContentsMargins(8, 8, 8, 8)
        外.setSpacing(8)

        self.说明标签 = QLabel("这些设置只影响观感与过滤，改完立刻生效（拖动滑块时 "
                          f"{防抖毫秒}ms 合并一次）。")
        self.说明标签.setStyleSheet(f"color: {颜色提示};")
        self.说明标签.setWordWrap(True)
        外.addWidget(self.说明标签)

        滚动 = QScrollArea()
        滚动.setWidgetResizable(True)
        滚动.setFrameShape(QFrame.Shape.NoFrame)
        内容 = QWidget()
        内容布局 = QVBoxLayout(内容)
        内容布局.setContentsMargins(0, 0, 6, 0)
        内容布局.setSpacing(8)
        内容布局.addWidget(self._建观感区())
        内容布局.addWidget(self._建时间轴区())
        内容布局.addWidget(self._建过滤区())
        内容布局.addWidget(self._建规则文件区())
        内容布局.addStretch(1)
        滚动.setWidget(内容)
        外.addWidget(滚动, 1)

    # ---- 观感 ----

    def _建观感区(self) -> QGroupBox:
        区 = QGroupBox("观感")
        表 = QFormLayout(区)
        表.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.显示勾 = QCheckBox("显示弹幕")
        self.显示勾.toggled.connect(lambda 开: self._配置变了(显示=bool(开)))
        表.addRow("", self.显示勾)

        self.速度滑块 = QSlider(Qt.Orientation.Horizontal)
        self.速度滑块.setRange(60, 600)
        self.速度滑块.setSingleStep(10)
        self.速度滑块.setPageStep(40)
        self.速度值标签 = QLabel("")
        self.速度滑块.valueChanged.connect(self._速度变了)
        表.addRow("速度", self._滑块行(self.速度滑块, self.速度值标签))

        self.不透明度滑块 = QSlider(Qt.Orientation.Horizontal)
        self.不透明度滑块.setRange(10, 100)      # 10% 是底线：再淡就等于没显示
        self.不透明度值标签 = QLabel("")
        self.不透明度滑块.valueChanged.connect(self._不透明度变了)
        表.addRow("不透明度", self._滑块行(self.不透明度滑块, self.不透明度值标签))

        self.字号滑块 = QSlider(Qt.Orientation.Horizontal)
        self.字号滑块.setRange(字号千分下限, 字号千分上限)
        self.字号值标签 = QLabel("")
        self.字号滑块.valueChanged.connect(self._字号变了)
        表.addRow("字号比例", self._滑块行(self.字号滑块, self.字号值标签))

        self.显示区滑块 = QSlider(Qt.Orientation.Horizontal)
        self.显示区滑块.setRange(10, 90)
        self.显示区值标签 = QLabel("")
        self.显示区滑块.valueChanged.connect(self._显示区变了)
        表.addRow("显示区比例", self._滑块行(self.显示区滑块, self.显示区值标签))

        self.安全间距框 = QSpinBox()
        self.安全间距框.setRange(0, 60)
        self.安全间距框.setSuffix(" px")
        self.安全间距框.setToolTip("同轨道两条弹幕之间至少留多远，太小会叠在一起")
        self.安全间距框.valueChanged.connect(
            lambda 值: self._配置变了(安全间距像素=float(值)))
        表.addRow("安全间距", self.安全间距框)

        self.最小字号框 = QSpinBox()
        self.最小字号框.setRange(8, 48)
        self.最小字号框.setSuffix(" px")
        self.最小字号框.valueChanged.connect(
            lambda 值: self._配置变了(最小字号像素=int(值)))
        表.addRow("最小字号", self.最小字号框)

        self.最大字号框 = QSpinBox()
        self.最大字号框.setRange(24, 200)
        self.最大字号框.setSuffix(" px")
        self.最大字号框.setToolTip("大屏上字号比例算出来太大时，用它封顶")
        self.最大字号框.valueChanged.connect(
            lambda 值: self._配置变了(最大字号像素=int(值)))
        表.addRow("最大字号", self.最大字号框)

        self.彩色勾 = QCheckBox("显示彩色弹幕（关掉就全用白色）")
        self.彩色勾.toggled.connect(lambda 开: self._配置变了(显示彩色=bool(开)))
        表.addRow("", self.彩色勾)

        self.顶部勾 = QCheckBox("允许顶部弹幕")
        self.顶部勾.toggled.connect(lambda 开: self._配置变了(允许顶部=bool(开)))
        表.addRow("", self.顶部勾)

        self.底部勾 = QCheckBox("允许底部弹幕")
        self.底部勾.setToolTip("底部弹幕会挡字幕，默认关")
        self.底部勾.toggled.connect(lambda 开: self._配置变了(允许底部=bool(开)))
        self.底部提示 = QLabel("（底部弹幕会挡字幕，默认关）")
        self.底部提示.setStyleSheet(f"color: {颜色警告}; font-size: 11px;")
        底行 = QHBoxLayout()
        底行.addWidget(self.底部勾)
        底行.addWidget(self.底部提示)
        底行.addStretch(1)
        表.addRow("", self._装成控件(底行))
        return 区

    # ---- 时间轴 ----

    def _建时间轴区(self) -> QGroupBox:
        区 = QGroupBox("时间轴")
        表 = QFormLayout(区)
        表.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.偏移滑块 = QSlider(Qt.Orientation.Horizontal)
        self.偏移滑块.setRange(偏移下限, 偏移上限)
        self.偏移滑块.setSingleStep(100)
        self.偏移滑块.setPageStep(1000)
        self.偏移滑块.valueChanged.connect(self._偏移变了)
        self.偏移值标签 = QLabel("")
        self.偏移值标签.setMinimumWidth(180)
        表.addRow("偏移", self._滑块行(self.偏移滑块, self.偏移值标签))

        按钮行 = QHBoxLayout()
        self.减半秒按钮 = QPushButton("−0.5s")
        self.减半秒按钮.setToolTip("弹幕比画面早了 → 整体往后挪 0.5 秒")
        self.减半秒按钮.clicked.connect(lambda: self.调偏移(-500))
        按钮行.addWidget(self.减半秒按钮)
        self.加半秒按钮 = QPushButton("+0.5s")
        self.加半秒按钮.setToolTip("弹幕比画面晚了 → 整体往前挪 0.5 秒")
        self.加半秒按钮.clicked.connect(lambda: self.调偏移(500))
        按钮行.addWidget(self.加半秒按钮)
        self.归零按钮 = QPushButton("归零")
        self.归零按钮.setToolTip("弹幕不偏移")
        self.归零按钮.clicked.connect(self.归零偏移)
        按钮行.addWidget(self.归零按钮)
        self.偏移提示 = QLabel("正值 = 弹幕往后挪（等字幕对不上时用）")
        self.偏移提示.setStyleSheet(f"color: {颜色提示}; font-size: 11px;")
        按钮行.addWidget(self.偏移提示)
        按钮行.addStretch(1)
        表.addRow("", self._装成控件(按钮行))
        return 区

    # ---- 过滤 ----

    def _建过滤区(self) -> QGroupBox:
        区 = QGroupBox("过滤规则")
        外 = QVBoxLayout(区)

        self.屏蔽词框 = self._多行框("一行一个；文本里出现这些词的弹幕会被挡掉")
        外.addWidget(self._带标题("屏蔽词", self.屏蔽词框))

        self.正则框 = self._多行框("一行一个正则（大小写不敏感）；写错了会被跳过，"
                              "不会影响其它规则")
        self.正则提示 = QLabel("")
        self.正则提示.setWordWrap(True)
        self.正则提示.setStyleSheet(f"color: {颜色提示}; font-size: 11px;")
        外.addWidget(self._带标题("正则", self.正则框, self.正则提示))

        self.发送者框 = self._多行框("一行一个发送者标识；这些人的弹幕全挡掉")
        外.addWidget(self._带标题("屏蔽发送者", self.发送者框))

        self.关键词框 = self._多行框("一行一个；**填了以后只显示含这些词的弹幕**")
        外.addWidget(self._带标题("只显示含关键词", self.关键词框))

        勾行 = QHBoxLayout()
        self.屏蔽顶部勾 = QCheckBox("屏蔽顶部")
        self.屏蔽底部勾 = QCheckBox("屏蔽底部")
        self.屏蔽滚动勾 = QCheckBox("屏蔽滚动")
        self.屏蔽彩色勾 = QCheckBox("屏蔽彩色")
        for 勾 in (self.屏蔽顶部勾, self.屏蔽底部勾, self.屏蔽滚动勾, self.屏蔽彩色勾):
            勾.toggled.connect(self._规则勾变了)
            勾行.addWidget(勾)
        勾行.addStretch(1)
        外.addLayout(勾行)

        数行 = QHBoxLayout()
        数行.addWidget(QLabel("最短文本"))
        self.最短文本框 = QSpinBox()
        self.最短文本框.setRange(0, 500)
        self.最短文本框.setSpecialValueText("不限")
        self.最短文本框.valueChanged.connect(
            lambda 值: self._规则变了(最短文本=int(值)))
        数行.addWidget(self.最短文本框)
        数行.addWidget(QLabel("最长文本"))
        self.最长文本框 = QSpinBox()
        self.最长文本框.setRange(0, 500)
        self.最长文本框.setSpecialValueText("不限")
        self.最长文本框.valueChanged.connect(
            lambda 值: self._规则变了(最长文本=int(值)))
        数行.addWidget(self.最长文本框)
        数行.addSpacing(12)
        数行.addWidget(QLabel("最小秒"))
        self.最小秒框 = QDoubleSpinBox()
        self.最小秒框.setRange(0.0, 86400.0)
        self.最小秒框.setDecimals(1)
        self.最小秒框.setSuffix(" 秒")
        self.最小秒框.setSpecialValueText("不限")
        self.最小秒框.setToolTip("0 = 不限；只想看开头几分钟时用")
        self.最小秒框.valueChanged.connect(
            lambda 值: self._规则变了(最小秒=float(值)))
        数行.addWidget(self.最小秒框)
        数行.addWidget(QLabel("最大秒"))
        self.最大秒框 = QDoubleSpinBox()
        self.最大秒框.setRange(0.0, 86400.0)
        self.最大秒框.setDecimals(1)
        self.最大秒框.setSuffix(" 秒")
        self.最大秒框.setSpecialValueText("不限")
        self.最大秒框.setToolTip("0 = 不限")
        self.最大秒框.valueChanged.connect(
            lambda 值: self._规则变了(最大秒=float(值)))
        数行.addWidget(self.最大秒框)
        数行.addStretch(1)
        外.addLayout(数行)

        self.去重勾 = QCheckBox("去重（同一时间、同一内容的重复弹幕只留一条）")
        self.去重勾.toggled.connect(lambda 开: self._规则变了(去重=bool(开)))
        外.addWidget(self.去重勾)

        # 「启用正则过滤」在数据模型里属于**配置**（见 弹幕配置），所以它的变化
        # 走 配置变化 而不是 规则变化 —— 界面上挨着正则放只是为了好找
        self.启用正则勾 = QCheckBox("启用正则过滤（关掉后正则一条都不参与判定）")
        self.启用正则勾.toggled.connect(
            lambda 开: self._配置变了(启用正则过滤=bool(开)))
        外.addWidget(self.启用正则勾)
        return 区

    # ---- 规则文件 ----

    def _建规则文件区(self) -> QGroupBox:
        区 = QGroupBox("规则导入 / 导出")
        外 = QVBoxLayout(区)
        行 = QHBoxLayout()
        self.导出规则按钮 = QPushButton("导出规则…")
        self.导出规则按钮.setToolTip("把当前过滤规则存成 JSON 文件（可备份/分享）")
        self.导出规则按钮.clicked.connect(self._点导出)
        行.addWidget(self.导出规则按钮)
        self.导入规则按钮 = QPushButton("导入规则…")
        self.导入规则按钮.setToolTip("读一个 JSON 规则文件（坏文件只会提示，不会改当前规则）")
        self.导入规则按钮.clicked.connect(self._点导入)
        行.addWidget(self.导入规则按钮)
        self.规则提示标签 = QLabel("")
        self.规则提示标签.setStyleSheet(f"color: {颜色提示}; font-size: 11px;")
        行.addWidget(self.规则提示标签, 1)
        外.addLayout(行)
        return 区

    # ---- 布局小工具 ----

    @staticmethod
    def _装成控件(布局) -> QWidget:
        """把一行布局包成一个控件（QFormLayout 只收控件）。"""
        壳 = QWidget()
        布局.setContentsMargins(0, 0, 0, 0)
        壳.setLayout(布局)
        return 壳

    @staticmethod
    def _滑块行(滑块: QSlider, 值标签: QLabel) -> QWidget:
        行 = QHBoxLayout()
        行.setContentsMargins(0, 0, 0, 0)
        滑块.setMinimumWidth(240)
        值标签.setMinimumWidth(210)
        行.addWidget(滑块, 1)
        行.addWidget(值标签)
        return 弹幕设置面板._装成控件(行)

    @staticmethod
    def _带标题(标题: str, 控件: QWidget, 提示: Optional[QLabel] = None) -> QWidget:
        壳 = QWidget()
        列 = QVBoxLayout(壳)
        列.setContentsMargins(0, 0, 0, 0)
        列.setSpacing(2)
        名 = QLabel(标题)
        名.setStyleSheet("font-weight: 600;")
        列.addWidget(名)
        列.addWidget(控件)
        if 提示 is not None:
            列.addWidget(提示)
        return 壳

    def _多行框(self, 占位: str) -> QPlainTextEdit:
        框 = QPlainTextEdit()
        框.setPlaceholderText(占位)
        框.setFixedHeight(66)
        框.textChanged.connect(self._规则文本变了)
        return 框

    # ---------------- 控件 → 配置/规则 ----------------

    def _发配置(self) -> None:
        self.配置变化.emit(self._配置)

    def _发规则(self) -> None:
        self.规则变化.emit(self._规则)

    def _配置变了(self, **改动) -> None:
        """控件的新值并进配置（**立刻**），信号走"首帧立即 + 60ms 合并"。"""
        if self._回填中:
            return
        self._配置 = self._配置.复制(**改动)
        if not self._配置防抖.isActive():
            self.配置变化.emit(self._配置)
        self._配置防抖.start()

    def _规则变了(self, **改动) -> None:
        if self._回填中:
            return
        self._规则 = self._规则.复制(**改动)
        if not self._规则防抖.isActive():
            self.规则变化.emit(self._规则)
        self._规则防抖.start()

    # ---- 各控件的槽（顺带刷新旁边的说明文字）----

    def _速度变了(self, 值: int) -> None:
        self.速度值标签.setText(f"{int(值)} px/s（1920 宽约 {1920 / max(1, 值):.1f} 秒穿完）")
        self._配置变了(速度像素每秒=float(值))

    def _不透明度变了(self, 值: int) -> None:
        self.不透明度值标签.setText(f"{int(值)}%")
        self._配置变了(不透明度=int(值) / 100.0)

    def _字号变了(self, 千分: int) -> None:
        比例 = int(千分) / 1000.0
        # 换算成 1080p 下的像素：比例本身很抽象，用户想知道的是"字多大"
        像素 = int(round(比例 * 基准高度))
        像素 = max(self.最小字号框.value(), min(self.最大字号框.value(), 像素))
        self.字号值标签.setText(f"{比例 * 100:.1f}%｜1080p 下约 {像素} px")
        self._配置变了(字号比例=比例)

    def _显示区变了(self, 值: int) -> None:
        self.显示区值标签.setText(f"{int(值)}%（顶部起算）")
        self._配置变了(显示区比例=int(值) / 100.0)

    def _偏移变了(self, 值: int) -> None:
        self.偏移值标签.setText(self._偏移文本(int(值)))
        self._配置变了(时间轴偏移毫秒=int(值))

    @staticmethod
    def _偏移文本(毫秒: int) -> str:
        if not 毫秒:
            return "0 ms（不偏移）"
        return f"{毫秒:+d} ms（{毫秒 / 1000:+.1f} 秒）"

    def _规则文本变了(self) -> None:
        """四个多行框共用一个槽：逐行解析成列表，顺手检查正则是否能用。"""
        if self._回填中:
            return
        self._查正则()
        self._规则变了(屏蔽词=_解析多行(self.屏蔽词框.toPlainText()),
                    正则们=_解析多行(self.正则框.toPlainText()),
                    屏蔽发送者=_解析多行(self.发送者框.toPlainText()),
                    只显示含关键词=_解析多行(self.关键词框.toPlainText()))

    def _规则勾变了(self, _开: bool) -> None:
        self._规则变了(屏蔽顶部=self.屏蔽顶部勾.isChecked(),
                    屏蔽底部=self.屏蔽底部勾.isChecked(),
                    屏蔽滚动=self.屏蔽滚动勾.isChecked(),
                    屏蔽彩色=self.屏蔽彩色勾.isChecked())

    def _查正则(self) -> list[str]:
        """把用不了的正则标红提示；返回坏正则表（**不阻止任何保存**）。"""
        坏 = _坏正则(_解析多行(self.正则框.toPlainText()))
        if 坏:
            self.正则框.setStyleSheet(
                f"border: 1px solid {颜色错}; background: #2a1c1e;")
            self.正则提示.setStyleSheet(f"color: {颜色错}; font-size: 11px;")
            self.正则提示.setText(
                f"⚠️ {len(坏)} 条正则用不了（判定时会跳过，其它规则照常）："
                + "、".join(坏[:3]) + ("…" if len(坏) > 3 else ""))
        else:
            self.正则框.setStyleSheet("")
            self.正则提示.setStyleSheet(f"color: {颜色提示}; font-size: 11px;")
            self.正则提示.setText("正则都可用" if self.正则框.toPlainText().strip() else "")
        return 坏

    def _点导出(self) -> None:
        路径, _过滤 = QFileDialog.getSaveFileName(self, "导出过滤规则", "弹幕规则.json",
                                               规则文件过滤)
        if 路径:
            self.导出到文件(路径)

    def _点导入(self) -> None:
        路径, _过滤 = QFileDialog.getOpenFileName(self, "导入过滤规则", "",
                                               规则文件过滤)
        if 路径:
            self.从文件导入(路径)

    # ---------------- 配置/规则 → 控件 ----------------

    def _回填配置(self, 配置: 弹幕配置) -> None:
        self._回填中 = True
        try:
            self._配置防抖.stop()
            self.显示勾.setChecked(bool(配置.显示))
            self.速度滑块.setValue(int(round(float(配置.速度像素每秒))))
            self.不透明度滑块.setValue(int(round(float(配置.不透明度) * 100)))
            self.字号滑块.setValue(int(round(float(配置.字号比例) * 1000)))
            self.显示区滑块.setValue(int(round(float(配置.显示区比例) * 100)))
            self.安全间距框.setValue(int(round(float(配置.安全间距像素))))
            self.最小字号框.setValue(int(配置.最小字号像素))
            self.最大字号框.setValue(int(配置.最大字号像素))
            self.彩色勾.setChecked(bool(配置.显示彩色))
            self.顶部勾.setChecked(bool(配置.允许顶部))
            self.底部勾.setChecked(bool(配置.允许底部))
            self.偏移滑块.setValue(int(配置.时间轴偏移毫秒))
            self.启用正则勾.setChecked(bool(配置.启用正则过滤))
            # 滑块被 setValue 触发过 valueChanged，标签已经刷过；这里再兜一次底
            self._速度变了(self.速度滑块.value())
            self._不透明度变了(self.不透明度滑块.value())
            self._字号变了(self.字号滑块.value())
            self._显示区变了(self.显示区滑块.value())
            self._偏移变了(self.偏移滑块.value())
        finally:
            self._回填中 = False

    def _回填规则(self, 规则: 过滤规则) -> None:
        self._回填中 = True
        try:
            self._规则防抖.stop()
            self.屏蔽词框.setPlainText(_合并行(规则.屏蔽词))
            self.正则框.setPlainText(_合并行(规则.正则们))
            self.发送者框.setPlainText(_合并行(规则.屏蔽发送者))
            self.关键词框.setPlainText(_合并行(规则.只显示含关键词))
            self.屏蔽顶部勾.setChecked(bool(规则.屏蔽顶部))
            self.屏蔽底部勾.setChecked(bool(规则.屏蔽底部))
            self.屏蔽滚动勾.setChecked(bool(规则.屏蔽滚动))
            self.屏蔽彩色勾.setChecked(bool(规则.屏蔽彩色))
            self.最短文本框.setValue(int(规则.最短文本))
            self.最长文本框.setValue(int(规则.最长文本))
            self.最小秒框.setValue(float(规则.最小秒))
            self.最大秒框.setValue(float(规则.最大秒))
            self.去重勾.setChecked(bool(规则.去重))
        finally:
            self._回填中 = False
        self._查正则()

    # ---------------- 小工具 ----------------

    def _规则摘要(self) -> str:
        规则 = self._规则
        return (f"屏蔽词 {len(规则.屏蔽词)}｜正则 {len(规则.正则们)}"
                f"｜发送者 {len(规则.屏蔽发送者)}｜关键词 {len(规则.只显示含关键词)}"
                f"｜去重 {'开' if 规则.去重 else '关'}")

    def _规则提示(self, 文本: str, 颜色: str) -> None:
        self.规则提示标签.setStyleSheet(f"color: {颜色}; font-size: 11px;")
        self.规则提示标签.setText(str(文本 or ""))
