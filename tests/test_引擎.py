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

    def setUp(self):
        # 把引擎日志收下来：测试失败时打印出来 —— 否则"为什么不出画面"全靠猜
        # （Windows CI 上第一次跑就吃了这个亏：只看到"帧数=1"，看不到原因）
        self._日志行: list[str] = []

    def tearDown(self):
        """测试失败时把引擎日志打出来（"为什么不出画面"不能靠猜）。"""
        失败 = False
        try:
            结果 = getattr(self, "_outcome", None)
            for 属性 in ("errors", "failures"):
                for 项 in (getattr(结果, 属性, None) or []):
                    if len(项) >= 2 and 项[1]:
                        失败 = True
        except Exception:  # noqa: BLE001 - 打印日志这件事本身绝不能把测试搞挂
            失败 = False
        if 失败 or not self._日志行:
            print("\n---- 引擎日志（最后 30 行）----")
            for 行 in self._日志行[-30:]:
                print("  " + 行)
            print("---- 日志结束 ----")

    def _起播(self):
        引擎 = 播放引擎(日志回调=self._日志行.append)
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

    def test_空输出也按真实时间走(self):
        """没有声卡（Windows CI / 服务器）时空输出必须按真实时间阻塞。

        这是 Windows CI 上抓到的真 bug：空输出的"写"立刻返回 → 音频线程几毫秒
        "写"完整个文件 → 音频时钟瞬间冲到片尾 → 视频永远等不到自己的时刻 →
        **25 帧的片子只出 1 帧**。修法是空输出按样本 sleep + 音频时钟加真实时间护栏。
        """
        import os as _os
        旧 = _os.environ.get("V2_音频后端")
        _os.environ["V2_音频后端"] = "空"
        try:
            引擎 = 播放引擎(日志回调=self._日志行.append)
            self.addCleanup(引擎.停止)
            引擎.打开(str(self.素材))
            self.assertIsInstance(引擎.输出, type(引擎.输出))
            self.assertIn("空输出", 引擎.输出.名字)
            引擎.播放()
            time.sleep(1.0)
            进度 = 引擎.时钟.现在秒()
            self.assertLess(进度, 1.6, f"空输出下时钟不该冲到片尾（现在 {进度:.2f}s）")
            self.assertGreater(进度, 0.6, f"时钟要走（现在 {进度:.2f}s）")
            self.assertGreater(引擎.统计.已解视频帧, 10,
                               "空输出下也要正常出画面（Windows 走的就是这条路）")
        finally:
            if 旧 is None:
                _os.environ.pop("V2_音频后端", None)
            else:
                _os.environ["V2_音频后端"] = 旧

    def test_截图会自动建目录(self):
        """QImage.save 失败是静默的：目录不存在时只返回 False。

        全新检出的仓库里没有 数据/ 目录，Windows CI 上验收脚本就因此报
        "截图没成功"（画面明明有）。所以截图必须先建目录，失败了还要说原因。
        """
        from pathlib import Path as _Path
        引擎 = self._起播()
        截止 = time.time() + 6.0
        while time.time() < 截止 and 引擎.取最新帧() is None:
            time.sleep(0.05)
        目标 = _Path(self.临时.name) / "没有这个目录" / "更里面" / "截图.png"
        if 目标.parent.exists():
            import shutil
            shutil.rmtree(目标.parent)
        self.assertTrue(引擎.截图(str(目标)), "有画面时截图必须成功（哪怕目录不存在）")
        self.assertTrue(目标.is_file())
        self.assertGreater(目标.stat().st_size, 500)

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
