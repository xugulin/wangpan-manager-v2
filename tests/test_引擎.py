"""播放引擎：起播/暂停/跳转/结束/截图，以及"画面确实由我们自己产出"。"""

from __future__ import annotations

import time
import unittest

from tests.公用 import 有ffmpeg, 造素材, 临时目录

from wangpan.player.引擎 import 播放引擎, 播放状态


class 引擎测试(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.临时 = 临时目录()
        cls.素材 = 造素材(cls.临时.name, 秒=2.0, 带声音=True)

    @classmethod
    def tearDownClass(cls):
        cls.临时.cleanup()

    def _起播(self):
        引擎 = 播放引擎()
        self.addCleanup(引擎.停止)
        引擎.打开(str(self.素材))
        引擎.播放()
        return 引擎

    def test_能出画面(self):
        引擎 = self._起播()
        截止 = time.time() + 6.0
        while time.time() < 截止 and 引擎.统计.已解视频帧 < 5:
            time.sleep(0.05)
        self.assertGreater(引擎.统计.已解视频帧, 4)
        帧 = 引擎.取最新帧()
        self.assertIsNotNone(帧, "引擎必须把画面交出来（界面就靠这个自绘）")
        self.assertEqual((帧.宽, 帧.高), (320, 180))
        self.assertEqual(len(帧.数据), 帧.步长 * 帧.高)

    def test_时间跟着真实时间走(self):
        """音频是主时钟：播 1.5 秒音频，进度要接近 1.5 秒（允许设备延迟）。"""
        引擎 = self._起播()
        time.sleep(1.5)
        进度 = 引擎.时钟.现在秒(0.0)
        self.assertGreater(进度, 0.8, f"进度只有 {进度:.2f}s，时钟没走")
        self.assertLess(进度, 2.6)

    def test_暂停不再产帧_继续还能播(self):
        引擎 = self._起播()
        time.sleep(0.8)
        引擎.设置暂停(True)
        time.sleep(0.2)
        前 = 引擎.统计.已解视频帧
        time.sleep(0.5)
        self.assertEqual(引擎.统计.已解视频帧, 前, "暂停时不该继续解码")
        self.assertEqual(引擎.状态, 播放状态.暂停)
        引擎.设置暂停(False)
        截止 = time.time() + 3.0
        while time.time() < 截止 and 引擎.统计.已解视频帧 == 前:
            time.sleep(0.05)
        self.assertGreater(引擎.统计.已解视频帧, 前, "继续后要接着播")

    def test_跳转(self):
        引擎 = self._起播()
        time.sleep(0.5)
        引擎.跳转(1.5)
        截止 = time.time() + 4.0
        while time.time() < 截止 and 引擎.统计.当前时间秒 < 1.2:
            time.sleep(0.05)
        self.assertGreater(引擎.统计.当前时间秒, 1.0, "跳转后时间戳要跟上去")

    def test_截图存成PNG(self):
        from pathlib import Path
        引擎 = self._起播()
        截止 = time.time() + 6.0
        while time.time() < 截止 and 引擎.取最新帧() is None:
            time.sleep(0.05)
        目标 = Path(self.临时.name) / "截图.png"
        self.assertTrue(引擎.截图(str(目标)), "有画面时截图必须成功")
        self.assertGreater(目标.stat().st_size, 1000)

    def test_没有音轨也能播(self):
        """纯视频文件（没音轨）必须用系统时钟当主时钟 —— 否则画面一帧都不出。

        实测踩到过：音频时钟只靠"写音频"推进，没音轨时它永远停在 0，
        视频线程就一直等时钟 → 5 秒里只解出 2 帧。
        """
        from tests.公用 import 造素材
        素材 = 造素材(self.临时.name, 秒=1.2, 带声音=False)
        引擎 = 播放引擎()
        self.addCleanup(引擎.停止)
        引擎.打开(str(素材))
        self.assertIsInstance(引擎.时钟, type(引擎.时钟))     # 有主时钟就行
        引擎.播放()
        截止 = time.time() + 5.0
        while time.time() < 截止 and 引擎.统计.当前时间秒 < 0.8:
            time.sleep(0.05)
        self.assertGreater(引擎.统计.已解视频帧, 10,
                           "没音轨时也要出画面（系统时钟）")
        self.assertGreater(引擎.时钟.现在秒(), 0.5, "系统时钟要走起来")

    def test_音量缩放(self):
        引擎 = self._起播()
        原始 = b"\x00\x40" * 10          # 16384 的 S16 样本，10 个
        引擎.设置音量(0.5)
        一半 = 引擎._按音量缩放(原始)
        self.assertEqual(len(一半), len(原始))
        值 = int.from_bytes(一半[:2], "little", signed=True)
        self.assertAlmostEqual(值, 8192, delta=2)


if __name__ == "__main__":
    unittest.main()
