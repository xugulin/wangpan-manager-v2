"""海报墙页：影视资料库的浏览界面（工具条 + 继续观看 + 海报网格 + 状态行）。

为什么是 QListView + 自定义 Model/Delegate，而不是"QScrollArea 里摆 QLabel"
=======================================================================
* 资料库动辄几千上万条。往布局里塞几千个 QLabel，**光是创建控件就要几秒**，
  而且每个控件都参与布局计算/样式解析 —— 滚动时必卡；
* ``QListView`` 只给**可见的那几十个** item 调 ``paint()``，慢的部分被结构性地
  排除掉了：滚动开销与总条数无关。所以这里：
  - :class:`海报模型` 只持有"已取回的 Row + 每行的海报状态"，
    ``rowCount()`` 直接报**库里的总数**（还没取回的行显示占位卡，滚动条长度因此
    从一开始就是对的，不会"越滚越长"）；
  - **分页取数**：首屏 :data:`默认页大小` 条，滚到接近底部再取下一页（偏移递增），
    单页上限 :data:`单页上限`；一次查询 = 一条带 LIMIT/OFFSET 的 SQL，
    几千条的库翻页是毫秒级的；
  - :class:`海报代理` 只做"贴图 + 两行字"，尺寸恒定（``setUniformItemSizes(True)``
    让视图不必逐条问 ``sizeHint``，这是 Qt 文档里点名的滚动优化）。

缩略图怎么加载（性能取舍的核心）
================================
* 读盘/解码**必须离开界面线程**：一张 500×750 的 JPEG 解码要 10–30 ms，
  几十张就是肉眼可见的卡顿；所以走 :class:`~wangpan.ui.详情页.图片工作线程`；
* 工人线程只回 ``QImage``（``QPixmap`` 只能在界面线程建），信号排队投递回界面线程
  再转 ``QPixmap`` 并 ``dataChanged`` 触发重绘；
* **同一张图不重复排队**：``_加载中`` 集合去重；读不出来的进 ``_无图``，
  避免每滚一次就重试一遍（失败也重试 = 滚动时反复打盘）；
* 完成的图放 ``dict[int, QPixmap]``：字典在 Python 3.7+ 是有序的，
  用"取出来再塞回去"就得到 LRU；上限 :data:`默认缩略图缓存上限` 张，
  超了淘汰最久没看的。代价是"淘汰后往回滚要重新解码"，换来的是内存封顶
  （200×300 的图约 0.24 MB，2000 张 ≈ 480 MB 是**上限**，不是常驻）；
* 加载尺寸就按卡片设计尺寸取（不做"先取原图再缩"）：TMDB 的 CDN 按尺寸给图，
  列宽自适应时最坏放大 25%（列数是"最多能放几列"，所以不会更糟）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (QAbstractListModel, QModelIndex, QPoint, QRect, QRectF,
                            QSize, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontMetrics, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox,
                               QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListView, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPushButton, QStyle,
                               QStyledItemDelegate, QVBoxLayout, QWidget)

from ..scrape.库 import 资料库, 查询条件
from ..scrape.模型 import 媒体类型
from .详情页 import 图片工作线程, 详情页, 选集对话框

__all__ = ["海报墙页", "海报模型", "海报代理", "海报视图", "继续观看条", "海报行"]

#: 卡片设计尺寸：海报 2:3（200×300）+ 两行文字的 40 px。
#: 再大只是让一屏少放几张（"海报墙"要的是**一屏尽量多的封面**）。
卡片宽 = 200
卡片高 = 340
间距 = 10

默认页大小 = 60
#: 单页上限：一页取太多，第一次查询就要读很多行 + 建很多占位卡，得不偿失
单页上限 = 200
默认缩略图缓存上限 = 2000

#: Model 里额外暴露的角色（Delegate 靠它们拿数据，避免反复查库）
行角色 = Qt.ItemDataRole.UserRole + 1
海报状态角色 = Qt.ItemDataRole.UserRole + 2


class 海报状态:
    """一张海报的加载状态（Delegate 靠它决定占位卡长什么样）。"""

    未加载 = 0
    加载中 = 1
    已加载 = 2
    失败 = 3


#: 无海报时的占位色（按 id 取模）：同一条作品永远是同一个颜色，
#: 滚动时不会"闪色"，也让用户能用颜色区分不同卡片。
占位色 = ("#3b4a63", "#4a3f5c", "#2f4f4a", "#5c4436", "#3d4a35", "#4a3540")


@dataclass(slots=True)
class 海报行:
    """一条列表查询结果的内存副本。

    为什么不直接存 ``sqlite3.Row``：Row 是按需去连接里取列的（还很慢），
    而且它绑在"当时那次查询"上；复制成普通对象后，Model 与库彻底解耦
    （滚动/重绘不会再碰 SQLite）。
    """

    id: int
    标题: str = ""
    原名: str = ""
    年份: int = 0
    评分: float = 0.0
    类型: str = ""
    海报: str = ""
    简介: str = ""
    时长分钟: int = 0
    总季数: int = 0
    总集数: int = 0

    @classmethod
    def 从(cls, 行) -> "海报行":
        """从 :meth:`资料库.列表` 返回的 Row 造一份（只取用得到的列）。"""
        return cls(id=int(行["id"]), 标题=行["标题"] or "", 原名=行["原名"] or "",
                   年份=int(行["年份"] or 0), 评分=float(行["评分"] or 0.0),
                   类型=行["类型"] or "", 海报=行["海报"] or "",
                   简介=行["简介"] or "", 时长分钟=int(行["时长分钟"] or 0),
                   总季数=int(行["总季数"] or 0), 总集数=int(行["总集数"] or 0))

    def 副标题(self) -> str:
        片段 = []
        if self.年份:
            片段.append(str(self.年份))
        if self.评分:
            片段.append(f"★ {self.评分:.1f}")
        if self.类型 == 媒体类型.剧集.value:
            片段.append(f"{self.总季数} 季" if self.总季数 else "剧集")
        elif self.时长分钟:
            片段.append(f"{self.时长分钟} 分钟")
        return "　".join(片段)


class 海报模型(QAbstractListModel):
    """只存"已经取回来的行"，但 ``rowCount()`` 报库里的总数（未取回的是占位卡）。"""

    def __init__(self, 父=None) -> None:
        super().__init__(父)
        self._行们: list[海报行] = []
        self._状态: dict[int, int] = {}
        self._总数 = 0

    # ---------------- Qt 接口 ----------------

    def rowCount(self, 父=QModelIndex()) -> int:      # noqa: N802 - Qt 命名
        return 0 if 父.isValid() else self._总数

    def data(self, 索引, 角色=Qt.ItemDataRole.DisplayRole):   # noqa: N802 - Qt 命名
        if not 索引.isValid():
            return None
        行号 = 索引.row()
        if 角色 == Qt.ItemDataRole.DisplayRole:
            行 = self.行(行号)
            return 行.标题 if 行 is not None else ""
        if 角色 == 行角色:
            return self.行(行号)
        if 角色 == 海报状态角色:
            return self.状态(行号)
        if 角色 == Qt.ItemDataRole.ToolTipRole:
            行 = self.行(行号)
            if 行 is None:
                return "这一条还没取回来（往下滚会自动加载）"
            尾巴 = f"\n{行.简介[:90]}…" if len(行.简介) > 90 else (
                f"\n{行.简介}" if 行.简介 else "")
            return f"{行.标题}（{行.年份 or '年份未知'}）\n{行.副标题()}{尾巴}"
        return None

    def flags(self, 索引):        # noqa: N802 - Qt 命名
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    # ---------------- 给本页用 ----------------

    def 设置总数(self, 总数: int) -> None:
        if int(总数) == self._总数:
            return
        # 总数变了 = 行数变了，让视图整个重来（比逐条 insert 稳，且这种情况很少发生）
        self.beginResetModel()
        self._总数 = max(0, int(总数))
        self.endResetModel()

    def 重新填(self, 行们: list[海报行]) -> None:
        self.beginResetModel()
        self._行们 = list(行们)
        self._状态 = {}
        self.endResetModel()

    def 追加(self, 行们: list[海报行]) -> None:
        if not 行们:
            return
        起 = len(self._行们)
        self.beginInsertRows(QModelIndex(), 起, 起 + len(行们) - 1)
        self._行们.extend(行们)
        self.endInsertRows()

    def 已加载条数(self) -> int:
        return len(self._行们)

    def 行们(self) -> list[海报行]:
        return list(self._行们)

    def 行(self, 行号: int) -> Optional[海报行]:
        if 0 <= 行号 < len(self._行们):
            return self._行们[行号]
        return None

    def 状态(self, 行号: int) -> int:
        return self._状态.get(行号, 海报状态.未加载)

    def 标状态(self, 行号: int, 状态: int) -> None:
        if not (0 <= 行号 < len(self._行们)):
            return
        if self._状态.get(行号) == 状态:
            return                    # 同状态不重复发信号：dataChanged 会让视图重绘
        self._状态[行号] = 状态
        索引 = self.index(行号, 0)
        self.dataChanged.emit(索引, 索引, [海报状态角色])


class 海报代理(QStyledItemDelegate):
    """画一张卡片：海报（2:3）+ 标题 + 副标题；没图时画"纯色 + 首字"占位。

    ``sizeHint`` 是**常量**（不随 index 变），配合 ``setUniformItemSizes(True)``
    让 QListView 不必为每条数据问一次尺寸 —— 几千条时这是实打实的省。
    """

    边距 = 4
    文字高 = 40

    def __init__(self, 取缩略图, 父=None) -> None:
        super().__init__(父)
        self._取缩略图 = 取缩略图
        基 = 父.font() if 父 is not None else QFont()
        self.标题字体 = QFont(基)
        self.标题字体.setBold(True)
        self.副字体 = QFont(基)
        self.副字体.setPointSize(max(7, 基.pointSize() - 1))
        self.首字字体 = QFont(基)
        self.首字字体.setPointSize(max(20, 基.pointSize() + 20))
        self.首字字体.setBold(True)

    def sizeHint(self, 选项=None, 索引=None) -> QSize:      # noqa: N802 - Qt 命名
        return QSize(卡片宽, 卡片高)

    def paint(self, 绘制器: QPainter, 选项, 索引: QModelIndex) -> None:  # noqa: D102
        行 = 索引.data(行角色)
        区域 = 选项.rect.adjusted(self.边距, self.边距, -self.边距, -self.边距)
        if 区域.width() < 24 or 区域.height() < 48:
            return
        选中 = bool(选项.state & QStyle.StateFlag.State_Selected)
        绘制器.save()
        绘制器.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        绘制器.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        路径 = QPainterPath()
        路径.addRoundedRect(QRectF(区域), 8, 8)
        绘制器.fillPath(路径, QColor("#2c3749") if 选中 else QColor("#222834"))
        if 选中:
            绘制器.setPen(QPen(QColor("#5b9dff"), 2))
            绘制器.drawPath(路径)
        绘制器.setClipPath(路径)

        文字区 = QRect(区域.left() + 6, 区域.bottom() - self.文字高 + 4,
                     区域.width() - 12, self.文字高 - 6)
        图区 = QRect(区域.left(), 区域.top(), 区域.width(),
                    max(24, 区域.height() - self.文字高))
        海报框 = self._海报框(图区)
        self._画海报(绘制器, 海报框, 行, 索引)
        self._画文字(绘制器, 文字区, 行)
        绘制器.restore()

    @staticmethod
    def _海报框(图区: QRect) -> QRect:
        """在图区里放一个 2:3 的海报框（放不下就按高度反算）。"""
        高 = min(int(图区.width() * 1.5), 图区.height())
        宽 = max(16, int(高 * 2 / 3))
        return QRect(图区.left() + (图区.width() - 宽) // 2, 图区.top(), 宽, 高)

    def _画海报(self, 绘制器: QPainter, 框: QRect, 行: Optional[海报行],
              索引: QModelIndex) -> None:
        图 = self._取缩略图(行.id) if 行 is not None else None
        if 图 is not None and not 图.isNull():
            绘制器.drawPixmap(框, 图)          # 尺寸就是按这个框加载的：一次直贴
            return
        颜色 = QColor(占位色[(行.id if 行 is not None else 索引.row()) % len(占位色)])
        绘制器.fillRect(框, 颜色)
        if 行 is None:
            提示 = "载入中…"
        elif not 行.海报 or 索引.data(海报状态角色) == 海报状态.失败:
            # 没图（或读失败）也要看出"这里有一条作品"：纯色 + 首字
            提示 = (行.标题 or "?")[:1]
        else:
            提示 = "…"                         # 已排队，等工人线程
        绘制器.setFont(self.首字字体 if len(提示) == 1 else self.副字体)
        绘制器.setPen(QColor(255, 255, 255, 200))
        绘制器.drawText(框, Qt.AlignmentFlag.AlignCenter, 提示)

    def _画文字(self, 绘制器: QPainter, 区: QRect, 行: Optional[海报行]) -> None:
        标题 = 行.标题 if 行 is not None else "载入中…"
        绘制器.setFont(self.标题字体)
        绘制器.setPen(QColor("#e6edf8"))
        标题框 = QRect(区.left(), 区.top(), 区.width(), 20)
        绘制器.drawText(标题框, Qt.AlignmentFlag.AlignLeft |
                    Qt.AlignmentFlag.AlignVCenter,
                    QFontMetrics(self.标题字体).elidedText(
                        标题, Qt.TextElideMode.ElideRight, 标题框.width()))
        绘制器.setFont(self.副字体)
        绘制器.setPen(QColor("#93a3b8"))
        副框 = QRect(区.left(), 区.top() + 20, 区.width(), 18)
        绘制器.drawText(副框, Qt.AlignmentFlag.AlignLeft |
                    Qt.AlignmentFlag.AlignVCenter,
                    QFontMetrics(self.副字体).elidedText(
                        行.副标题() if 行 is not None else "",
                        Qt.TextElideMode.ElideRight, 副框.width()))


class 海报视图(QListView):
    """海报网格。

    为什么要子类：``QListView`` **默认不把回车当"打开"**（Qt 源码里只有
    Ctrl+回车才发 ``activated``），回车会顺着事件链漏到父窗口，触发那边的默认按钮。
    这里自己发一个 :attr:`回车` 信号，行为可预期。
    """

    回车 = Signal(QModelIndex)
    视口变了 = Signal()

    def keyPressEvent(self, 事件) -> None:      # noqa: D102 - QWidget
        if 事件.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and self.currentIndex().isValid():
            self.回车.emit(self.currentIndex())
            事件.accept()
            return
        super().keyPressEvent(事件)

    def resizeEvent(self, 事件) -> None:        # noqa: D102 - QWidget
        super().resizeEvent(事件)
        self.视口变了.emit()


class 继续观看条(QWidget):
    """横向的"继续观看"（最多十几条，有记录才显示）。

    为什么用 QListWidget 而不是"一堆 QPushButton"：条目数量随记录变，
    列表控件改数据不用重建布局；而且它只画文字**不排图** ——
    为了十几条记录再排一轮海报下载不划算（海报墙主体才是要图的地方）。
    """

    要播放 = Signal(str)

    def __init__(self, 父=None) -> None:
        super().__init__(父)
        布局 = QVBoxLayout(self)
        布局.setContentsMargins(4, 2, 4, 2)
        布局.setSpacing(2)
        标题 = QLabel("继续观看")
        字体 = 标题.font()
        字体.setBold(True)
        标题.setFont(字体)
        布局.addWidget(标题)
        self.列表 = QListWidget()
        self.列表.setViewMode(QListView.ViewMode.IconMode)
        self.列表.setFlow(QListView.Flow.LeftToRight)
        self.列表.setWrapping(False)
        self.列表.setMovement(QListView.Movement.Static)
        self.列表.setResizeMode(QListView.ResizeMode.Adjust)
        self.列表.setFixedHeight(84)
        self.列表.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.列表.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.列表.itemClicked.connect(self._点)
        布局.addWidget(self.列表)

    def 填(self, 行们) -> None:
        self.列表.clear()
        for 行 in 行们:
            路径 = 行["路径"]
            位置 = float(行["位置秒"] or 0.0)
            时长 = float(行["时长秒"] or 0.0)
            标题 = 行["标题"] or Path(路径).name
            进度 = f"{_时间文本(位置)} / {_时间文本(时长)}" if 时长 else _时间文本(位置)
            项目 = QListWidgetItem(f"{标题}\n{进度}")
            项目.setData(Qt.ItemDataRole.UserRole, str(路径))
            项目.setToolTip(str(路径))
            项目.setSizeHint(QSize(180, 60))
            self.列表.addItem(项目)

    def 条数(self) -> int:
        return self.列表.count()

    def 路径们(self) -> list[str]:
        return [str(self.列表.item(i).data(Qt.ItemDataRole.UserRole))
                for i in range(self.列表.count())]

    def 播放第(self, 行号: int) -> str:
        """点第几条（给"键盘/测试"用；返回值就是发出去的路径）。"""
        项目 = self.列表.item(int(行号))
        return self._点(项目) if 项目 is not None else ""

    def _点(self, 项目: QListWidgetItem) -> str:
        if 项目 is None:
            return ""
        路径 = str(项目.data(Qt.ItemDataRole.UserRole) or "")
        if 路径:
            self.要播放.emit(路径)
        return 路径


def _时间文本(秒: float) -> str:
    秒 = max(0, int(秒 or 0))
    if 秒 >= 3600:
        return f"{秒 // 3600}:{秒 % 3600 // 60:02d}:{秒 % 60:02d}"
    return f"{秒 // 60:02d}:{秒 % 60:02d}"


class 海报墙页(QWidget):
    """海报墙（工具条 + 继续观看 + 海报网格 + 状态行）。"""

    要播放 = Signal(str)          # 本地文件路径（电影文件 或 剧集某一集的文件）
    要看详情 = Signal(int)        # 媒体id（本页也会自己弹详情，信号是给外部联动用）
    要刮削 = Signal(str)          # 文件/目录路径
    状态变化 = Signal(str)        # 给状态栏用的一句话
    #: 约定的四个信号之外多一个：详情页弹窗的"手动匹配"要转发出去（主窗口接线用）
    要手动匹配 = Signal(int)
    #: 工具条上的「✅ 待确认 N」被点了（主窗口去开待确认队列那一屏）
    要处理待确认 = Signal()
    #: 打开「媒体库文件夹」管理框（可加**多个**文件夹，含网盘内的）
    要管理文件夹 = Signal()

    def __init__(self, 库: 资料库, 图片缓存, 父=None,
                 页大小: int = 默认页大小,
                 缩略图缓存上限: int = 默认缩略图缓存上限) -> None:
        super().__init__(父)
        self.库 = 库
        self.缓存 = 图片缓存
        self.页大小 = max(1, min(单页上限, int(页大小)))
        self.缩略图缓存上限 = max(64, int(缩略图缓存上限))
        self.本页弹详情 = True            # 关掉它 = 只发 要看详情，由外部决定怎么展示

        self.当前条件 = 查询条件(条数=self.页大小)
        self._总数 = 0
        self._行号表: dict[int, int] = {}      # 媒体id -> 当前行号（图回来时定位用）
        self._缩略图: dict[int, QPixmap] = {}  # 有续字典 = 访问序，正好当 LRU 用
        self._加载中: set[int] = set()
        self._无图: set[int] = set()
        self._待图 = 0
        self._图计数 = 0
        self._格宽 = 卡片宽
        self._重排排期中 = False
        self._详情弹窗: Optional[详情页] = None
        self._库统计文本 = ""
        self._缓存统计文本 = ""

        self.模型 = 海报模型(self)
        self.代理 = 海报代理(self.取缩略图, self)
        self._工作 = 图片工作线程(图片缓存, self)     # 懒启动：库里没图就不开线程
        self._工作.图好了.connect(self._图好了)
        self._建界面()
        self.刷新()
        # 退出时兜底停线程：**QThread 还在跑就被析构 = 进程直接 abort**。
        # （正常路径是主窗口调 关闭()；这条只防"整个 app 退出"这一种。）
        应用 = QApplication.instance()
        if 应用 is not None:
            应用.aboutToQuit.connect(self.关闭)

    # ---------------- 界面 ----------------

    def _建界面(self) -> None:
        布局 = QVBoxLayout(self)
        布局.setContentsMargins(8, 8, 8, 6)
        布局.setSpacing(6)
        布局.addLayout(self._建工具条())

        self.继续条 = 继续观看条()
        self.继续条.要播放.connect(self.要播放)
        self.继续条.setVisible(False)
        布局.addWidget(self.继续条)

        # 空状态与网格叠在同一格里：空库时把网格藏起来（"不要显示一堆空白卡片"）
        self.视图 = 海报视图()
        self.视图.setModel(self.模型)
        self.视图.setItemDelegate(self.代理)
        self.视图.setViewMode(QListView.ViewMode.IconMode)
        self.视图.setResizeMode(QListView.ResizeMode.Adjust)
        self.视图.setMovement(QListView.Movement.Static)
        self.视图.setUniformItemSizes(True)       # 尺寸恒定：视图不必逐条问 sizeHint
        self.视图.setSpacing(间距)
        self.视图.setWordWrap(False)
        self.视图.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.视图.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.视图.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.视图.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.视图.doubleClicked.connect(self._双击)
        self.视图.回车.connect(self._回车)
        self.视图.customContextMenuRequested.connect(self._右键菜单)
        self.视图.视口变了.connect(self._视口变了)
        self.视图.verticalScrollBar().valueChanged.connect(self._滚动变化)

        self.空状态标签 = QLabel(
            "还没有刮削过的影片。\n\n"
            "点上面的「📁 扫描媒体库…」选一个目录开始，\n"
            "刮削完成后海报就会出现在这里。")
        self.空状态标签.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.空状态标签.setStyleSheet("color: #7d8794; font-size: 14px;")
        self.空状态标签.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        网格 = QHBoxLayout()
        网格.addWidget(self.视图, 1)
        网格.addWidget(self.空状态标签, 1)
        布局.addLayout(网格, 1)

        self.状态标签 = QLabel("")
        self.状态标签.setStyleSheet("color: #8fa2bb;")
        布局.addWidget(self.状态标签)

        # 滚动/缩放都别立刻干活：40 ms 的短防抖把"连续滚动事件"合成一次，
        # 否则滚动条每动一像素就要算一次可见区间（滚动越快越亏）。
        self._滚动定时 = QTimer(self)
        self._滚动定时.setSingleShot(True)
        self._滚动定时.setInterval(40)
        self._滚动定时.timeout.connect(self.检查滚动)
        self._防抖 = QTimer(self)
        self._防抖.setSingleShot(True)
        self._防抖.setInterval(300)              # 搜索框 300 ms 防抖
        self._防抖.timeout.connect(self.刷新)

    def _建工具条(self) -> QHBoxLayout:
        行 = QHBoxLayout()
        行.setSpacing(6)
        行.addWidget(QLabel("搜索"))
        self.搜索框 = QLineEdit()
        self.搜索框.setPlaceholderText("标题 / 原名（回车立即搜，输入时 300ms 防抖）")
        self.搜索框.setClearButtonEnabled(True)
        self.搜索框.setMinimumWidth(200)
        self.搜索框.textChanged.connect(lambda _t: self._防抖.start())
        self.搜索框.returnPressed.connect(self.刷新)
        行.addWidget(self.搜索框, 2)

        行.addWidget(QLabel("类型"))
        self.类型下拉 = QComboBox()
        self.类型下拉.addItem("全部", None)
        self.类型下拉.addItem("电影", 媒体类型.电影)
        self.类型下拉.addItem("剧集", 媒体类型.剧集)
        self.类型下拉.currentIndexChanged.connect(lambda _i: self.刷新())
        行.addWidget(self.类型下拉)

        行.addWidget(QLabel("排序"))
        self.排序下拉 = QComboBox()
        for 名字 in ("更新时间", "标题", "年份", "评分"):
            self.排序下拉.addItem(名字, 名字)
        self.排序下拉.currentIndexChanged.connect(lambda _i: self.刷新())
        行.addWidget(self.排序下拉)
        self.倒序按钮 = QPushButton("↓ 倒序")
        self.倒序按钮.setCheckable(True)
        self.倒序按钮.setChecked(True)
        self.倒序按钮.toggled.connect(self._换方向)
        行.addWidget(self.倒序按钮)

        行.addWidget(QLabel("评分"))
        self.评分下拉 = QComboBox()
        self.评分下拉.addItem("全部", 0.0)
        for 分 in (6.0, 7.0, 8.0, 9.0):
            self.评分下拉.addItem(f"{分:.0f} 分以上", 分)
        self.评分下拉.currentIndexChanged.connect(lambda _i: self.刷新())
        行.addWidget(self.评分下拉)

        行.addWidget(QLabel("标签"))
        self.标签下拉 = QComboBox()
        self.标签下拉.addItem("全部", "")
        self.标签下拉.currentIndexChanged.connect(lambda _i: self.刷新())
        行.addWidget(self.标签下拉)

        行.addStretch(1)
        self.刷新按钮 = QPushButton("🔄 刷新")
        self.刷新按钮.clicked.connect(self.刷新)
        行.addWidget(self.刷新按钮)
        self.扫描按钮 = QPushButton("📁 扫描媒体库…")
        self.扫描按钮.setToolTip("选**一个**目录立刻扫（临时用；要长期扫多个目录见右边那个按钮）")
        self.扫描按钮.clicked.connect(self.选目录扫描)
        行.addWidget(self.扫描按钮)
        # 用户要求：媒体库要能加**多个**文件夹、还要能加网盘里的文件夹，
        # 所以单目录的「扫描媒体库…」旁边再给一个清单管理入口。
        self.文件夹按钮 = QPushButton("📂 媒体库文件夹…")
        self.文件夹按钮.setToolTip("管理要长期扫描的文件夹：可加多个，本地和网盘都行")
        self.文件夹按钮.clicked.connect(self.要管理文件夹.emit)
        行.addWidget(self.文件夹按钮)
        self.待确认数 = 0
        self.待确认按钮 = QPushButton("✅ 待确认")
        self.待确认按钮.setToolTip("自动匹配拿不准的条目会进这个队列（现在是空的）")
        self.待确认按钮.clicked.connect(self.要处理待确认.emit)
        行.addWidget(self.待确认按钮)
        return 行

    # ---------------- 查询 ----------------

    def 取条件(self, 偏移: int = 0, 条数: Optional[int] = None) -> 查询条件:
        return 查询条件(
            关键词=self.搜索框.text().strip(),
            类型=self.类型下拉.currentData(),
            最低评分=float(self.评分下拉.currentData() or 0.0),
            标签=str(self.标签下拉.currentData() or ""),
            排序=str(self.排序下拉.currentData() or "更新时间"),
            倒序=self.倒序按钮.isChecked(),
            偏移=int(偏移),
            条数=int(条数 or self.页大小))

    def 刷新(self) -> None:
        """重新查第一页（筛选/排序/搜索变了都走这里）。"""
        self._防抖.stop()
        self._刷标签下拉()
        self.当前条件 = self.取条件(偏移=0)
        self._总数 = self.库.计数(self.当前条件)
        self._行号表 = {}
        self._加载中.clear()
        self._无图.clear()
        self.模型.设置总数(self._总数)
        行们 = ([海报行.从(r) for r in self.库.列表(self.当前条件)]
              if self._总数 else [])
        self.模型.重新填(行们)
        for 行号, 行 in enumerate(行们):
            self._行号表[行.id] = 行号
        self._刷继续观看()
        self._刷状态()
        self._刷空状态()
        # 布局要等事件循环转一圈才有效（此时视图还没算过可见区间），
        # 所以首屏图片延后一拍再排，顺带覆盖"页面还没 show"的场景。
        QTimer.singleShot(0, self.请求首屏图片)
        self.状态变化.emit(
            f"资料库：{self._总数} 部作品（已显示前 {self.模型.已加载条数()} 条）"
            if self._总数 else "资料库是空的：点「📁 扫描媒体库…」开始刮削")

    def 加载下一页(self) -> bool:
        """取下一页并追加（返回是否真的取到了）。"""
        已 = self.模型.已加载条数()
        if 已 >= self._总数:
            return False
        条数 = min(self.页大小, self._总数 - 已)
        行们 = [海报行.从(r) for r in
               self.库.列表(replace(self.当前条件, 偏移=已, 条数=条数))]
        if not 行们:
            return False
        self.模型.追加(行们)
        for 序号, 行 in enumerate(行们):
            self._行号表[行.id] = 已 + 序号
        self._刷状态(全量=False)
        return True

    def 检查滚动(self) -> None:
        """滚动条一动就调（防抖后）：快到底就续页，顺带把可见的图排上队。"""
        if self._快要到底() or self._页没填满屏():
            self.加载下一页()
        self.请求可见图片()

    def _快要到底(self) -> bool:
        return self._可见末行() >= self.模型.已加载条数() - max(1, self._每行列数())

    def _页没填满屏(self) -> bool:
        """已取回的卡片有没有把视口填满？

        没填满就得继续取 —— 否则**滚不动也就触发不了"滚到底续页"**：
        一屏能放 60 多张的大屏（4K 竖屏）上，第一页铺不满就成了死局。
        """
        已 = self.模型.已加载条数()
        if 已 == 0 or 已 >= self._总数:
            return False
        矩形 = self.视图.visualRect(self.模型.index(已 - 1, 0))
        return 矩形.isValid() and 矩形.bottom() < self.视图.viewport().height() - 4

    def 请求可见图片(self, 预取屏数: int = 1) -> int:
        """给"看得见的那几十条"排图（上下各多预取一屏，滚过去就是现成的）。"""
        起, 止 = self._可见行区间()
        每行 = self._每行列数()
        起 = max(0, 起 - 每行 * max(0, 预取屏数))
        止 = min(self.模型.已加载条数() - 1, 止 + 每行 * max(0, 预取屏数))
        return sum(1 for 行号 in range(起, 止 + 1) if self._排图(行号))

    def 请求首屏图片(self, 条数: int = 24) -> int:
        """给前若干条排图（页面刚建好/刚刷新时用，此时还没有可见区间）。"""
        return sum(1 for 行号 in range(min(self.模型.已加载条数(), max(1, 条数)))
                   if self._排图(行号))

    def 取缩略图(self, 媒体id: int) -> Optional[QPixmap]:
        """Delegate 从这里取图；取一次就把它挪到字典末尾（LRU 的"最近使用"）。"""
        图 = self._缩略图.get(int(媒体id))
        if 图 is None:
            return None
        self._缩略图.pop(int(媒体id))
        self._缩略图[int(媒体id)] = 图
        return 图

    def 已加载图数(self) -> int:
        return len(self._缩略图)

    def 状态行文本(self) -> str:
        return self.状态标签.text()

    def 空状态可见(self) -> bool:
        return self.空状态标签.isVisibleTo(self)

    def 选中的行(self) -> Optional[海报行]:
        索引 = self.视图.currentIndex()
        return 索引.data(行角色) if 索引.isValid() else None

    def 选中媒体id(self) -> int:
        行 = self.选中的行()
        return int(行.id) if 行 is not None else 0

    def 可见条数(self) -> int:
        """当前实际取回的对象数（界面上是"真卡片"，其余是占位卡）。"""
        return self.模型.已加载条数()

    def 等待图片(self, 超时毫秒: int = 3000, 至少几张: int = 1) -> int:
        """转事件循环等缩略图就位，返回已加载张数（截图/导出/测试用）。

        没有它，"等图加载完"就只能 sleep 猜时间，测试会变成偶尔绿。
        """
        应用 = QApplication.instance()
        截止 = time.monotonic() + max(0, int(超时毫秒)) / 1000.0
        while 应用 is not None and self.已加载图数() < 至少几张 \
                and time.monotonic() < 截止:
            应用.processEvents()
            time.sleep(0.004)
        return self.已加载图数()

    # ---------------- 交互 ----------------

    def 选目录扫描(self) -> None:
        """工具条按钮：选一个目录，把路径交给刮削流程。"""
        目录 = QFileDialog.getExistingDirectory(self, "选要扫描的媒体目录",
                                            str(Path.home()))
        if 目录:
            self.请扫描(目录)

    def 请扫描(self, 路径: str) -> None:
        """把"要扫描的目录/文件"发出去（本页不做刮削，那是 scrape 模块的活）。"""
        self.状态变化.emit(f"📁 已请求扫描：{路径}")
        self.要刮削.emit(str(路径))

    def 主文件路径(self, 媒体id: int) -> str:
        """这部作品该播哪个文件（电影=主文件；剧集=第一集有文件的）。"""
        条目 = self.库.取媒体(int(媒体id))
        if 条目 is None:
            return ""
        if 条目.文件路径 and Path(条目.文件路径).is_file():
            return str(条目.文件路径)
        for 行 in self.库.文件们(int(媒体id)):
            return str(行["路径"])
        for 季对象 in 条目.季们:
            for 集对象 in 季对象.集们:
                if 集对象.文件路径:
                    return str(集对象.文件路径)
        return str(条目.文件路径 or "")

    def 小节路径们(self, 媒体id: int) -> list[tuple[str, str]]:
        """剧集里"有本地文件"的集：(集编号, 路径)。给主窗口做播放列表用。"""
        条目 = self.库.取媒体(int(媒体id))
        if 条目 is None:
            return []
        return [(集对象.编号, str(集对象.文件路径))
                for 季对象 in 条目.季们 for 集对象 in 季对象.集们
                if 集对象.文件路径]

    def 播放媒体(self, 媒体id: int) -> str:
        """播放：电影直接播主文件；剧集只有一集就直接播，多集弹选集对话框。"""
        条目 = self.库.取媒体(int(媒体id))
        if 条目 is None:
            self.状态变化.emit(f"库里没有 id={媒体id} 的作品")
            return ""
        if 条目.是剧集:
            有文件 = self.小节路径们(媒体id)
            if not 有文件:
                路径 = self.主文件路径(媒体id)
                if 路径:
                    self.要播放.emit(路径)
                    return 路径
                self.状态变化.emit("这部剧还没有本地文件")
                return ""
            if len(有文件) == 1:
                self.状态变化.emit(f"▶ {有文件[0][0]}")
                self.要播放.emit(有文件[0][1])
                return 有文件[0][1]
            对话框 = 选集对话框(条目, self)
            if 对话框.exec() and 对话框.选中路径:
                self.要播放.emit(对话框.选中路径)
                return 对话框.选中路径
            return ""
        路径 = self.主文件路径(媒体id)
        if not 路径:
            self.状态变化.emit("这部作品没有本地文件")
            return ""
        self.状态变化.emit(f"▶ {Path(路径).name}")
        self.要播放.emit(路径)
        return 路径

    def 重新刮削(self, 媒体id: int) -> bool:
        路径 = self.主文件路径(媒体id)
        if not 路径:
            self.状态变化.emit("找不到本地文件，没法重新刮削")
            return False
        self.状态变化.emit(f"🔄 重新刮削：{Path(路径).name}")
        self.要刮削.emit(路径)
        return True

    def 打开所在目录(self, 媒体id: int) -> bool:
        """在系统文件管理器里打开文件所在目录。

        用 ``QDesktopServices.openUrl(目录)`` 而不是"打开并选中该文件"：
        后者要靠各平台文件管理器的私有开关（Windows 的 ``explorer /select``），
        在非 Explorer 环境下会被当成路径参数报错 —— 打开目录是处处都对的。
        """
        路径 = self.主文件路径(媒体id)
        if not 路径:
            self.状态变化.emit("这部作品没有本地文件，没地方可打开")
            return False
        目录 = Path(路径).parent
        if not 目录.is_dir():
            self.状态变化.emit(f"目录不在了：{目录}")
            return False
        好 = QDesktopServices.openUrl(QUrl.fromLocalFile(str(目录)))
        self.状态变化.emit(("📂 已打开 " if 好 else "打不开 ") + str(目录))
        return bool(好)

    def 删除媒体(self, 媒体id: int, 要确认: bool = True) -> bool:
        """从库里删（**只删资料，不动硬盘上的文件**）。

        删除是不可逆的，所以默认要弹一次确认；``要确认=False`` 是给
        "已经确认过了"的调用方（含测试）用的。
        """
        条目 = self.库.取媒体(int(媒体id))
        if 条目 is None:
            return False
        if 要确认:
            答案 = QMessageBox.question(
                self, "从库中删除",
                f"把《{条目.标题 or '未命名'}》从资料库里删掉？\n"
                f"（只删资料，硬盘上的文件不动）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if 答案 != QMessageBox.StandardButton.Yes:
                return False
        好 = bool(self.库.删除媒体(int(媒体id)))
        self._缩略图.pop(int(媒体id), None)
        self.状态变化.emit(f"🗑 已从库中删除《{条目.标题 or '未命名'}》" if 好
                      else "删除失败")
        self.刷新()
        return 好

    def 弹详情(self, 媒体id: int) -> bool:
        """在本页弹一个详情窗口（外部联动仍然走 :attr:`要看详情` 信号）。"""
        if not self.本页弹详情:
            return False
        self._关详情()
        窗 = 详情页(self.库, self.缓存, self)
        窗.setWindowFlag(Qt.WindowType.Window, True)
        窗.setWindowTitle("作品详情")
        窗.resize(980, 760)
        窗.要播放.connect(self.要播放)
        窗.要刮削.connect(lambda i: self.重新刮削(i))     # 详情页给的是 id，本页转成路径
        窗.要手动匹配.connect(self.要手动匹配)
        if not 窗.显示媒体(int(媒体id)):
            窗.close()
            self.状态变化.emit(f"详情打不开：库里没有 id={媒体id}")
            return False
        窗.show()
        self._详情弹窗 = 窗
        return True

    def 弹出中的详情(self) -> Optional[详情页]:
        return self._详情弹窗

    def _关详情(self) -> None:
        窗, self._详情弹窗 = self._详情弹窗, None
        if 窗 is not None:
            try:
                窗.close()
            except RuntimeError:      # C++ 侧已经没了（被父窗口带走）
                pass

    def 关闭(self) -> None:
        """停线程、关弹窗。**QThread 还在跑就被析构 = 进程直接崩**。"""
        self._滚动定时.stop()
        self._防抖.stop()
        self._关详情()
        try:
            self._工作.停()
            self._工作.wait(3000)
        except Exception:       # noqa: BLE001 - 收尾失败不该再抛
            pass

    def closeEvent(self, 事件) -> None:      # noqa: D102 - QWidget
        self.关闭()
        super().closeEvent(事件)

    def resizeEvent(self, 事件) -> None:     # noqa: D102 - QWidget
        super().resizeEvent(事件)
        self.重排()

    def showEvent(self, 事件) -> None:       # noqa: D102 - QWidget
        super().showEvent(事件)
        self.重排()
        QTimer.singleShot(0, self.请求首屏图片)
        QTimer.singleShot(0, self.检查滚动)

    # ---------------- 槽 ----------------

    def _换方向(self, 倒序: bool) -> None:
        self.倒序按钮.setText("↓ 倒序" if 倒序 else "↑ 正序")
        self.刷新()

    def _双击(self, 索引: QModelIndex) -> None:
        行 = 索引.data(行角色) if 索引.isValid() else None
        if 行 is None:
            return
        self.要看详情.emit(int(行.id))
        self.弹详情(int(行.id))

    def _回车(self, 索引: QModelIndex) -> None:
        self._双击(索引)

    def _滚动变化(self, _值: int) -> None:
        self._滚动定时.start()

    def _视口变了(self) -> None:
        """视口尺寸变了（含滚动条出现/消失）→ 重算网格。

        延后一拍再做：直接在 resizeEvent 里改 gridSize 会触发又一次布局，
        虽然这里靠"值没变就不设"能收敛，但延后一拍更稳（也少一次重排）。
        """
        if self._重排排期中:
            return
        self._重排排期中 = True
        QTimer.singleShot(0, self._重排落定)

    def _重排落定(self) -> None:
        self._重排排期中 = False
        self.重排()
        self.检查滚动()

    def _右键菜单(self, 位置: QPoint) -> None:
        索引 = self.视图.indexAt(位置)
        行 = 索引.data(行角色) if 索引.isValid() else None
        if 行 is None:
            return
        self.视图.setCurrentIndex(索引)
        菜单 = QMenu(self)
        播放项 = 菜单.addAction("▶ 播放")
        详情项 = 菜单.addAction("ℹ 详情")
        菜单.addSeparator()
        刮削项 = 菜单.addAction("🔄 重新刮削")
        打开项 = 菜单.addAction("📂 在文件管理器中打开")
        菜单.addSeparator()
        删除项 = 菜单.addAction("🗑 从库中删除")
        选中 = 菜单.exec(self.视图.viewport().mapToGlobal(位置))
        if 选中 is None:
            return
        if 选中 is 播放项:
            self.播放媒体(行.id)
        elif 选中 is 详情项:
            self.弹详情(行.id)
        elif 选中 is 刮削项:
            self.重新刮削(行.id)
        elif 选中 is 打开项:
            self.打开所在目录(行.id)
        elif 选中 is 删除项:
            self.删除媒体(行.id)

    # ---------------- 私有：布局与图 ----------------

    def 重排(self) -> None:
        """按视口宽度算网格：列数随窗口变，列宽均分（不留一条空边）。

        列数取"最多能放几列"，所以卡片最多被拉宽到接近"再加一列就放不下"的位置，
        相对设计尺寸的放大不超过约 25%（加载尺寸就按设计尺寸取，够用）。
        """
        视口宽 = max(120, self.视图.viewport().width())
        列数 = max(1, (视口宽 - 间距) // (卡片宽 + 间距))
        格宽 = max(卡片宽, (视口宽 - 间距 * (列数 + 1)) // 列数)
        新 = QSize(int(格宽), 卡片高 + 间距)
        if self.视图.gridSize() != 新:
            self.视图.setGridSize(新)
        self._格宽 = int(格宽)

    def _每行列数(self) -> int:
        宽 = max(1, self.视图.viewport().width())
        return max(1, 宽 // max(1, self._格宽))

    def _可见行区间(self) -> tuple[int, int]:
        """当前可见的首/末行号。

        用少量探针点问 ``indexAt``（O(1)），而不是遍历所有行的 ``visualRect``：
        已加载的行可能上千条，而滚动事件是**高频**的（每次滚动条变化都问一遍），
        遍历会让滚动变成 O(已加载条数)。
        """
        视口 = self.视图.viewport().rect()
        if 视口.width() <= 4 or 视口.height() <= 4:
            return (0, 0)
        起 = -1
        止 = -1
        for 点 in ((2, 2), (视口.width() - 3, 2), (2, 视口.height() - 3),
                  (视口.width() // 2, 视口.height() - 3),
                  (视口.width() - 3, 视口.height() - 3)):
            索引 = self.视图.indexAt(QPoint(点[0], 点[1]))
            if not 索引.isValid():
                continue
            行号 = int(索引.row())
            起 = 行号 if 起 < 0 else min(起, 行号)
            止 = max(止, 行号)
        if 起 < 0:
            return (0, 0)
        return (起, 止)

    def _可见末行(self) -> int:
        return self._可见行区间()[1]

    def _取图尺寸(self) -> tuple[int, int]:
        """工人线程就按这个尺寸解码：比例 > 1 的高分屏也够清楚。"""
        比例 = float(self.视图.devicePixelRatioF() or 1.0)
        return (max(32, int(卡片宽 * 比例)), max(48, int(卡片宽 * 1.5 * 比例)))

    def _排图(self, 行号: int) -> bool:
        """给一行排一张缩略图；返回是否真的排了（已缓存/在排队/没图都不排）。"""
        行 = self.模型.行(行号)
        if 行 is None:
            return False
        if not 行.海报:
            self.模型.标状态(行号, 海报状态.失败)   # 没图也是"确定的结果"，画首字占位
            return False
        if 行.id in self._缩略图 or 行.id in self._加载中 or 行.id in self._无图:
            return False
        self._加载中.add(行.id)                     # 去重：同一张不重复排队
        self.模型.标状态(行号, 海报状态.加载中)
        self._待图 += 1
        self._工作.开()
        self._工作.排(行.id, 行.海报, "", self._取图尺寸(), "w500")
        return True

    def _存缩略图(self, 媒体id: int, 图: QPixmap) -> None:
        self._缩略图.pop(媒体id, None)
        self._缩略图[媒体id] = 图
        while len(self._缩略图) > self.缩略图缓存上限:
            最旧 = next(iter(self._缩略图))
            self._缩略图.pop(最旧, None)

    def _图好了(self, 媒体id, 图) -> None:
        """槽在界面线程跑（跨线程信号默认排队投递）：QPixmap 只能在这里建。"""
        媒体id = int(媒体id)
        self._加载中.discard(媒体id)
        self._待图 = max(0, self._待图 - 1)
        行号 = self._行号表.get(媒体id, -1)
        if 图 is None or 图.isNull():
            # 记进"没图"：不然每滚过一次就重排一次，白读盘
            self._无图.add(媒体id)
            self.模型.标状态(行号, 海报状态.失败)
            return
        self._存缩略图(媒体id, QPixmap.fromImage(图))
        self.模型.标状态(行号, 海报状态.已加载)
        self._图计数 += 1
        if self._图计数 % 20 == 0:
            self._刷状态(全量=False)      # 缓存统计要扫缓存目录，不能每张图都算

    # ---------------- 私有：状态行 ----------------

    def _刷标签下拉(self) -> None:
        """标签下拉从库里**实际出现过的标签**动态填（否则会列一堆空标签）。"""
        现在 = str(self.标签下拉.currentData() or "")
        名称们 = [行["名称"] for 行 in
                self.库.全部("SELECT DISTINCT 名称 FROM 标签 ORDER BY 名称")]
        self.标签下拉.blockSignals(True)     # 重建会触发 currentIndexChanged → 递归刷新
        self.标签下拉.clear()
        self.标签下拉.addItem("全部", "")
        for 名字 in 名称们:
            self.标签下拉.addItem(名字, 名字)
        位置 = self.标签下拉.findData(现在)
        self.标签下拉.setCurrentIndex(位置 if 位置 >= 0 else 0)
        self.标签下拉.blockSignals(False)

    def _刷继续观看(self) -> None:
        行们 = self.库.继续观看(12)
        self.继续条.填(行们)
        self.继续条.setVisible(bool(行们))    # 有数据才显示（空着一条很碍眼）

    def _刷空状态(self) -> None:
        空 = self._总数 == 0
        self.视图.setVisible(not 空)          # 空库不显示一堆空白卡片
        self.空状态标签.setVisible(空)

    def _刷状态(self, 全量: bool = True) -> None:
        """底部状态行。

        库统计是十几条 COUNT，缓存统计要扫一遍缓存目录 —— 只在"刷新"时算
        （``全量=True``），翻页只改"已显示"那一段（``全量=False``）。
        """
        if 全量:
            self._库统计文本 = self.库.统计().摘要()
            self._缓存统计文本 = self.缓存.统计一下().摘要()
            self._刷待确认按钮()
        筛选 = f"筛选 {self._总数} 部 · 已显示 {self.模型.已加载条数()}"
        self.状态标签.setText("　｜　".join(
            x for x in (self._库统计文本, 筛选, self._缓存统计文本) if x))

    def _刷待确认按钮(self) -> None:
        """工具条上的「✅ 待确认 N」：数字就是队列长度（点开就是那一屏）。"""
        数 = 0
        try:
            数 = int(self.库.待确认数())
        except Exception:  # noqa: BLE001 - 老库/异常都不该让海报墙打不开
            数 = 0
        self.待确认数 = 数
        self.待确认按钮.setText(f"✅ 待确认 {数}" if 数 else "✅ 待确认")
        self.待确认按钮.setEnabled(True)
        self.待确认按钮.setToolTip(
            f"有 {数} 条刮削拿不准，等人点一下（选候选 → 采纳 → 入库）"
            if 数 else "自动匹配拿不准的条目会进这个队列（现在是空的）")
