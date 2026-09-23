"""逐集弹幕记忆：记住"这个文件对应哪一集"，重启后自动装好；用户选定的不许被自动覆盖。"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.公用 import 临时目录

from wangpan.danmaku.引擎 import 弹幕控制器
from wangpan.danmaku.记忆 import 弹幕记忆, 默认记忆路径
from wangpan.danmaku.模型 import 弹幕, 弹幕池
from wangpan.danmaku.源.接口 import 素材信息


class 假源:
    标识 = "animeko"
    名字 = "假源"

    def __init__(self) -> None:
        self.取的: list[str] = []

    def 能匹配(self) -> bool:
        return True

    def 取弹幕(self, 标识: str) -> 弹幕池:
        self.取的.append(str(标识))
        return 弹幕池([弹幕(1000, f"来自{标识}")])

    def 匹配(self, 素材) -> list:
        return []

    def 关闭(self) -> None:
        return


def 造候选(标识="1227087", 标题="葬送的芙莉莲", 集标题="冒险的结束", 集号="1", 源="animeko"):
    return type("候选", (), {"标识": 标识, "标题": 标题, "集标题": 集标题,
                           "集号": 集号, "原始": {"源": 源}})()


class 记忆测试(unittest.TestCase):
    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.路径 = Path(self.临时.name) / "弹幕记忆.json"
        self.记忆 = 弹幕记忆(self.路径)

    def test_记与取(self):
        self.记忆.记("/影片/a.mkv", "animeko", "111", "作品", "第一集", "1")
        条 = self.记忆.取("/影片/a.mkv")
        self.assertIsNotNone(条)
        self.assertEqual(条.标识, "111")
        self.assertFalse(条.用户选定)
        self.assertIn("自动匹配", 条.可读())

    def test_落盘与读回(self):
        self.记忆.记("/影片/a.mkv", "animeko", "111", "作品")
        self.assertTrue(self.记忆.存())
        新 = 弹幕记忆(self.路径)
        self.assertEqual(新.取("/影片/a.mkv").标识, "111")

    def test_用户选定不被自动覆盖(self):
        self.记忆.记("/影片/a.mkv", "animeko", "111", "作品")
        self.记忆.记("/影片/a.mkv", "dandanplay", "222", "别的", 用户选定=True)
        self.assertEqual(self.记忆.取("/影片/a.mkv").标识, "222")
        self.记忆.记("/影片/a.mkv", "animeko", "333", "又自动")
        self.assertEqual(self.记忆.取("/影片/a.mkv").标识, "222",
                         "人定过的，自动匹配不许改")
        self.assertTrue(self.记忆.取("/影片/a.mkv").用户选定)

    def test_直链忽略签名(self):
        self.assertEqual(弹幕记忆.键("https://cdn/a.mp4?sign=AAA"),
                         弹幕记忆.键("https://cdn/a.mp4?sign=BBB"))

    def test_键是绝对路径(self):
        键 = 弹幕记忆.键("相对路径.mp4")
        self.assertTrue(Path(键).is_absolute())

    def test_忘掉与清空(self):
        self.记忆.记("/影片/a.mkv", "animeko", "111")
        self.assertTrue(self.记忆.忘掉("/影片/a.mkv"))
        self.assertIsNone(self.记忆.取("/影片/a.mkv"))
        self.记忆.记("/影片/b.mkv", "animeko", "222")
        self.记忆.清空()
        self.assertEqual(len(self.记忆.全部()), 0)

    def test_坏文件不影响(self):
        self.路径.write_text("{坏 JSON", encoding="utf-8")
        记忆 = 弹幕记忆(self.路径)
        self.assertEqual(len(记忆.全部()), 0)
        self.assertIsNone(记忆.取("/x.mkv"))

    def test_统计(self):
        self.记忆.记("/a.mkv", "animeko", "1")
        self.记忆.记("/b.mkv", "animeko", "2", 用户选定=True)
        self.记忆.记("/c.mkv", "dandanplay", "3")
        统计 = self.记忆.统计()
        self.assertEqual(统计["总数"], 3)
        self.assertEqual(统计["用户选定"], 1)
        self.assertEqual(统计["按源"], {"animeko": 2, "dandanplay": 1})

    def test_默认路径在项目数据目录(self):
        self.assertIn("数据", str(默认记忆路径()))
        self.assertTrue(str(默认记忆路径()).endswith("弹幕记忆.json"))


class 控制器记忆测试(unittest.TestCase):
    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.路径 = Path(self.临时.name) / "弹幕记忆.json"
        self.素材 = 素材信息(路径=Path("/影片/某片 S01E01.mkv"),
                        文件名="某片 S01E01.mkv", 集号=1)

    def _控(self):
        控 = 弹幕控制器()
        控.记忆 = 弹幕记忆(self.路径)
        return 控

    def test_用户选定会被记住(self):
        控 = self._控()
        源 = 假源()
        结果 = 控.用候选装载(self.素材, 造候选(), [源])
        self.assertTrue(结果.成功)
        self.assertEqual(源.取的, ["1227087"])
        self.assertTrue(控.记忆.取("/影片/某片 S01E01.mkv").用户选定)

    def test_重启后走记忆不再匹配(self):
        控 = self._控()
        控.用候选装载(self.素材, 造候选(), [假源()])
        控2 = self._控()                      # 模拟重启
        源2 = 假源()
        结果 = 控2.装载(self.素材, [源2])
        self.assertTrue(结果.成功)
        self.assertIn("记忆", 结果.来源)
        self.assertEqual(源2.取的, ["1227087"], "走记忆时只取那一集的弹幕")
        self.assertEqual(控2.统计["记忆命中"], 1)

    def test_记忆取不到时退回匹配(self):
        控 = self._控()
        控.用候选装载(self.素材, 造候选(标识="不存在的集"), [假源()])

        class 会失败的源(假源):
            def 取弹幕(self, 标识):
                if 标识 == "不存在的集":
                    raise RuntimeError("404")
                return super().取弹幕(标识)

        控2 = self._控()
        源2 = 会失败的源()
        结果 = 控2.装载(self.素材, [源2])
        self.assertFalse(结果.成功, "记忆失效时不该假装成功")
        self.assertTrue(结果.需要人工确认 or "取不到" in 结果.说明 or "没匹配" in 结果.说明,
                        f"应该给出可读的说明：{结果.说明}")


if __name__ == "__main__":
    unittest.main()
