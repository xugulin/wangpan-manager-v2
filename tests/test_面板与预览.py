"""面板 / 预览 / 独立窗口这几处"摆放"的测试。

对应需求：
* 面板改为独立悬浮窗口，高度和 GUI 高度一致；
* 预览改成紧贴进度条的悬浮小窗（不再占一条长条）；
* 独立窗口播放要适应各种屏幕，不要超出屏幕。
"""

from __future__ import annotations

import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication


def _取应用() -> QApplication:
    return QApplication.instance() or QApplication([])


class 悬浮面板测试(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _取应用()

    def _建(self):
        from wangpan.ui.播放页 import 播放页
        from v8_3.界面.悬浮面板 import 悬浮面板窗口
        from PySide6.QtWidgets import QTabWidget
        标签 = QTabWidget()
        标签.addTab(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel("清单"),
                  "📋 播放清单")
        主 = __import__("PySide6.QtWidgets", fromlist=["QWidget"]).QWidget()
        主.resize(1000, 700)
        主.show()
        _取应用().processEvents()
        return 悬浮面板窗口(标签, 主窗口=主), 主, 标签

    def test_是顶层窗口且高度跟随(self):
        窗, 主, 标签 = self._建()
        try:
            self.assertTrue(窗.isWindow(), "必须是顶层窗口（独立悬浮）")
            self.assertIs(标签.parent(), 窗, "标签页要被搬进悬浮窗（不是复制）")
            窗.显示()
            _取应用().processEvents()
            屏幕 = (主.screen() or QGuiApplication.primaryScreen()).availableGeometry()
            期望 = min(主.frameGeometry().height(), 屏幕.height())
            self.assertLessEqual(abs(窗.height() - 期望), 2,
                                 f"高度该是 min(主窗口, 屏幕)：{窗.height()} vs {期望}")
            self.assertLessEqual(窗.geometry().bottom(), 屏幕.bottom() + 1,
                                 "不许超出屏幕下边缘")
        finally:
            窗.收尾()
            主.close()

    def test_主窗口变高面板跟着变(self):
        窗, 主, _标签 = self._建()
        try:
            窗.显示()
            _取应用().processEvents()
            主.resize(1000, 520)
            _取应用().processEvents()
            低 = 窗.height()
            主.resize(1000, 760)
            _取应用().processEvents()
            高 = 窗.height()
            self.assertGreater(高, 低, "主窗口变高，面板要跟着长高")
        finally:
            窗.收尾()
            主.close()

    def test_收尾后不再跟随(self):
        窗, 主, _标签 = self._建()
        窗.收尾()
        主.resize(900, 400)
        _取应用().processEvents()
        self.assertFalse(窗.isVisible(), "收尾之后不该还显示着")


class 悬浮预览测试(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _取应用()

    def _页(self):
        from wangpan.ui.播放页 import 播放页
        页 = 播放页()
        页.resize(1000, 700)
        页.show()
        _取应用().processEvents()
        self.addCleanup(页.关闭)
        return 页

    def test_控制条里不再有常驻预览条(self):
        """用户嫌原来那条 90px 高的预览区"长又粗"——它必须已经不在控制条里了。"""
        页 = self._页()
        self.assertTrue(页.预览窗.isWindow(), "预览必须是**独立小窗**（不是嵌在控制条里）")
        条 = 页.按钮滚动区.parentWidget()          # 控制条（预览 + 进度 + 按钮那一行）
        布局 = 条.layout()
        子们 = [布局.itemAt(i).widget() for i in range(布局.count())]
        self.assertNotIn(页.预览窗, 子们, "预览窗不该常驻在控制条里（那就是原来的长条）")
        self.assertNotIn(页.预览, 子们, "老的预览标签也已经被拿掉了")
        self.assertIs(页.预览.parent(), 页.预览窗,
                      "老的 `预览` 名字现在指向悬浮小窗里的图")

    def test_给图就弹在进度条上方(self):
        页 = self._页()
        图 = QImage(320, 180, QImage.Format.Format_RGB32)
        图.fill(Qt.GlobalColor.red)
        页._显示预览(图, 95.0)
        _取应用().processEvents()
        self.assertTrue(页.预览显示中 and 页.预览窗.isVisible(), "要给预览就得弹出来")
        self.assertEqual(页.预览窗.时间.text(), "01:35")
        self.assertFalse(页.预览窗.图.pixmap().isNull(), "小窗里要有图")
        # 位置：在进度条**上方**（紧贴），并且不超出屏幕
        条 = 页.进度
        条顶 = 条.mapToGlobal(条.rect().topLeft()).y()
        self.assertLessEqual(页.预览窗.geometry().bottom(), 条顶 + 1,
                             "小窗该贴在进度条上方，不遮住它")
        屏幕 = QGuiApplication.primaryScreen().availableGeometry()
        self.assertGreaterEqual(页.预览窗.geometry().x(), 屏幕.x())
        self.assertLessEqual(页.预览窗.geometry().right(), 屏幕.right() + 1)

    def test_收起之后就不显示了(self):
        页 = self._页()
        图 = QImage(32, 18, QImage.Format.Format_RGB32)
        页._显示预览(图, 1.0)
        _取应用().processEvents()
        self.assertTrue(页.预览窗.isVisible())
        页._收起预览()
        _取应用().processEvents()
        self.assertFalse(页.预览窗.isVisible(), "收起后就该看不见")
        self.assertFalse(页.预览显示中)


class 独立窗口贴屏幕测试(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _取应用()

    def test_开出来就在屏幕内(self):
        from v8_3.界面.播放器窗口 import 播放器窗口
        窗 = 播放器窗口(会话=None, 标题="测试")
        窗.show()
        _取应用().processEvents()
        try:
            屏幕 = (窗.screen() or QGuiApplication.primaryScreen()).availableGeometry()
            角 = 窗.frameGeometry()
            self.assertGreaterEqual(角.x(), 屏幕.x())
            self.assertGreaterEqual(角.y(), 屏幕.y())
            self.assertLessEqual(角.right(), 屏幕.right() + 1,
                                 f"不许超出屏幕右边：{角} vs {屏幕}")
            self.assertLessEqual(角.bottom(), 屏幕.bottom() + 1,
                                 f"不许超出屏幕下边：{角} vs {屏幕}")
        finally:
            窗.close()

    def test_被撑到比屏幕还大也会收回来(self):
        窗 = None
        try:
            from v8_3.界面.播放器窗口 import 播放器窗口
            窗 = 播放器窗口(会话=None, 标题="测试")
        except Exception as 错:  # noqa: BLE001
            self.skipTest(f"建不了独立窗口：{错}")
        窗.show()
        _取应用().processEvents()
        try:
            屏幕 = (窗.screen() or QGuiApplication.primaryScreen()).availableGeometry()
            窗.resize(屏幕.width() + 600, 屏幕.height() + 400)
            _取应用().processEvents()
            角 = 窗.frameGeometry()
            self.assertLessEqual(角.width(), 屏幕.width(),
                                 f"宽要收回屏幕内：{角.width()} vs {屏幕.width()}")
            self.assertLessEqual(角.height(), 屏幕.height(),
                                 f"高要收回屏幕内：{角.height()} vs {屏幕.height()}")
        finally:
            窗.close()


if __name__ == "__main__":
    unittest.main()
