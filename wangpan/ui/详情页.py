"""详情页：一部作品的全貌（背景 + 海报 + 徽章 + 标签 + 简介 + 演职员 + 季集）。

为什么长这样
============
* **一个页面同时服务电影与剧集**：两者的差别只有"集列表是不是空的"，
  拆成两个页面会让徽章/标签/简介/演职员这些**完全一样**的部分写两遍；
* **图片一律走工作线程**：详情页首屏要读背景 + 海报 + 十几个头像，
  同步读在最坏情况下会把界面**冻住好几秒**；而 ``QLabel.setPixmap`` 又只能在
  界面线程调，所以走"排队 → 线程读/下 → 信号回界面线程再上屏"这条路；
* **背景自己画**（:class:`背景画布`）：没有背景图时退化成纵向渐变而不是留白，
  并且不管有没有图都压一层蒙层 —— 深色蒙层保证白字在任何海报上都读得清；
* **正文整体放在滚动区里**：窗口矮的时候靠滚动，而不是把集列表挤成一条缝
  （"布局被内容撑破"是这类详情页最常见的毛病）。简介本身再套一层小滚动区，
  这样超长简介不会把演职员/剧集顶到看不见的地方去。

线程安全约定
============
工人线程（:class:`图片工作线程`）只碰 :class:`~wangpan.scrape.图片.图片缓存`、
``str`` 与 ``Path``，**绝不碰任何 QWidget**；它把 ``QImage`` 通过信号送回界面线程
（``QImage`` 可以跨线程，``QPixmap`` 不行 —— 这是 Qt 的硬规定）。
"""

from __future__ import annotations

import queue
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QColor, QFontMetrics, QImage, QLinearGradient, QPainter,
                           QPixmap)
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFrame,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..scrape.库 import 资料库
from ..scrape.模型 import 人物工种, 媒体条目, 媒体类型

__all__ = ["详情页", "图片工作线程", "背景画布"]

#: 各图框的目标像素：**读图时就缩到这个尺寸**（而不是原图进内存再缩），
#: 一张 1280 宽背景图 4 MB、缩到 420 高只有 0.7 MB，滚动/切作品时省的是内存带宽。
海报框 = (200, 300)
背景框 = (1100, 420)
头像框 = (96, 96)

#: 标签最多显示几个（超出的折成 "+n"）：标签一般 3–8 个，
#: 为"极端情况"写一个自定义流式布局不划算，收起 + 悬停看全部更省事。
标签上限 = 10


class 图片工作线程(QThread):
    """一个后台读图/下图线程：队列进、信号出。

    为什么是"一个 QThread + 队列"，不是 ``QThreadPool`` + ``QRunnable``：
    * 每个页面**只有一个消费者**，队列顺序就是上屏顺序（先看到的先加载），
      线程池会按 CPU 核数并发，快速滚动时反而互相抢盘；
    * 生命周期简单：往队列里放一个 ``None`` 就能干净退出，
      不至于出现"窗口关了线程还在跑"（那在 Qt 里会直接崩）。
    """

    图好了 = Signal(object, object)      # (键, QImage|None)

    def __init__(self, 缓存, 父=None) -> None:
        super().__init__(父)
        self._缓存 = 缓存
        self._队列: "queue.Queue" = queue.Queue()
        self._停 = False

    # ---------------- 给界面线程用 ----------------

    def 排(self, 键, 本地路径, 远端路径: str, 目标: tuple[int, int],
           尺寸档: str) -> None:
        """排一张图。``本地路径`` 优先；没有就按 ``远端路径`` 走图片缓存下载。"""
        self._队列.put_nowait((键, str(本地路径 or ""), str(远端路径 or ""),
                             tuple(目标), str(尺寸档)))

    def 开(self) -> None:
        """确保线程在跑（用于"关掉之后又被用上"：页面隐藏 ≠ 销毁）。"""
        self._停 = False
        if not self.isRunning():
            self.start()

    def 停(self) -> None:
        self._停 = True
        self._队列.put_nowait(None)

    # ---------------- 工人线程 ----------------

    def run(self) -> None:      # noqa: D102 - QThread 的入口
        while not self._停:
            任务 = self._队列.get()
            if 任务 is None:
                break
            键, 本地, 远端, 目标, 尺寸档 = 任务
            try:
                图 = self._取图(本地, 远端, 目标, 尺寸档)
            except Exception:       # noqa: BLE001 - 读图/下图的任何异常都不该打死线程
                图 = None
            self.图好了.emit(键, 图)

    def _取图(self, 本地: str, 远端: str, 目标: tuple[int, int],
             尺寸档: str) -> Optional[QImage]:
        if 本地:
            图 = self._缓存.读图(本地, 目标)
            if 图 is not None and not 图.isNull():
                return 图
        if not 远端:
            return None
        落点 = self._缓存.确保(远端, 尺寸档)
        if 落点 is None:
            return None
        return self._缓存.读图(落点, 目标)


