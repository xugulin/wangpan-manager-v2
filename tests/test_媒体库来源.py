"""媒体库"多个文件夹（含网盘）"的测试。

对应需求：*媒体库改为可添加多个文件夹，包括网盘内的文件夹*。
"""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from tests.公用 import 临时目录

from wangpan.ui.媒体库来源 import (来源, 来源清单, 库路径, 拆库路径,
                              本地视频们, 远端视频们)


class 来源清单测试(unittest.TestCase):
    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.路径 = Path(self.临时.name) / "媒体库来源.json"
        self.清单 = 来源清单(self.路径)

    def test_能加多个文件夹(self):
        self.assertTrue(self.清单.加本地("/mnt/电影"))
        self.assertTrue(self.清单.加本地("/mnt/纪录片"))
        self.assertTrue(self.清单.加网盘("guangya", "/电影", "光鸭·电影"))
        self.assertEqual(len(self.清单), 3, "应该记住三个文件夹")
        # 重复加同一个不算
        self.assertFalse(self.清单.加本地("/mnt/电影"))
        self.assertFalse(self.清单.加网盘("guangya", "/电影", "光鸭·电影"))
        self.assertEqual(len(self.清单), 3)

    def test_存盘并能读回来(self):
        self.清单.加本地("/mnt/电影")
        self.清单.加网盘("baidu", "/动画", "百度·动画")
        又 = 来源清单(self.路径)
        项们 = 又.全部()
        self.assertEqual(len(项们), 2)
        self.assertEqual({(x.类型, x.网盘, x.路径) for x in 项们},
                         {("本地", "", "/mnt/电影"), ("网盘", "baidu", "/动画")})
        数据 = json.loads(self.路径.read_text(encoding="utf-8"))
        self.assertIn("来源", 数据)

    def test_移除(self):
        self.清单.加本地("/mnt/a")
        self.清单.加本地("/mnt/b")
        self.assertTrue(self.清单.移除(0))
        self.assertEqual([x.路径 for x in self.清单.全部()], ["/mnt/b"])
        self.assertFalse(self.清单.移除(99), "越界下标要返回 False")

    def test_清单文件坏了也不崩(self):
        self.路径.write_text("{ 这不是 json", encoding="utf-8")
        self.assertEqual(len(来源清单(self.路径)), 0, "坏文件当空清单，不该抛异常")

    def test_一句话能认出来(self):
        self.assertIn("💾", 来源(类型="本地", 路径="/mnt/电影").一句话())
        self.assertIn("☁️", 来源(类型="网盘", 网盘="guangya",
                            路径="/电影").一句话())


class 库路径测试(unittest.TestCase):
    def test_拼与拆(self):
        标记 = 库路径("guangya", "/电影/沙丘.mkv")
        self.assertEqual(标记, "guangya:/电影/沙丘.mkv")
        self.assertEqual(拆库路径(标记, ["guangya", "baidu"]),
                         ("guangya", "/电影/沙丘.mkv"))

    def test_本地路径不会被误判成网盘(self):
        """Windows 盘符里也有冒号 —— 只能按**已知网盘标识**判断。"""
        self.assertEqual(拆库路径(r"C:\电影\沙丘.mkv", ["guangya"]),
                         ("", r"C:\电影\沙丘.mkv"))
        self.assertEqual(拆库路径("/mnt/电影/沙丘.mkv", ["guangya"]),
                         ("", "/mnt/电影/沙丘.mkv"))

    def test_网盘名恰好是本地路径前缀时才认(self):
        self.assertEqual(拆库路径("guangya:/x.mkv", ["guangya"])[0], "guangya")
        self.assertEqual(拆库路径("guangya:/x.mkv", ["baidu"])[0], "",
                         "没在已知清单里的前缀不该当成网盘")


