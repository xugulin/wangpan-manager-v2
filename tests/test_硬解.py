"""M2 硬解：真设备上验证硬解真的用上了，并且失败/关闭时能干净回退软解。

判据不是"日志里写了硬解"，而是：
* 解码器自报的 :attr:`视频解码器.硬解帧数` > 0（说明硬件帧真的被拷回来用过）；
* 关掉硬解（`V2_不要硬解=1`）后必须走软解且照样出帧；
* 两种情况解出的帧数与尺寸一致（硬解不能少帧、不能变尺寸）。
"""

from __future__ import annotations

import os
import time
import unittest

try:                        # resource 是 Unix 专有（Windows 上没有这个模块）
    import resource
except ImportError:         # pragma: no cover - Windows
    resource = None         # type: ignore[assignment]

from tests.公用 import 有ffmpeg, 造素材, 临时目录

from wangpan.player.解码 import 视频解码器
from wangpan.player.解封装 import 输入


def 有渲染节点() -> bool:
    return os.path.exists("/dev/dri/renderD128") or os.path.exists(
        "/dev/dri/renderD129")


class 硬解测试(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.临时 = 临时目录()
        cls.素材 = 造素材(cls.临时.name, 秒=1.5, 带声音=False)

    @classmethod
    def tearDownClass(cls):
        cls.临时.cleanup()

    def _解一遍(self, 硬解: bool, 输出: tuple[int, int] = (320, 180)):
        旧 = os.environ.get("V2_不要硬解")
        if 硬解:
            os.environ.pop("V2_不要硬解", None)
        else:
            os.environ["V2_不要硬解"] = "1"
        try:
            输入对象 = 输入.打开(str(self.素材))
            解码器 = 视频解码器(输入对象.绑定, 输入对象.视频流)
            解码器.设置输出尺寸(*输出)
            解码器.打开()
            帧数 = 0
            cpu0 = (resource.getrusage(resource.RUSAGE_SELF)
                    if resource is not None else None)
            try:
                while True:
                    包 = 输入对象.读包()
                    if 包 is None:
                        解码器.送空包()
                    elif 包["流序号"] == 输入对象.视频流.序号:
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
                            帧数 += 1
                            尺寸 = (帧.宽, 帧.高)
                    if 包 is None:
                        break
            finally:
                cpu1 = (resource.getrusage(resource.RUSAGE_SELF)
                        if resource is not None else None)
                解码器.关()
                输入对象.关闭()
            return {"帧数": 帧数, "尺寸": 尺寸, "硬解": 解码器.硬解,
                    "硬解帧": 解码器.硬解帧数,
                    "cpu秒": ((cpu1.ru_utime + cpu1.ru_stime)
                            - (cpu0.ru_utime + cpu0.ru_stime)
                            if (cpu0 is not None and cpu1 is not None) else 0.0)}
        finally:
            if 旧 is None:
                os.environ.pop("V2_不要硬解", None)
            else:
                os.environ["V2_不要硬解"] = 旧

    def test_软解永远可用(self):
        结果 = self._解一遍(硬解=False)
        self.assertGreater(结果["帧数"], 20)
        self.assertIn("软解", 结果["硬解"])
        self.assertEqual(结果["硬解帧"], 0)

    @unittest.skipUnless(有渲染节点(), "本机没有 /dev/dri/renderD*（无硬件解码设备）")
    def test_硬解真的用上了(self):
        结果 = self._解一遍(硬解=True)
        self.assertGreater(结果["帧数"], 20)
        self.assertIn("硬解", 结果["硬解"], f"应该走硬解，实际：{结果['硬解']}")
        self.assertGreater(结果["硬解帧"], 0, "硬件帧数为 0，说明 get_format 没选硬件格式")

    @unittest.skipUnless(有渲染节点(), "本机没有 /dev/dri/renderD*")
    def test_硬解与软解帧数一致(self):
        软 = self._解一遍(硬解=False)
        硬 = self._解一遍(硬解=True)
        self.assertEqual(软["帧数"], 硬["帧数"], "硬解不能少帧")
        self.assertEqual(软["尺寸"], 硬["尺寸"], "硬解不能改变输出尺寸")

    def test_输出尺寸被遵守(self):
        结果 = self._解一遍(硬解=False, 输出=(160, 90))
        self.assertEqual(结果["尺寸"], (160, 90))

    def test_不放大(self):
        """素材只有 320x180：要求输出 1280x720 时不该放大（省一次无谓的放大）。"""
        结果 = self._解一遍(硬解=False, 输出=(1280, 720))
        self.assertEqual(结果["尺寸"], (320, 180))


if __name__ == "__main__":
    unittest.main()