class 背景画布(QWidget):
    """顶部背景：有图就铺满裁切，没图就画渐变；上面永远压一层蒙层。

    预先把图缩到控件尺寸再存着（而不是每次 ``paintEvent`` 都缩）：
    背景在拖动/缩放窗口时会重绘几十次，每次缩一张 1100×420 的图是**纯浪费**。
    """

    def __init__(self, 父=None) -> None:
        super().__init__(父)
        self._原图: Optional[QPixmap] = None
        self._铺满: Optional[QPixmap] = None
        self.setMinimumHeight(206)

    def 设置图(self, 图: Optional[QImage]) -> None:
        self._原图 = (QPixmap.fromImage(图)
                   if (图 is not None and not 图.isNull()) else None)
        self._重算()

    def 有图(self) -> bool:
        return self._原图 is not None

    def resizeEvent(self, 事件) -> None:      # noqa: D102 - QWidget
        super().resizeEvent(事件)
        self._重算()

    def _重算(self) -> None:
        if self._原图 is None or self.width() <= 0 or self.height() <= 0:
            self._铺满 = None
        else:
            self._铺满 = self._原图.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
        self.update()

    def paintEvent(self, 事件) -> None:      # noqa: D102 - QWidget
        绘制器 = QPainter(self)
        矩形 = self.rect()
        if self._铺满 is not None:
            # 居中裁切：背景图比例和控件对不上时，宁可裁掉两边也不要黑边
            绘制器.drawPixmap((矩形.width() - self._铺满.width()) // 2,
                          (矩形.height() - self._铺满.height()) // 2, self._铺满)
        else:
            渐变 = QLinearGradient(0, 0, 0, 矩形.height())
            渐变.setColorAt(0.0, QColor("#33405a"))
            渐变.setColorAt(1.0, QColor("#12161d"))
            绘制器.fillRect(矩形, 渐变)
        蒙层 = QLinearGradient(0, 0, 0, 矩形.height())
        蒙层.setColorAt(0.0, QColor(0, 0, 0, 80))
        蒙层.setColorAt(1.0, QColor(0, 0, 0, 205))
        绘制器.fillRect(矩形, 蒙层)


def 小节标题(文本: str) -> QLabel:
    """一个统一样式的小节标题（简介/演职员/剧集）。"""
    标签 = QLabel(文本)
    字体 = 标签.font()
    字体.setBold(True)
    标签.setFont(字体)
    标签.setStyleSheet("color: #9fc4ff;")
    return 标签


def 小节(文本: str) -> QWidget:
    """小节标题 + 一条分隔线（视觉上把"一块一块"分开，省得用户找不着北）。"""
    块 = QWidget()
    布局 = QVBoxLayout(块)
    布局.setContentsMargins(0, 6, 0, 2)
    布局.setSpacing(2)
    布局.addWidget(小节标题(文本))
    线 = QFrame()
    线.setFrameShape(QFrame.Shape.HLine)
    线.setStyleSheet("color: #2c3444;")
    布局.addWidget(线)
    return 块


class 详情页(QWidget):
    """影视详情页：可以单独当窗口弹出来，也能被嵌进主窗口的标签页。"""

    要播放 = Signal(str)          # 参数：本地文件路径（电影主文件 或 选中集的文件）
    要刮削 = Signal(int)          # 参数：媒体id（重新刮削）
    要手动匹配 = Signal(int)      # 参数：媒体id（人工指定 TMDB 条目）

    def __init__(self, 库: 资料库, 图片缓存, 父=None) -> None:
        super().__init__(父)
        self.库 = 库
        self.缓存 = 图片缓存
        self.条目: Optional[媒体条目] = None
        self.媒体id = 0
        self._待图 = 0                       # 还在路上的图（等待/测试用）
        self._头像标签: dict[str, QLabel] = {}
        self._标签们: list[QLabel] = []
        self._演员卡片们: list[QWidget] = []
        self._工作 = 图片工作线程(图片缓存, self)   # 线程懒启动：没人看图就不占线程
        self._工作.图好了.connect(self._图好了)
        self._建界面()
        # 退出时兜底停线程：**QThread 还在跑就被析构 = 进程直接 abort**。
        # （正常路径是父窗口调 关闭()/closeEvent；这条只防"整个 app 退出"。）
        应用 = QApplication.instance()
        if 应用 is not None:
            应用.aboutToQuit.connect(self.关闭)

    # ---------------- 界面 ----------------

    def _建界面(self) -> None:
        外层 = QVBoxLayout(self)
        外层.setContentsMargins(0, 0, 0, 0)
        外层.setSpacing(0)

        # 顶部：背景画布上叠海报 + 信息列
        self.顶部 = 背景画布()
        顶布局 = QHBoxLayout(self.顶部)
        顶布局.setContentsMargins(16, 14, 16, 14)
        顶布局.setSpacing(16)
        self.海报标签 = QLabel("海报")
        self.海报标签.setFixedSize(150, 225)
        self.海报标签.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.海报标签.setStyleSheet(
            "background: rgba(255,255,255,0.10); border-radius: 8px; color: #ccd6e6;")
        顶布局.addWidget(self.海报标签, 0, Qt.AlignmentFlag.AlignTop)

        信息列 = QVBoxLayout()
        信息列.setSpacing(4)
        self.标题标签 = QLabel("—")
        字体 = self.标题标签.font()
        字体.setPointSize(max(16, 字体.pointSize() + 10))
        字体.setBold(True)
        self.标题标签.setFont(字体)
        self.标题标签.setStyleSheet("color: #ffffff;")
        self.标题标签.setWordWrap(True)
        信息列.addWidget(self.标题标签)
        self.原名标签 = QLabel("")
        self.原名标签.setStyleSheet("color: #b9c4d4;")
        信息列.addWidget(self.原名标签)
        self.信息标签 = QLabel("")
        self.信息标签.setStyleSheet("color: #d7e0ee;")
        信息列.addWidget(self.信息标签)

        徽章行 = QHBoxLayout()
        徽章行.setSpacing(8)
        self.评分标签 = self._徽章("★ —", "#f3c34a")
        self.影评人标签 = self._徽章("影评人 —", "#7fd0a8")
        self.分级标签 = self._徽章("分级 —", "#9fb6ff")
        self.语言标签 = self._徽章("语言 —", "#c8b3ff")
        self.状态标签 = self._徽章("状态 —", "#ffb27f")
        for 徽 in (self.评分标签, self.影评人标签, self.分级标签, self.语言标签,
                   self.状态标签):
            徽章行.addWidget(徽)
        徽章行.addStretch(1)
        信息列.addLayout(徽章行)

        self.标签条 = QWidget()
        self.标签布局 = QHBoxLayout(self.标签条)
        self.标签布局.setContentsMargins(0, 0, 0, 0)
        self.标签布局.setSpacing(6)
        self.标签布局.addStretch(1)      # 末尾留伸缩：不然几个胶囊会被拉成通栏长条
        信息列.addWidget(self.标签条)
        信息列.addStretch(1)
        顶布局.addLayout(信息列, 1)
        外层.addWidget(self.顶部)

        # 正文：放进滚动区，窗口矮的时候滚动而不是挤扁（集列表永远看得见）
        滚动 = QScrollArea()
        滚动.setWidgetResizable(True)
        滚动.setFrameShape(QFrame.Shape.NoFrame)
        正文 = QWidget()
        内容 = QVBoxLayout(正文)
        内容.setContentsMargins(14, 6, 14, 12)
        内容.setSpacing(6)
        滚动.setWidget(正文)
        外层.addWidget(滚动, 1)

        内容.addWidget(小节("简介"))
        self.简介框 = QScrollArea()
        self.简介框.setWidgetResizable(True)
        self.简介框.setFrameShape(QFrame.Shape.NoFrame)
        self.简介框.setMaximumHeight(112)     # 别撑破布局：超长简介在这里自己滚
        self.简介框.setMinimumHeight(30)
        self.简介标签 = QLabel("（暂无简介）")
        self.简介标签.setWordWrap(True)
        self.简介标签.setAlignment(Qt.AlignmentFlag.AlignTop |
                              Qt.AlignmentFlag.AlignLeft)
        self.简介标签.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.简介框.setWidget(self.简介标签)
        内容.addWidget(self.简介框)

        内容.addWidget(小节("演职员"))
        self.导演标签 = QLabel("导演：—")
        self.编剧标签 = QLabel("编剧：—")
        for 标签 in (self.导演标签, self.编剧标签):
            标签.setWordWrap(True)
        内容.addWidget(self.导演标签)
        内容.addWidget(self.编剧标签)
        self.演员框 = QScrollArea()
        self.演员框.setWidgetResizable(True)
        self.演员框.setFrameShape(QFrame.Shape.NoFrame)
        self.演员框.setFixedHeight(136)       # 头像 64 + 名字 + 角色
        self.演员框.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.演员行 = QWidget()
        self.演员布局 = QHBoxLayout(self.演员行)
        self.演员布局.setContentsMargins(0, 0, 0, 0)
        self.演员布局.setSpacing(10)
        self.演员布局.addStretch(1)
        self.演员框.setWidget(self.演员行)
        内容.addWidget(self.演员框)

        self.剧集区 = QWidget()
        剧布局 = QVBoxLayout(self.剧集区)
        剧布局.setContentsMargins(0, 0, 0, 0)
        剧布局.setSpacing(4)
        剧头 = QHBoxLayout()
        剧头.addWidget(小节标题("剧集"))
        剧头.addWidget(QLabel("季"))
        self.季下拉 = QComboBox()
        self.季下拉.currentIndexChanged.connect(lambda _i: self.填集列表())
        剧头.addWidget(self.季下拉)
        剧头.addStretch(1)
        剧布局.addLayout(剧头)
        self.集列表 = QListWidget()
        self.集列表.setMinimumHeight(150)
        self.集列表.itemClicked.connect(self._点集)       # 单击即播：只连单击，
                                                        # 连双击会重复发一次信号
        剧布局.addWidget(self.集列表)
        self.集提示标签 = QLabel("")
        self.集提示标签.setStyleSheet("color: #8fa2bb;")
        剧布局.addWidget(self.集提示标签)
        内容.addWidget(self.剧集区)

        按钮行 = QHBoxLayout()
        self.播放按钮 = QPushButton("▶ 播放")
        self.播放按钮.clicked.connect(self.播放)
        self.刮削按钮 = QPushButton("🔄 重新刮削")
        self.刮削按钮.clicked.connect(
            lambda: self.要刮削.emit(int(self.媒体id)))
        self.匹配按钮 = QPushButton("🔎 手动匹配")
        self.匹配按钮.clicked.connect(
            lambda: self.要手动匹配.emit(int(self.媒体id)))
        self.关闭按钮 = QPushButton("关闭")
        self.关闭按钮.clicked.connect(self._关闭点击)
        for 按钮 in (self.播放按钮, self.刮削按钮, self.匹配按钮):
            按钮行.addWidget(按钮)
        按钮行.addStretch(1)
        按钮行.addWidget(self.关闭按钮)
        内容.addLayout(按钮行)
        内容.addStretch(1)

    @staticmethod
    def _徽章(文本: str, 颜色: str) -> QLabel:
        标签 = QLabel(文本)
        标签.setStyleSheet(
            f"background: rgba(255,255,255,0.12); color: {颜色};"
            f"border-radius: 9px; padding: 2px 9px;")
        return 标签

    # ---------------- 对外 ----------------

    def 显示媒体(self, 媒体id: int) -> bool:
        """按 id 从库里取完整资料并展示；库里没有就返回 False（不抛异常）。"""
        条目 = self.库.取媒体(int(媒体id))
        if 条目 is None:
            self.媒体id = 0
            self.标题标签.setText("（找不到这部作品）")
            self.集提示标签.setText(f"库里没有 id={媒体id} 的作品，可能刚被删掉")
            return False
        self.媒体id = int(媒体id)
        return self._展示(条目)

    def 显示(self, 条目: 媒体条目) -> bool:
        """直接展示一个内存里的 :class:`媒体条目`（便于测试与"刮削完立刻预览"）。"""
        if 条目 is None:
            return False
        # 媒体条目里没有 id 字段，只能反查；查不到就是 0（见 _推断媒体id 的注释）
        self.媒体id = self._推断媒体id(条目)
        return self._展示(条目)

    def _展示(self, 条目: 媒体条目) -> bool:
        self.条目 = 条目
        self._填文字(条目)
        self._填标签(条目)
        self._填演职员(条目)
        self._填季集(条目)
        self._请求图片(条目)
        # 简介高度要等布局算过一遍才知道（此时 viewport 宽度还是 0）
        QTimer.singleShot(0, self._调简介高度)
        return True

    def 取播放路径(self) -> str:
        """当前该播哪个文件（电影=主文件，剧集=选中的集/第一集有文件的）。"""
        条目 = self.条目
        if 条目 is None:
            return ""
        if not 条目.是剧集:
            return self.主文件路径()
        项目 = self.集列表.currentItem()
        if 项目 is not None:
            路径 = 项目.data(Qt.ItemDataRole.UserRole)
            if 路径:
                return str(路径)
        # 没选就挑第一条有文件的："点播放"应该总能播点什么，而不是干瞪眼
        for 行号 in range(self.集列表.count()):
            路径 = self.集列表.item(行号).data(Qt.ItemDataRole.UserRole)
            if 路径:
                return str(路径)
        return ""

    def 主文件路径(self) -> str:
        """电影的主文件。

        注意：``库.取媒体()`` **不回填** ``条目.文件路径``（文件在"文件"表里），
        所以内存里有就用内存的，没有就查 :meth:`资料库.文件们`（主文件排最前）。
        """
        条目 = self.条目
        if 条目 is None:
            return ""
        if 条目.文件路径 and Path(条目.文件路径).is_file():
            return str(条目.文件路径)
        if self.媒体id:
            for 行 in self.库.文件们(self.媒体id):
                return str(行["路径"])
        if 条目.文件路径:
            return str(条目.文件路径)         # 文件不在盘上也认了：让播放器去报错
        return ""

    def 播放(self) -> None:
        路径 = self.取播放路径()
        if not 路径:
            self.集提示标签.setText("本地没有可播放的文件（重新刮削或检查文件还在不在）")
            return
        self.集提示标签.setText(f"▶ {路径}")
        self.要播放.emit(路径)

    def 演员数量(self) -> int:
        return len(self._演员卡片们)

    def 标签文本们(self) -> list[str]:
        return [标签.text() for 标签 in self._标签们]

    def 集路径们(self) -> list[str]:
        return [str(self.集列表.item(i).data(Qt.ItemDataRole.UserRole) or "")
                for i in range(self.集列表.count())]

    def 等待图片(self, 超时毫秒: int = 3000) -> int:
        """转事件循环等图片就位，返回还没到的张数（截图/导出/测试用）。

        为什么要这个方法：图片在**另一个线程**读，没有它就只能靠 sleep 猜时间，
        测试会变成"偶尔绿"；有了它，"等图加载完"这件事是可等待、可断言的。
        """
        应用 = QApplication.instance()
        截止 = time.monotonic() + max(0, int(超时毫秒)) / 1000.0
        while self._待图 > 0 and 应用 is not None and time.monotonic() < 截止:
            应用.processEvents()
            time.sleep(0.004)
        return self._待图

    def 关闭(self) -> None:
        """停掉工作线程。**QThread 还在跑就被析构 = 进程直接崩**，关窗前必须调。"""
        try:
            self._工作.停()
            self._工作.wait(3000)
        except Exception:       # noqa: BLE001 - 关窗失败也不该再抛
            pass

    def closeEvent(self, 事件) -> None:      # noqa: D102 - QWidget
        self.关闭()
        super().closeEvent(事件)

    def resizeEvent(self, 事件) -> None:     # noqa: D102 - QWidget
        super().resizeEvent(事件)
        if self.条目 is not None:
            self._调简介高度()

    # ---------------- 填内容 ----------------

    def _推断媒体id(self, 条目: 媒体条目) -> int:
        """内存条目没有 id（:class:`媒体条目` 里就没有这个字段），只能反查。

        顺序：文件路径 → 集的文件路径 → 标题。反查不到就返回 0
        （此时"重新刮削/手动匹配"会带着 0 发出去，外部据此提示用户即可）。
        """
        if 条目.文件路径:
            找到 = self.库.按路径找媒体(条目.文件路径)
            if 找到:
                return int(找到)
        for 季对象 in 条目.季们:
            for 集对象 in 季对象.集们:
                if 集对象.文件路径:
                    找到 = self.库.按路径找媒体(集对象.文件路径)
                    if 找到:
                        return int(找到)
        if 条目.标题:
            行 = self.库.一个("SELECT id FROM 媒体 WHERE 标题=? ORDER BY id LIMIT 1",
                          (条目.标题,))
            if 行:
                return int(行["id"])
        return 0

    def _填文字(self, 条目: 媒体条目) -> None:
        self.标题标签.setText(条目.标题 or "未命名")
        原名 = 条目.原名 if (条目.原名 and 条目.原名 != 条目.标题) else ""
        self.原名标签.setText(原名)
        self.原名标签.setVisible(bool(原名))

        类型文本 = {媒体类型.电影: "电影", 媒体类型.剧集: "剧集"}.get(条目.类型, "未知")
        时长文本 = f"{条目.时长分钟} 分钟" if 条目.时长分钟 else ""
        季集文本 = (f"{len(条目.季们)} 季 × {条目.汇总集数()} 集"
                 if 条目.是剧集 else "")
        片段 = [f"{条目.年份}" if 条目.年份 else "", 类型文本,
               时长文本 or 季集文本]
        self.信息标签.setText("　｜　".join(x for x in 片段 if x))

        self.评分标签.setText(f"★ {条目.评分:.1f}" if 条目.评分 else "★ 暂无评分")
        # 影评人评分是 0–100，和 ★ 的 0–10 **不是一个量纲**，所以这里把单位写死，
        # 免得用户把 82 当成"8.2 分"（TMDB 上这两个数经常一起出现）。
        self.影评人标签.setText(f"影评人 {条目.影评人评分:.0f}/100（百分制）"
                           if 条目.影评人评分 else "影评人 暂无（百分制）")
        分级 = 条目.取分级()
        self.分级标签.setText(f"分级 {分级.可读()}" if 分级 else "分级 暂无分级")
        self.语言标签.setText(f"语言 {条目.原始语言}" if 条目.原始语言 else "语言 未知")
        self.状态标签.setText(f"状态 {条目.状态}" if 条目.状态 else "状态 未知")
        self.简介标签.setText(条目.简介 or "（暂无简介）")

    def _填标签(self, 条目: 媒体条目) -> None:
        for 旧 in self._标签们:
            旧.setParent(None)
            旧.deleteLater()
        self._标签们 = []
        for 名字 in 条目.标签[:标签上限]:
            标签 = QLabel(名字)
            标签.setStyleSheet(
                "background: rgba(120,170,255,0.18); color: #d8e6ff;"
                "border-radius: 9px; padding: 2px 10px;")
            self.标签布局.insertWidget(self.标签布局.count() - 1, 标签)
            self._标签们.append(标签)
        多出 = len(条目.标签) - len(self._标签们)
        if 多出 > 0:
            尾巴 = QLabel(f"+{多出}")
            尾巴.setToolTip("、".join(条目.标签))
            尾巴.setStyleSheet("color: #9fb6d6;")
            self.标签布局.insertWidget(self.标签布局.count() - 1, 尾巴)
            self._标签们.append(尾巴)

    def _填演职员(self, 条目: 媒体条目) -> None:
        导演 = [x.人物.名字 for x in 条目.参演们
              if x.工种 is 人物工种.导演 and x.人物.名字]
        编剧 = [x.人物.名字 for x in 条目.参演们
              if x.工种 is 人物工种.编剧 and x.人物.名字]
        self.导演标签.setText("导演：" + ("、".join(导演) if 导演 else "—"))
        self.编剧标签.setText("编剧：" + ("、".join(编剧) if 编剧 else "—"))

        for 旧 in self._演员卡片们:
            旧.setParent(None)
            旧.deleteLater()
        self._演员卡片们 = []
        self._头像标签 = {}
        for 序号, 关系 in enumerate(条目.演员(20)):
            卡片 = QWidget()
            卡片.setFixedWidth(86)
            列 = QVBoxLayout(卡片)
            列.setContentsMargins(0, 0, 0, 0)
            列.setSpacing(2)
            头像 = QLabel("？")
            头像.setFixedSize(64, 64)
            头像.setAlignment(Qt.AlignmentFlag.AlignCenter)
            头像.setStyleSheet(
                "background: rgba(255,255,255,0.10); border-radius: 8px;"
                "color: #b9c4d4;")
            列.addWidget(头像, 0, Qt.AlignmentFlag.AlignHCenter)
            名字 = QLabel(关系.人物.名字 or "—")
            名字.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            名字.setToolTip(关系.人物.名字)
            # 长名字手动省略：QLabel 没有 elide，直接让它换行会把卡片撑高
            名字.setText(QFontMetrics(名字.font()).elidedText(
                关系.人物.名字 or "—", Qt.TextElideMode.ElideRight, 82))
            列.addWidget(名字)
            角色 = QLabel(关系.角色 or "")
            角色.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            角色.setStyleSheet("color: #93a3b8;")
            角色.setText(QFontMetrics(角色.font()).elidedText(
                关系.角色 or "", Qt.TextElideMode.ElideRight, 82))
            列.addWidget(角色)
            self.演员布局.insertWidget(self.演员布局.count() - 1, 卡片)
            self._演员卡片们.append(卡片)
            self._头像标签[f"头像{序号}"] = 头像
        self.演员框.setVisible(bool(self._演员卡片们))   # 一个演员都没有就别留一条空框

    def _调简介高度(self) -> None:
        """把简介框收成"刚好装下文本"的高度（最多 112）。

        QScrollArea 不会自己按内容收缩，不调的话短简介下面会留一大块空白，
        把演职员/剧集顶到看不见的地方去。
        """
        宽 = max(240, self.简介框.viewport().width())
        高 = self.简介标签.heightForWidth(宽)
        self.简介框.setFixedHeight(max(30, min(112, 高 + 10)))

    def _填季集(self, 条目: 媒体条目) -> None:
        self.剧集区.setVisible(条目.是剧集)
        self.季下拉.blockSignals(True)
        self.季下拉.clear()
        if not 条目.是剧集 or not 条目.季们:
            self.季下拉.addItem("（没有季信息）", 0)
            self.季下拉.setEnabled(False)
            self.集列表.clear()
            self.集提示标签.setText("电影没有分集" if not 条目.是剧集
                              else "还没刮到分集信息，重新刮削一次试试")
        else:
            # 默认选"全部"：这样集列表条数恒等于 汇总集数()，
            # 用户一眼就能看到本地到底有哪几集（选季只是过滤，不是必须操作）。
            self.季下拉.addItem(f"全部（{条目.汇总集数()} 集）", 0)
            for 季对象 in 条目.季们:
                名字 = 季对象.标题 or f"第 {季对象.季号} 季"
                self.季下拉.addItem(f"{名字}·{len(季对象.集们)} 集", 季对象.季号)
            self.季下拉.setEnabled(True)
            self.季下拉.setCurrentIndex(0)
        self.季下拉.blockSignals(False)
        self.填集列表()

    def 填集列表(self) -> None:
        """按季下拉重建集列表（季号 0 = 全部）。"""
        条目 = self.条目
        self.集列表.clear()
        if 条目 is None:
            return
        季号 = int(self.季下拉.currentData() or 0)
        有文件 = 0
        for 季对象 in 条目.季们:
            if 季号 and 季对象.季号 != 季号:
                continue
            for 集对象 in 季对象.集们:
                文本 = f"{集对象.编号}　{集对象.标题}".rstrip("　")
                尾巴 = []
                if 集对象.时长分钟:
                    尾巴.append(f"{集对象.时长分钟} 分钟")
                if 集对象.播出日期:
                    尾巴.append(集对象.播出日期)
                if 尾巴:
                    文本 += "　·　" + "　·　".join(尾巴)
                项目 = QListWidgetItem()
                if 集对象.文件路径:
                    有文件 += 1
                    项目.setText(文本)
                    项目.setData(Qt.ItemDataRole.UserRole, str(集对象.文件路径))
                    项目.setToolTip(f"▶ 单击播放：{集对象.文件路径}")
                else:
                    # 置灰 + 写明原因：不然用户点了没反应会以为界面坏了
                    项目.setText(文本 + "　·　本地没有这一集")
                    项目.setForeground(QColor("#7d8794"))
                    项目.setToolTip("本地没有这一集")
                项目.setData(Qt.ItemDataRole.UserRole + 1, 集对象.编号)
                self.集列表.addItem(项目)
        if self.集列表.count():
            self.集提示标签.setText(
                f"共 {self.集列表.count()} 集，其中 {有文件} 集有本地文件"
                f"（点一下就播；灰的是本地没有的）")
        elif 条目.是剧集:
            self.集提示标签.setText("这一季没有分集信息")

    def _请求图片(self, 条目: 媒体条目) -> None:
        self._待图 = 0
        self.顶部.设置图(None)
        self.海报标签.setPixmap(QPixmap())
        self.海报标签.setText("海报")
        海报 = 条目.海报
        if 海报 is not None:
            self._排队("海报", 海报.本地路径, 海报.远端路径, 海报框, "w500")
        背景 = 条目.背景
        if 背景 is not None:
            self._排队("背景", 背景.本地路径, 背景.远端路径, 背景框, "w1280")
        for 标签 in self._头像标签.values():
            标签.setPixmap(QPixmap())
            标签.setText("？")
        for 序号, 关系 in enumerate(条目.演员(20)):
            标签 = self._头像标签.get(f"头像{序号}")
            头像 = 关系.人物.头像
            if 标签 is not None and 头像 is not None:
                self._排队(f"头像{序号}", 头像.本地路径, 头像.远端路径,
                         头像框, "w185")

    def _排队(self, 键, 本地路径, 远端路径: str, 目标: tuple[int, int],
            尺寸档: str) -> None:
        if not 本地路径 and not 远端路径:
            return                    # 连"线索"都没有：别浪费一次线程往返
        self._待图 += 1
        self._工作.开()
        self._工作.排(键, 本地路径, 远端路径, 目标, 尺寸档)

    def _图好了(self, 键, 图) -> None:
        """槽在**界面线程**执行（跨线程信号默认排队投递）—— QPixmap 只能在这儿建。"""
        self._待图 = max(0, self._待图 - 1)
        if 图 is None or 图.isNull():
            return
        像素 = QPixmap.fromImage(图)
        if 键 == "海报":
            self.海报标签.setPixmap(像素.scaled(
                self.海报标签.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            self.海报标签.setText("")
        elif 键 == "背景":
            self.顶部.设置图(图)
        else:
            标签 = self._头像标签.get(str(键))
            if 标签 is not None:
                标签.setPixmap(像素.scaled(
                    标签.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation))
                标签.setText("")

    # ---------------- 交互 ----------------

    def _点集(self, 项目: QListWidgetItem) -> None:
        路径 = 项目.data(Qt.ItemDataRole.UserRole) if 项目 is not None else None
        if not 路径:
            self.集提示标签.setText("本地没有这一集，换一集吧（或者先去刮削/下载）")
            return
        self.集提示标签.setText(f"▶ {路径}")
        self.要播放.emit(str(路径))

    def _关闭点击(self) -> None:
        """作为弹窗时关掉自己；被嵌进标签页时退化成"隐藏"（父窗口说了算）。"""
        if self.isWindow():
            self.close()
        else:
            self.setVisible(False)


class 选集对话框(QDialog):
    """剧集右键"播放"时的选集小窗（只有有本地文件的集才列出来）。"""

    def __init__(self, 条目: 媒体条目, 父=None) -> None:
        super().__init__(父)
        self.setWindowTitle(f"选集 · {条目.标题 or '未命名'}")
        self.resize(460, 380)
        self.选中路径 = ""
        self.列表 = QListWidget()
        布局 = QVBoxLayout(self)
        布局.addWidget(QLabel("选一集播放（灰的没有本地文件）："))
        布局.addWidget(self.列表, 1)
        确定 = QPushButton("▶ 播放这一集")
        确定.clicked.connect(lambda: self._选(self.列表.currentItem()))
        取消 = QPushButton("取消")
        取消.clicked.connect(self.reject)
        行 = QHBoxLayout()
        行.addWidget(确定)
        行.addWidget(取消)
        布局.addLayout(行)
        for 季对象 in 条目.季们:
            for 集对象 in 季对象.集们:
                项目 = QListWidgetItem(f"{集对象.编号}　{集对象.标题}".rstrip("　"))
                if 集对象.文件路径:
                    项目.setData(Qt.ItemDataRole.UserRole, str(集对象.文件路径))
                else:
                    项目.setForeground(QColor("#7d8794"))
                    项目.setText(项目.text() + "　·　本地没有这一集")
                self.列表.addItem(项目)
        self.列表.itemDoubleClicked.connect(self._选)

    def _选(self, 项目: QListWidgetItem) -> None:
        路径 = 项目.data(Qt.ItemDataRole.UserRole) if 项目 is not None else None
        if not 路径:
            return
        self.选中路径 = str(路径)
        self.accept()
