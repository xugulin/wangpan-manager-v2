"""M5 体验：倍速、逐帧、播放记录/续播、播放列表。"""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

from tests.公用 import 有ffmpeg, 造素材, 临时目录

from wangpan.player.引擎 import 播放引擎, 播放状态, 系统时钟, 音频时钟
from wangpan.player.记录 import 记录本, 播放记录


class 记录本测试(unittest.TestCase):
    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.本 = 记录本(Path(self.临时.name) / "播放记录.json")

    def test_记位置与续播(self):
        self.本.记开始("/影片/a.mp4", 100.0, "a.mp4")
        self.本.记位置("/影片/a.mp4", 42.0, 100.0)
        self.assertEqual(self.本.续播位置("/影片/a.mp4"), 42.0)
        self.assertTrue(self.本.存())
        # 换一个实例读回来（真的落盘了）
        新本 = 记录本(Path(self.临时.name) / "播放记录.json")
        self.assertEqual(新本.续播位置("/影片/a.mp4"), 42.0)

    def test_太靠前不续播(self):
        self.本.记位置("/a.mp4", 3.0, 100.0)
        self.assertEqual(self.本.续播位置("/a.mp4"), 0.0, "才看 3 秒不该续播")

    def test_看完了不续播(self):
        self.本.记位置("/a.mp4", 99.0, 100.0)
        self.本.记看完("/a.mp4")
        self.assertEqual(self.本.续播位置("/a.mp4"), 0.0)

    def test_结尾附近不续播(self):
        self.本.记位置("/a.mp4", 97.0, 100.0)
        self.assertEqual(self.本.续播位置("/a.mp4"), 0.0, "离结尾 3 秒不该续播")

    def test_网络地址忽略签名(self):
        本 = self.本
        本.记位置("https://cdn/a.mp4?sign=AAA&t=1", 30.0, 100.0)
        self.assertEqual(本.续播位置("https://cdn/a.mp4?sign=BBB&t=2"), 30.0,
                         "签名每次都变，记录要按去掉查询串的地址算")

    def test_坏文件不影响(self):
        (Path(self.临时.name) / "坏.json").write_text("{不是 JSON", encoding="utf-8")
        本 = 记录本(Path(self.临时.name) / "坏.json")
        self.assertEqual(len(本.最近()), 0)
        self.assertEqual(本.续播位置("/x.mp4"), 0.0)

    def test_最近排序(self):
        本 = self.本
        本.记位置("/旧.mp4", 10, 100)
        time.sleep(0.01)
        本.记位置("/新.mp4", 10, 100)
        self.assertEqual(本.最近(1)[0].标识, "/新.mp4")


class 倍速与逐帧测试(unittest.TestCase):
    def test_时钟按倍速折算(self):
        钟 = 系统时钟()
        钟.重置(0.0, 倍速=2.0)
        time.sleep(0.3)
        self.assertGreater(钟.现在秒(), 0.4, "2 倍速时时钟要走两倍快")
        self.assertLess(钟.现在秒(), 1.0)

    def test_音频时钟按倍速折算(self):
        钟 = 音频时钟(48000)
        钟.重置(0.0, 倍速=2.0)
        钟.记写入(48000)                 # 写了 1 秒的样本
        self.assertAlmostEqual(钟.现在秒(), 2.0, delta=0.05)

    def test_倍速被夹在合理范围(self):
        引擎 = 播放引擎()
        self.addCleanup(引擎.停止)
        self.assertEqual(引擎.设置倍速(99.0), 4.0)
        self.assertEqual(引擎.设置倍速(0.01), 0.25)

    @unittest.skipUnless(有ffmpeg(), "需要系统 ffmpeg 造测试素材")
    def test_逐帧会前进(self):
        临时 = 临时目录()
        self.addCleanup(临时.cleanup)
        素材 = 造素材(临时.name, 秒=1.0, 带声音=False)
        引擎 = 播放引擎()
        self.addCleanup(引擎.停止)
        引擎.打开(str(素材))
        引擎.播放()
        time.sleep(0.4)
        引擎.设置暂停(True)
        self.assertEqual(引擎.状态, 播放状态.暂停)
        前 = 引擎.统计.已解视频帧
        引擎.逐帧()
        self.assertGreater(引擎.统计.已解视频帧, 前, "逐帧要真的前进一帧")


if __name__ == "__main__":
    unittest.main()
