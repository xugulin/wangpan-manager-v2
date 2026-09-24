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
        """2 倍速时：同样 0.15 秒真实时间，时钟要走 0.3 秒（样本也是 2 倍地写）。"""
        钟 = 音频时钟(48000)
        钟.重置(0.0, 倍速=2.0)
        time.sleep(0.15)
        钟.记写入(int(48000 * 0.15))          # 0.15 秒的样本；时钟会自己乘 2 倍速
        现在 = 钟.现在秒()
        self.assertGreater(现在, 0.2, f"2 倍速下走得太慢：{现在:.3f}s")
        self.assertLess(现在, 0.5, f"2 倍速下超出护栏太多：{现在:.3f}s")

    def test_音频时钟不许超过真实时间(self):
        """设备不阻塞时（Windows 的空输出曾经如此）时钟会瞬间冲到片尾 →
        护栏必须把它压在真实时间附近，否则视频永远等不到自己的时刻。"""
        钟 = 音频时钟(48000)
        钟.重置(0.0)
        钟.记写入(48000 * 60)            # 一口气写了 60 秒的样本
        self.assertLess(钟.现在秒(), 1.0, "时钟不能被样本数带着飞")

    def test_音频放完后由真实时间接管(self):
        """音频放完（EOF）后时钟必须继续走，否则片尾几帧会死等（Windows CI 抓到过）。"""
        钟 = 音频时钟(48000)
        钟.重置(0.0)
        钟.记写入(48000 * 5)             # 一口气写了 5 秒的样本（模拟"数据已经全在缓冲里"）
        time.sleep(0.6)
        前 = 钟.现在秒()
        self.assertLess(前, 1.6, "音频没放完时，护栏要把它压在真实时间附近")
        钟.音频已完 = True               # 音频到达 EOF：此后不再被"已写样本"封顶
        time.sleep(0.3)
        现在 = 钟.现在秒()
        self.assertGreater(现在, 前 + 0.15, "音频放完后时钟要由真实时间继续推进")
        self.assertGreater(现在, 1.5, "音频放完后不该还停在样本数上")

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


class 播放中改输出尺寸测试(unittest.TestCase):
    """播放中反复改"解码输出尺寸"不许崩 —— 这是用户真机崩溃场景的回归。

    为什么会有这个测试：界面在**窗口 resize** 时会调 ``设置输出尺寸``（省掉每帧
    搬 25 MB 的拷贝）。原实现在界面线程里当场 ``sws_freeContext`` 并清零目标尺寸，
    而解码线程正在 ``取帧`` 里用它们 → use-after-free / 越界写。
    复现脚本（播放中每 2ms 改一次随机尺寸）改前 3/3 必崩、改后 3/3 存活。
    这里做成常驻用例：**崩了就是测试进程死掉**，CI 一定会红。
    """

    @classmethod
    def setUpClass(cls):
        if not 有ffmpeg():
            raise unittest.SkipTest("本机没有 ffmpeg，造不出素材")
        cls.临时 = 临时目录()
        cls.素材 = Path(cls.临时.name) / "长一点.mp4"

    @classmethod
    def tearDownClass(cls):
        cls.临时.cleanup()

    def test_猛改尺寸不崩(self):
        import os
        import random
        import subprocess
        if not self.素材.is_file():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                            "-i", "testsrc2=size=320x180:rate=25:duration=6",
                            "-c:v", "libx264", "-preset", "ultrafast",
                            "-pix_fmt", "yuv420p", str(self.素材)],
                           capture_output=True, timeout=180, check=False)
        if not self.素材.is_file():
            self.skipTest("素材没造出来")
        引擎 = 播放引擎()
        self.addCleanup(引擎.停止)
        引擎.打开(str(self.素材))
        引擎.播放()
        time.sleep(0.5)
        开始 = time.time()
        次数 = 0
        while time.time() - 开始 < 1.5:
            引擎.设置输出尺寸(random.randint(1, 2000), random.randint(1, 1200))
            次数 += 1
            time.sleep(0.002)
        self.assertGreater(次数, 100, "这一段要真的改很多次才有意义")
        # 关键：进程还活着、解码线程还在出帧
        帧前 = 引擎.取帧序号()
        time.sleep(0.4)
        self.assertGreater(引擎.取帧序号(), 帧前,
                           f"改了 {次数} 次输出尺寸后解码还得继续出帧（不许崩/卡死）")


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
        # ⚠️ 别用固定的 4 秒死等：CI 的 runner 一忙起来（实测同一份代码
        #    从 90s 变成 144s），解码线程 4 秒内推不到 3.5 秒处，这条就**假红**
        #    （Windows CI #37 抓到：1 != 2）。改成"轮询条件 + 宽松死线"：
        #    真坏（跳章节没生效）永远等不到 2，依然会失败；慢机器不再误报。
        截止 = time.time() + 20
        见到 = 0
        while time.time() < 截止:
            当前 = 引擎.当前章节() or 0
            见到 = max(见到, 当前)
            if 见到 >= 2:
                break
            time.sleep(0.05)
        self.assertEqual(见到, 2,
                         "跳到第三章后当前章节应该是 2；"
                         f"实际只到 {见到}（当前时间 {引擎.统计.当前时间秒:.2f}s）")
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


