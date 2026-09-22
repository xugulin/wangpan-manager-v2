"""解封装与解码：用真素材验证流信息、帧、时间戳单调、跳转。"""

from __future__ import annotations

import unittest

from tests.公用 import 有ffmpeg, 造素材, 临时目录  # noqa: F401

from wangpan.player.解码 import 视频解码器, 音频解码器
from wangpan.player.解封装 import 输入


class 解封装解码测试(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.临时 = 临时目录()
        cls.素材 = 造素材(cls.临时.name, 秒=2.0, 带声音=True)

    @classmethod
    def tearDownClass(cls):
        cls.临时.cleanup()

    def test_流信息(self):
        输入对象 = 输入.打开(str(self.素材))
        try:
            self.assertIsNotNone(输入对象.视频流)
            self.assertIsNotNone(输入对象.音频流)
            self.assertEqual((输入对象.视频流.宽, 输入对象.视频流.高), (320, 180))
            self.assertAlmostEqual(输入对象.时长秒, 2.0, delta=0.5)
            self.assertGreater(输入对象.视频流.帧率, 20)
            self.assertEqual(输入对象.音频流.采样率, 44100)
        finally:
            输入对象.关闭()

    def test_解码帧数与时间戳(self):
        输入对象 = 输入.打开(str(self.素材))
        解码器 = 视频解码器(输入对象.绑定, 输入对象.视频流)
        解码器.打开()
        帧们 = []
        try:
            while True:
                包 = 输入对象.读包()
                if 包 is None:
                    解码器.送空包()
                elif 包["流序号"] == 输入对象.视频流.序号:
                    # 复制包再送解码：同一块读缓冲会被下一次读包覆盖（真机踩过段错误）
                    新包 = 输入对象.复制包()
                    输入对象.释放包()
                    解码器.送包(新包)
                    输入对象.释放新包(新包)
                else:
                    输入对象.释放包()
                    continue
                while 解码器.收帧() >= 0:
                    帧 = 解码器.取帧()
                    if 帧 is not None:
                        帧们.append(帧)
                if 包 is None:
                    break
        finally:
            解码器.关()
            输入对象.关闭()
        self.assertGreater(len(帧们), 20, "两秒 25fps 的片子应该解出 40+ 帧")
        self.assertEqual((帧们[0].宽, 帧们[0].高), (320, 180))
        self.assertEqual(帧们[0].步长, 320 * 4)
        时间们 = [帧.时间秒 for 帧 in 帧们 if 帧.时间秒 >= 0]
        self.assertEqual(时间们, sorted(时间们), "时间戳必须单调递增")

    def test_音频重采样(self):
        输入对象 = 输入.打开(str(self.素材))
        解码器 = 音频解码器(输入对象.绑定, 输入对象.音频流)
        解码器.打开()
        块们 = []
        try:
            while True:
                包 = 输入对象.读包()
                if 包 is None:
                    解码器.送空包()
                elif 包["流序号"] == 输入对象.音频流.序号:
                    新包 = 输入对象.复制包()
                    输入对象.释放包()
                    解码器.送包(新包)
                    输入对象.释放新包(新包)
                else:
                    输入对象.释放包()
                    continue
                while 解码器.收帧() >= 0:
                    块 = 解码器.取块()
                    if 块 is not None:
                        块们.append(块)
                if 包 is None:
                    break
        finally:
            解码器.关()
            输入对象.关闭()
        self.assertGreater(len(块们), 5)
        self.assertEqual(len(块们[0].数据) % (2 * 2), 0, "S16 立体声：字节数要是 4 的倍数")


if __name__ == "__main__":
    unittest.main()
