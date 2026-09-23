"""关窗不能崩：海报墙的工作线程必须在关窗前停掉。

真机踩到过：库里有数据 → 海报墙起了缩略图工作线程 → 关窗时那个 QThread
还在跑就被析构 → **进程直接 abort**（终端只留下一句
"QThread: Destroyed while thread is still running"）。

这条测试的价值在于"崩了就整进程挂"—— unittest 会红，跑不过去。
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
from wangpan.scrape.模型 import 媒体条目, 媒体类型, 图片, 图片类型  # noqa: E402
from wangpan.ui.海报墙页 import 海报墙页  # noqa: E402
from wangpan.scrape.图片 import 图片缓存  # noqa: E402

应用 = QApplication.instance() or QApplication([])


class 关窗测试(unittest.TestCase):
    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.库 = 资料库(Path(self.临时.name) / "库.db")
        self.addCleanup(self.库.关闭)
        # 造几张有海报的条目：让海报墙真的会去加载图片（也就是真的会起线程）
        图 = Path(self.临时.name) / "p.jpg"
        from PySide6.QtGui import QColor, QImage
        QImage(200, 300, QImage.Format.Format_RGB32).save(str(图)) if False else None
        画 = QImage(200, 300, QImage.Format.Format_RGB32)
        画.fill(QColor(90, 140, 200))
        画.save(str(图))
        for i in range(6):
            条 = 媒体条目(类型=媒体类型.电影, 标题=f"片子{i}", 年份=2000 + i, 评分=7.0)
            条.海报 = 图片(图片类型.海报, f"/p{i}.jpg", 图, 200, 300)
            self.库.存条目(条)
        self.缓存 = 图片缓存(Path(self.临时.name) / "图")
        self.墙 = 海报墙页(self.库, self.缓存)
        self.墙.resize(900, 600)
        self.墙.show()

    def test_关窗会停掉工作线程(self):
        for _ in range(8):
            应用.processEvents()
        self.墙.关闭()                       # 真机崩溃就发生在没调它的那次
        工作 = getattr(self.墙, "_工作", None)
        if 工作 is not None and hasattr(工作, "isRunning"):
            self.assertFalse(工作.isRunning(), "关窗后工作线程必须停下")
        self.墙.close()

    def test_反复开关不崩(self):
        for _ in range(3):
            self.墙.关闭()
            应用.processEvents()
            self.墙.show()
            应用.processEvents()
        self.墙.关闭()


if __name__ == "__main__":
    unittest.main()
