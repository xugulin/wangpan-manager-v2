"""绑定层：库能加载、版本读得到、ABI 自检能过、常量与 FFmpeg 头文件一致。"""

from __future__ import annotations

import unittest

from tests.公用 import 项目根  # noqa: F401  （顺带把项目根加进 sys.path）

from wangpan.ffmpeg import 加载, 绑定


class 绑定测试(unittest.TestCase):
    def test_库能加载(self):
        self.assertTrue(加载.可用(), 加载.不可用原因())
        for 名 in ("avformat", "avcodec", "avutil", "swscale", "swresample"):
            self.assertTrue(加载.路径(名), f"{名} 没加载到")

    def test_版本可读(self):
        版本 = 加载.库版本()
        self.assertGreaterEqual(len(版本), 4, f"读到的版本太少：{版本}")
        for 名, 号 in 版本.items():
            self.assertGreater(号, 0, f"{名} 版本为 0")

    def test_ABI自检(self):
        """偏移表与当前库必须匹配（不匹配要当场报错，而不是读出乱码）。"""
        实例 = 绑定.取绑定()
        实例.检查ABI()
        self.assertGreater(绑定.偏移["AVFormatContext.nb_streams"], 0)
        self.assertLess(绑定.偏移["AVPacket.pts"], 绑定.偏移["AVPacket.dts"])

    def test_编码名(self):
        实例 = 绑定.取绑定()
        # 不猜 AVCodecID 的数值：用真素材验证"编码名能读出来"（见 test_解封装解码）
        self.assertEqual(实例.编码名(27), "h264")
        self.assertTrue(实例.编码名(1))          # 任何合法 id 都要给个非空名字

    def test_错误文本(self):
        self.assertEqual(绑定.错误文本(绑定.常量["AVERROR_EOF"]), "文件结束")
        self.assertIn("需要", 绑定.错误文本(绑定.常量["AVERROR_EAGAIN"]))


if __name__ == "__main__":
    unittest.main()
