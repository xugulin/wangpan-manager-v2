"""主窗口：视频控件 + 控制条 + 状态栏（**没有第三方播放器界面**，全是我们自己的控件）。

为什么这样设计（V2 的核心主张）
================================
画面是 :class:`视频控件` 自己画的 → "视频跑到别的窗口里"这种事**结构上不可能发生**。
V1 踩过的坑（set_xwindow / set_hwnd / 输出模块平台差异 / 窗口就绪竞态 / 游离窗口巡检）
在 V2 里都不存在，因为它们都是"把画布交给别人"带来的。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel,
                             QMainWindow, QPushButton, QSlider, QTabWidget,
                             QVBoxLayout, QWidget)

from ..player.引擎 import 播放引擎, 播放状态
from ..pan.工厂 import 建注册表
from ..player.记录 import 记录本
from .网盘页 import 网盘页
from ..subtitle import (画字幕, 字幕轨道, 找同名字幕, 读字幕文件,
                      位置底部, 位置顶部)          # noqa: F401 - 字幕（M4）
from .视频控件 import 视频控件

__all__ = ["主窗口"]


def 时间文本(秒: float) -> str:
    秒 = max(0, int(秒 or 0))
    if 秒 >= 3600:
        return f"{秒 // 3600}:{秒 % 3600 // 60:02d}:{秒 % 60:02d}"
    return f"{秒 // 60:02d}:{秒 % 60:02d}"


class 主窗口(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("网盘管理 V2 · 自研播放内核")
        self.resize(1100, 680)
        self.引擎 = 播放引擎(日志回调=self._写日志)
        # ---- 弹幕控制器（引擎每帧推进；视频控件负责画）----
        from ..danmaku.引擎 import 弹幕控制器
        self.弹幕 = 弹幕控制器(日志回调=self._写日志)
        self.记录本 = 记录本()
        self.列表: list[str] = []          # 播放列表（本地文件或直链）
        self.列表序号 = -1
        self._帧序号 = -1
        self._拖动中 = False
        self._日志行: list[str] = []

        self.视频 = 视频控件()
        self.视频.设置弹幕控制器(self.弹幕)      # 弹幕画在画面之上（视频控件负责绘制）
        self.读弹幕设置()                       # 载入上次的观感/过滤规则
        self.视频.双击.connect(self._切全屏)
        self.视频.单击.connect(self._切播放)

        播放页 = QWidget()
        布局 = QVBoxLayout(播放页)
        布局.setContentsMargins(0, 0, 0, 0)
        布局.setSpacing(0)
        布局.addWidget(self.视频, 1)

        # ---- 控制条 ----
        条 = QWidget()
        条布局 = QVBoxLayout(条)
        条布局.setContentsMargins(8, 4, 8, 4)
        条布局.setSpacing(4)
        self.预览 = QLabel()
        self.预览.setFixedHeight(90)
        self.预览.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.预览.setStyleSheet("background:#111;color:#888;")
        self.预览.setText("（鼠标停在进度条上可预览画面）")
        条布局.addWidget(self.预览)
        self._预览缓存: dict[int, QImage] = {}
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
        行 = QHBoxLayout()
        self.弹幕按钮 = QPushButton("🗨 弹幕")
        self.弹幕按钮.setCheckable(True)
        self.弹幕按钮.setChecked(True)
        self.弹幕按钮.setToolTip("开关弹幕（右键：时间轴偏移 / 透明度）")
        self.弹幕按钮.clicked.connect(self._切弹幕)
        self.弹幕按钮.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.弹幕按钮.customContextMenuRequested.connect(self._弹幕菜单)
        行.addWidget(self.弹幕按钮)
        self.弹幕设置按钮 = QPushButton("⚙ 弹幕设置")
        self.弹幕设置按钮.setToolTip("字号/速度/不透明度/时间轴/过滤规则（可导入导出）")
        self.弹幕设置按钮.clicked.connect(self._开弹幕设置)
        行.addWidget(self.弹幕设置按钮)
        self.找弹幕按钮 = QPushButton("⬇ 找弹幕")
        self.找弹幕按钮.setToolTip("按文件名到弹弹play 找这一集的弹幕（需要 AppId；见 数据/弹幕源.json）")
        self.找弹幕按钮.clicked.connect(self._装载弹幕)
        行.addWidget(self.找弹幕按钮)
        self.字幕按钮 = QPushButton("💬 字幕")
        self.字幕按钮.setToolTip("加载同名字幕 / 开关字幕显示")
        self.字幕按钮.clicked.connect(self._切字幕)
        self.打开按钮 = QPushButton("📂 打开文件")
        self.打开按钮.clicked.connect(self._选文件)
        self.播放按钮 = QPushButton("▶ 播放")
        self.播放按钮.clicked.connect(self._切播放)
        行.addWidget(self.打开按钮)
        行.addWidget(self.播放按钮)
        行.addWidget(self.字幕按钮)
        self.时间标签 = QLabel("00:00 / 00:00")
        行.addWidget(self.时间标签)
        行.addStretch(1)
        行.addWidget(QLabel("🔊"))
        self.音量 = QSlider(Qt.Orientation.Horizontal)
        self.音量.setRange(0, 150)
        self.音量.setValue(100)
        self.音量.setFixedWidth(140)
        self.音量.valueChanged.connect(lambda v: self.引擎.设置音量(v / 100))
        行.addWidget(self.音量)
        self.字幕按钮2 = None
        self.上一集 = QPushButton("⏮ 上一集")
        self.上一集.clicked.connect(lambda: self._切列表(-1))
        self.下一集 = QPushButton("⏭ 下一集")
        self.下一集.clicked.connect(lambda: self._切列表(1))
        行.addWidget(self.上一集)
        行.addWidget(self.下一集)
        行.addWidget(QLabel("章节"))
        self.章节框 = QComboBox()
        self.章节框.setMinimumWidth(120)
        self.章节框.addItem("（无章节表）", -1)
        self.章节框.currentIndexChanged.connect(self._跳章节)
        行.addWidget(self.章节框)
        行.addWidget(QLabel("倍速"))
        self.倍速框 = QComboBox()
        for 值 in ("0.5", "0.75", "1", "1.25", "1.5", "2", "3"):
            self.倍速框.addItem(f"{值}×", float(值))
        self.倍速框.setCurrentIndex(2)
        self.倍速框.currentIndexChanged.connect(
            lambda _i: self.引擎.设置倍速(self.倍速框.currentData()))
        行.addWidget(self.倍速框)
        self.逐帧按钮 = QPushButton("⏯ 逐帧")
        self.逐帧按钮.setToolTip("暂停后每点一次前进一帧")
        self.逐帧按钮.clicked.connect(self._逐帧)
        行.addWidget(self.逐帧按钮)
        self.截图按钮 = QPushButton("📷 截图")
        self.截图按钮.clicked.connect(self._截图)
        行.addWidget(self.截图按钮)
        self.全屏按钮 = QPushButton("⛶ 全屏")
        self.全屏按钮.clicked.connect(self._切全屏)
        行.addWidget(self.全屏按钮)
        条布局.addLayout(行)
        布局.addWidget(条)

        # ---- 网盘页（M6）：左边网盘、右边传输，双击/播放按钮把直链交给播放器 ----
        self.注册表 = 建注册表()
        self.网盘页 = 网盘页(self.注册表)
        self.网盘页.要播放.connect(self.播放直链)
        self.标签 = QTabWidget()
        self.标签.addTab(播放页, "▶ 播放")
        self.标签.addTab(self.网盘页, "☁ 网盘")
        # ---- 媒体库（海报墙）：起不来也不能连累播放 ----
        try:
            from ..scrape.库 import 资料库
            from ..scrape.图片 import 图片缓存
            from .海报墙页 import 海报墙页
            self.资料库 = 资料库()
            self.图片缓存 = 图片缓存()
            self.海报墙 = 海报墙页(self.资料库, self.图片缓存)
            self.海报墙.要播放.connect(self._播放库里的文件)
            self.海报墙.要刮削.connect(self._刮削路径)
            self.海报墙.要手动匹配.connect(self._开手动匹配)
            self.标签.addTab(self.海报墙, "🎞 媒体库")
        except Exception as 错:  # noqa: BLE001
            self.资料库 = None
            self.海报墙 = None
            self._写日志(f"[媒体库] 初始化失败（不影响播放）：{错}")
        self.setCentralWidget(self.标签)

        self.状态 = self.statusBar()
        self.状态.showMessage("就绪：打开一个视频文件即可播放（画面由本程序自己绘制）")
        self._定时器 = QTimer(self)
        self._定时器.setInterval(16)
        self._定时器.timeout.connect(self._刷新)
        self._定时器.start()
        self._存档定时器 = QTimer(self)          # 每 5 秒把播放位置落盘
        self._存档定时器.setInterval(5000)
        self._存档定时器.timeout.connect(self._存档位置)
        self._存档定时器.start()

    # ---------------- 对外 ----------------

    def 打开(self, 路径: str, 续播: bool = True) -> bool:
        try:
            self.引擎.打开(str(路径))
        except Exception as 错:  # noqa: BLE001
            self.状态.showMessage(f"❌ 打不开：{错}")
            return False
        self.视频.清空()
        self._预览缓存 = {}
        self._缩略图器 = None
        # M4：同名字幕自动加载（影片.chs.srt / 影片.ass 等）
        self._字幕们: list = []
        self._字幕序号 = -1
        try:
            self._字幕们 = 找同名字幕(str(路径))
        except Exception:  # noqa: BLE001
            self._字幕们 = []
        if self._字幕们:
            self._字幕序号 = 0
            self.视频.设置字幕(self._字幕们[0], True)
            self.状态.showMessage(f"💬 已加载字幕：{self._字幕们[0].名字}")
        else:
            self.视频.设置字幕(None, False)
        self.引擎.播放()
        self.播放按钮.setText("⏸ 暂停")
        self._装章节(str(路径))
        # 自动加载"同目录同名弹幕文件"（本地优先，不联网）
        try:
            from ..danmaku.源.本地 import 读同名字幕弹幕
            本地弹幕 = 读同名字幕弹幕(Path(路径))
            if 本地弹幕 is not None and len(本地弹幕):
                self.弹幕.装载本地(本地弹幕, "本地同名")
                self.状态.showMessage(f"🗨 已加载本地弹幕 {len(本地弹幕)} 条")
        except Exception:  # noqa: BLE001
            pass
        # 有逐集记忆就直接装好（不联网、不搜索）：老片子重开时弹幕应当自动就位。
        # 没记忆也不自动搜（免得每次打开都去打人家接口），让用户点「⬇ 找弹幕」。
        try:
            记录 = self.弹幕.记忆.取(Path(路径))
            if 记录 is not None and 记录.标识:
                from ..danmaku.源.接口 import 素材信息
                集号 = int(记录.集号) if str(记录.集号).isdigit() else None
                装载 = self.弹幕.装载(素材信息(
                    路径=Path(路径), 文件名=Path(路径).name,
                    时长秒=self.引擎.统计.总时长秒, 标题=记录.标题 or "", 集号=集号))
                if 装载.成功:
                    self.状态.showMessage(f"🗨 从记忆自动装好 {装载.条数} 条"
                                     f"（{记录.标题} {记录.集标题}）")
        except Exception as 错:  # noqa: BLE001
            self._写日志(f"[弹幕] 用记忆装载失败（不影响播放）：{错}")
        # M5：记住"看过哪儿"，下次接着播
        try:
            时长 = self.引擎.统计.总时长秒
            self.记录本.记开始(str(路径), 时长, Path(路径).name)
            位置 = self.记录本.续播位置(str(路径)) if 续播 else 0.0
            if 位置 > 1.0:
                self.引擎.跳转(位置)
                self.状态.showMessage(f"⏩ 从上次看到的位置继续：{时间文本(位置)}")
        except Exception:  # noqa: BLE001
            pass
        self.setWindowTitle(f"网盘管理 V2 · {Path(路径).name}")
        self._建列表(str(路径))
        self.状态.showMessage(f"▶ {self.引擎.统计.音视频}｜时长 "
                          f"{时间文本(self.引擎.统计.总时长秒)}")
        return True

    # ---------------- 槽 ----------------

    def 播放直链(self, 地址: str, 请求头: dict | None = None) -> bool:
        """播一路直链（网盘页/外部调用）。"""
        try:
            self.引擎.打开(地址, 请求头=请求头 or {})
        except Exception as 错:  # noqa: BLE001
            self.状态.showMessage(f"❌ 打不开：{错}")
            return False
        self.视频.清空()
        self._字幕们 = []
        self._字幕序号 = -1
        self.引擎.播放()
        self.播放按钮.setText("⏸ 暂停")
        self.标签.setCurrentIndex(0)
        self.状态.showMessage(f"▶ {self.引擎.统计.音视频}")
        return True

    def _选文件(self) -> None:
        路径, _ = QFileDialog.getOpenFileName(
            self, "选一个视频文件", str(Path.home()),
            "视频 (*.mp4 *.mkv *.mov *.webm *.avi *.ts *.flv);;所有文件 (*)")
        if 路径:
            self.打开(路径)

    def _切播放(self) -> None:
        if self.引擎.输入 is None:
            self._选文件()
            return
        if self.引擎.状态 == 播放状态.播放中:
            self.引擎.暂停切换()
            self.播放按钮.setText("▶ 继续")
        else:
            self.引擎.播放()
            self.播放按钮.setText("⏸ 暂停")

    def _切全屏(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.全屏按钮.setText("⛶ 全屏")
        else:
            self.showFullScreen()
            self.全屏按钮.setText("🗗 退出全屏")

    def _开始拖(self) -> None:
        self._拖动中 = True

    def _结束拖(self) -> None:
        self._拖动中 = False
        总 = self.引擎.统计.总时长秒
        if 总 > 0:
            self.引擎.跳转(总 * self.进度.value() / 1000.0)

    def _装章节(self, 路径: str) -> None:
        """有章节表就把章节填进下拉（没有就显示"无章节表"，不留空控件骗人）。"""
        self.章节框.blockSignals(True)
        self.章节框.clear()
        章节们 = self.引擎.章节们()
        if not 章节们:
            self.章节框.addItem("（无章节表）", -1)
        else:
            for 项 in 章节们:
                self.章节框.addItem(f"{时间文本(项.起始秒)} {项.标题}", 项.序号)
        self.章节框.blockSignals(False)

    def _跳章节(self, _序号: int) -> None:
        值 = self.章节框.currentData()
        if isinstance(值, int) and 值 >= 0:
            self.引擎.跳章节(值)

    def _建列表(self, 路径: str) -> None:
        """本地文件：把同目录的视频排成播放列表（按文件名排序）。"""
        目标 = Path(路径)
        if not 目标.is_file():
            self.列表, self.列表序号 = [路径], 0
            return
        后缀 = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts", ".flv", ".m4v", ".wmv"}
        兄弟们 = sorted(p for p in 目标.parent.iterdir()
                     if p.is_file() and p.suffix.lower() in 后缀)
        自己 = [p for p in 兄弟们 if p.resolve() == 目标.resolve()]
        self.列表 = [str(p) for p in (兄弟们 or 自己 or [目标])]
        self.列表序号 = self.列表.index(str(目标)) if str(目标) in self.列表 else 0

    def _切列表(self, 步: int) -> None:
        if not self.列表:
            return
        self.列表序号 = max(0, min(len(self.列表) - 1, self.列表序号 + 步))
        self.打开(self.列表[self.列表序号])

    def _逐帧(self) -> None:
        if self.引擎.逐帧():
            self.状态.showMessage(f"⏯ 逐帧：{self.引擎.统计.当前时间秒:.3f}s")
        else:
            self.状态.showMessage("⏯ 逐帧：没有可前进的帧（先暂停或先播放一下）")

    def _切字幕(self) -> None:
        """有多个同名字幕就轮换；一个都没有就手动选一个；都没有就提示。"""
        轨道们 = getattr(self, "_字幕们", []) or []
        if not 轨道们:
            路径, _ = QFileDialog.getOpenFileName(
                self, "选一个字幕文件", str(Path.home()),
                "字幕 (*.srt *.ass *.ssa *.vtt);;所有文件 (*)")
            if not 路径:
                return
            try:
                轨道 = 读字幕文件(路径)
            except Exception as 错:  # noqa: BLE001
                self.状态.showMessage(f"❌ 读字幕失败：{错}")
                return
            self._字幕们 = [轨道]
            self._字幕序号 = 0
        else:
            self._字幕序号 = (self._字幕序号 + 1) % (len(轨道们) + 1)
        if self._字幕序号 >= len(轨道们):
            self.视频.设置字幕(None, False)
            self.状态.showMessage("💬 已关闭字幕")
            return
        轨道 = 轨道们[self._字幕序号]
        self.视频.设置字幕(轨道, True)
        self.状态.showMessage(f"💬 字幕：{轨道.名字}"
                          f"（{len(轨道.条目们)} 条）")

    def eventFilter(self, 对象, 事件):  # noqa: N802 - Qt 命名
        """鼠标在进度条上移动 → 起一个"防抖"定时器去解缩略图（不要每像素解一帧）。"""
        if 对象 is self.进度 and 事件.type() == 事件.Type.MouseMove:
            if self.引擎.统计.总时长秒 > 0:
                self._要预览(self.进度.value())
        return super().eventFilter(对象, 事件)

    def _要预览(self, _值: int) -> None:
        if self.引擎.统计.总时长秒 > 0:
            self._预览定时器.start()          # 防抖：连着动只解最后一次

    def _出预览图(self) -> None:
        """在**工作线程**里解一帧缩略图（绝不能在界面线程里解码，会卡界面）。"""
        总 = self.引擎.统计.总时长秒
        if 总 <= 0 or self.引擎.输入 is None:
            return
        秒 = 总 * self.进度.value() / 1000.0
        键 = int(秒)
        if 键 in self._预览缓存:
            self._显示预览(self._预览缓存[键], 秒)
            return
        地址 = self.引擎.输入.地址
        头 = dict(getattr(self.引擎, "_请求头", {}) or {})

        def 干活():
            from ..player.缩略图 import 缩略图器
            器 = self._缩略图器 or 缩略图器(地址, 头, 320, self._写日志)
            self._缩略图器 = 器
            图 = 器.取图(秒)
            if 图 is not None:
                self._预览缓存[键] = 图
                if len(self._预览缓存) > 60:
                    self._预览缓存.pop(next(iter(self._预览缓存)))
            self._预览结果 = (键, 图, 秒)

        import threading
        self._预览结果 = None
        threading.Thread(target=干活, daemon=True, name="V2-缩略图").start()
        QTimer.singleShot(400, self._看预览结果)

    def _看预览结果(self) -> None:
        结果 = getattr(self, "_预览结果", None)
        if not 结果:
            return
        _键, 图, 秒 = 结果
        self._预览结果 = None
        if 图 is not None:
            self._显示预览(图, 秒)

    def _显示预览(self, 图, 秒: float) -> None:
        from PySide6.QtGui import QPixmap
        缩放 = 图.scaledToHeight(84, Qt.TransformationMode.SmoothTransformation)
        self.预览.setPixmap(QPixmap.fromImage(缩放))
        self.预览.setText("")
        self.状态.showMessage(f"预览 {时间文本(秒)}")

    # ---------------- 弹幕 ----------------

    def _切弹幕(self) -> None:
        self.弹幕.设置显示(self.弹幕按钮.isChecked())
        self.状态.showMessage("🗨 弹幕：" + ("开" if self.弹幕.显示中 else "关")
                          + ("｜" + self.弹幕.摘要() if self.弹幕.有弹幕 else ""))

    def _弹幕菜单(self) -> None:
        """右键弹幕按钮：调时间轴偏移 / 透明度（弹幕跟画面对不上时最常用）。"""
        from PySide6.QtWidgets import QInputDialog
        偏移, 好 = QInputDialog.getInt(
            self, "弹幕时间轴偏移", "弹幕相对视频平移（毫秒，负数=弹幕提前）：",
            self.弹幕.时间轴偏移, -30000, 30000, 100)
        if 好:
            self.弹幕.设置时间轴偏移(偏移)
            self.状态.showMessage(f"🗨 弹幕偏移 {偏移:+d} ms")
            return
        透明, 好 = QInputDialog.getInt(self, "弹幕不透明度", "10–100：",
                                  int(self.弹幕.配置.不透明度 * 100), 10, 100, 5)
        if 好:
            self.弹幕.配置 = self.弹幕.配置.复制(不透明度=透明 / 100.0)
            self.弹幕.渲染器.设置配置(self.弹幕.配置)
            self.状态.showMessage(f"🗨 弹幕不透明度 {透明}%")

    def _装载弹幕(self) -> None:
        """按当前正在播的文件名到弹弹play 找弹幕（找不到就给候选让人选）。"""
        if self.引擎.输入 is None:
            self.状态.showMessage("先播一个片子，再点「找弹幕」")
            return
        from ..danmaku.源 import 建默认源
        from ..danmaku.源.接口 import 素材信息
        地址 = str(self.引擎.输入.地址)
        # ⚠️ 必须先把文件名解析出"季/集"再送去匹配：
        #    只给文件名时，匹配打分拿不到集号 → 分数压在 78 上下、还要人工确认，
        #    而且可能选中特别篇（真机实测：S01E01 被选成了 sort=0 的 SP）。
        路径 = Path(地址) if Path(地址).is_file() else None
        标题 = ""
        季号 = 集号 = None
        附属: dict = {}
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
        self._写日志(f"[弹幕] 素材：{Path(地址).name}｜标题「{标题}」"
                   f"｜季 {季号}｜集 {集号}")
        self.状态.showMessage("🗨 正在找弹幕…")
        应用 = QApplication.instance()
        if 应用 is not None:
            应用.processEvents()
        结果 = self.弹幕.装载(素材, 建默认源())
        if 结果.成功:
            self.状态.showMessage(f"🗨 已装载 {结果.条数} 条（{结果.来源}）")
            return
        if 结果.候选们:
            from PySide6.QtWidgets import QInputDialog
            名字们 = [c.可读() for c in 结果.候选们[:12]]
            选, 好 = QInputDialog.getItem(self, "选择匹配的节目",
                                       "没把握自动选，帮你列出来（分数越高越像）：",
                                       名字们, 0, False)
            if 好 and 选:
                # 走控制器的用户选定通道：取弹幕并记住这个选择（下次打开自动装好）
                采纳 = 结果.候选们[名字们.index(选)]
                装载 = self.弹幕.用候选装载(素材, 采纳, 建默认源(), 用户选定=True)
                if 装载.成功:
                    self.状态.showMessage(f"🗨 已装载 {装载.条数} 条（{装载.来源}），并记住了这个匹配")
                else:
                    self.状态.showMessage(f"🗨 {装载.说明 or '这个候选没取到弹幕'}")
                return
        self.状态.showMessage(f"🗨 {结果.说明 or '没找到弹幕'}")

    def _刮削路径(self, 路径: str, 强制标识: str = "",
               强制类型=None) -> None:
        """刮一个目录/文件：在**后台线程**里跑，界面只显示进度（别卡住 UI）。

        为什么用线程 + 进度框：刮削要联网、要下图，几秒钟到几分钟都有可能；
        放在界面线程里会整个卡死（我们字幕/截图都踩过"界面线程里干重活"的坑）。
        """
        from PySide6.QtCore import QThread
        from PySide6.QtWidgets import QMessageBox, QProgressDialog
        from ..scrape.库 import 资料库
        from ..scrape.图片 import 图片缓存
        from ..scrape.服务 import 刮削服务, 刮削设置
        from ..scrape.tmdb import TMDB客户端, TMDB配置
        if getattr(self, "资料库", None) is None:
            return
        客户端 = None
        try:
            配置 = TMDB配置.从环境与配置()
            if 配置.token or 配置.api_key:
                客户端 = TMDB客户端(配置)
        except Exception as 错:  # noqa: BLE001
            self._写日志(f"[刮削] TMDB 客户端初始化失败：{错}")
        if 客户端 is None:
            QMessageBox.information(
                self, "还没有配置 TMDB",
                "刮削需要 TMDB 的 API Key（免费）：\n\n"
                "1. 到 https://www.themoviedb.org/settings/api 申请；\n"
                "2. 把 token 写进 数据/刮削.json（{\"tmdb\": {\"token\": \"…\"}}），\n"
                "   或设环境变量 V2_TMDB_TOKEN。\n\n"
                "没配置也能用：扫描 + 本地 NFO/图片仍然会入库。")
        进度框 = QProgressDialog("正在扫描…", "取消", 0, 0, self)
        进度框.setWindowTitle("刮削中")
        进度框.setMinimumDuration(0)
        进度框.show()
        结果盒: dict = {}

        class 干活(QThread):
            def run(自己):
                try:
                    库 = 资料库()
                    缓存 = 图片缓存()
                    服务 = 刮削服务(
                        库, 客户端, 缓存, 刮削设置(),
                        进度回调=lambda p: 结果盒.setdefault("进度", p.摘要()),
                        日志回调=self._写日志)
                    服务.扫库([路径])
                    if 强制标识:
                        结果盒["结果"] = 服务.刮路径(路径, 强制标识, 强制类型)
                    else:
                        结果盒["结果"] = 服务.续跑()
                    结果盒["统计"] = 库.统计().摘要()
                    服务.关闭()
                    库.关闭()
                except Exception as 错:  # noqa: BLE001
                    结果盒["错误"] = str(错)

        线程 = 干活(self)
        self._刮削线程 = 线程
        定时 = QTimer(self)
        定时.setInterval(400)
        定时.timeout.connect(lambda: 进度框.setLabelText(
            str(结果盒.get("进度") or "正在扫描…")))
        定时.start()
        进度框.canceled.connect(lambda: self._写日志("[刮削] 用户取消（本批跑完会停）"))

        def 收尾():
            定时.stop()
            进度框.close()
            if self.海报墙 is not None:
                self.海报墙.刷新()
            结果 = 结果盒.get("结果")
            self.状态.showMessage("🎞 刮削完成：" + (结果.摘要() if 结果 else
                                              str(结果盒.get("错误") or "无结果"))
                             + f"｜{结果盒.get('统计', '')}")
        线程.finished.connect(收尾)
        线程.start()

    # ---------------- 弹幕设置（持久化到 数据/弹幕设置.json）----------------

    def _弹幕设置路径(self) -> Path:
        return Path(__file__).resolve().parents[2] / "数据" / "弹幕设置.json"

    def 读弹幕设置(self) -> None:
        """启动时把上次的观感/过滤规则装上（坏了就用默认，不影响播放）。"""
        try:
            路径 = self._弹幕设置路径()
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
            路径 = self._弹幕设置路径()
            路径.parent.mkdir(parents=True, exist_ok=True)
            规则 = getattr(self.弹幕._过滤器, "规则", None)
            路径.write_text(_json.dumps(
                {"配置": asdict(self.弹幕.配置),
                 "规则": asdict(规则) if 规则 is not None else {}},
                ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as 错:  # noqa: BLE001
            self._写日志(f"[弹幕] 存设置失败：{错}")

    def _开弹幕设置(self) -> None:
        """弹幕设置面板（非模态窗口），改完立刻生效并落盘。"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout
        from ..danmaku.过滤 import 过滤器, 过滤规则
        from .弹幕设置 import 弹幕设置面板
        if getattr(self, "_弹幕设置窗", None) is not None:
            self._弹幕设置窗.show()
            self._弹幕设置窗.raise_()
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
            self.状态.showMessage("🗨 弹幕设置：" + 新配置.摘要())

        def 规则变了(新规则):
            self.弹幕.设置过滤器(过滤器(新规则))
            self.存弹幕设置()
            self.状态.showMessage(f"🗨 过滤规则已更新（屏蔽词 {len(新规则.屏蔽词)} 条、"
                             f"正则 {len(新规则.正则们)} 条）")
        面板.配置变化.connect(配置变了)
        面板.规则变化.connect(规则变了)
        self._弹幕设置窗 = 窗
        窗.show()

    # ---------------- 手动匹配（TMDB 搜不到/匹配错时用）----------------

    def _开手动匹配(self, 媒体id: int) -> None:
        from .手动匹配 import 手动匹配对话框
        from .手动资料 import 手动资料对话框
        条目 = self.资料库.取媒体(int(媒体id)) if self.资料库 is not None else None
        类型 = 条目.类型 if 条目 is not None else None
        客户端 = self._建TMDB客户端()
        对话 = 手动匹配对话框(客户端, (条目.标题 if 条目 else "") or "",
                        (条目.年份 if 条目 else None), 类型 or 媒体类型.电影, self)
        要手动 = {"要": False}

        def 手动():
            要手动["要"] = True
        对话.要手动填写.connect(手动)
        对话.exec()
        if 要手动["要"]:
            self._开手动资料(媒体id)
            return
        选择 = 对话.取选择()
        if 选择 is None or not 选择.标识:
            return
        文件 = self._找媒体主文件(媒体id)
        if not 文件:
            self.状态.showMessage("🎞 这条资料没有对应的本地文件，改不了")
            return
        self._写日志(f"[刮削] 手动指定 {选择.可读()}")
        self._刮削路径(str(文件), 强制标识=选择.标识, 强制类型=选择.类型)

    def _开手动资料(self, 媒体id: int) -> None:
        """手动填写/修改资料（**不需要网络**）。"""
        from .手动资料 import 手动资料对话框
        if self.资料库 is None:
            return
        对话 = 手动资料对话框(self.资料库, self.图片缓存,
                        int(媒体id) if 媒体id else None, self)
        if 对话.exec() and 对话.取条目() is not None:
            新id = int(媒体id) if 媒体id else None
            if self.海报墙 is not None:
                self.海报墙.刷新()
            self.状态.showMessage("🎞 资料已保存（手动填写）"
                             + (f"，媒体 id {新id}" if 新id else ""))

    def _找媒体主文件(self, 媒体id: int) -> str:
        try:
            文件们 = self.资料库.文件们(int(媒体id))
            for 行 in 文件们:
                if 行["是主文件"]:
                    return str(行["路径"])
            return str(文件们[0]["路径"]) if 文件们 else ""
        except Exception:  # noqa: BLE001
            return ""

    def _建TMDB客户端(self):
        try:
            from ..scrape.tmdb import TMDB客户端, TMDB配置
            配置 = TMDB配置.从环境与配置()
            if not (配置.token or 配置.api_key):
                return None
            return TMDB客户端(配置)
        except Exception as 错:  # noqa: BLE001
            self._写日志(f"[刮削] 建 TMDB 客户端失败：{错}")
            return None

    def _播放库里的文件(self, 路径: str) -> None:
        self.标签.setCurrentIndex(0)
        self.打开(路径)

    def _截图(self) -> None:
        目录 = Path("数据/截图")
        目录.mkdir(parents=True, exist_ok=True)
        目标 = 目录 / f"V2_{time.strftime('%Y%m%d_%H%M%S')}.png"
        if self.引擎.截图(str(目标)):
            self.状态.showMessage(f"📷 已保存 {目标}")
        else:
            self.状态.showMessage("📷 截图失败：还没有画面")

    def _存档位置(self) -> None:
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

    def _写日志(self, 文本: str) -> None:
        self._日志行.append(文本)
        del self._日志行[:-200]

    def resizeEvent(self, 事件):  # noqa: N802 - Qt 命名
        """窗口尺寸变了就告诉解码器（按显示尺寸缩放 = 省掉 4K 全尺寸拷贝）。"""
        super().resizeEvent(事件)
        区域 = self.视频.size()
        if 区域.width() > 0 and 区域.height() > 0:
            self.引擎.设置输出尺寸(区域.width(), 区域.height())

    def _刷新(self) -> None:
        self.引擎.统计.总时长秒 = self.引擎.统计.总时长秒 or 0.0
        序号 = self.引擎.取帧序号()
        if 序号 != self._帧序号:
            self._帧序号 = 序号
            帧 = self.引擎.取最新帧()
            if 帧 is not None:
                try:
                    self.视频.设置帧(帧.转QImage())
                except Exception:  # noqa: BLE001
                    pass
        统计 = self.引擎.统计
        try:
            self.视频.设置当前秒(统计.当前时间秒)
        except Exception:  # noqa: BLE001
            pass
        try:
            区域 = self.视频.目标矩形()
            if 区域.width() > 8 and 区域.height() > 8:
                self.弹幕.推进(统计.当前时间秒 * 1000.0,
                            区域.width(), 区域.height())
        except Exception:  # noqa: BLE001
            pass
        if not self._拖动中 and 统计.总时长秒 > 0:
            self.进度.setValue(int(min(1.0, 统计.当前时间秒 / 统计.总时长秒) * 1000))
        self.时间标签.setText(f"{时间文本(统计.当前时间秒)} / "
                          f"{时间文本(统计.总时长秒)}")
        self.状态.showMessage(统计.摘要() + f"｜{统计.音视频}")
        if self.引擎.是否结束():
            self.播放按钮.setText("▶ 重播")

    # ---------------- 键盘 ----------------

    def keyPressEvent(self, 事件):  # noqa: N802 - Qt 命名
        if 事件.key() == Qt.Key.Key_Space:
            self._切播放()
            return
        if 事件.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self._切全屏()
            return
        if 事件.key() == Qt.Key.Key_Period:          # . 逐帧
            self._逐帧()
            return
        if 事件.key() == Qt.Key.Key_BracketRight:    # ] 加速
            self.倍速框.setCurrentIndex(min(self.倍速框.count() - 1,
                                        self.倍速框.currentIndex() + 1))
            return
        if 事件.key() == Qt.Key.Key_BracketLeft:     # [ 减速
            self.倍速框.setCurrentIndex(max(0, self.倍速框.currentIndex() - 1))
            return
        if 事件.key() == Qt.Key.Key_PageDown:
            self._切列表(1)
            return
        if 事件.key() == Qt.Key.Key_PageUp:
            self._切列表(-1)
            return
        if 事件.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            步 = -5.0 if 事件.key() == Qt.Key.Key_Left else 5.0
            self.引擎.跳转(max(0.0, self.引擎.统计.当前时间秒 + 步))
            return
        super().keyPressEvent(事件)

    def closeEvent(self, 事件):  # noqa: N802 - Qt 命名
        self._存档位置()                     # 关窗再存一次（别丢最后几秒）
        self.存弹幕设置()                     # 弹幕观感/过滤规则也存一下
        try:
            self.引擎.停止()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.网盘页.关闭()
        except Exception:  # noqa: BLE001
            pass
        try:
            # ⚠️ 顺序要紧：**先停海报墙的工作线程，再关库**。
            #    QThread 还在跑就被析构 = 进程直接 abort（真机退出时崩过：
            #    "QThread: Destroyed while thread is still running"）。
            if getattr(self, "海报墙", None) is not None:
                self.海报墙.关闭()
        except Exception:  # noqa: BLE001
            pass
        try:
            线 = getattr(self, "_刮削线程", None)
            if 线 is not None and 线.isRunning():
                线.wait(3000)
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.资料库 is not None:
                self.资料库.关闭()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(事件)
