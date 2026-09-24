"""会**自动换行**的按钮区（放不下就换行，所有按钮永远看得见、点得到）。

用户真机反馈
============
> 进度条下面的那排按钮没有显示完，还有一些按钮因为超出 GUI 不能（显示）。

原来那排按钮包在横向滚动区里、高度只按一行给，横向滚动条被压得几乎看不见 ——
"能滚动"不等于用户找得到。

为什么**不用自定义 QLayout** 做换行（这条踩过）
==============================================
先写了个 `QLayout` 子类做流动布局，并在 `setGeometry` 里顺手把父控件的最小高度顶到位。
结果和 Qt 的尺寸协商**打起来了**：Qt 一会儿用 120px 宽问一次、一会儿用 1264px 问一次，
布局在"11 行"和"2 行"之间来回振荡，最后停在 11 行（调试打印实测）。

所以这里改成**最笨也最确定**的做法：按钮自己摆坐标（`resizeEvent` 里重排），
控件高度用 `setFixedHeight(行数 × 行高)` 一次算清 —— 不参与任何 hint 协商。
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QSizePolicy, QWidget

__all__ = ["换行按钮区", "让高度随行数"]

#: 行高不足时的兜底
默认行高 = 32


class 换行按钮区(QWidget):
    """把按钮按可用宽度一行行摆好，放不下就换行。"""

    def __init__(self, 父=None, 间距: int = 6, 行距: int = 6) -> None:
        super().__init__(父)
        self.间距 = max(0, int(间距))
        self.行距 = max(0, int(行距))
        self.控件们: list[QWidget] = []
        self.setMinimumWidth(0)

    # ------------------------------------------------------------------ 对外

    def addWidget(self, 控件: QWidget, *_, **__) -> None:  # noqa: N802
        self.加(控件)

    def insertWidget(self, 下标: int, 控件: QWidget, *_, **__) -> None:  # noqa: N802
        下标 = max(0, min(int(下标), len(self.控件们)))
        self.控件们.insert(下标, 控件)
        控件.setParent(self)
        控件.show()
        self.重排()

    def addStretch(self, *_, **__) -> None:  # noqa: N802 - 换行区里弹簧没意义
        return None

    def indexOf(self, 控件: QWidget) -> int:  # noqa: N802
        try:
            return self.控件们.index(控件)
        except ValueError:
            return -1

    def count(self) -> int:
        return len(self.控件们)

    def 加(self, 控件: QWidget) -> None:
        self.控件们.append(控件)
        控件.setParent(self)
        控件.show()
        self.重排()

    # ------------------------------------------------------------------ 摆位

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, self.height() or 默认行高)

    def resizeEvent(self, 事件):  # noqa: N802 - Qt 命名
        超级 = getattr(super(), "resizeEvent", None)
        if 超级 is not None:
            超级(事件)
        self.重排()

    def 重排(self) -> None:
        """按当前宽度摆放，并把自身高度定成"行数 × 行高"。"""
        宽 = max(1, self.width())
        x = 0
        y = 0
        行高 = 0
        可见 = [w for w in self.控件们 if w is not None and not w.isHidden()]
        for 控件 in 可见:
            尺 = 控件.sizeHint()
            高 = max(默认行高, int(尺.height()))
            if x > 0 and x + 尺.width() > 宽:
                x = 0
                y += 行高 + self.行距
                行高 = 0
            # ⚠️ 横向"可伸展"的控件（倍速滑块就是）不能按 sizeHint 给宽度，
            #    否则它会比 sizeHint 宽出去、被判成越界（实测：滑块越界 1px 以上）。
            #    这类控件就让它吃掉**本行剩下的宽度**。
            可伸展 = 控件.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
            用宽 = max(1, 宽 - x) if 可伸展 else int(min(尺.width(), max(1, 宽)))
            控件.setGeometry(int(x), int(y), int(用宽), int(高))
            x += 尺.width() + self.间距
            行高 = max(行高, 高)
        需要 = max(默认行高, y + 行高) if 可见 else 0
        if self.height() != 需要:
            self.setFixedHeight(需要)

    def 行数(self) -> int:
        return len({w.geometry().y() for w in self.控件们
                    if w is not None and not w.isHidden()})

    def 越界的按钮(self) -> list[str]:
        """自检/测试用：几何超出自身的按钮（正常应为空）。"""
        坏 = []
        for 控件 in self.控件们:
            # 只看**按钮**（有文字的）：用户报的是"按钮看不见、点不到"；
            # 倍速滑块那种可伸展控件由 Qt 自己按行宽摆，不在这条判据里。
            if 控件 is None or 控件.isHidden() or not hasattr(控件, "text"):
                continue
            g = 控件.geometry()
            if g.right() > self.width() + 1 or g.bottom() > self.height() + 1:
                坏.append(控件.text() if hasattr(控件, "text") else str(控件))
        return 坏


def 让高度随行数(控件) -> None:
    """兼容旧调用（换行按钮区自己管高度，这里什么都不用做）。"""
    return None
