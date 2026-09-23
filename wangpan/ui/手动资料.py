"""手动填写/修改影片资料（**完全不需要网络**的一条路）。

为什么必须有这条路
==================
刮削靠 TMDB，而 TMDB 的 API 域名在很多网络环境下根本连不上（实测被解析到无关 IP，
IPv4/IPv6 一起超时）。用户手里明明有片子、只想自己填个标题/年份/简介/评分，
不能因为"外网不通"就让资料库一直空着。本模块**只碰本地 SQLite 与本地图片文件**，
一个网络请求都不发 —— 断网时它就是唯一能用的入库方式。

为什么选图要先"纳入缓存"
========================
库里只记"缓存目录里的文件名"（见 :mod:`wangpan.scrape.图片`）。若把用户桌面上的
原图路径直接写进库，用户哪天移动/删掉那张图，海报墙就全是破图；所以选图后先用
:meth:`图片缓存.确保本地文件` 把图**复制进缓存目录**，库里记的是缓存路径。

为什么对话框只是个"壳"
======================
字段、校验、写库这些逻辑只写一份，放在 :class:`手动资料面板` 里；
:class:`手动资料对话框` = 面板 + 一个「关闭」。这样"嵌进详情页"与"弹窗"两种用法
永远不会有行为差异（也就不会出现"弹窗能存、嵌进去存不了"这种鬼故事）。

写库的两个坑（细节见 :meth:`手动资料面板.保存`）
================================================
* 编辑已有条目时**必须在原条目上改字段**，不能另建空条目 —— 否则季/集结构会丢；
* ``库.存条目`` 是按 ``(来源, tmdb_id)`` 找行的，而手动条目常常没有任何外部 id
  （键是空串）：两条手动记录共用空键时，后存的会把前一条**整条覆盖**。所以这里
  会先挑一个不会撞车的键（撞车时给一个本地内部编号）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from ..scrape.库 import 资料库
from ..scrape.模型 import 分级, 图片, 图片类型, 媒体条目, 媒体类型
from ..scrape.图片 import 图片缓存

__all__ = ["手动资料对话框", "手动资料面板", "图片选择框", "手动来源"]

#: 手动条目的来源标记：与 TMDB 刮削的条目区分开（"这条是人填的"，一眼能看出来）
手动来源 = "手动"

#: 年份上界（0 = 未知）：给人手输入留足余地，又能挡住"年份多打一位数"
年份上限 = 2100

#: 两个评分**量纲不同**，界面上必须分开写清楚，否则用户会把 8.5 填进百分制里
评分上限 = 10.0
影评人上限 = 100.0

#: 选图对话框的过滤串（webp 也放进来：现在很多自备图是 webp）
图片过滤 = "图片 (*.jpg *.jpeg *.png *.webp);;所有文件 (*)"

#: 分级国家的固定候选（CN 排前面：中文用户最常填它；TMDB 反而没有 CN）
分级国家们 = ("CN", "HK", "TW", "US", "JP")

#: 自动分配的内部键前缀（库里没有外部 id 的手动条目用它当行身份，见 _可用主键）
内部键前缀 = "本地-"

#: 行内提示的四种颜色（行内提示比模态弹窗好：不打断操作、也不会卡住调用方）
颜色提示 = "#8fa2bb"
颜色好 = "#7ddc8a"
颜色警告 = "#ffb454"
颜色错 = "#ff7b72"


def _解析标签(文本: str) -> list[str]:
    """逗号分隔 → 标签表：中英文逗号都认，去首尾空白、去空项、保留顺序去重。

    为什么要去重：标签表在库里是 ``UNIQUE(媒体id, 名称)``，重复项存进去本来就会被
    丢掉，不如在这里就规整好，用户看到的就是库里真实的样子。
    """
    结果: list[str] = []
    for 段 in str(文本 or "").replace("，", ",").split(","):
        名 = 段.strip()
        if 名 and 名 not in 结果:
            结果.append(名)
    return 结果


def _夹(值: float, 低: float, 高: float) -> float:
    """把数值夹进 ``[低, 高]``。

    控件本身有范围，但"控件范围"是界面约定、"夹一次"是数据约定：以后有人从代码里
    塞值（批量导入/脚本），库里也不该出现 8.5 分制的 85 分。
    """
    return max(低, min(高, float(值)))


class 图片选择框(QWidget):
    """一张主图（海报/背景/标志）：预览 + 「选择本地图片…」+「清除」。

    为什么单独做成控件：三种图的行为完全一样（选/清/预览/取图片），
    在表单里写三遍就是三份要同步维护的代码，迟早有一份忘了改。
    """

    变化 = Signal()                 # 用户换了图或清了图（装载时**不发**）

    def __init__(self, 缓存, 图类型: 图片类型, 说明: str,
                 预览尺寸: tuple[int, int] = (140, 200), 父=None) -> None:
        super().__init__(父)
        self.缓存 = 缓存
        self.图类型 = 图类型
        self.说明 = 说明
        self._图片: Optional[图片] = None

        布局 = QVBoxLayout(self)
        布局.setContentsMargins(0, 0, 0, 0)
        布局.setSpacing(4)

        self.标题标签 = QLabel(说明)
        self.标题标签.setStyleSheet("font-weight: 600;")
        布局.addWidget(self.标题标签)

        self.预览标签 = QLabel("（未选择）")
        self.预览标签.setFixedSize(*预览尺寸)
        self.预览标签.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.预览标签.setStyleSheet(
            f"border: 1px solid #3a4350; background: #1b1f26; color: {颜色提示};")
        布局.addWidget(self.预览标签)

        按钮行 = QHBoxLayout()
        按钮行.setSpacing(4)
        self.选择按钮 = QPushButton("选择本地图片…")
        self.选择按钮.setToolTip("从本机选一张图（jpg/png/webp）；会复制进图片缓存，"
                             "以后移动原图也不影响")
        self.选择按钮.clicked.connect(self._选图)
        按钮行.addWidget(self.选择按钮)
        self.清除按钮 = QPushButton("清除")
        self.清除按钮.setToolTip("不用这张图了（只是不写进条目，缓存文件留在缓存目录）")
        self.清除按钮.clicked.connect(self.清除)
        按钮行.addWidget(self.清除按钮)
        布局.addLayout(按钮行)

        self.状态标签 = QLabel("未选择")
        self.状态标签.setStyleSheet(f"color: {颜色提示}; font-size: 11px;")
        self.状态标签.setWordWrap(True)
        布局.addWidget(self.状态标签)

    # ---------------- 对外 ----------------

    def 取图片(self) -> Optional[图片]:
        return self._图片

    def 有图(self) -> bool:
        return self._图片 is not None and self._图片.可用()

    def 设图片(self, 图: Optional[图片], 发信号: bool = False) -> None:
        """装载时用（默认不发信号：回填不是"用户改了图"）。"""
        self._图片 = 图
        self._刷新预览()
        if 发信号:
            self.变化.emit()

    def 选本地文件(self, 来源: Path | str) -> bool:
        """把一张本地图纳入缓存并作为当前图。

        文件对话框与测试都走这里：``确保本地文件`` 是**复制**，所以之后用户删掉原图
        也不影响海报墙。"远端路径键"用 ``手动/<文件名>``：缓存文件名是哈希过的，
        中文/空格都不会出问题，同时键本身还能看出图是从哪来的。
        """
        路径 = Path(来源)
        if not 路径.is_file():
            self.状态标签.setText(f"❌ 找不到文件：{路径.name}")
            return False
        键 = f"手动/{路径.name}"
        目标 = self.缓存.确保本地文件(路径, 键, 尺寸="本地")
        if 目标 is None:
            self.状态标签.setText(f"❌ 纳入图片缓存失败：{路径.name}")
            return False
        图 = self.缓存.读图(目标)
        if 图 is None:
            # 文件在、但 Qt 解不出来（选错文件、图损坏）：给一句人话，别留个空框
            self.状态标签.setText(f"❌ 这不是能识别的图片：{路径.name}")
            return False
        self.设图片(图片(类型=self.图类型, 远端路径=键, 本地路径=Path(目标),
                        宽=图.width(), 高=图.height(), 来源=手动来源), 发信号=True)
        return True

    def 清除(self) -> bool:
        """清掉当前图（返回是否真的清掉了；缓存文件不动 —— 别的条目可能在用）。"""
        if self._图片 is None:
            return False
        self._图片 = None
        self._刷新预览()
        self.变化.emit()
        return True

    # ---------------- 内部 ----------------

    def _选图(self) -> None:
        路径, _过滤 = QFileDialog.getOpenFileName(self, f"选择{self.说明}", "",
                                               图片过滤)
        if 路径:
            self.选本地文件(路径)

    def _刷新预览(self) -> None:
        图 = self._图片
        if 图 is None or 图.本地路径 is None:
            self.预览标签.clear()            # 先清 pixmap 再写文字，否则文字不显示
            self.预览标签.setText("（未选择）")
            self.状态标签.setText("未选择")
            self.预览标签.setToolTip("")
            return
        本地 = Path(图.本地路径)
        if not 本地.is_file():
            self.预览标签.clear()
            self.预览标签.setText("（文件没了）")
            self.状态标签.setText(f"⚠️ 缓存里的文件不在了：{本地.name}")
            return
        # 缩略到预览框内（原图可能几千像素，直接塞进小框会白占内存）
        框 = self.预览标签.size()
        缩放 = self.缓存.读图(本地, (max(1, 框.width()), max(1, 框.height())))
        if 缩放 is None:
            self.预览标签.clear()
            self.预览标签.setText("（读不出来）")
            self.状态标签.setText(f"⚠️ 读不出这张图：{本地.name}")
            return
        self.预览标签.setPixmap(QPixmap.fromImage(缩放))
        self.预览标签.setText("")
        大 = f"{图.宽}×{图.高}" if 图.宽 and 图.高 else "尺寸未知"
        self.状态标签.setText(f"✅ {本地.name}（{大}）")
        self.预览标签.setToolTip(str(本地))


class 手动资料面板(QWidget):
    """手动填写/修改一条资料的**非模态**表单（嵌详情页或独立窗口都行）。

    校验失败一律走"行内提示 + 信号"，**不弹模态框**：这个面板会被代码直接调用
    （批量整理、测试），模态框会把调用方卡死在那儿 —— 界面卡死比少个弹窗严重得多。
    """

    已保存 = Signal(int)            # 媒体id（写库成功）
    校验失败 = Signal(str)          # 为什么没保存（宿主想弹提示就自己弹）

    def __init__(self, 库: 资料库, 图片缓存, 媒体id: int | None = None,
                 父=None) -> None:
        super().__init__(父)
        self.库 = 库
        self.缓存 = 图片缓存
        self._媒体id: Optional[int] = None
        #: 组装/保存都以它为基础：**装载时是库里的那一条、新建时是一条空的**，
        #: 一律"在它上面改字段"而不是另建 —— 剧集装载时新建空条目会把季集丢掉
        self._原条目: Optional[媒体条目] = None
        #: 装载/上次保存那一刻的值快照（:meth:`有未保存改动` 用它对比）
        self._载入快照: tuple = ()
        #: 这次保存有没有自动分配内部键（有的话提示里要说清楚）
        self._自动键 = ""

        self._建界面()
        self.新建()
        if 媒体id is not None:
            self.装载(媒体id)

    # ---------------- 界面 ----------------

    def _建界面(self) -> None:
        外 = QVBoxLayout(self)
        外.setContentsMargins(8, 8, 8, 8)
        外.setSpacing(6)

        self.说明标签 = QLabel("手动填写不联网；填完保存到本地资料库，海报墙立刻能看到")
        self.说明标签.setStyleSheet(f"color: {颜色好}; font-weight: 600;")
        self.说明标签.setWordWrap(True)
        外.addWidget(self.说明标签)

        # 表单很高（十几行字段 + 三张图），窄窗口里必须能滚，否则下面的图永远看不见
        滚动 = QScrollArea()
        滚动.setWidgetResizable(True)
        滚动.setFrameShape(QFrame.Shape.NoFrame)
        内容 = QWidget()
        内容布局 = QVBoxLayout(内容)
        内容布局.setContentsMargins(0, 0, 6, 0)
        内容布局.setSpacing(8)
        内容布局.addWidget(self._建基本信息区())
        内容布局.addWidget(self._建简介区())
        内容布局.addWidget(self._建分级区())
        内容布局.addWidget(self._建外部ID区())
        内容布局.addWidget(self._建图片区())
        内容布局.addStretch(1)
        滚动.setWidget(内容)
        外.addWidget(滚动, 1)

        底部 = QHBoxLayout()
        self.提示标签 = QLabel("")
        self.提示标签.setStyleSheet(f"color: {颜色提示};")
        self.提示标签.setWordWrap(True)
        底部.addWidget(self.提示标签, 1)
        self.保存按钮 = QPushButton("💾 保存到资料库")
        self.保存按钮.setToolTip("写进本地资料库（不联网）")
        self.保存按钮.clicked.connect(self._点保存)
        底部.addWidget(self.保存按钮)
        外.addLayout(底部)

    def _建基本信息区(self) -> QGroupBox:
        区 = QGroupBox("基本信息")
        表 = QFormLayout(区)
        表.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.类型框 = QComboBox()
        for 类型 in (媒体类型.电影, 媒体类型.剧集, 媒体类型.未知):
            self.类型框.addItem(类型.value, 类型)
        表.addRow("类型", self.类型框)

        self.标题框 = QLineEdit()
        self.标题框.setPlaceholderText("必填：片名（海报墙上显示的就是它）")
        self.标题框.textChanged.connect(self._有改动)
        表.addRow("标题", self.标题框)

        self.原名框 = QLineEdit()
        self.原名框.setPlaceholderText("可选：原文名（罗马字/英文名）")
        表.addRow("原名", self.原名框)

        self.年份框 = QSpinBox()
        self.年份框.setRange(0, 年份上限)
        self.年份框.setSpecialValueText("未知")     # 0 就显示"未知"，别让人猜 0 是什么
        表.addRow("年份", self.年份框)

        self.时长框 = QSpinBox()
        self.时长框.setRange(0, 100000)
        self.时长框.setSuffix(" 分钟")
        self.时长框.setSpecialValueText("未知")
        表.addRow("时长", self.时长框)

        self.评分框 = QDoubleSpinBox()
        self.评分框.setRange(0.0, 评分上限)
        self.评分框.setDecimals(1)
        self.评分框.setSingleStep(0.1)
        self.评分框.setSuffix(f" / {评分上限:.0f}")
        表.addRow("评分", self.评分框)

        self.票数框 = QSpinBox()
        self.票数框.setRange(0, 2_000_000_000)
        self.票数框.setSpecialValueText("未知")
        表.addRow("评分票数", self.票数框)

        self.影评人框 = QDoubleSpinBox()
        self.影评人框.setRange(0.0, 影评人上限)
        self.影评人框.setDecimals(1)
        self.影评人框.setSingleStep(1.0)
        self.影评人框.setSuffix(" / 100（百分制）")
        表.addRow("影评人评分", self.影评人框)

        self.状态框 = QLineEdit()
        self.状态框.setPlaceholderText("可选：已上映 / 连载中 …")
        表.addRow("状态", self.状态框)

        self.语言框 = QLineEdit()
        self.语言框.setPlaceholderText("可选：原始语言代码，如 zh / en / ja")
        表.addRow("原始语言", self.语言框)

        self.标签框 = QLineEdit()
        self.标签框.setPlaceholderText("用逗号分隔，例如：动作, 科幻（中英文逗号都行）")
        表.addRow("标签", self.标签框)
        return 区

    def _建简介区(self) -> QGroupBox:
        区 = QGroupBox("简介")
        布局 = QVBoxLayout(区)
        self.简介框 = QPlainTextEdit()
        self.简介框.setPlaceholderText("剧情简介（可留空；多行随便写）")
        self.简介框.setFixedHeight(96)
        布局.addWidget(self.简介框)
        return 区

    def _建分级区(self) -> QGroupBox:
        区 = QGroupBox("分级（可多条）")
        布局 = QVBoxLayout(区)

        行 = QHBoxLayout()
        行.addWidget(QLabel("国家"))
        self.国家框 = QComboBox()
        self.国家框.addItems(list(分级国家们))
        self.国家框.setEditable(True)        # 不在候选里的国家也能填（比如 DE/FR）
        行.addWidget(self.国家框)
        行.addWidget(QLabel("值"))
        self.分级值框 = QLineEdit()
        self.分级值框.setPlaceholderText("如 PG-13 / R / 辅导级12")
        self.分级值框.returnPressed.connect(self.添加分级)
        行.addWidget(self.分级值框, 1)
        self.添加分级按钮 = QPushButton("添加")
        self.添加分级按钮.clicked.connect(self.添加分级)
        行.addWidget(self.添加分级按钮)
        布局.addLayout(行)

        self.分级表 = QListWidget()
        self.分级表.setFixedHeight(78)
        self.分级表.setToolTip("双击一行可以删掉它")
        self.分级表.itemDoubleClicked.connect(self._双击分级行)
        布局.addWidget(self.分级表)

        按钮行 = QHBoxLayout()
        按钮行.addStretch(1)
        self.删除分级按钮 = QPushButton("删除选中")
        self.删除分级按钮.clicked.connect(self.删除选中分级)
        按钮行.addWidget(self.删除分级按钮)
        布局.addLayout(按钮行)
        return 区

    def _建外部ID区(self) -> QGroupBox:
        区 = QGroupBox("外部 ID（只做记录，不校验格式）")
        网格 = QGridLayout(区)
        self.tmdb框 = QLineEdit()
        self.tmdb框.setPlaceholderText("TMDB id（没有就留空）")
        self.imdb框 = QLineEdit()
        self.imdb框.setPlaceholderText("tt0000000（没有就留空）")
        self.tvdb框 = QLineEdit()
        self.tvdb框.setPlaceholderText("TVDB id（没有就留空）")
        for 列, (名字, 控件) in enumerate((("tmdb", self.tmdb框),
                                        ("imdb", self.imdb框),
                                        ("tvdb", self.tvdb框))):
            网格.addWidget(QLabel(名字), 0, 列 * 2)
            网格.addWidget(控件, 0, 列 * 2 + 1)
            网格.setColumnStretch(列 * 2 + 1, 1)
        return 区

    def _建图片区(self) -> QGroupBox:
        区 = QGroupBox("海报 / 背景 / 标志（从本机选，会复制进图片缓存）")
        网格 = QGridLayout(区)
        self.海报框 = 图片选择框(self.缓存, 图片类型.海报, "海报", (140, 200))
        self.背景框 = 图片选择框(self.缓存, 图片类型.背景, "背景", (200, 112))
        self.标志框 = 图片选择框(self.缓存, 图片类型.标志, "标志", (140, 70))
        self.海报框.变化.connect(self._图片动了)
        self.背景框.变化.connect(self._图片动了)
        self.标志框.变化.connect(self._图片动了)
        for 列, 控件 in enumerate((self.海报框, self.背景框, self.标志框)):
            网格.addWidget(控件, 0, 列)
        return 区

    # ---------------- 状态 ----------------

    def 新建(self) -> None:
        """清空成"新记录"状态（类型默认电影：绝大多数手动入库的是电影）。"""
        self._媒体id = None
        # 新建也要有一条**持久的**基准条目：组装条目一律在基准条目上改字段，
        # 这样"有没有未保存改动"之类需要比较基准的判断才不会每次都换对象
        self._原条目 = 媒体条目(类型=媒体类型.电影, 来源=手动来源)
        self._回填(self._原条目)
        self.设置提示("新建：填好标题就能保存（不联网）", "好")

    def 装载(self, 媒体id: int) -> bool:
        """把库里的一条读进表单；找不到返回 False（不抛异常 —— 界面不该因为
        一个过期 id 就崩掉）。"""
        条目 = self.库.取媒体(int(媒体id))
        if 条目 is None:
            self.设置提示(f"⚠️ 资料库里没有 id={媒体id} 的记录", "警告")
            return False
        self._媒体id = int(媒体id)
        self._原条目 = 条目
        self._回填(条目)
        季集 = f"｜季 {len(条目.季们)}｜集 {条目.汇总集数()}" if 条目.季们 else ""
        self.设置提示(f"已载入「{条目.标题 or '未命名'}」（id={媒体id}，"
                  f"来源 {条目.来源}{季集}）", "好")
        return True

    def 当前媒体id(self) -> Optional[int]:
        return self._媒体id

    def 有未保存改动(self) -> bool:
        """当前表单与"库里那一份"是否不同（宿主关窗前问一句用）。

        比的是**装载时拍下的值快照**，不是"原条目对象"：组装条目是在原条目上改字段的
        （为了保住季集），对象和自己比永远相等，等于白比。
        """
        return self._快照(self.组装条目()) != self._载入快照

    @staticmethod
    def _快照(条目: 媒体条目) -> tuple:
        """把一条资料里"界面能改的东西"压成不可变元组（值快照，之后改条目不影响它）。"""
        def 图(一个: Optional[图片]) -> str:
            return str(一个.本地路径) if 一个 is not None and 一个.本地路径 else ""

        外部 = dict(条目.外部ID or {})
        return (条目.类型.value, 条目.标题, 条目.原名, 条目.年份, 条目.简介,
                tuple(条目.标签 or ()), 条目.时长分钟, 条目.评分, 条目.评分票数,
                条目.影评人评分, 条目.状态, 条目.原始语言,
                tuple((一条.国家, 一条.值) for 一条 in (条目.分级们 or ())),
                # 三个外部 ID 一律按"规范化后的样子"比：新建的条目里 ExternalID 可能是
                # 空字典、装载回来的却是带三个空串的字典，不拉平就会永远判成"有改动"
                tuple((名字, str(外部.get(名字, "") or "").strip())
                      for 名字 in ("tmdb", "imdb", "tvdb")),
                图(条目.海报), 图(条目.背景), 图(条目.标志))

    # ---------------- 分级增删 ----------------

    def 添加分级(self) -> bool:
        """把"国家 + 值"加进列表（值为空不加：空分级写进库没有意义）。"""
        国家 = str(self.国家框.currentText() or "").strip().upper()
        值 = self.分级值框.text().strip()
        if not 值:
            self.设置提示("⚠️ 分级的值不能为空（比如 PG-13）", "警告")
            return False
        if not 国家:
            self.设置提示("⚠️ 分级要先选/填一个国家", "警告")
            return False
        for 行号 in range(self.分级表.count()):
            项 = self.分级表.item(行号)
            if 项 and 项.text() == f"{国家}:{值}":
                self.设置提示(f"这条分级已经有了：{国家}:{值}", "警告")
                return False
        self.分级表.addItem(f"{国家}:{值}")
        self.分级值框.clear()
        self.设置提示(f"已添加分级 {国家}:{值}", "好")
        return True

    def 删除选中分级(self) -> bool:
        行号 = self.分级表.currentRow()
        if 行号 < 0:
            self.设置提示("先选中一条分级再删", "警告")
            return False
        项 = self.分级表.takeItem(行号)
        self.设置提示(f"已删除分级 {项.text() if 项 else ''}", "好")
        return True

    def _双击分级行(self, 项) -> None:
        # 删的是**双击的那一行**（"双击"的位置比"当前选中行"更贴近用户的意思）
        行号 = self.分级表.row(项)
        if 行号 >= 0:
            self.分级表.setCurrentRow(行号)
        self.删除选中分级()

    def _取分级们(self) -> list[分级]:
        """列表里的每一行 → :class:`分级`（值里再出现冒号也只切第一次）。"""
        结果: list[分级] = []
        for 行号 in range(self.分级表.count()):
            项 = self.分级表.item(行号)
            文本 = str(项.text() if 项 else "")
            if not 文本:
                continue
            国家, _, 值 = 文本.partition(":")
            结果.append(分级(国家=国家.strip(), 值=值.strip(), 来源=手动来源))
        return 结果

    # ---------------- 表单 ↔ 条目 ----------------

    def 校验(self) -> str:
        """返回空串 = 通过；否则是**人能看懂**的拒绝原因。"""
        if not self.标题框.text().strip():
            return "标题不能为空：海报墙上显示的就是标题"
        年份 = int(self.年份框.value())
        if not (0 <= 年份 <= 年份上限):
            return f"年份要在 0（未知）到 {年份上限} 之间，现在是 {年份}"
        if not (0.0 <= float(self.评分框.value()) <= 评分上限):
            return f"评分要在 0–{评分上限:.0f} 之间（一位小数）"
        if not (0.0 <= float(self.影评人框.value()) <= 影评人上限):
            return f"影评人评分是百分制，要在 0–{影评人上限:.0f} 之间"
        return ""

    def 组装条目(self) -> 媒体条目:
        """把界面上的值写进条目（**在原条目上改字段**，保住季/集/参演/图片）。

        为什不新建 :class:`媒体条目`：新建等于把"这条剧集有几季几集"整个丢掉 ——
        用户只想改个标题，结果集列表没了，那是最糟的一类 bug。
        """
        条目 = self._原条目 if self._原条目 is not None else 媒体条目()
        条目.类型 = self._取类型()
        条目.标题 = self.标题框.text().strip()
        条目.原名 = self.原名框.text().strip()
        年份 = int(self.年份框.value())
        条目.年份 = 年份 if 年份 > 0 else None       # 0 = 未知 → 库里存 NULL
        条目.简介 = self.简介框.toPlainText().strip()
        条目.时长分钟 = max(0, int(self.时长框.value()))
        条目.评分 = _夹(self.评分框.value(), 0.0, 评分上限)
        条目.评分票数 = max(0, int(self.票数框.value()))
        条目.影评人评分 = _夹(self.影评人框.value(), 0.0, 影评人上限)
        条目.状态 = self.状态框.text().strip()
        条目.原始语言 = self.语言框.text().strip()
        条目.标签 = _解析标签(self.标签框.text())
        条目.分级们 = self._取分级们()
        条目.海报 = self.海报框.取图片()
        条目.背景 = self.背景框.取图片()
        条目.标志 = self.标志框.取图片()
        # 外部 ID：去掉首尾空白就够了（格式千奇百怪，校验格式只会挡住用户）；
        # 但**其它键要保留**（别的东西可能也往这里塞过东西）
        外部ID = dict(条目.外部ID or {})
        for 名, 控件 in (("tmdb", self.tmdb框), ("imdb", self.imdb框),
                        ("tvdb", self.tvdb框)):
            外部ID[名] = 控件.text().strip()
        条目.外部ID = 外部ID
        条目.来源 = 手动来源
        return 条目

    def 取条目(self) -> 媒体条目:
        """当前表单对应的条目（还没写库）。**对话框版本**的 ``取条目`` 语义不同：
        那个返回"用户保存后的结果"。"""
        return self.组装条目()

    def _取类型(self) -> 媒体类型:
        数据 = self.类型框.currentData()
        if isinstance(数据, 媒体类型):
            return 数据
        try:
            return 媒体类型(str(self.类型框.currentText() or ""))
        except ValueError:
            return 媒体类型.未知

    def _回填(self, 条目: 媒体条目) -> None:
        """把条目铺到控件上（**不发信号**：装载不是用户操作）。"""
        表格 = (self.类型框, self.标题框, self.原名框, self.年份框, self.时长框,
                self.评分框, self.票数框, self.影评人框, self.状态框, self.语言框,
                self.标签框, self.简介框, self.国家框, self.分级值框, self.分级表,
                self.tmdb框, self.imdb框, self.tvdb框, self.海报框, self.背景框,
                self.标志框)
        原先 = [控件.blockSignals(True) for 控件 in 表格]
        try:
            位置 = self.类型框.findData(条目.类型)
            self.类型框.setCurrentIndex(位置 if 位置 >= 0 else self.类型框.count() - 1)
            self.标题框.setText(条目.标题 or "")
            self.原名框.setText(条目.原名 or "")
            self.年份框.setValue(int(条目.年份 or 0))
            self.时长框.setValue(max(0, int(条目.时长分钟 or 0)))
            self.评分框.setValue(_夹(条目.评分 or 0.0, 0.0, 评分上限))
            self.票数框.setValue(max(0, int(条目.评分票数 or 0)))
            self.影评人框.setValue(_夹(条目.影评人评分 or 0.0, 0.0, 影评人上限))
            self.状态框.setText(条目.状态 or "")
            self.语言框.setText(条目.原始语言 or "")
            self.标签框.setText(", ".join(条目.标签 or []))
            self.简介框.setPlainText(条目.简介 or "")
            self.国家框.setCurrentIndex(0)
            self.分级值框.clear()
            self.分级表.clear()
            for 一条 in 条目.分级们 or []:
                self.分级表.addItem(f"{一条.国家}:{一条.值}")
            外部 = dict(条目.外部ID or {})
            self.tmdb框.setText(str(外部.get("tmdb", "") or ""))
            self.imdb框.setText(str(外部.get("imdb", "") or ""))
            self.tvdb框.setText(str(外部.get("tvdb", "") or ""))
            self.海报框.设图片(条目.海报)
            self.背景框.设图片(条目.背景)
            self.标志框.设图片(条目.标志)
        finally:
            for 控件, 原 in zip(表格, 原先):
                控件.blockSignals(原)
        self._载入快照 = self._快照(条目)      # 记下"现在等于库里的哪一份"

    # ---------------- 写库 ----------------

    def _点保存(self) -> None:
        """按钮走的这条路：失败时把行内提示**再**点亮一次（用户刚点的，得看见）。"""
        媒体id = self.保存()
        if 媒体id is None:
            self.提示标签.setStyleSheet(f"color: {颜色错}; font-weight: 600;")
            self.提示标签.setText("⚠️ " + (self.校验() or "保存失败，看上面提示"))

    def 保存(self) -> Optional[int]:
        """校验 + 写库；成功返回 媒体id，失败返回 None。

        为什么失败不弹模态框：这个函数会被代码直接调用（脚本/测试），模态框会把调用
        方卡死在事件循环里。失败原因走行内提示 + :attr:`校验失败` 信号。
        """
        原因 = self.校验()
        if 原因:
            self.设置提示("⚠️ " + 原因, "警告")
            self.校验失败.emit(原因)
            return None
        条目 = self.组装条目()
        媒体id = self._写库(条目)
        if 媒体id is None:
            return None
        self._媒体id = 媒体id
        # 存完把"基准条目"换成库里的那一份：之后再改就是**改这一条**，
        # 而不是又插一条（季/集也不会被复制成两份）
        新条目 = self.库.取媒体(媒体id)
        if 新条目 is not None:
            self._原条目 = 新条目
            self._载入快照 = self._快照(新条目)
        提示 = f"✅ 已保存到本地资料库（id={媒体id}）：{条目.一句话()}"
        if self._自动键:
            # 内部编号这件事必须说出来：它在界面上看起来像个"TMDB id"
            提示 += f"｜没填外部 ID，已给这条分配内部编号 {self._自动键}（免得和别的手动记录互相覆盖）"
        self.设置提示(提示, "好")
        self.已保存.emit(媒体id)
        return 媒体id

    def _写库(self, 条目: 媒体条目) -> Optional[int]:
        self._自动键 = ""
        键 = self._可用主键(str(条目.外部ID.get("tmdb", "") or ""))
        条目.外部ID["tmdb"] = 键
        if 键 != str(self.tmdb框.text() or "").strip():
            self.tmdb框.blockSignals(True)
            self.tmdb框.setText(键)
            self.tmdb框.blockSignals(False)
        try:
            if self._媒体id is not None:
                # 库.存条目 按 (来源, tmdb_id) 找行。我们刚把"来源"改成 手动，
                # 不先把本行的键对齐，它就会当成新记录再插一条 ——
                # 结果是一条记录变两条、原 id 还停在旧标题上（季/集被复制）。
                self.库.全部("UPDATE 媒体 SET 来源=?, tmdb_id=? WHERE id=?",
                           (手动来源, 键, self._媒体id))
            媒体id = int(self.库.存条目(条目))
        except Exception as 错:                      # noqa: BLE001
            # 写库失败（锁住/磁盘满）不该让界面崩：说清楚就行，用户改完还能再存一次
            self.设置提示(f"❌ 写库失败：{错}", "错")
            return None
        return 媒体id

    def _可用主键(self, 键: str) -> str:
        """挑一个不会把别人的行覆盖掉的 tmdb 主键。

        库表是 ``UNIQUE(来源, tmdb_id)``，而手动条目常常一个外部 id 都没有（键为空串）。
        两条手动记录共用空键时，``存条目`` 会认为"还是那一条"，后存的把前一条整条盖掉。
        所以空键（或任何已被别人占用的键）先让开，给一个本地内部编号 ——
        来源仍是"手动"，界面上的外部 ID 框也会如实显示这个编号。
        """
        键 = str(键 or "").strip()
        我的 = self._媒体id
        if self._没被别人占(键, 我的):
            return 键
        序号 = 1
        while 序号 <= 9999:
            候选 = f"{内部键前缀}{序号}"
            if self._没被别人占(候选, 我的):
                self._自动键 = 候选          # 让上层提示里说清楚（用户看得见这个编号）
                return 候选
            序号 += 1
        self._自动键 = f"{内部键前缀}{int(time.time())}"
        return self._自动键                  # 兜底：时间戳，几乎不可能再撞

    def _没被别人占(self, 键: str, 我的: Optional[int]) -> bool:
        行 = self.库.一个("SELECT id FROM 媒体 WHERE 来源=? AND tmdb_id=?",
                        (手动来源, 键))
        return 行 is None or (我的 is not None and int(行["id"]) == int(我的))

    # ---------------- 小工具 ----------------

    def _有改动(self, _文本: str = "") -> None:
        if self.提示标签.text().startswith("✅"):
            self.设置提示("有改动没保存（点右下角「保存到资料库」）", "警告")

    def _图片动了(self) -> None:
        self.设置提示("图片已选/已清（点右下角「保存到资料库」才写进库里）", "警告")

    def 设置提示(self, 文本: str, 级别: str = "提示") -> None:
        颜色 = {"好": 颜色好, "警告": 颜色警告, "错": 颜色错}.get(级别, 颜色提示)
        self.提示标签.setStyleSheet(f"color: {颜色};")
        self.提示标签.setText(str(文本 or ""))


class 手动资料对话框(QDialog):
    """弹窗版：内容就是 :class:`手动资料面板` + 一个「关闭」。

    为什么不在这里再放一个「保存」：面板自己有一个，两个"保存"按钮只会让人
    搞不清该信哪个。点面板的保存**不关闭**窗口 —— 改完一条接着改下一条更顺手。
    """

    def __init__(self, 库: 资料库, 图片缓存, 媒体id: int | None = None,
                 父=None) -> None:
        super().__init__(父)
        self.库 = 库
        self._结果: Optional[媒体条目] = None

        self.setWindowTitle("手动填写资料（不联网）" if 媒体id is None
                            else f"修改资料（id={媒体id}）")
        self.resize(760, 640)

        self.面板 = 手动资料面板(库, 图片缓存, 媒体id, self)
        self.面板.已保存.connect(self._记结果)
        布局 = QVBoxLayout(self)
        布局.setContentsMargins(8, 8, 8, 8)
        布局.addWidget(self.面板, 1)
        底 = QHBoxLayout()
        self.结果标签 = QLabel("")
        self.结果标签.setStyleSheet(f"color: {颜色提示};")
        底.addWidget(self.结果标签, 1)
        self.关闭按钮 = QPushButton("关闭")
        self.关闭按钮.clicked.connect(self.reject)
        底.addWidget(self.关闭按钮)
        布局.addLayout(底)

    def _记结果(self, 媒体id: int) -> None:
        self._结果 = self.库.取媒体(int(媒体id))
        if self._结果 is not None:
            self.结果标签.setText(f"已保存：{self._结果.一句话()}")

    def 保存(self) -> Optional[int]:
        """写库并记住结果（返回 媒体id；失败 None）。"""
        媒体id = self.面板.保存()
        if 媒体id is not None:
            self._记结果(媒体id)
        return 媒体id

    def 取条目(self) -> Optional[媒体条目]:
        """用户保存后的结果；**没保存过（取消/直接关掉）返回 None**。"""
        return self._结果

    def 装载(self, 媒体id: int) -> bool:
        return self.面板.装载(媒体id)

    def 取面板(self) -> 手动资料面板:
        return self.面板

    def __getattr__(self, 名字: str):
        """字段访问直接转给面板（少写几十个转发属性）。

        只在正常查找失败时才会走到这里，且只转发**公开名字**：Qt/shiboken 会
        探测各种下划线开头的内部属性，那些必须老老实实报 AttributeError。
        """
        if 名字.startswith("_"):
            raise AttributeError(名字)
        面板 = self.__dict__.get("面板")
        if 面板 is not None and hasattr(面板, 名字):
            return getattr(面板, 名字)
        raise AttributeError(名字)
