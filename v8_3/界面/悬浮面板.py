"""把「视频信息 / 播放清单 / AI 助手」做成**独立悬浮窗口**。

用户要求
========
> 把面板改为一个独立的悬浮窗口，**高度和 GUI 高度一致**。

为什么值得这么做（不只是"挪个位置"）
====================================
面板原来钉在播放页右侧的 QSplitter 里，占掉 260~460px 的宽度 —— 画面被挤窄，
而且面板本身只有约 400px 高、下面一大片空着。做成独立窗口之后：

* 画面吃满主窗口宽度（画面更大）；
* 面板高度跟主窗口**等高**，清单/AI 日志能一眼看很多行；
* 面板可以挪到第二块屏幕，不挡画面；
* 主窗口一移/一缩放，面板跟着走（高度实时同步）。

实现要点（都是踩过的坑）
========================
1. **`Qt.Tool` + `WindowStaysOnTopHint`**：`Tool` 让它不出现在任务栏、
   也**不会**在 Alt+Tab 里多出一个条目；置顶是因为它要"跟着主窗口看"。
2. **只 `hide()` 不 `close()`/`deleteLater()`**：面板里挂着播放清单与 AI 面板的
   信号连接，重建一次就得多接一次线 —— 隐藏/显示最省事也最稳。
3. **主窗口事件靠 `eventFilter` 跟**：主窗口的 `Resize`/`Move`/`Show` 都要跟，
   而播放页只是主窗口的一个子控件，拿不到这些事件；在这边装过滤器最直接。
4. **贴屏幕边缘要**收**回来**：往右放不下就放左边，再不够就贴屏幕内边 ——
   用户明确说过"不要超出屏幕"（独立播放窗口那条），面板同理。
5. **主窗口关了，面板也要关**：不然会留下一个没有父窗口的小窗挂着不走。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QVBoxLayout, QWidget

__all__ = ["悬浮面板窗口"]

#: 面板默认宽度（与原来的 260~460 一致，取中间值）
默认宽 = 380
最小宽 = 260
最大宽 = 520


class 悬浮面板窗口(QWidget):
    """承载播放页那些标签页的独立悬浮窗口（高度跟随主窗口）。"""

    def __init__(self, 标签页: QWidget, 主窗口: QWidget | None = None,
                 宽: int = 默认宽, 父=None) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                        | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setWindowTitle("面板 · 视频信息 / 播放清单 / AI 助手")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        # ⚠️ **显示时不许抢焦点**：真机实测踩到过 —— 面板一弹出来就拿到焦点，
        #    之后发给主窗口的 Ctrl+Shift+P（收面板）、Ctrl+Shift+W（独立窗口）、
        #    空格（暂停）全都进了面板，用户会以为"快捷键失灵了"。
        #    悬浮面板是"跟着主窗口看的附属窗"，不该抢输入焦点。
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._标签页 = 标签页
        self._宽 = max(最小宽, min(最大宽, int(宽 or 默认宽)))
        self.主窗口 = 主窗口
        布局 = QVBoxLayout(self)
        布局.setContentsMargins(0, 0, 0, 0)
        布局.setSpacing(0)
        标签页.setParent(self)              # 搬进来（不是复制：信号连接都还在）
        布局.addWidget(标签页)
        self.resize(self._宽, 420)
        if 主窗口 is not None:
            主窗口.installEventFilter(self)

    # ------------------------------------------------------------------ 跟随

    def eventFilter(self, 对象, 事件):  # noqa: N802 - Qt 命名
        if 对象 is self.主窗口 and 事件.type() in (
                QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show,
                QEvent.Type.WindowStateChange):
            self.跟随主窗口()
        return super().eventFilter(对象, 事件)

    def 跟随主窗口(self) -> None:
        """高度与主窗口一致、纵向对齐、横向贴在主窗口旁边（放不下就换边）。"""
        主 = self.主窗口
        if 主 is None or not 主.isVisible():
            return
        try:
            角 = 主.frameGeometry()
            高 = max(240, int(角.height()))
            屏幕 = (主.screen() or QGuiApplication.primaryScreen()).availableGeometry()
            # 先试右边，放不下就放左边，都不行就贴屏幕右侧内边
            x = 角.x() + 角.width()
            if x + self._宽 > 屏幕.x() + 屏幕.width():
                x = 角.x() - self._宽
            if x < 屏幕.x():
                x = max(屏幕.x(), 屏幕.x() + 屏幕.width() - self._宽)
            y = max(屏幕.y(), min(角.y(), 屏幕.y() + 屏幕.height() - 高))
            高 = min(高, 屏幕.height())
            self.setGeometry(int(x), int(y), self._宽, int(高))
        except Exception:  # noqa: BLE001 - 跟随失败不该影响播放
            pass

    # ------------------------------------------------------------------ 显隐

    def 显示(self) -> None:
        """显示并把位置摆好；重复调用是幂等的。"""
        self.跟随主窗口()
        self.show()
        self.raise_()
        self.跟随主窗口()
        # ⚠️ 再把焦点**还给主窗口**。`WA_ShowWithoutActivating` 只是"别主动要焦点"，
        #    但 X11 上窗口一映射，窗口管理器照样可能把焦点给它 —— 真机实测：
        #    面板弹出后，发给主窗口的 Ctrl+Shift+P（收面板）/ Ctrl+Shift+W /
        #    空格（暂停）全都进了面板，看起来就是"快捷键失灵"。
        #    面板里没有输入框（只有按钮/列表/日志），所以设成不接受焦点 + 还焦点是安全的。
        # ⚠️ 还焦点必须**延后一拍**：Qt 的 activateWindow() 如果在 show() 里立刻调，
        #    窗口管理器随后才处理"新窗口映射"，于是又把焦点给了新窗口 —— 实测就是这样：
        #    面板弹出后任何发给主窗口的快捷键都进不去。等它把新窗口处理完再要回来。
        from PySide6.QtCore import QTimer
        # 连着要几次：窗口管理器处理"新窗口映射"的时刻不确定（实测 120ms 一次不够），
        # 多要几次才稳。
        for 毫秒 in (60, 200, 500, 900):
            QTimer.singleShot(毫秒, self._把焦点还给主窗口)

    def focusInEvent(self, 事件):  # noqa: N802 - Qt 命名
        """**一拿到焦点就还回去**。

        真机实测：面板一弹出来，窗口管理器就把键盘焦点给了它，之后发给主窗口的
        Ctrl+Shift+P（收面板）/ Ctrl+Shift+W（独立窗口）/ 空格（暂停）全都进不来，
        用户会以为"快捷键失灵了"。面板里没有输入框（只有按钮/列表/日志），
        所以把焦点立刻退回主窗口是安全的。
        """
        超级 = getattr(super(), "focusInEvent", None)
        if 超级 is not None:
            超级(事件)
        self._把焦点还给主窗口()

    def _把焦点还给主窗口(self) -> None:
        try:
            if self.主窗口 is not None and self.主窗口.isVisible():
                self.主窗口.raise_()
                self.主窗口.activateWindow()
        except Exception:  # noqa: BLE001
            pass

    def 隐藏(self) -> None:
        self.hide()

    def 收尾(self) -> None:
        """主窗口退出时调：摘掉事件过滤器再关，避免主窗口销毁后还回调过来。"""
        try:
            if self.主窗口 is not None:
                self.主窗口.removeEventFilter(self)
        except Exception:  # noqa: BLE001
            pass
        self.hide()
        self.close()
