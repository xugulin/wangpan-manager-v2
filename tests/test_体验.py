"""M5 体验：倍速、逐帧、播放记录/续播、播放列表。"""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

from tests.公用 import 有ffmpeg, 造素材, 临时目录

from wangpan.player.解封装 import 输入
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
        # ⚠️ 标识是"绝对路径"：Windows 上 /新.mp4 会变成 D:\新.mp4，
        #    所以断言要用 记录本.标识() 自己算一遍（别写死平台相关的字符串）
        本.记位置("/旧.mp4", 10, 100)
        time.sleep(0.01)
        本.记位置("/新.mp4", 10, 100)
        self.assertEqual(本.最近(1)[0].标识, 记录本.标识("/新.mp4"))


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


class 章节与缩略图测试(unittest.TestCase):
    """M5 剩余项：章节表读取/跳转 + 进度预览用的缩略图（用随仓库带的小素材）。"""

    @classmethod
    def setUpClass(cls):
        from tests.公用 import 自带带章节素材, 自带素材
        cls.临时 = 临时目录()
        cls.带章节 = 自带带章节素材()
        cls.基础 = 自带素材(带声音=False)

    @classmethod
    def tearDownClass(cls):
        cls.临时.cleanup()

    def test_没有章节表就是空列表(self):
        输入对象 = 输入.打开(str(self.基础))
        try:
            self.assertEqual(输入对象.章节们, [])
        finally:
            输入对象.关闭()

    def test_读到章节表(self):
        输入对象 = 输入.打开(str(self.带章节))
        try:
            self.assertEqual(len(输入对象.章节们), 3)
            第一 = 输入对象.章节们[0]
            self.assertAlmostEqual(第一.起始秒, 0.0, delta=0.05)
            self.assertAlmostEqual(第一.结束秒, 2.0, delta=0.05)
            self.assertAlmostEqual(输入对象.章节们[2].起始秒, 4.0, delta=0.05)
        finally:
            输入对象.关闭()

    def test_跳章节(self):
        引擎 = 播放引擎()
        self.addCleanup(引擎.停止)
        引擎.打开(str(self.带章节))
        引擎.播放()
        time.sleep(0.3)
        self.assertTrue(引擎.跳章节(2))
        截止 = time.time() + 4
        while time.time() < 截止 and 引擎.统计.当前时间秒 < 3.5:
            time.sleep(0.05)
        self.assertEqual(引擎.当前章节(), 2, "跳到第三章后当前章节应该是 2")
        self.assertFalse(引擎.跳章节(99), "越界的章节号要返回 False")

    def test_缩略图能出图(self):
        from wangpan.player.缩略图 import 缩略图器
        器 = 缩略图器(str(self.带章节), None, 160)
        self.addCleanup(器.关闭)
        图 = 器.取图(3.0)
        self.assertIsNotNone(图, "缩略图必须能出图")
        self.assertEqual(图.width(), 160)
        self.assertEqual(图.height(), 90)
        # 不是纯黑（说明真的解到了画面）
        非黑 = sum(1 for x in range(0, 图.width(), 20)
                 for y in range(0, 图.height(), 20)
                 if 图.pixelColor(x, y).lightness() > 20)
        self.assertGreater(非黑, 3, "缩略图不该是纯黑")
