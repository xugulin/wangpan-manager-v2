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
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMainWindow,
                             QPushButton, QSlider, QVBoxLayout, QWidget)

from ..player.引擎 import 播放引擎, 播放状态
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
        self._帧序号 = -1
        self._拖动中 = False
        self._日志行: list[str] = []

        self.视频 = 视频控件()
        self.视频.双击.connect(self._切全屏)
        self.视频.单击.connect(self._切播放)

        中央 = QWidget()
        布局 = QVBoxLayout(中央)
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
        self.打开按钮 = QPushButton("📂 打开文件")
        self.打开按钮.clicked.connect(self._选文件)
        self.播放按钮 = QPushButton("▶ 播放")
        self.播放按钮.clicked.connect(self._切播放)
        行.addWidget(self.打开按钮)
        行.addWidget(self.播放按钮)
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
        self.截图按钮 = QPushButton("📷 截图")
        self.截图按钮.clicked.connect(self._截图)
        行.addWidget(self.截图按钮)
        self.全屏按钮 = QPushButton("⛶ 全屏")
        self.全屏按钮.clicked.connect(self._切全屏)
        行.addWidget(self.全屏按钮)
        条布局.addLayout(行)
        布局.addWidget(条)
        self.setCentralWidget(中央)

        self.状态 = self.statusBar()
        self.状态.showMessage("就绪：打开一个视频文件即可播放（画面由本程序自己绘制）")
        self._定时器 = QTimer(self)
        self._定时器.setInterval(16)
        self._定时器.timeout.connect(self._刷新)
        self._定时器.start()

    # ---------------- 对外 ----------------

    def 打开(self, 路径: str) -> bool:
        try:
            self.引擎.打开(str(路径))
        except Exception as 错:  # noqa: BLE001
            self.状态.showMessage(f"❌ 打不开：{错}")
            return False
        self.视频.清空()
        self.引擎.播放()
        self.播放按钮.setText("⏸ 暂停")
        self.setWindowTitle(f"网盘管理 V2 · {Path(路径).name}")
        self.状态.showMessage(f"▶ {self.引擎.统计.音视频}｜时长 "
                          f"{时间文本(self.引擎.统计.总时长秒)}")
        return True

    # ---------------- 槽 ----------------

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

    def _截图(self) -> None:
        目录 = Path("数据/截图")
        目录.mkdir(parents=True, exist_ok=True)
        目标 = 目录 / f"V2_{time.strftime('%Y%m%d_%H%M%S')}.png"
        if self.引擎.截图(str(目标)):
            self.状态.showMessage(f"📷 已保存 {目标}")
        else:
            self.状态.showMessage("📷 截图失败：还没有画面")

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
        if 事件.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            步 = -5.0 if 事件.key() == Qt.Key.Key_Left else 5.0
            self.引擎.跳转(max(0.0, self.引擎.统计.当前时间秒 + 步))
            return
        super().keyPressEvent(事件)

    def closeEvent(self, 事件):  # noqa: N802 - Qt 命名
        try:
            self.引擎.停止()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(事件)
