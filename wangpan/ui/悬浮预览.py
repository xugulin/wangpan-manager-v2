"""进度条预览：**紧贴进度条的悬浮小窗**。

用户要求
========
> 视频播放器的预览画面区域长又粗影响观看视频，要求改为紧贴进度条预览位置的
> 悬浮小窗口显示预览图片。

原来的做法：在进度条**上方**常驻一条 90px 高的黑条（写着"鼠标停在进度条上可预览
画面"）。它一直占着画面下方 90px，不预览时也杵在那里 —— 就是用户说的"长又粗"。

现在改成：平时**什么都不显示**，鼠标在进度条上移动时，在**光标正上方**弹出一个
小窗（192×108 的预览图 + 一行时间码），像 B 站/YouTube 那样。移开鼠标就消失。

实现要点
========
* 窗口用 ``Qt.WindowType.ToolTip``：无边框、不抢焦点、不进任务栏 —— 正是"悬浮提示"
  该有的样子。用普通 `QWidget` 会在任务栏多出一个条目、点一下还会抢走焦点。
* ``WA_ShowWithoutActivating``：弹出时不许把主窗口的输入焦点抢走，否则鼠标一动
  进度条就"失焦"，滑块跟手的手感全没了。
* **贴边要收**：光标在最右端时小窗不能跑出屏幕（用户对独立播放窗口提过同样要求）。
  放在进度条上方放不下（贴着屏幕上沿）时改放**下方**。
* 只 `hide()` 不 `close()`：鼠标在进度条上来回移动会频繁弹出，反复建窗口会闪；
  一个窗口复用最稳。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

__all__ = ["悬浮预览窗"]

#: 预览图尺寸（16:9，够看清又不挡画面）
预览宽 = 192
预览高 = 108
#: 小窗与进度条的间距（像素）
间距 = 8


class 悬浮预览窗(QWidget):
    """贴在进度条上方的预览小窗（无边框、不抢焦点）。"""

    def __init__(self, 父=None) -> None:
        super().__init__(None, Qt.WindowType.ToolTip)
        # 给个标题：真机测试/排查时能在窗口列表里认出它（ToolTip 窗不显示标题栏）
        self.setWindowTitle("悬浮预览")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        布局 = QVBoxLayout(self)
        布局.setContentsMargins(4, 4, 4, 4)
        布局.setSpacing(2)
        self.图 = QLabel()
        self.图.setFixedSize(预览宽, 预览高)
        self.图.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.图.setStyleSheet("background:#000;color:#666;font-size:11px;")
        self.图.setText("解码预览…")
        布局.addWidget(self.图)
        self.时间 = QLabel("")
        self.时间.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.时间.setStyleSheet("color:#ddd;font-size:11px;")
        布局.addWidget(self.时间)
        self.setStyleSheet(
            "悬浮预览窗{background:#111;border:1px solid #555;border-radius:4px;}")

    # ------------------------------------------------------------------ 对外

    def 设图(self, 图, 时间文本: str = "") -> None:
        if 图 is None:
            self.图.setText("（这一处解不出画面）")
        else:
            self.图.setText("")
            self.图.setPixmap(QPixmap.fromImage(图.scaled(
                预览宽, 预览高,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)))
        self.时间.setText(str(时间文本 or ""))

    def 贴在(self, 锚点控件, 全局x: int | None = None) -> None:
        """贴在锚点控件（进度条）的上方，横向对齐光标位置。

        :param 全局x: 想要对齐的全局横坐标（默认取当前光标）。
        """
        try:
            左上 = 锚点控件.mapToGlobal(QPoint(0, 0))
            宽 = 锚点控件.width()
            高 = 锚点控件.height()
        except Exception:  # noqa: BLE001
            左上, 宽, 高 = QPoint(0, 0), 0, 0
        self.adjustSize()
        我的宽, 我的高 = self.width(), self.height()
        x = int(全局x if 全局x is not None else QCursor.pos().x())
        x = x - 我的宽 // 2
        y = 左上.y() - 我的高 - 间距                 # 默认放上方
        屏幕 = QGuiApplication.screenAt(QPoint(x, y)) or QGuiApplication.primaryScreen()
        区域 = 屏幕.availableGeometry() if 屏幕 is not None else None
        if 区域 is not None:
            if y < 区域.y():                          # 上面放不下 → 放下面
                y = 左上.y() + 高 + 间距
            x = max(区域.x(), min(x, 区域.x() + 区域.width() - 我的宽))
            y = max(区域.y(), min(y, 区域.y() + 区域.height() - 我的高))
        self.move(int(x), int(y))

    def 弹出(self, 锚点控件, 全局x: int | None = None) -> None:
        self.贴在(锚点控件, 全局x)
        if not self.isVisible():
            self.show()
        self.raise_()

    def 收起(self) -> None:
        if self.isVisible():
            self.hide()
