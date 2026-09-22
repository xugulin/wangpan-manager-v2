"""视频控件：**画面我们自己画**（这就是 V2 存在的理由）。

对比 V1（把窗口句柄交给 libvlc，让它往里画）：
* 不存在"嵌入失败 → 播放器自己开一个窗口"这种事 —— 画面数据在我们手里；
* 缩放/黑边/比例由这一层说了算（保持比例 + 居中 + 黑底）；
* 截图、缩略图、字幕叠加、滤镜都能在这层加（后面几步的活）。

M4 加的字幕叠加也走同一条路：**同一张画布**上再画一层字，
所以"字幕窗口和视频窗口对不齐""全屏后字幕没了"这类问题结构上不存在。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QWidget

from ..subtitle import 画字幕

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
        # ---- M4 字幕叠加（默认不开：没有轨道时一个像素都不多画）----
        self._字幕轨道 = None
        self._字幕启用 = True
        self._当前秒 = 0.0

    # ---------------- 对外 ----------------

    def 设置帧(self, 图: QImage) -> None:
        self._图 = 图
        self._显示计数 += 1
        self.update()

    def 设置字幕(self, 轨道, 启用: bool = True) -> None:
        """挂上/摘掉一条字幕轨（``轨道`` 为 None = 不显示字幕）。

        传对象而不是传"当前该显示哪条"：控件自己拿着 ``_当前秒`` 去问轨道
        （:meth:`wangpan.subtitle.字幕轨道.取` 是二分，16ms 问一次也不心疼），
        界面那边就不用每帧算字幕、不会出现"字幕比画面慢一帧"的错位。
        """
        self._字幕轨道 = 轨道
        self._字幕启用 = bool(启用)
        self.update()

    def 设置当前秒(self, 秒: float) -> None:
        """播放进度（秒）。界面每帧喂一次，字幕按它取当前条目。"""
        try:
            self._当前秒 = max(0.0, float(秒 or 0.0))
        except (TypeError, ValueError):
            self._当前秒 = 0.0

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
        self._画字幕层()
        self._画弹幕层()

    def 设置弹幕控制器(self, 控制器) -> None:
        """接上弹幕控制器（引擎侧每帧推进；这里只负责画）。"""
        self._弹幕 = 控制器

    def _画弹幕层(self) -> None:
        """画弹幕：**位置由引擎按当前时间算**，这里只把结果贴出来。

        画在"视频区域"而不是整个控件：黑边里飘弹幕看着很奇怪。
        """
        控制器 = getattr(self, "_弹幕", None)
        if 控制器 is None or not getattr(控制器, "显示中", False):
            return
        区域 = self.目标矩形()
        if 区域.width() <= 8 or 区域.height() <= 8:
            return
        画 = QPainter(self)
        画.setClipRect(区域)
        画.translate(区域.x(), 区域.y())
        try:
            控制器.绘制(画, 区域.width(), 区域.height())
        finally:
            画.end()

    def _画字幕层(self) -> None:
        """画面画完之后**另起一个** QPainter 画字幕。

        为什么不在上面那个 QPainter 里画：同一个绘制设备同时只能有一个 QPainter，
        嵌套 ``begin`` 会直接失败（而且是"整屏都不画"这种难查的失败）。
        字幕出任何问题都只该"这一帧没字幕"，绝不能把画面绘制流程打断。
        """
        if not self._字幕启用 or self._字幕轨道 is None:
            return
        try:
            当前条目 = self._字幕轨道.取(self._当前秒)
            画字幕(self, 当前条目, self.size())
        except Exception:  # noqa: BLE001 - 绘制里抛异常会连带整帧黑掉，这里必须兜住
            return

    # ---------------- 交互 ----------------

    def mouseDoubleClickEvent(self, _事件):  # noqa: N802
        self.双击.emit()

    def mousePressEvent(self, _事件):  # noqa: N802
        self.单击.emit()
