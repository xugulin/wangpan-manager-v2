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


class 输出尺寸线程安全测试(unittest.TestCase):
    """``设置输出尺寸`` 可能被**界面线程**调用（窗口 resize 时），它只能登记请求。

    背景（用户真机报的崩溃）：播放时点「面板」会 resize 主窗口 → 界面线程进
    ``设置输出尺寸``。原实现当场 ``sws_freeContext`` + 清零 ``_目标宽/_目标高``，
    而解码线程正在 ``取帧`` 里用同一个缩放器和这两个值 →
    use-after-free / 按旧尺寸往新缓冲区写。本机复现：播放中每 2ms 改一次尺寸，
    8 秒内**必崩**（3/3）。现在改成"界面只登记、解码线程在 _准备缩放 里应用"。
    """

    def test_登记请求不改解码状态(self):
        from tests.公用 import 自带素材
        from wangpan.ffmpeg import 加载
        from wangpan.player.解码 import 视频解码器
        from wangpan.player.解封装 import 输入
        if not 加载.可用():
            self.skipTest("本机没有可用的 libav")
        输入对象 = 输入.打开(str(自带素材()))
        解码器 = 视频解码器(输入对象.绑定, 输入对象.视频流)
        try:
            # 先真解一帧，让缩放器建起来（这样"界面线程不许碰它"才有意义）
            解码器.打开()
            解码器.设置输出尺寸(320, 180)
            解出 = None
            for _ in range(400):
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
                    解出 = 解码器.取帧() or 解出
                if 解出 is not None:
                    break
            self.assertIsNotNone(解出, "至少要能解出一帧")
            缩放器 = 解码器.缩放器
            self.assertNotEqual(缩放器, 0, "第一次取帧后缩放器该建好了")

            解码器.设置输出尺寸(1234, 567)
            # 关键：**立刻**不能改 _输出宽/_输出高，也不能碰缩放器
            self.assertEqual((解码器._输出宽, 解码器._输出高), (320, 180),
                             "界面线程调用时不许当场改解码状态（要与解码线程同步）")
            self.assertEqual(解码器._请求尺寸, (1234, 567), "请求要登记下来")
            self.assertEqual(解码器.缩放器, 缩放器,
                             "界面线程不许释放缩放器（那正是崩溃的原因）")
            # 解码线程侧的"安全点"才会应用它
            解码器._应用输出尺寸请求()
            self.assertEqual((解码器._输出宽, 解码器._输出高), (1234, 567))
            self.assertEqual(解码器.缩放器, 0, "尺寸变了要丢掉旧缩放器，等下次取帧重建")
        finally:
            解码器.关()
            输入对象.关闭()


if __name__ == "__main__":
    unittest.main()
