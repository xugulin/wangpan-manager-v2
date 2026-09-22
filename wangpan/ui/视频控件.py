"""视频控件：**画面我们自己画**（这就是 V2 存在的理由）。

对比 V1（把窗口句柄交给 libvlc，让它往里画）：
* 不存在"嵌入失败 → 播放器自己开一个窗口"这种事 —— 画面数据在我们手里；
* 缩放/黑边/比例由这一层说了算（保持比例 + 居中 + 黑底）；
* 截图、缩略图、字幕叠加、滤镜都能在这层加（后面几步的活）。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QWidget

__all__ = ["视频控件"]


class 视频控件(QWidget):
    """显示最新一帧（保持比例、居中、黑底）。"""

    双击 = Signal()
    单击 = Signal()

    def __init__(self, 父=None) -> None:
        super().__init__(父)
        self.setMinimumSize(320, 180)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._图: Optional[QImage] = None
        self._原始帧 = None
        self._显示计数 = 0

    # ---------------- 对外 ----------------

    def 设置帧(self, 图: QImage) -> None:
        self._图 = 图
        self._显示计数 += 1
        self.update()

    def 清空(self) -> None:
        self._图 = None
        self._显示计数 = 0
        self.update()

    @property
    def 有画面(self) -> bool:
        return self._图 is not None

    # ---------------- 绘制 ----------------

    def 目标矩形(self) -> QRect:
        """把画面按比例放进控件（居中，留黑边）。"""
        区域 = self.rect()
        if self._图 is None or self._图.isNull():
            return 区域
        图宽, 图高 = self._图.width(), self._图.height()
        if 图宽 <= 0 or 图高 <= 0:
            return 区域
        缩放 = min(区域.width() / 图宽, 区域.height() / 图高)
        宽 = max(1, int(图宽 * 缩放))
        高 = max(1, int(图高 * 缩放))
        return QRect((区域.width() - 宽) // 2, (区域.height() - 高) // 2, 宽, 高)

    def paintEvent(self, _事件):  # noqa: N802 - Qt 命名
        画 = QPainter(self)
        画.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._图 is not None and not self._图.isNull():
            画.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            画.drawImage(self.目标矩形(), self._图)
        画.end()

    # ---------------- 交互 ----------------

    def mouseDoubleClickEvent(self, _事件):  # noqa: N802
        self.双击.emit()

    def mousePressEvent(self, _事件):  # noqa: N802
        self.单击.emit()