class 扫描测试(unittest.TestCase):
    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)

    def test_本地扫到视频且认深度(self):
        电影 = self.根 / "电影"
        (电影 / "子目录" / "更深").mkdir(parents=True)
        (电影 / "a.mp4").write_bytes(b"x")
        (电影 / "说明.txt").write_bytes(b"x")
        (电影 / "子目录" / "b.mkv").write_bytes(b"x")
        (电影 / "子目录" / "更深" / "c.mp4").write_bytes(b"x")
        默认 = 本地视频们(str(电影))
        self.assertEqual([Path(x).name for x in 默认], ["a.mp4", "b.mkv", "c.mp4"])
        # `最大深度` = **往下最多钻几层**（1 = 根 + 一层子目录）
        self.assertEqual([Path(x).name for x in 本地视频们(str(电影), 最大深度=1)],
                         ["a.mp4", "b.mkv"], "深度 1：本层 + 一层子目录")
        self.assertEqual([Path(x).name for x in 本地视频们(str(电影), 最大深度=2)],
                         ["a.mp4", "b.mkv", "c.mp4"], "深度 2 能钻到更深那层")

    def test_不存在的目录返回空(self):
        self.assertEqual(本地视频们(str(self.根 / "没有这个目录")), [])

    def test_网盘递归列目录(self):
        树 = {
            "/电影": [{"name": "沙丘.2021.mkv", "path": "/电影/沙丘.2021.mkv",
                     "is_dir": False, "size": 10},
                    {"name": "封面.jpg", "path": "/电影/封面.jpg",
                     "is_dir": False, "size": 1},
                    {"name": "合集", "path": "/电影/合集", "is_dir": True}],
            "/电影/合集": [{"name": "黑客帝国.mp4", "path": "/电影/合集/黑客帝国.mp4",
                       "is_dir": False, "size": 10}],
        }
        问过: list[str] = []

        def 列(标识, 路径):
            问过.append(路径)
            return 树.get(路径, [])

        找到 = 远端视频们("guangya", "/电影", 列)
        self.assertEqual(找到, ["guangya:/电影/合集/黑客帝国.mp4",
                            "guangya:/电影/沙丘.2021.mkv"])
        self.assertIn("/电影", 问过, "根目录要**原样**问，不能自己加结尾斜杠")
        self.assertIn("/电影/合集", 问过, "子目录要钻进去")

    def test_网盘列目录失败不影响其它目录(self):
        def 列(标识, 路径):
            if 路径 == "/电影/坏的":
                raise RuntimeError("桥挂了")
            return {
                "/电影": [{"name": "坏的", "path": "/电影/坏的", "is_dir": True},
                        {"name": "a.mp4", "path": "/电影/a.mp4", "is_dir": False}],
            }.get(路径, [])
        找到 = 远端视频们("guangya", "/电影", 列)
        self.assertEqual(找到, ["guangya:/电影/a.mp4"],
                         "一个目录列不了，别的目录照样要扫出来")


class 远端单元测试(unittest.TestCase):
    """网盘文件造出来的待刮削单元：**不碰本地文件系统**。"""

    def test_按文件名解析出电影与剧集(self):
        from wangpan.scrape.扫描 import 造远端单元
        from wangpan.scrape.模型 import 媒体类型
        单元们 = 造远端单元("guangya", "/电影", [
            "/电影/沙丘.2021.2160p.mkv", "/电影/剧/怪奇物语.S01E01.mkv",
            "/电影/说明.txt"])
        self.assertEqual(len(单元们), 2, "非视频要被过滤掉")
        按名 = {u.标题: u for u in 单元们}
        电影 = next(u for u in 单元们 if u.类型 is 媒体类型.电影)
        self.assertEqual(电影.年份, 2021)
        self.assertEqual(str(电影.视频们[0]), "guangya:/电影/沙丘.2021.2160p.mkv")
        剧 = next(u for u in 单元们 if u.类型 is 媒体类型.剧集)
        self.assertEqual((剧.季号, 剧.集号), (1, 1))
        self.assertTrue(all(not Path(str(u.路径)).is_absolute() or
                            str(u.路径).startswith("guangya:")
                            for u in 单元们), "路径必须是 标识:远端路径 这种写法")
        self.assertEqual(按名["沙丘"].备注, "网盘文件（按文件名刮削）")

    def test_刮削服务用喂进来的远端单元_不去碰磁盘(self):
        """远端条目必须走 `远端单元` 这条路 —— 一旦去 `_造单元`（读本地目录）就会抛异常。"""
        from wangpan.scrape.服务 import 刮削服务
        from wangpan.scrape.库 import 资料库
        from wangpan.scrape.扫描 import 造远端单元
        临时 = 临时目录()
        self.addCleanup(临时.cleanup)
        库 = 资料库(Path(临时.name) / "资料库.db")
        self.addCleanup(库.关闭)
        单元们 = 造远端单元("guangya", "/电影", ["/电影/沙丘.2021.mkv"])
        服务 = 刮削服务(库, None, None, None, 日志回调=lambda _t: None,
                    远端单元={str(u.路径): u for u in 单元们})

        def 不许():
            raise AssertionError("远端条目不该去造本地单元（那会读本地目录）")

        服务._造单元 = 不许                    # 一旦走本地那条路就炸
        登记, 摘要 = 服务.记远端单元(单元们)
        self.assertEqual(登记, 1)
        self.assertIn("1", 摘要)
        任务 = 库.取任务("待处理", 10)
        self.assertEqual(len(任务), 1)
        self.assertEqual(任务[0]["路径"], "guangya:/电影",
                         "任务路径用 标识:远端目录（与单元路径一致，才查得到远端单元表）")


if __name__ == "__main__":
    unittest.main()
