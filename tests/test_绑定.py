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


class 大版本一致性测试(unittest.TestCase):
    """偏移表必须与运行时 FFmpeg 大版本一致 —— 这是最容易"悄悄错"的地方。

    Windows CI 实测：用 63 的偏移表去读 61 的库，帧时间戳读成 671088.64 秒，
    视频线程永远在等一个 67 万秒之后的时刻 → 25 帧的片子只出 1 帧，且不报任何错。
    所以绑定层要在启动时核对，不匹配就直接拦住。
    """

    def test_偏移表带主版本(self):
        from wangpan.ffmpeg import 偏移
        self.assertTrue(偏移.对应主版本.isdigit(), f"主版本异常：{偏移.对应主版本}")

    def test_当前库与偏移表一致(self):
        from wangpan.ffmpeg import 加载
        运行时 = str((int(加载.库("avcodec").avcodec_version()) >> 16) & 0xFF)
        from wangpan.ffmpeg import 偏移
        self.assertEqual(运行时, str(偏移.对应主版本),
                         "本机 FFmpeg 与偏移表大版本不一致（跑 tools/生成偏移.py 或换库）")
        绑定.取绑定().检查ABI()          # 一致时不该抛

    def test_不匹配时要明确报错(self):
        """假造一个"运行时是 99"的场景：**新建**实例时必须报错。

        注意用新建实例（绑定() 构造里就会自检），不要去动全局单例 ——
        动单例会让这条测试"看执行顺序吃饭"（先跑别的用例就过、单独跑就错）。
        """
        from unittest import mock
        from wangpan.ffmpeg import 加载, 绑定 as 绑定模块
        句柄 = 加载.库("avcodec")
        真的 = 句柄.avcodec_version
        with mock.patch.object(句柄, "avcodec_version", lambda: (99 << 16)):
            with self.assertRaises(RuntimeError) as 上:
                绑定模块.绑定()
            self.assertIn("大版本不匹配", str(上.exception))
            self.assertIn("99", str(上.exception))
        句柄.avcodec_version = 真的                     # 恢复，别影响别的用例
        绑定模块.取绑定().检查ABI()
