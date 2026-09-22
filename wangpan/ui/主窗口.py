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
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel,
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
        self.记录本 = 记录本()
        self.列表: list[str] = []          # 播放列表（本地文件或直链）
        self.列表序号 = -1
        self._帧序号 = -1
        self._拖动中 = False
        self._日志行: list[str] = []

        self.视频 = 视频控件()
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
        self.进度 = QSlider(Qt.Orientation.Horizontal)
        self.进度.setRange(0, 1000)
        self.进度.sliderPressed.connect(self._开始拖)
        self.进度.sliderReleased.connect(self._结束拖)
        条布局.addWidget(self.进度)
        行 = QHBoxLayout()
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
        try:
            self.引擎.停止()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.网盘页.关闭()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(事件)
