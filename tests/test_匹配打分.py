"""匹配打分的测试（**全部离线**，不碰网络也不需要真 TMDB 数据）。

分数断言都写成**区间**而不是写死某个小数：权重将来会调，但"完全一致要高分、
`沙丘` vs `沙丘2` 不能自动采纳、年份差 1 年还能用但分数下降"这些性质不能变。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.公用 import 临时目录

from wangpan.scrape.匹配打分 import (权重, 从文件名猜查询词, 区分季与剧场版, 打分候选,
                              是包含关系, 规范化标题, 标题相似度, 排序候选, 自动决策)
from wangpan.scrape.模型 import 匹配候选, 媒体类型
from wangpan.scrape.命名解析 import 解析结果


def 电影候选(标题: str, 年份=None, 原名: str = "", 热度: float = 0.0,
          标识: str = "1", 类型: 媒体类型 = 媒体类型.电影) -> 匹配候选:
    return 匹配候选(来源标识=标识, 标题=标题, 原名=原名, 年份=年份, 类型=类型, 热度=热度)


def 电影目标(标题: str = "沙丘", 年份=None) -> 解析结果:
    return 解析结果(类型="电影", 标题=标题, 年份=年份)


class 规范化测试(unittest.TestCase):
    def test_全角标点大小写归一(self):
        self.assertEqual(规范化标题("Dune: Part Two（2024）"), "dune part two 2024")
        self.assertEqual(规范化标题("Spider-Man"), 规范化标题("Spider Man"))
        self.assertEqual(规范化标题("你的名字。"), 规范化标题("你的名字"))

    def test_季信息不能丢(self):
        """"第2季"要变成标记留在结尾，绝不能直接删掉（否则第二季会配到第一季）。"""
        self.assertEqual(规范化标题("沙丘 第二季"), "沙丘 s2")
        self.assertEqual(规范化标题("Show Season 2"), "show s2")
        self.assertNotEqual(规范化标题("沙丘 第二季"), 规范化标题("沙丘"))
        self.assertNotEqual(规范化标题("沙丘 第二季"), 规范化标题("沙丘 第三季"))

    def test_剧场版信息保留(self):
        self.assertEqual(规范化标题("名侦探柯南 剧场版"), "名侦探柯南 剧场版")
        self.assertNotEqual(规范化标题("名侦探柯南 剧场版"), 规范化标题("名侦探柯南"))

    def test_区分季与剧场版分别返回(self):
        核心, 季号, 剧场版 = 区分季与剧场版("名侦探柯南 第2季 剧场版")
        self.assertEqual(季号, 2)
        self.assertTrue(剧场版)
        self.assertNotIn("第2季", 核心)

    def test_区分季_各种写法(self):
        for 文本, 期望 in (("沙丘 第二季", 2), ("沙丘 第2季", 2), ("Show Season 3", 3),
                        ("Show S04", 4), ("Show 5th Season", 5)):
            with self.subTest(文本=文本):
                self.assertEqual(区分季与剧场版(文本)[1], 期望)


class 相似度测试(unittest.TestCase):
    def test_完全一致(self):
        self.assertEqual(标题相似度("沙丘", "沙丘"), 1.0)
        self.assertEqual(标题相似度("Dune", "dune"), 1.0)
        self.assertEqual(标题相似度("Spider-Man", "Spider Man"), 1.0)

    def test_包含关系给0_9(self):
        """官方要求：包含关系给 0.9 而不是 1.0。"""
        self.assertEqual(标题相似度("沙丘", "沙丘2"), 0.9)
        self.assertEqual(标题相似度("Dune", "Dune Part Two"), 0.9)
        self.assertTrue(是包含关系("沙丘", "沙丘2"))

    def test_完全无关(self):
        self.assertEqual(标题相似度("沙丘", "星球大战"), 0.0)
        self.assertEqual(标题相似度("", "沙丘"), 0.0)

    def test_拼写差异是中间值(self):
        值 = 标题相似度("The Matrix", "The Matrx")
        self.assertGreater(值, 0.5)
        self.assertLess(值, 1.0)

    def test_不相等的相似字符串不算包含(self):
        self.assertFalse(是包含关系("沙丘", "沙丘"))


class 打分之性质测试(unittest.TestCase):
    def test_完全一致拿高分(self):
        分, 理由 = 打分候选(电影候选("沙丘", 2021, 原名="Dune", 热度=100.0),
                       电影目标("沙丘", 2021))
        self.assertGreaterEqual(分, 95.0)
        self.assertLessEqual(分, 100.0)
        self.assertIn("标题完全一致", 理由)

    def test_沙丘2不能被自动采纳(self):
        """本次实现最要紧的一条：续集不能靠"标题相似"混过去。"""
        分, 理由 = 打分候选(电影候选("沙丘2", 2024, 原名="Dune: Part Two"),
                       电影目标("沙丘", 2021))
        self.assertGreaterEqual(分, 45.0)          # 标题确实像，所以不是低分
        self.assertLess(分, 78.0)                  # 但绝不能到自动采纳线
        self.assertIn("包含关系", 理由)
        采纳, 需人工, 说明 = 自动决策([电影候选("沙丘2", 2024, 原名="Dune: Part Two")],
                             电影目标("沙丘", 2021))
        self.assertIsNone(采纳)
        self.assertTrue(需人工)
        self.assertIn("人工", 说明)

    def test_年份未知时包含关系也要人工确认(self):
        """`沙丘2` 在目标没年份时分数会到 79（刚好过线）—— 靠"包含关系"这条规则兜住。"""
        采纳, 需人工, 说明 = 自动决策([电影候选("沙丘2", 2024)], 电影目标("沙丘", None))
        self.assertIsNone(采纳)
        self.assertTrue(需人工)
        self.assertIn("包含", 说明)

    def test_年份差一年还能接受但分数下降(self):
        同年, _ = 打分候选(电影候选("沙丘", 2022), 电影目标("沙丘", 2022))
        差一年, 理由 = 打分候选(电影候选("沙丘", 2021), 电影目标("沙丘", 2022))
        self.assertLess(差一年, 同年)
        self.assertGreaterEqual(差一年, 78.0)       # 跨年上映很常见，仍然可用
        self.assertIn("年份差 1 年", 理由)

    def test_年份差两年归零(self):
        差两年, 理由 = 打分候选(电影候选("沙丘", 2024), 电影目标("沙丘", 2021))
        差一年, _ = 打分候选(电影候选("沙丘", 2020), 电影目标("沙丘", 2021))
        self.assertLess(差两年, 差一年)
        self.assertIn("年份不符", 理由)
        self.assertLess(差两年, 78.0)

    def test_类型不符要扣分(self):
        目标 = 解析结果(类型="集", 标题="剧名", 年份=2011)
        电影, 理由 = 打分候选(电影候选("剧名", 2011), 目标)
        剧集, _ = 打分候选(电影候选("剧名", 2011, 类型=媒体类型.剧集), 目标)
        self.assertLess(电影, 剧集)
        self.assertIn("结构不符", 理由)

    def test_类型不符不许自动采纳(self):
        """结构只占 10 分，标题年份全对时类型错了还有 90 分 —— 必须靠硬规则拦住。"""
        目标 = 解析结果(类型="集", 标题="剧名", 年份=2011)
        采纳, 需人工, 说明 = 自动决策([电影候选("剧名", 2011)], 目标)
        self.assertIsNone(采纳)
        self.assertTrue(需人工)
        self.assertIn("类型不符", 说明)

    def test_别名命中能救回不同写法(self):
        无别名, _ = 打分候选(电影候选("Dune", 2021), 电影目标("沙丘", 2021))
        有别名, 理由 = 打分候选(电影候选("Dune", 2021), 电影目标("沙丘", 2021),
                           ["Dune", "沙丘"])
        self.assertGreater(有别名, 无别名)
        self.assertGreaterEqual(有别名, 78.0)
        self.assertIn("别名", 理由)

    def test_热度最多加5分(self):
        低热, _ = 打分候选(电影候选("沙丘", 2021, 热度=0.0), 电影目标("沙丘", 2021))
        高热, _ = 打分候选(电影候选("沙丘", 2021, 热度=99999.0), 电影目标("沙丘", 2021))
        self.assertLessEqual(高热 - 低热, 权重["热度上限"] + 0.01)
        self.assertLessEqual(高热, 100.0)

    def test_热度不当评分用(self):
        """`popularity` 是热度不是评分：它只能当破平手段（最多 +5）。"""
        高热低分, _ = 打分候选(匹配候选(来源标识="1", 标题="沙丘", 年份=2021, 热度=5000.0,
                                  类型=媒体类型.电影, 额外={"评分": 1.0}),
                         电影目标("沙丘", 2021))
        self.assertLessEqual(高热低分, 100.0)

    def test_打分写回候选对象(self):
        候选 = 电影候选("沙丘", 2021)
        分, 理由 = 打分候选(候选, 电影目标("沙丘", 2021))
        self.assertEqual(候选.分数, 分)
        self.assertEqual(候选.理由, 理由)
        细节 = 候选.额外["匹配细节"]
        self.assertEqual(细节["名称一致度"], 1.0)
        self.assertFalse(细节["包含关系"])

    def test_分数恒在0到100之间(self):
        for 候选 in (电影候选("完全不相干的片名", 1900),
                   电影候选("沙丘", 2021, 热度=1e9),
                   电影候选("", None)):
            with self.subTest(候选=候选.标题):
                分, _ = 打分候选(候选, 电影目标("沙丘", 2021))
                self.assertGreaterEqual(分, 0.0)
                self.assertLessEqual(分, 100.0)


class 排序与决策测试(unittest.TestCase):
    def setUp(self):
        self.目标 = 电影目标("沙丘", 2021)

    def test_排序候选_从高到低(self):
        候选们 = [电影候选("沙丘2", 2024, 标识="2"), 电影候选("沙丘", 2021, 标识="1"),
                电影候选("星球大战", 1977, 标识="3")]
        排序后 = 排序候选(候选们, self.目标)
        self.assertEqual([c.标题 for c in 排序后], ["沙丘", "沙丘2", "星球大战"])
        self.assertGreaterEqual(排序后[0].分数, 排序后[1].分数)

    def test_排序不改动原列表顺序(self):
        候选们 = [电影候选("沙丘2", 2024, 标识="2"), 电影候选("沙丘", 2021, 标识="1")]
        排序候选(候选们, self.目标)
        self.assertEqual([c.标题 for c in 候选们], ["沙丘2", "沙丘"])

    def test_自动决策_唯一高分直接采纳(self):
        采纳, 需人工, 说明 = 自动决策([电影候选("沙丘", 2021, 标识="1"),
                              电影候选("星球大战", 1977, 标识="2")], self.目标)
        self.assertIsNotNone(采纳)
        self.assertFalse(需人工)
        self.assertEqual(采纳.标题, "沙丘")
        self.assertIn("自动采纳", 说明)

    def test_自动决策_两个接近要人工(self):
        候选们 = [电影候选("沙丘", 2021, 标识="1"),
                电影候选("沙丘 2021", 2021, 标识="2")]
        采纳, 需人工, 说明 = 自动决策(候选们, self.目标)
        self.assertIsNone(采纳)
        self.assertTrue(需人工)
        self.assertIn("太接近", 说明)

    def test_自动决策_分数不够要人工(self):
        采纳, 需人工, 说明 = 自动决策([电影候选("星球大战", 1977)], self.目标)
        self.assertIsNone(采纳)
        self.assertTrue(需人工)
        self.assertIn("低于自动阈值", 说明)

    def test_自动决策_没有候选(self):
        采纳, 需人工, 说明 = 自动决策([], self.目标)
        self.assertIsNone(采纳)
        self.assertTrue(需人工)
        self.assertIn("没有候选", 说明)

    def test_自动决策_自动阈值可配(self):
        候选 = [电影候选("沙丘", 2021)]               # 标题年份全对但解析年份差 1 年 → 85 分
        采纳, 需人工, _ = 自动决策(候选, 电影目标("沙丘", 2022))
        self.assertIsNotNone(采纳)
        self.assertFalse(需人工)
        采纳2, 需人工2, 说明 = 自动决策([电影候选("沙丘", 2021)], 电影目标("沙丘", 2022),
                                  自动阈值=90.0)
        self.assertIsNone(采纳2)
        self.assertTrue(需人工2)
        self.assertIn("低于自动阈值", 说明)

    def test_自动决策_差距阈值可配(self):
        两个一样的 = lambda: [电影候选("沙丘", 2021, 标识="1"),       # noqa: E731
                          电影候选("沙丘", 2021, 标识="2")]
        _, 需人工, 说明 = 自动决策(两个一样的(), self.目标)
        self.assertTrue(需人工)
        self.assertIn("太接近", 说明)
        采纳, 需人工2, _ = 自动决策(两个一样的(), self.目标, 差距阈值=0.0)
        self.assertIsNotNone(采纳)
        self.assertFalse(需人工2)


class 查询词测试(unittest.TestCase):
    def setUp(self):
        self._临时 = 临时目录()
        self.根 = Path(self._临时.__enter__())

    def tearDown(self):
        self._临时.__exit__(None, None, None)

    def _放(self, 相对: str) -> Path:
        路径 = self.根 / 相对
        路径.parent.mkdir(parents=True, exist_ok=True)
        路径.write_text("", encoding="utf-8")
        return 路径

    def test_电影_标题加年份优先(self):
        路径 = self._放("沙丘 (2021)/沙丘 (2021).mkv")
        词们 = 从文件名猜查询词(路径)
        self.assertEqual(词们[0], "沙丘 2021")
        self.assertIn("沙丘", 词们)

    def test_番剧_去掉字幕组前缀(self):
        路径 = self._放("[Nekomoe kissaten][Movie Title][1080p][BDRip].mkv")
        词们 = 从文件名猜查询词(路径)
        self.assertIn("Movie Title", 词们)

    def test_剧集_去季标识(self):
        路径 = self._放("葬送的芙莉莲 第二季/Season 02/葬送的芙莉莲 S02E01.mkv")
        词们 = 从文件名猜查询词(路径)
        self.assertTrue(词们)
        self.assertIn("葬送的芙莉莲", 词们)

    def test_季标识那一路查询词(self):
        路径 = self._放("Show Name 第二季/Show Name 第二季 S01E01.mkv")
        词们 = 从文件名猜查询词(路径)
        self.assertIn("Show Name", 词们)

    def test_空路径不炸(self):
        self.assertEqual(从文件名猜查询词(Path("")), [])

    def test_最多四个且去重(self):
        路径 = self._放("沙丘 (2021)/沙丘 (2021).mkv")
        词们 = 从文件名猜查询词(路径)
        self.assertLessEqual(len(词们), 4)
        self.assertEqual(len(词们), len(set(词们)))


if __name__ == "__main__":
    unittest.main()
