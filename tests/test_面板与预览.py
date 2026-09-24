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


class 控制条按钮换行测试(unittest.TestCase):
    """按钮**不许超出窗口**（用户真机反馈：进度条下面那排按钮没显示完、点不到）。

    原来包在横向滚动区里、高度只按一行给，滚动条被压得几乎看不见。
    现在换成"放不下就换行"的换行按钮区。
    """

    @classmethod
    def setUpClass(cls):
        _取应用()

    def _页(self, 宽):
        from wangpan.ui.播放页 import 播放页
        页 = 播放页()
        页.resize(宽, 700)
        页.show()
        _取应用().processEvents()
        for _ in range(3):
            _取应用().processEvents()
        self.addCleanup(页.关闭)
        return 页

    def test_任何宽度都不越界(self):
        from PySide6.QtWidgets import QPushButton
        for 宽 in (1400, 1000, 760, 520):
            页 = self._页(宽)
            容器 = 页.按钮滚动区
            # 显式重排一次：字体/主题是后面才定型的，全量跑（前面已经建过很多控件）
            # 时布局时机和单跑不同，会出现"还没重排就断言"的假红（实测抖过一次）。
            容器.重排()
            _取应用().processEvents()
            按钮们 = [b for b in 容器.findChildren(QPushButton) if b.isVisible()]
            self.assertGreater(len(按钮们), 5, "控制条上应该有一排按钮")
            越界 = [b.text() for b in 按钮们
                  if b.geometry().right() > 容器.width() + 1
                  or b.geometry().bottom() > 容器.height() + 1]
            self.assertEqual(越界, [], f"窗口 {宽} 时有按钮超出了可视范围：{越界}")
            self.assertEqual(容器.越界的按钮(), [])

    def test_窄了会换行变高(self):
        宽页 = self._页(1400)
        窄页 = self._页(520)
        self.assertLess(窄页.按钮滚动区.行数(), 0.75 * 宽页.按钮滚动区.行数() + 99,
                        "越窄行数该越多")
        self.assertGreaterEqual(窄页.按钮滚动区.行数(), 宽页.按钮滚动区.行数())
        self.assertGreater(窄页.按钮滚动区.height(), 0)


class 预览线程串行测试(unittest.TestCase):
    """缩略图**同一时刻只许跑一个**。

    背景（真机 coredump 实证）：原来每次悬停都新起线程，多线程同时喂同一个
    解码器 → FFmpeg 内部堆被写坏，崩溃线程名 `V2-缩略图`，栈在 avcodec_send_packet。
    """

    @classmethod
    def setUpClass(cls):
        _取应用()

    def test_忙的时候不再起新线程(self):
        from wangpan.ui.播放页 import 播放页
        页 = 播放页()
        self.addCleanup(页.关闭)
        self.assertTrue(hasattr(页, "_预览忙") and hasattr(页, "_预览锁"),
                        "预览必须有'忙'标记与锁（那是崩溃的根因）")
        页._预览忙 = True
        页.引擎.统计.总时长秒 = 10.0          # 免得因为"没时长"提前返回
        页._出预览图()
        self.assertTrue(页._预览忙, "上一个还没跑完时，不该再叠一个新任务")


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


class 预览只对本地源测试(unittest.TestCase):
    """网盘直链播放时**不做**悬停预览。

    为什么：预览要另开一路连接 + 另开一个解码器去读同一个直链，而网盘直链常常是
    一次性/短时效的。真机两次 core 的崩溃线程分别是 `V2-缩略图` 与 `V2-解封装`
    （abort 在 libavcodec 内部），现象就是"播放黑屏 + 崩溃"。
    """

    @classmethod
    def setUpClass(cls):
        _取应用()

    def test_直链不算本地源(self):
        from wangpan.ui.播放页 import 播放页
        页 = 播放页()
        self.addCleanup(页.关闭)

        class 假输入:
            def __init__(self, 地址):
                self.地址 = 地址

        for 地址 in ("https://example.com/a.mkv", "http://127.0.0.1/a.mp4",
                    "rtsp://x/y", ""):
            页.引擎.输入 = 假输入(地址)
            self.assertFalse(页._是本地源(), f"{地址} 不该算本地源")
        页.引擎.输入 = 假输入(__file__)          # 一个真实存在的本地文件
        self.assertTrue(页._是本地源(), "本地存在的文件该算本地源")

    def test_默认就开着这个限制(self):
        from wangpan.ui.播放页 import 播放页
        页 = 播放页()
        self.addCleanup(页.关闭)
        self.assertTrue(页.预览仅本地, "默认必须限制预览只对本地源（稳定性优先）")
