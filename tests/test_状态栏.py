"""状态栏提示不能被"每 16ms 刷一次"的播放统计吃掉（真机踩到的）。

真机现象：点媒体库工具条的「✅ 待确认」，界面**什么也没说** —— 因为
`_刷新()`（16ms 一次）里 `状态.showMessage(播放统计)` 把刚弹出来的提示瞬间覆盖了。
"刮削完成""已装弹幕 N 条""过滤规则已更新"这些提示全都被吃掉。

修法：播放统计改成状态栏的 **permanent 部件**，临时提示统一走 :meth:`主窗口.提示`
（`showMessage` 带超时）。这条测试盯住这两件事。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from tests.公用 import 临时目录

if not os.environ.get("DISPLAY") and os.name != "nt":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from wangpan.scrape.库 import 资料库  # noqa: E402

应用 = QApplication.instance() or QApplication([])


class 状态栏测试(unittest.TestCase):
    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)
        # 主窗口自己会 new 一个 资料库()（默认落在 数据/资料库.db）——
        # 测试里把它换成"临时目录里的库"，免得动用户的数据。
        库路径 = self.根 / "库.db"
        真的 = 资料库

        class 临时库(真的):                       # type: ignore[misc, valid-type]
            def __init__(自己, 路径=None):
                super().__init__(库路径)

        import wangpan.scrape.库 as 库模块
        库模块.资料库 = 临时库
        self.addCleanup(lambda: setattr(库模块, "资料库", 真的))

        from wangpan.ui.主窗口 import 主窗口
        self.窗口 = 主窗口()
        self.addCleanup(self.窗口.close)
        for _ in range(6):
            应用.processEvents()

    def test_提示不会被刷新盖掉(self):
        self.窗口.提示("🎞 刮削完成：成功 3", 8000)
        应用.processEvents()
        self.assertIn("刮削完成", self.窗口.当前提示())
        self.窗口._刷新()                       # 模拟播放中那一帧
        self.窗口._刷新()
        应用.processEvents()
        self.assertIn("刮削完成", self.窗口.当前提示(),
                     "播放统计把临时提示盖掉了（用户等于没看到）")

    def test_播放统计在常驻标签上(self):
        self.窗口._刷新()
        应用.processEvents()
        self.assertTrue(self.窗口.统计标签.text(), "统计要显示在常驻部件上")
        self.assertNotIn(self.窗口.统计标签.text(), self.窗口.当前提示(),
                         "统计不该占用临时提示的位置")

    def test_海报墙的状态变化会显示出来(self):
        self.assertIsNotNone(self.窗口.海报墙)
        self.窗口.海报墙.状态变化.emit("📁 已请求扫描：/tmp/某目录")
        应用.processEvents()
        self.assertIn("已请求扫描", self.窗口.当前提示())


if __name__ == "__main__":
    unittest.main()