class 多声道音频测试(unittest.TestCase):
    """**5.1 音频必须能播**（真机崩溃的根因就在这里，必须钉死）。

    背景：`解码._图像指针数组()` 原来默认只取 **4** 个平面，而 5.1 的 FLTP 音频
    有 **6** 个平面 → `swr_convert` 去读 data[4]/data[5]，那是数组外的野指针，
    越界访问把堆写坏 → 进程随机崩在任意 FFmpeg 组件里。
    真机三次 core 分别在 `V2-音频`（swr_convert）、`V2-解封装`、`V2-缩略图`；
    本地 1MB 的 5.1 素材也必崩（立体声因为只有 2 个平面一直没事）。
    """

    @classmethod
    def setUpClass(cls):
        if not 有ffmpeg():
            raise unittest.SkipTest("本机没有 ffmpeg，造不出多声道素材")
        cls.临时 = 临时目录()

    @classmethod
    def tearDownClass(cls):
        cls.临时.cleanup()

    def _造(self, 编码: str, 声道: int) -> Path | None:
        目标 = Path(self.临时.name) / f"{编码}_{声道}ch.mp4"
        if 目标.is_file():
            return 目标
        import subprocess
        参数 = ["ffmpeg", "-v", "error", "-y",
              "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3",
              "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
              "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
              "-c:a", 编码, "-ac", str(声道)]
        if 声道 > 2:
            参数 += ["-channel_layout", "5.1"]
        参数.append(str(目标))
        子 = subprocess.run(参数, capture_output=True, timeout=180, text=True)
        return 目标 if 目标.is_file() else None

    def _播(self, 素材: Path) -> tuple[int, int]:
        引擎 = 播放引擎()
        self.addCleanup(引擎.停止)
        引擎.打开(str(素材))
        引擎.播放()
        开始 = time.time()
        while time.time() - 开始 < 3.0:
            time.sleep(0.1)
            if 引擎.统计.已解视频帧 > 5:
                break
        return int(引擎.统计.已解视频帧), int(引擎.统计.已丢视频帧)

    def test_六声道不再崩且真的在解(self):
        for 编码 in ("aac", "ac3", "eac3"):
            素材 = self._造(编码, 6)
            if 素材 is None:
                self.skipTest(f"造不出 {编码} 5.1 素材")
            已解, _丢 = self._播(素材)
            self.assertGreater(已解, 5, f"{编码} 5.1 应该能正常解码（崩了的话这行都到不了）")

    def test_立体声照常(self):
        素材 = self._造("aac", 2)
        if 素材 is None:
            self.skipTest("造不出立体声素材")
        已解, _丢 = self._播(素材)
        self.assertGreater(已解, 5, "立体声当然要正常（回归保护）")
