"""播放页（内核级）：视频画面 + 控制条 + 弹幕 + 字幕 + 进度缩略图。

这一页**只依赖 V2 自己的东西**（`player` / `subtitle` / `danmaku` / `scrape.命名解析`），
不 import 任何界面层模块 —— 所以它既可以被 V2 的"纯内核"主窗口直接用，
也可以被整合后的主界面（V1 那套 GUI）当作画面与控制区嵌进去。

为什么画面自己画
================
画面是 :class:`~wangpan.ui.视频控件` 用 QPainter 画的（帧 → QImage → 控件），
**不是把某个窗口句柄交给外部播放器**。所以：

* "视频跑到别的窗口里播放"这种事结构上不可能发生；
* 字幕和弹幕是在**同一次 paintEvent** 里画上去的，不会与画面不同步；
* 两个页面（主界面 + 独立窗口）可以同时显示同一路播放，各自取帧，不需要"交接仪式"。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QInputDialog,
                             QLabel, QPushButton, QScrollArea, QSlider, QVBoxLayout,
                             QWidget)

from ..player.记录 import 记录本
from ..player.引擎 import 播放状态
from ..subtitle import 找同名字幕, 读字幕文件
from .播放会话 import 播放会话
from .视频控件 import 视频控件
from .悬浮预览 import 悬浮预览窗
from .流动布局 import 换行按钮区

__all__ = ["播放页", "时间文本"]


def 时间文本(秒: float) -> str:
    秒 = max(0, int(秒 or 0))
    if 秒 >= 3600:
        return f"{秒 // 3600}:{秒 % 3600 // 60:02d}:{秒 % 60:02d}"
    return f"{秒 // 60:02d}:{秒 % 60:02d}"


#: 同目录里算作"同一部剧"的后缀
视频后缀们 = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts", ".flv", ".m4v", ".wmv")


class 播放页(QWidget):
    """播放画面 + 控制条 + 弹幕/字幕/预览。"""

    状态更新 = Signal(dict)          # 准备完成 / 调优建议 / 各种 AI 结果
    日志追加 = Signal(str)
    提示 = Signal(str)
    播放状态变了 = Signal(str)
    播放结束 = Signal()
    要上一集 = Signal()
    要下一集 = Signal()
    要全屏 = Signal(bool)
    要打开本地 = Signal()
    标题变了 = Signal(str)

    def __init__(self, 会话: Optional[播放会话] = None, *,
                 日志回调: Optional[Callable[[str], None]] = None,
                 父: Optional[QWidget] = None) -> None:
        super().__init__(父)
        self.会话 = 会话 if 会话 is not None else 播放会话()
        self.引擎 = self.会话.引擎
        self._外部日志 = 日志回调
        self.记录本 = 记录本()

        # ---- 弹幕控制器（引擎每帧推进；视频控件负责画）----
        from ..danmaku.引擎 import 弹幕控制器
        self.弹幕 = 弹幕控制器(日志回调=self._写日志)
        self.会话.界面字幕回调 = self._界面装字幕

        self.列表: list[str] = []
        self.列表序号 = -1
        self._帧序号 = -1
        self._拖动中 = False
        self._上次状态 = ""
        #: 预览小窗现在是不是弹着的（真机测试/自检会看它）
        self._预览显示中 = False
        #: 网盘/直链播放时**默认不做**悬停预览缩略图。
        #: 为什么（真机两次崩溃的结论）：预览要**另开一路连接 + 另开一个解码器**去读
        #: 同一个直链，而网盘直链常常是一次性/短时效的 —— 实测现象正是"播放黑屏 +
        #: libavcodec 内部 abort（SIGABRT）"，两次 core 的崩溃线程分别是 `V2-缩略图`
        #: 与 `V2-解封装`。本地文件不受影响（不走网络、随便再开一路），照常有预览。
        self.预览仅本地 = True
        #: 缩略图任务**同一时刻只许跑一个**，且共用的解码器必须串行访问。
        #: 为什么（真机 coredump 实证）：原来每次悬停都新起一个线程，多个线程
        #: 同时喂同一个解码器 —— FFmpeg 的内部堆会被写坏，崩在 avcodec_send_packet，
        #: 崩溃线程名就是 `V2-缩略图`（systemd-coredump 里能查到）。
        self._预览忙 = False
        self._预览锁 = threading.Lock()
        self._预览缓存: dict[int, QImage] = {}
        self._缩略图器 = None
        self._预览结果 = None
        self._字幕们: list = []
        self._字幕序号 = -1

        self._构建()
        self.读弹幕设置()
        self.视频.设置弹幕控制器(self.弹幕)
        self.视频.双击.connect(self.切换全屏)
        self.视频.单击.connect(self.播放暂停)
        # 视频区自己变尺寸时同步引擎的输出尺寸。这样"隐藏侧栏让画面变大"这件事
        # 就不再需要去 resize 主窗口（原先正是它导致点一下面板窗口就变形）。
        self.视频.尺寸变了.connect(self._视频区变了)

        self._定时器 = QTimer(self)
        self._定时器.setInterval(16)
        self._定时器.timeout.connect(self._刷新)
        self._定时器.start()
        self._存档定时器 = QTimer(self)
        self._存档定时器.setInterval(5000)
        self._存档定时器.timeout.connect(self.存档位置)
        self._存档定时器.start()

    # ------------------------------------------------------------------ 界面

    def _构建(self) -> None:
        布局 = QVBoxLayout(self)
        布局.setContentsMargins(0, 0, 0, 0)
        布局.setSpacing(0)
        self.视频 = 视频控件()
        布局.addWidget(self.视频, 1)
        布局.addWidget(self._建控制条())

    def _建控制条(self) -> QWidget:
        """控制条：预览 + 进度条 + 一行按钮。

        ⚠️ 按钮那一行必须包在**横向滚动区**里。原因（真机实测）：
        这一行有十来个按钮，最小宽度加起来 1400+ px，会把这个播放页的
        `minimumSizeHint` 顶到 1400 —— 放进 QSplitter 后，右侧的
        「视频信息 / 播放清单 / AI 助手」面板直接被挤出窗口外**看不见**了。
        包成滚动区之后：窄窗口只让按钮行内部滚动，页面本身可以任意窄。
        """
        条 = QWidget()
        条布局 = QVBoxLayout(条)
        条布局.setContentsMargins(8, 4, 8, 4)
        条布局.setSpacing(4)

        # ---- 预览：**不再占一条 90px 高的常驻黑条**（用户嫌它"长又粗影响观看"），
        #      改成鼠标在进度条上移动时，贴在光标上方弹出的悬浮小窗。----
        self.预览窗 = 悬浮预览窗(self)
        #: 兼容老名字：`预览` 现在指的是小窗里那张图（老代码/测试拿它取 pixmap）
        self.预览 = self.预览窗.图

        self._预览定时器 = QTimer(self)
        self._预览定时器.setSingleShot(True)
        self._预览定时器.setInterval(180)
        self._预览定时器.timeout.connect(self._出预览图)

        self.进度 = QSlider(Qt.Orientation.Horizontal)
        self.进度.setRange(0, 1000)
        self.进度.setMouseTracking(True)
        self.进度.installEventFilter(self)
        self.进度.sliderPressed.connect(self._开始拖)
        self.进度.sliderReleased.connect(self._结束拖)
        self.进度.sliderMoved.connect(self._要预览)
        条布局.addWidget(self.进度)

        # ---- 按钮行：**自动换行**（用户真机反馈："这排按钮没有显示完，
        #      还有一些超出 GUI 看不见"）。原来包在横向滚动区里、高度只给一行，
        #      横向滚动条被压得几乎看不见 —— 看不见也点不到。----
        行容器 = 换行按钮区(间距=6, 行距=6)
        self._控制行 = 行容器
        self._建控制按钮(self._控制行)
        #: 兼容老名字（自检/验收脚本按 `按钮滚动区` 找这一块）
        self.按钮滚动区 = 行容器
        条布局.addWidget(行容器)
        # 布局要再排几次：控制条是在 __init__ 里建的，那会儿按钮的 sizeHint
        # 还没定型（文字/主题/字体都在后面才落），第一次排出来行数会偏多；
        # 而且几何没变时 Qt 不会再次调 setGeometry —— 真机实测 11 个按钮被排成 11 行。
        for _毫秒 in (0, 120, 400):
            QTimer.singleShot(_毫秒, self._重排控制按钮)
        self._控制行.重排()

        return 条

    def 加控制按钮(self, 按钮: QPushButton, 在谁前面: Optional[QWidget] = None) -> None:
        """往控制条那一行插一个按钮（宿主界面加自己的按钮时用）。

        为什么不直接 `全屏按钮.parentWidget().layout().insertWidget(...)`：
        按钮的 parent 是**整条控制条**，它的 layout 是竖直布局 —— 插进去按钮会跑到
        预览图上方（真机就这么错过一次）。这里由播放页自己维护"那一行"的引用。
        """
        位置 = -1
        if 在谁前面 is not None:
            位置 = self._控制行.indexOf(在谁前面)
        if 位置 < 0:
            self._控制行.addWidget(按钮)
        else:
            self._控制行.insertWidget(位置, 按钮)

    def _建控制按钮(self, 行: QHBoxLayout) -> None:
        self.弹幕按钮 = QPushButton("🗨 弹幕")
        self.弹幕按钮.setCheckable(True)
        self.弹幕按钮.setChecked(True)
        self.弹幕按钮.setToolTip("开关弹幕（右键：时间轴偏移 / 透明度）")
        self.弹幕按钮.clicked.connect(self.切换弹幕)
        self.弹幕按钮.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.弹幕按钮.customContextMenuRequested.connect(self._弹幕菜单)
        行.addWidget(self.弹幕按钮)

        self.弹幕设置按钮 = QPushButton("⚙ 弹幕设置")
        self.弹幕设置按钮.setToolTip("字号/速度/不透明度/时间轴/过滤规则（可导入导出）")
        self.弹幕设置按钮.clicked.connect(self.开弹幕设置)
        行.addWidget(self.弹幕设置按钮)

        self.找弹幕按钮 = QPushButton("⬇ 找弹幕")
        self.找弹幕按钮.setToolTip(
            "按文件名找这一集的弹幕：Animeko 公益源（零凭据）→ 弹弹play → 本地同名文件；"
            "记住的匹配下次打开会自动装")
        self.找弹幕按钮.clicked.connect(self.装载弹幕)
        行.addWidget(self.找弹幕按钮)

        self.字幕按钮 = QPushButton("💬 字幕")
        self.字幕按钮.setToolTip("加载同名字幕 / 开关字幕显示")
        self.字幕按钮.clicked.connect(self.切换字幕)
        行.addWidget(self.字幕按钮)

        self.打开按钮 = QPushButton("📂 打开文件")
        self.打开按钮.clicked.connect(lambda: self.要打开本地.emit())
        行.addWidget(self.打开按钮)

        self.播放按钮 = QPushButton("▶ 播放")
        self.播放按钮.clicked.connect(self.播放暂停)
        行.addWidget(self.播放按钮)

        self.时间标签 = QLabel("00:00 / 00:00")
        行.addWidget(self.时间标签)
        行.addStretch(1)

        行.addWidget(QLabel("🔊"))
        self.音量 = QSlider(Qt.Orientation.Horizontal)
        self.音量.setRange(0, 100)
        self.音量.setValue(100)
        self.音量.setFixedWidth(120)
        self.音量.valueChanged.connect(self.设置音量)
        行.addWidget(self.音量)

        self.上一集 = QPushButton("⏮ 上一集")
        self.上一集.clicked.connect(lambda: self.切列表(-1))
        self.下一集 = QPushButton("⏭ 下一集")
        self.下一集.clicked.connect(lambda: self.切列表(1))
        行.addWidget(self.上一集)
        行.addWidget(self.下一集)

        行.addWidget(QLabel("章节"))
        self.章节框 = QComboBox()
        self.章节框.setMinimumWidth(120)
        self.章节框.addItem("（无章节表）", -1)
        self.章节框.currentIndexChanged.connect(self.跳章节下拉)
        行.addWidget(self.章节框)

        行.addWidget(QLabel("倍速"))
        self.倍速框 = QComboBox()
        for 值 in ("0.5", "0.75", "1", "1.25", "1.5", "2", "3"):
            self.倍速框.addItem(f"{值}×", float(值))
        self.倍速框.setCurrentIndex(2)
        self.倍速框.currentIndexChanged.connect(
            lambda _i: self.设置倍速(float(self.倍速框.currentData() or 1.0)))
        行.addWidget(self.倍速框)

        self.逐帧按钮 = QPushButton("⏯ 逐帧")
        self.逐帧按钮.setToolTip("暂停后每点一次前进一帧")
        self.逐帧按钮.clicked.connect(self.逐帧)
        行.addWidget(self.逐帧按钮)

        self.截图按钮 = QPushButton("📷 截图")
        self.截图按钮.clicked.connect(self.截图)
        行.addWidget(self.截图按钮)

        self.全屏按钮 = QPushButton("⛶ 全屏")
        self.全屏按钮.clicked.connect(self.切换全屏)
        行.addWidget(self.全屏按钮)

    # ------------------------------------------------------------------ 日志/提示

    def _写日志(self, 文本: str) -> None:
        self.日志追加.emit(str(文本))
        if self._外部日志 is not None:
            try:
                self._外部日志(str(文本))
            except Exception:  # noqa: BLE001
                pass

    def _提示(self, 文本: str) -> None:
        self.提示.emit(str(文本))

    # ------------------------------------------------------------------ 打开

    def 打开(self, 路径: str, 续播: bool = True) -> bool:
        """打开一个本地文件（含同名字幕 / 本地弹幕 / 续播 / 同目录播放列表）。"""
        会话 = self.会话
        会话.网盘标识 = "本地"
        会话.远端路径 = str(路径)
        会话.标题 = Path(路径).name
        会话.直链信息 = {"url": str(路径), "headers": {}, "name": Path(路径).name, "size": 0}
        try:
            self.引擎.打开(str(路径))
        except Exception as 错:  # noqa: BLE001
            self._提示(f"❌ 打不开：{错}")
            return False
        self.视频.清空()
        self._预览缓存 = {}
        self._缩略图器 = None
        self._收起预览()
        # 媒体信息 / 探测结果：本地文件用内核自带那份就够（不问带宽）
        try:
            会话.媒体 = 会话._内核探测媒体(str(路径), {}, 本地=True,
                                        大小=Path(路径).stat().st_size)
            会话.探测 = 会话._内核探测直链(str(路径), {}, 本地=True,
                                        大小=Path(路径).stat().st_size)
        except Exception:  # noqa: BLE001
            pass
        self._装同名字幕(str(路径))
        self.引擎.播放()
        self.播放按钮.setText("⏸ 暂停")
        self._装章节(str(路径))
        self._装本地弹幕(str(路径))
        self._按记忆装弹幕(str(路径))
        self._续播(str(路径), 续播)
        self.标题变了.emit(Path(路径).name)
        self._建列表(str(路径))
        self._提示(f"▶ {self.引擎.统计.音视频}｜时长 "
                 f"{时间文本(self.引擎.统计.总时长秒)}")
        return True

    def 起播后同步(self) -> None:
        """会话已经自己起播了（外部直接调 `会话.准备 → 起播`）之后，把画面层对齐一次。

        为什么需要它：网盘直链那条路是"先在后台准备（取直链/探测/问 AI），再起播"，
        起播由宿主持有会话直接调；画面清空、按钮文字、章节表这些界面状态得有人补一次。
        """
        self.视频.清空()
        self.播放按钮.setText("⏸ 暂停")
        self._帧序号 = -1
        self._装章节(str(self.引擎.输入.地址 if self.引擎.输入 else ""))

    def 播放直链(self, 地址: str, 请求头: Optional[dict] = None, *,
               名字: str = "", 网盘标识: str = "网络", 远端路径: str = "") -> bool:
        """直接播一路地址（网盘直链已经拿到手时用；不做探测、不问 AI）。"""
        from .播放会话 import 播放设置
        会话 = self.会话
        会话.网盘标识 = str(网盘标识 or "网络")
        会话.远端路径 = str(远端路径 or 地址)
        会话.标题 = str(名字 or Path(str(远端路径)).name or "网络视频")
        会话.直链信息 = {"url": str(地址), "headers": dict(请求头 or {}),
                       "name": 会话.标题, "size": 0}
        会话.设置 = 播放设置(来源="直链", 网络缓存毫秒=3000,
                          理由="直接播地址，未做带宽探测")
        try:
            self.引擎.打开(str(地址), 请求头=dict(请求头 or {}))
        except Exception as 错:  # noqa: BLE001
            self._提示(f"❌ 打不开：{错}")
            return False
        self.视频.清空()
        self._字幕们 = []
        self._字幕序号 = -1
        self.引擎.播放()
        self.播放按钮.setText("⏸ 暂停")
        self._提示(f"▶ {self.引擎.统计.音视频}")
        return True

    def 播放源(self, 网盘标识: str, 远端路径: str) -> bool:
        """按"网盘 + 远端路径"播（准备 → 起播），网盘页/清单都走这条。"""
        try:
            摘要 = self.会话.准备(str(网盘标识), str(远端路径))
        except Exception as 错:  # noqa: BLE001
            self._提示(f"❌ 打不开：{错}")
            return False
        self.状态更新.emit(dict(摘要, 类型="准备完成"))
        self._提示(f"▶ 正在起播：{self.会话.标题}")
        应用 = QApplication.instance()
        if 应用 is not None:
            应用.processEvents()
        if not self.会话.起播(0):
            self._提示("❌ 起播失败（看日志）")
            self.状态更新.emit({"类型": "准备失败", "说明": "起播失败"})
            return False
        self.视频.清空()
        self.播放按钮.setText("⏸ 暂停")
        self._装章节(str(self.会话.直链信息.get("url") or ""))
        return True

    def _装同名字幕(self, 路径: str) -> None:
        try:
            self._字幕们 = list(找同名字幕(str(路径)))
        except Exception:  # noqa: BLE001
            self._字幕们 = []
        self._字幕序号 = 0 if self._字幕们 else -1
        self.会话.装字幕们(self._字幕们)
        self._界面装字幕(self.会话.取当前字幕轨道())
        if self._字幕们:
            self._提示(f"💬 已加载字幕：{self._字幕们[0].名字}")

    def _界面装字幕(self, 轨道) -> None:
        """会话说"现在该显示这条字幕"，画面层照办。"""
        try:
            self.视频.设置字幕(轨道, 轨道 is not None)
        except Exception:  # noqa: BLE001
            pass

    def _装本地弹幕(self, 路径: str) -> None:
        try:
            from ..danmaku.源.本地 import 读同名字幕弹幕
            本地弹幕 = 读同名字幕弹幕(Path(路径))
            if 本地弹幕 is not None and len(本地弹幕):
                self.弹幕.装载本地(本地弹幕, "本地同名")
                self._提示(f"🗨 已加载本地弹幕 {len(本地弹幕)} 条")
        except Exception:  # noqa: BLE001
            pass

    def _按记忆装弹幕(self, 路径: str) -> None:
        """有逐集记忆就直接装好（不联网、不搜索）。没记忆就让用户点「找弹幕」。"""
        try:
            记录 = self.弹幕.记忆.取(Path(路径))
            if 记录 is None or not 记录.标识:
                return
            from ..danmaku.源.接口 import 素材信息
            集号 = int(记录.集号) if str(记录.集号).isdigit() else None
            装载 = self.弹幕.装载(素材信息(
                路径=Path(路径), 文件名=Path(路径).name,
                时长秒=self.引擎.统计.总时长秒, 标题=记录.标题 or "", 集号=集号))
            if 装载.成功:
                self._提示(f"🗨 从记忆自动装好 {装载.条数} 条"
                         f"（{记录.标题} {记录.集标题}）")
        except Exception as 错:  # noqa: BLE001
            self._写日志(f"[弹幕] 用记忆装载失败（不影响播放）：{错}")

    def _续播(self, 路径: str, 续播: bool) -> None:
        try:
            时长 = self.引擎.统计.总时长秒
            self.记录本.记开始(str(路径), 时长, Path(路径).name)
            位置 = self.记录本.续播位置(str(路径)) if 续播 else 0.0
            if 位置 > 1.0:
                self.引擎.跳转(位置)
                self._提示(f"⏩ 从上次看到的位置继续：{时间文本(位置)}")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ 播放控制

    def 播放暂停(self) -> None:
        if self.引擎.输入 is None:
            self.要打开本地.emit()
            return
        if self.引擎.状态 == 播放状态.播放中:
            self.引擎.暂停切换()
            self.播放按钮.setText("▶ 继续")
        else:
            self.引擎.播放()
            self.播放按钮.setText("⏸ 暂停")

    def 停止(self) -> None:
        self.引擎.停止()
        self.视频.清空()
        self.播放按钮.setText("▶ 播放")

    def 跳转(self, 秒: float) -> None:
        self.引擎.跳转(max(0.0, float(秒 or 0.0)))

    def 相对跳转(self, 秒: float) -> None:
        self.跳转(float(self.引擎.统计.当前时间秒 or 0.0) + float(秒 or 0.0))

    def 跳到结尾(self) -> None:
        总 = float(self.引擎.统计.总时长秒 or 0.0)
        if 总 > 0:
            self.跳转(max(0.0, 总 - 1.0))

    def 逐帧(self) -> None:
        if self.引擎.逐帧():
            self._提示(f"⏯ 逐帧：{self.引擎.统计.当前时间秒:.3f}s")
        else:
            self._提示("⏯ 逐帧：没有可前进的帧（先暂停或先播放一下）")

    def 设置倍速(self, 倍速: float) -> None:
        值 = self.引擎.设置倍速(float(倍速 or 1.0))
        for i in range(self.倍速框.count()):
            if abs(float(self.倍速框.itemData(i)) - 值) < 1e-6:
                self.倍速框.blockSignals(True)
                self.倍速框.setCurrentIndex(i)
                self.倍速框.blockSignals(False)
                break

    def 当前倍速(self) -> float:
        return float(self.引擎.倍速 or 1.0)

    def 设置音量(self, 值: int) -> None:
        self.引擎.设置音量(max(0, min(100, int(值))) / 100.0)

    def 当前音量(self) -> int:
        return int(round(float(self.引擎.音量 or 0.0) * 100))

    def 设置静音(self, 静音: bool) -> None:
        if 静音:
            self._静音前 = max(1, self.当前音量())
            self.音量.setValue(0)
        else:
            self.音量.setValue(getattr(self, "_静音前", 100))

    def 是否静音(self) -> bool:
        return self.当前音量() == 0

    def 切换全屏(self) -> None:
        self.要全屏.emit(not self.是全屏())

    def 是全屏(self) -> bool:
        窗 = self.window()
        try:
            return bool(窗.isFullScreen())
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ 章节 / 列表

    def _装章节(self, 路径: str) -> None:
        self.章节框.blockSignals(True)
        self.章节框.clear()
        章节们 = self.引擎.章节们()
        if not 章节们:
            self.章节框.addItem("（无章节表）", -1)
        else:
            for 项 in 章节们:
                self.章节框.addItem(f"{时间文本(项.起始秒)} {项.标题}", 项.序号)
        self.章节框.blockSignals(False)

    def 跳章节下拉(self, _序号: int) -> None:
        值 = self.章节框.currentData()
        if isinstance(值, int) and 值 >= 0:
            self.引擎.跳章节(值)

    def 章节数(self) -> int:
        return len(self.引擎.章节们())

    def 当前章节(self) -> int:
        return int(self.引擎.当前章节())

    def 跳章节(self, 编号: int) -> None:
        self.引擎.跳章节(int(编号))

    def 下一章(self) -> None:
        self.引擎.跳章节(min(max(0, len(self.引擎.章节们()) - 1),
                          max(0, self.当前章节()) + 1))

    def 上一章(self) -> None:
        self.引擎.跳章节(max(0, max(0, self.当前章节()) - 1))

    def _建列表(self, 路径: str) -> None:
        """本地文件：同目录的视频按文件名排成播放列表。"""
        目标 = Path(路径)
        if not 目标.is_file():
            self.列表, self.列表序号 = [str(路径)], 0
            return
        兄弟们 = sorted(p for p in 目标.parent.iterdir()
                     if p.is_file() and p.suffix.lower() in 视频后缀们)
        self.列表 = [str(p) for p in (兄弟们 or [目标])]
        self.列表序号 = self.列表.index(str(目标)) if str(目标) in self.列表 else 0

    def 切列表(self, 步: int) -> None:
        if not self.列表:
            return
        self.列表序号 = max(0, min(len(self.列表) - 1, self.列表序号 + 步))
        self.打开(self.列表[self.列表序号])

    def 下一个(self) -> None:
        self.切列表(1)

    def 上一个(self) -> None:
        self.切列表(-1)

    # ------------------------------------------------------------------ 字幕

    def 切换字幕(self) -> None:
        """有多个同名字幕就轮换；一个都没有就手动选一个。"""
        if not self._字幕们:
            路径, _ = QFileDialog.getOpenFileName(
                self, "选一个字幕文件", str(Path.home()),
                "字幕 (*.srt *.ass *.ssa *.vtt);;所有文件 (*)")
            if not 路径:
                return
            try:
                轨道 = 读字幕文件(路径)
            except Exception as 错:  # noqa: BLE001
                self._提示(f"❌ 读字幕失败：{错}")
                return
            self._字幕们 = [轨道]
            self._字幕序号 = 0
            self.会话.装字幕们(self._字幕们)
            self.会话.设置当前字幕轨道(0)
            self._提示(f"💬 字幕：{轨道.名字}（{len(轨道.条目们)} 条）")
            return
        序号 = self.会话.切换字幕()
        self._字幕序号 = 序号
        if 序号 < 0:
            self._提示("💬 已关闭字幕")
            return
        轨道 = self.会话.取当前字幕轨道()
        self._提示(f"💬 字幕：{getattr(轨道, '名字', '')}"
                 f"（{len(getattr(轨道, '条目们', []) or [])} 条）")

    def 挂字幕文件(self, 路径: str) -> bool:
        if not self.会话.挂字幕(str(路径)):
            self._提示("❌ 这个字幕文件读不了")
            return False
        self._字幕们 = self.会话.字幕们
        self._字幕序号 = self.会话.字幕序号
        self._提示(f"💬 已挂字幕：{Path(路径).name}")
        return True

    # ------------------------------------------------------------------ 弹幕

    def 切换弹幕(self) -> None:
        self.弹幕.设置显示(self.弹幕按钮.isChecked())
        self._提示("🗨 弹幕：" + ("开" if self.弹幕.显示中 else "关")
                 + ("｜" + self.弹幕.摘要() if self.弹幕.有弹幕 else ""))

    def _弹幕菜单(self) -> None:
        偏移, 好 = QInputDialog.getInt(
            self, "弹幕时间轴偏移", "弹幕相对视频平移（毫秒，负数=弹幕提前）：",
            self.弹幕.时间轴偏移, -30000, 30000, 100)
        if 好:
            self.弹幕.设置时间轴偏移(偏移)
            self._提示(f"🗨 弹幕偏移 {偏移:+d} ms")
            return
        透明, 好 = QInputDialog.getInt(self, "弹幕不透明度", "10–100：",
                                  int(self.弹幕.配置.不透明度 * 100), 10, 100, 5)
        if 好:
            self.弹幕.配置 = self.弹幕.配置.复制(不透明度=透明 / 100.0)
            self.弹幕.渲染器.设置配置(self.弹幕.配置)
            self._提示(f"🗨 弹幕不透明度 {透明}%")

    def 装载弹幕(self) -> None:
        """按当前片名找这一集的弹幕（Animeko → 弹弹play → 本地同名）。"""
        if self.引擎.输入 is None:
            self._提示("先播一个片子，再点「找弹幕」")
            return
        from ..danmaku.源 import 建默认源
        from ..danmaku.源.接口 import 素材信息
        地址 = str(self.引擎.输入.地址)
        # ⚠️ 必须先把文件名解析出"季/集"再送去匹配：只给文件名时匹配打分拿不到集号，
        #    分数压在 78 上下、还要人工确认，甚至可能选中特别篇（真机实测踩过）。
        路径 = Path(地址) if Path(地址).is_file() else None
        标题, 季号, 集号, 附属 = "", None, None, {}
        try:
            from ..scrape.命名解析 import 解析 as 解析文件名
            解析结果 = 解析文件名(Path(地址).name,
                            父目录名=路径.parent.name if 路径 else "",
                            季目录名=路径.parent.name if 路径 else "")
            标题 = 解析结果.标题 or ""
            季号, 集号 = 解析结果.季, 解析结果.集
            附属 = {"集标题": "", "季号": 季号, "集号": 集号}
        except Exception as 错:  # noqa: BLE001
            self._写日志(f"[弹幕] 文件名解析失败（不影响继续找）：{错}")
        素材 = 素材信息(路径=路径, 文件名=Path(地址).name,
                    时长秒=self.引擎.统计.总时长秒, 标题=标题,
                    季号=季号, 集号=集号, 额外=附属)
        self._提示("🗨 正在找弹幕…")
        应用 = QApplication.instance()
        if 应用 is not None:
            应用.processEvents()
        结果 = self.弹幕.装载(素材, 建默认源())
        if 结果.成功:
            self._提示(f"🗨 已装载 {结果.条数} 条（{结果.来源}）")
            return
        if 结果.候选们:
            名字们 = [c.可读() for c in 结果.候选们[:12]]
            选, 好 = QInputDialog.getItem(self, "选择匹配的节目",
                                       "没把握自动选，帮你列出来（分数越高越像）：",
                                       名字们, 0, False)
            if 好 and 选:
                采纳 = 结果.候选们[名字们.index(选)]
                装载 = self.弹幕.用候选装载(素材, 采纳, 建默认源(), 用户选定=True)
                if 装载.成功:
                    self._提示(f"🗨 已装载 {装载.条数} 条（{装载.来源}），并记住了这个匹配")
                else:
                    self._提示(f"🗨 {装载.说明 or '这个候选没取到弹幕'}")
                return
        self._提示(f"🗨 {结果.说明 or '没找到弹幕'}")

    # ---- 弹幕设置持久化（数据/弹幕设置.json）----

    def 弹幕设置路径(self) -> Path:
        return Path(__file__).resolve().parents[2] / "数据" / "弹幕设置.json"

    def 读弹幕设置(self) -> None:
        try:
            路径 = self.弹幕设置路径()
            if not 路径.is_file():
                return
            import json as _json
            数据 = _json.loads(路径.read_text(encoding="utf-8"))
            from ..danmaku.过滤 import 过滤规则, 过滤器
            from ..danmaku.模型 import 弹幕配置
            配置字段 = {k: v for k, v in (数据.get("配置") or {}).items()
                     if k in 弹幕配置.__dataclass_fields__}
            if 配置字段:
                self.弹幕.配置 = self.弹幕.配置.复制(**配置字段)
                self.弹幕.渲染器.设置配置(self.弹幕.配置)
            规则字段 = {k: v for k, v in (数据.get("规则") or {}).items()
                     if k in 过滤规则.__dataclass_fields__}
            if 规则字段:
                self.弹幕.设置过滤器(过滤器(过滤规则(**规则字段)))
            self._写日志("[弹幕] 已载入上次的弹幕设置")
        except Exception as 错:  # noqa: BLE001
            self._写日志(f"[弹幕] 读设置失败（用默认）：{错}")

    def 存弹幕设置(self) -> None:
        try:
            import json as _json
            from dataclasses import asdict
            路径 = self.弹幕设置路径()
            路径.parent.mkdir(parents=True, exist_ok=True)
            规则 = getattr(self.弹幕._过滤器, "规则", None)
            路径.write_text(_json.dumps(
                {"配置": asdict(self.弹幕.配置),
                 "规则": asdict(规则) if 规则 is not None else {}},
                ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as 错:  # noqa: BLE001
            self._写日志(f"[弹幕] 存设置失败：{错}")

    def 开弹幕设置(self) -> None:
        """弹幕设置面板（非模态），改完立刻生效并落盘。"""
        from PySide6.QtWidgets import QDialog
        from ..danmaku.过滤 import 过滤器, 过滤规则
        from .弹幕设置 import 弹幕设置面板
        窗 = getattr(self, "_弹幕设置窗", None)
        if 窗 is not None:
            窗.show()
            窗.raise_()
            return
        规则 = getattr(self.弹幕._过滤器, "规则", None) or 过滤规则()
        窗 = QDialog(self)
        窗.setWindowTitle("弹幕设置")
        窗.resize(560, 720)
        布局 = QVBoxLayout(窗)
        面板 = 弹幕设置面板(self.弹幕.配置, 规则, 窗)
        布局.addWidget(面板)

        def 配置变了(新配置):
            self.弹幕.设置显示(bool(新配置.显示))
            self.弹幕.配置 = 新配置
            self.弹幕.渲染器.设置配置(新配置)
            self.弹幕按钮.setChecked(bool(新配置.显示))
            self.存弹幕设置()
            self._提示("🗨 弹幕设置：" + 新配置.摘要())

        def 规则变了(新规则):
            self.弹幕.设置过滤器(过滤器(新规则))
            self.存弹幕设置()
            self._提示(f"🗨 过滤规则已更新（屏蔽词 {len(新规则.屏蔽词)} 条、"
                     f"正则 {len(新规则.正则们)} 条）")
        面板.配置变化.connect(配置变了)
        面板.规则变化.connect(规则变了)
        self._弹幕设置窗 = 窗
        窗.show()

    # ------------------------------------------------------------------ 缩略图预览

    def eventFilter(self, 对象, 事件):  # noqa: N802 - Qt 命名
        if 对象 is self.进度:
            if 事件.type() == 事件.Type.MouseMove:
                if self.引擎.统计.总时长秒 > 0:
                    # 先把小窗按光标位置弹出来（内容是"解码预览…"），用户立刻有反馈；
                    # 缩略图 180ms 去抖 + 后台线程解出来后再补进去 —— 原来只在图出来
                    # 之后才弹，网络片源解一帧要一会儿，用户会以为"悬停没反应"。
                    self._预览窗.弹出(self.进度)
                    self._预览定时器.start()
            elif 事件.type() in (事件.Type.Leave, 事件.Type.Hide):
                # 鼠标离开进度条 → 小窗立刻收起（原来那条常驻黑条是永远在的）
                self._收起预览()
        return super().eventFilter(对象, 事件)

    @property
    def 预览显示中(self) -> bool:
        """预览小窗现在弹着没有（自检/真机测试看它）。"""
        return bool(getattr(self, "_预览显示中", False))

    def _重排控制按钮(self) -> None:
        """让按钮区按**当前**宽度重排（尺寸没变时 Qt 不会再调 resizeEvent）。"""
        try:
            self._控制行.重排()
        except Exception:  # noqa: BLE001
            pass

    def _收起预览(self) -> None:
        self._预览定时器.stop()
        try:
            self.预览窗.收起()
        except Exception:  # noqa: BLE001
            pass
        self._预览显示中 = False

    def _要预览(self, _值: int) -> None:
        if self.引擎.统计.总时长秒 > 0:
            self._预览定时器.start()

    def _开始拖(self) -> None:
        self._拖动中 = True

    def _结束拖(self) -> None:
        self._拖动中 = False
        self._收起预览()
        总 = float(self.引擎.统计.总时长秒 or 0.0)
        if 总 > 0:
            self.跳转(总 * self.进度.value() / 1000.0)

    def _出预览图(self) -> None:
        """在**工作线程**里解一帧缩略图（界面线程里解码会卡界面）。"""
        if getattr(self, "_预览忙", False):
            return                      # 上一个还没解完：直接跳过，别叠线程（会崩）
        总 = self.引擎.统计.总时长秒
        if 总 <= 0 or self.引擎.输入 is None:
            return
        if self.预览仅本地 and not self._是本地源():
            # 说清楚为什么没预览，免得用户以为坏了（只在第一次说一遍）
            if not getattr(self, "_说过网络不预览", False):
                self._说过网络不预览 = True
                self._写日志("[播放] 网盘直链不做悬停预览：预览要另开一路连接，"
                          "会影响正在播的流（实测会导致黑屏/崩溃）。本地文件有预览。")
            return
        秒 = 总 * self.进度.value() / 1000.0
        键 = int(秒)
        if 键 in self._预览缓存:
            self._显示预览(self._预览缓存[键], 秒)
            return
        地址 = self.引擎.输入.地址
        头 = dict(self.会话.直链信息.get("headers") or {})

        def 干活():
            try:
                from ..player.缩略图 import 缩略图器
                # ⚠️ 锁住：这个解码器是**跨悬停复用**的，两个线程同时用它会把
                #    FFmpeg 的堆写坏（真机 coredump：崩在 avcodec_send_packet）。
                with self._预览锁:
                    器 = self._缩略图器 or 缩略图器(地址, 头, 320, self._写日志)
                    self._缩略图器 = 器
                    图 = 器.取图(秒)
                if 图 is not None:
                    self._预览缓存[键] = 图
                    if len(self._预览缓存) > 60:
                        self._预览缓存.pop(next(iter(self._预览缓存)))
                self._预览结果 = (键, 图, 秒)
            except Exception as 错:  # noqa: BLE001 - 解不出缩略图不该影响播放
                self._写日志(f"[播放] 缩略图失败：{错}")
                self._预览结果 = (键, None, 秒)
            finally:
                self._预览忙 = False

        import threading
        self._预览结果 = None
        self._预览忙 = True
        threading.Thread(target=干活, daemon=True, name="V2-缩略图").start()
        QTimer.singleShot(400, self._看预览结果)

    def _是本地源(self) -> bool:
        """当前播的是不是本地文件（决定能不能做悬停预览）。"""
        try:
            地址 = str(getattr(self.引擎.输入, "地址", "") or "")
            if not 地址:
                return False
            if 地址.startswith(("http://", "https://", "rtsp://", "rtmp://")):
                return False
            return Path(地址).is_file()
        except Exception:  # noqa: BLE001
            return False

    def _看预览结果(self) -> None:
        结果 = getattr(self, "_预览结果", None)
        if not 结果:
            return
        _键, 图, 秒 = 结果
        self._预览结果 = None
        if 图 is not None:
            self._显示预览(图, 秒)

    def _显示预览(self, 图, 秒: float) -> None:
        """把缩略图贴到进度条上方弹出来（不占常驻空间）。"""
        self.预览窗.设图(图, 时间文本(秒))
        self.预览窗.弹出(self.进度)
        self._预览显示中 = True

    # ------------------------------------------------------------------ 截图 / 收尾

    def 截图(self) -> str:
        目录 = Path(__file__).resolve().parents[2] / "数据" / "截图"
        目录.mkdir(parents=True, exist_ok=True)
        目标 = 目录 / f"V2_{time.strftime('%Y%m%d_%H%M%S')}.png"
        if self.会话.截图(str(目标)):
            self._提示(f"📷 已保存 {目标}")
            return str(目标)
        self._提示("📷 截图失败：还没有画面")
        return ""

    def 存档位置(self) -> None:
        try:
            统计 = self.引擎.统计
            if self.引擎.输入 is not None and 统计.当前时间秒 > 1.0:
                self.记录本.记位置(str(self.引擎.输入.地址), 统计.当前时间秒,
                                统计.总时长秒, Path(self.引擎.输入.地址).name)
                self.记录本.存()
                if 统计.总时长秒 > 0 and 统计.当前时间秒 >= 统计.总时长秒 - 2:
                    self.记录本.记看完(str(self.引擎.输入.地址))
                    self.记录本.存()
        except Exception:  # noqa: BLE001
            pass

    def 关闭(self) -> None:
        """收尾：停定时器、存位置与弹幕设置、关会话。"""
        for 定时 in (getattr(self, "_定时器", None), getattr(self, "_存档定时器", None)):
            try:
                if 定时 is not None:
                    定时.stop()
            except Exception:  # noqa: BLE001
                pass
        self.存档位置()
        self.存弹幕设置()
        try:
            self.会话.关闭()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ 每帧刷新

    def _刷新(self) -> None:
        统计 = self.引擎.统计
        序号 = self.引擎.取帧序号()
        if 序号 != self._帧序号:
            self._帧序号 = 序号
            帧 = self.引擎.取最新帧()
            if 帧 is not None:
                try:
                    self.视频.设置帧(帧.转QImage())
                except Exception:  # noqa: BLE001
                    pass
        try:
            self.视频.设置当前秒(统计.当前时间秒)
        except Exception:  # noqa: BLE001
            pass
        try:
            区域 = self.视频.目标矩形()
            if 区域.width() > 8 and 区域.height() > 8:
                self.弹幕.推进(统计.当前时间秒 * 1000.0, 区域.width(), 区域.height())
        except Exception:  # noqa: BLE001
            pass
        if not self._拖动中 and 统计.总时长秒 > 0:
            self.进度.setValue(int(min(1.0, 统计.当前时间秒 / 统计.总时长秒) * 1000))
        self.时间标签.setText(f"{时间文本(统计.当前时间秒)} / "
                          f"{时间文本(统计.总时长秒)}")
        状态 = self.会话.状态文本()
        if 状态 != self._上次状态:
            self._上次状态 = 状态
            self.播放状态变了.emit(状态)
        if self.引擎.是否结束():
            self.播放按钮.setText("▶ 重播")

    def _视频区变了(self, 宽: int, 高: int) -> None:
        """视频区尺寸变了 → 告诉引擎"界面只需要这么大"。

        只改**解码输出尺寸**（省掉每帧搬 25 MB），绝不改窗口尺寸。
        """
        if 宽 <= 0 or 高 <= 0:
            return
        try:
            self.引擎.设置输出尺寸(int(宽), int(高))
        except Exception:  # noqa: BLE001 - 只是优化，失败不该影响播放
            pass

    def resizeEvent(self, 事件):  # noqa: N802 - Qt 命名
        超级 = getattr(super(), "resizeEvent", None)
        if 超级 is not None:
            超级(事件)
        区域 = self.视频.size()
        self._视频区变了(区域.width(), 区域.height())
        self._重排控制按钮()          # 窗口一变宽窄，按钮立刻重排（该换行就换行）

    # ------------------------------------------------------------------ 键盘

    def keyPressEvent(self, 事件):  # noqa: N802 - Qt 命名
        键 = 事件.key()
        if 键 == Qt.Key.Key_Space:
            self.播放暂停()
            return
        if 键 == Qt.Key.Key_Escape and self.是全屏():
            self.要全屏.emit(False)
            return
        if 键 == Qt.Key.Key_Period:
            self.逐帧()
            return
        if 键 == Qt.Key.Key_BracketRight:
            self.倍速框.setCurrentIndex(min(self.倍速框.count() - 1,
                                        self.倍速框.currentIndex() + 1))
            return
        if 键 == Qt.Key.Key_BracketLeft:
            self.倍速框.setCurrentIndex(max(0, self.倍速框.currentIndex() - 1))
            return
        if 键 == Qt.Key.Key_PageDown:
            self.下一个()
            return
        if 键 == Qt.Key.Key_PageUp:
            self.上一个()
            return
        if 键 in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.相对跳转(-5.0 if 键 == Qt.Key.Key_Left else 5.0)
            return
        超级 = getattr(super(), "keyPressEvent", None)
        if 超级 is not None:
            超级(事件)
