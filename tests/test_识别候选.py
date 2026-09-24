"""③ 候选生成（``wangpan/identify/候选.py``）的单测：**纯函数，全部离线**。

覆盖的重点（每条都对应一个真机踩过的坑或一条需求）：
1. 被改名规避限制的文件（``146.SDR.8bit…mp4``）靠**目录链**找回作品名"仙逆"；
2. 中英混排标题**分别**产出中文段与英文段（``三体 Three-Body`` 两条都要搜）；
3. 目录链**每一层**都贡献查询词，越近的层权重越高；
4. 通用分类目录（``电影``/``动漫``）与画质标签（``1080p``/``x265``/``中字``）不许变成查询词；
5. 别名库命中给**最高**权重（P4 的"越用越准"落在这里）；
6. **纯数字的两面**：集号/季号要丢，片名不许丢（``2012`` / ``1984`` / ``1917`` /
   ``Blade Runner 2049`` / ``10 Cloverfield Lane`` —— 一刀切删数字会把这些片子删瞎）；
7. 年份是**独立的过滤条件**，不拼进查询词；
8. 去重与"这条查询词还从哪些来源出现过"的证据记录；
9. 鸭子类型：dataclass / dict 两种入参都能用（P1 的结果模型还没定死）。
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Optional

from wangpan.identify.候选 import (最多查询词, 去标签, 是标签, 是纯数字, 权重表, 生成查询,
                              来源_中文段, 来源_别名, 来源_文件标题, 来源_目录标题,
                              来源_英文段, 归一类型, 查询集合)


@dataclass
class 假结构:
    """P1 ``识别结构`` 的最小替身（只放 ③ 真的会取的字段）。"""

    标题: str = ""
    年份: Optional[int] = None
    类型: str = "unknown"
    季: Optional[int] = None
    集: Optional[int] = None
    标题候选们: list = field(default_factory=list)


def 映射(结果: 查询集合) -> dict:
    """查询词 → (来源, 权重)，方便断言。"""
    return {x.文本: (x.来源, x.权重) for x in 结果.查询词们}


class 目录链兜底测试(unittest.TestCase):
    """真机那条"改名规避限制"的剧：文件标题是垃圾，作品名只在目录里。"""

    def test_改名文件从目录链拿到作品名(self):
        结构 = 假结构(标题="146 SDR 60fps DDP5 1", 类型="tv", 季=1, 集=146)
        结果 = 生成查询(结构, 目录链=("Season 01", "仙逆.4K高码.SDR.60fps"))
        文本们 = 结果.文本们()
        self.assertIn("仙逆", 文本们)
        映射表 = 映射(结果)
        self.assertEqual(映射表["仙逆"][0], 来源_目录标题)
        # 集号不许当查询词（搜 "146" 只会拿回一堆不相关的东西）
        self.assertNotIn("146", 文本们)
        # 目录里的画质标签一个都不许漏出来
        for 噪声 in ("1080p", "2160p", "60fps", "SDR", "4K高码", "Season 01"):
            self.assertNotIn(噪声, 文本们)

    def test_目录层级越近权重越高(self):
        结果 = 生成查询(假结构(类型="tv"), 目录链=("某剧", "动漫", "2024"))
        权重 = {x.文本: x.权重 for x in 结果.查询词们}
        self.assertGreater(权重["某剧"], 权重.get("动漫", 0))
        self.assertIn("某剧", 权重)

    def test_目录链每一层都能贡献(self):
        结果 = 生成查询(假结构(), 目录链=("Season 01", "葬送的芙莉莲", "番剧"))
        self.assertIn("葬送的芙莉莲", 结果.文本们())

    def test_通用分类目录被丢掉并写明原因(self):
        结果 = 生成查询(假结构(标题="", 类型="movie"), 目录链=("电影",))
        self.assertNotIn("电影", 结果.文本们())
        原因们 = " ".join(原因 for _, 原因 in 结果.丢弃们)
        self.assertIn("通用分类目录", 原因们)

    def test_文件标题叫电影时不被通用目录表吃掉(self):
        # 通用目录词只作用于**目录层**：真有片子叫《电影》也得能搜。
        结果 = 生成查询(假结构(标题="电影", 类型="movie"), 目录链=("电影",))
        self.assertIn("电影", 结果.文本们())


class 中英混排测试(unittest.TestCase):

    def test_中英分段各自产出查询词(self):
        结果 = 生成查询(假结构(标题="葬送的芙莉莲 Sousou no Frieren", 类型="tv"),
                    目录链=("Season 01",))
        映射表 = 映射(结果)
        self.assertIn("葬送的芙莉莲", 映射表)
        self.assertIn("Sousou no Frieren", 映射表)
        self.assertEqual(映射表["葬送的芙莉莲"][0], 来源_中文段)
        self.assertEqual(映射表["Sousou no Frieren"][0], 来源_英文段)
        # 整串也要出（少数作品官方标题真带外文副标题），但要降权到分段之下
        self.assertIn("葬送的芙莉莲 Sousou no Frieren", 映射表)
        self.assertLess(映射表["葬送的芙莉莲 Sousou no Frieren"][1],
                        映射表["葬送的芙莉莲"][1])

    def test_纯中文标题不额外产出英文段(self):
        结果 = 生成查询(假结构(标题="沙丘", 年份=2021, 类型="movie"), 目录链=("电影",))
        self.assertEqual(结果.文本们(), ["沙丘"])

    def test_标题候选们也会被摊开(self):
        结果 = 生成查询(假结构(标题="沙丘", 年份=2021, 类型="movie",
                          标题候选们=["沙丘 Dune"]), 目录链=("电影",))
        self.assertIn("Dune", 结果.文本们())

    def test_语言标记用于中英分开打分(self):
        结果 = 生成查询(假结构(标题="三体 Three-Body", 类型="tv"), 目录链=("三体",))
        语言 = {x.文本: x.语言 for x in 结果.查询词们}
        self.assertEqual(语言["三体"], "zh")
        self.assertEqual(语言["Three Body"], "en")
        self.assertEqual(语言["三体 Three Body"], "混")


class 纯数字两面测试(unittest.TestCase):
    """标注集里 2012/1917/1984/2049/10 都是**片名的一部分**，一刀切删数字是错的。"""

    def test_集号与季号被丢掉(self):
        结果 = 生成查询(假结构(标题="146", 类型="tv", 季=1, 集=146), 目录链=("仙逆",))
        self.assertNotIn("146", 结果.文本们())
        self.assertIn("仙逆", 结果.文本们())

    def test_整条标题就是片名数字时保留(self):
        for 标题, 年份 in (("2012", None), ("1984", 1984), ("1917", 2019)):
            结果 = 生成查询(假结构(标题=标题, 年份=年份, 类型="movie"), 目录链=("电影",))
            self.assertIn(标题, 结果.文本们(), f"{标题} 是片名，不许被当噪声删掉")

    def test_多词标题里的年份数字被摘掉_片名数字留下(self):
        结果 = 生成查询(假结构(标题="1917 2019", 年份=2019, 类型="movie"), 目录链=("电影",))
        self.assertEqual(结果.文本们(), ["1917"])

    def test_续集数字不许删(self):
        for 标题, 期望 in (("Blade Runner 2049", "Blade Runner 2049"),
                        ("10 Cloverfield Lane", "10 Cloverfield Lane"),
                        ("2001 A Space Odyssey", "2001 A Space Odyssey")):
            结果 = 生成查询(假结构(标题=标题, 年份=2017, 类型="movie"), 目录链=("电影",))
            self.assertIn(期望, 结果.文本们(), f"{标题} 的数字/续集号是片名的一部分")

    def test_纯数字标题降权(self):
        结果 = 生成查询(假结构(标题="2012", 类型="movie"))
        词 = 结果.查询词们[0]
        self.assertLess(词.权重, 权重表[来源_文件标题])
        self.assertIn("纯数字", 词.备注)


class 标签过滤测试(unittest.TestCase):

    def test_常见标签表(self):
        for 标记 in ("1080p", "2160p", "x265", "H265", "HEVC", "WEB-DL", "DDP5.1", "中字",
                     "简繁内嵌", "国语", "60fps", "10bit", "S01E138", "第03集", "03话",
                     "Season 01", "4K高码", "HDR10+", "REMUX", "BluRay", "Atmos"):
            self.assertTrue(是标签(标记), f"{标记} 应当被认成标记")

    def test_正常标题不被误判(self):
        for 文本 in ("沙丘", "沙丘2", "The Batman", "Blade Runner 2049", "Se7en", "2012",
                     "10", "1917", "某剧", "Spider Man", "Dune", "Sousou no Frieren",
                     "The Office US", "Planet Earth II", "Severance"):
            self.assertFalse(是标签(文本), f"{文本} 是标题，不许被当成标记")

    def test_去标签只摘标记与结构数字(self):
        文本, 丢掉 = 去标签("沙丘 2021 2160p WEB-DL H265", 年份=2021)
        self.assertEqual(文本, "沙丘")
        self.assertIn("2021", 丢掉)
        self.assertIn("2160p", 丢掉)
        文本, _ = 去标签("Blade Runner 2049 2017 1080p", 年份=2017)
        self.assertEqual(文本, "Blade Runner 2049")

    def test_纯数字判定(self):
        self.assertTrue(是纯数字("2012"))
        self.assertFalse(是纯数字("2012a"))
        self.assertFalse(是纯数字("Blade"))


class 别名测试(unittest.TestCase):

    def test_别名命中给最高权重(self):
        结构 = 假结构(标题="", 类型="tv", 季=1, 集=5)
        结果 = 生成查询(结构, 目录链=("Season 01", "仙逆.4K高码"), 别名表={"仙逆": "Renegade Immortal"})
        映射表 = 映射(结果)
        self.assertIn("Renegade Immortal", 映射表)
        self.assertEqual(映射表["Renegade Immortal"][0], 来源_别名)
        self.assertEqual(映射表["Renegade Immortal"][1], 权重表[来源_别名])
        # 别名排在最前面：它是人工确认过的知识，比任何规则猜的标题都可信
        self.assertEqual(结果.查询词们[0].文本, "Renegade Immortal")

    def test_别名表反方向也认(self):
        结构 = 假结构(标题="Renegade Immortal", 类型="tv", 季=1, 集=5)
        结果 = 生成查询(结构, 目录链=("Season 01",), 别名表={"Renegade Immortal": ["仙逆"]})
        映射表 = 映射(结果)
        self.assertIn("仙逆", 映射表)
        self.assertEqual(映射表["仙逆"][0], 来源_别名)
        备注 = next(x.备注 for x in 结果.查询词们 if x.文本 == "仙逆")
        self.assertIn("别名库命中", 备注)
        self.assertIn("Renegade Immortal", 备注)       # 记下"这条别名是被谁带出来的"

    def test_别名值可以是列表或字典(self):
        结构 = 假结构(标题="某剧", 类型="tv")
        结果 = 生成查询(结构, 目录链=(), 别名表={"某剧": ["Alias One", {"标题": "Alias Two",
                                                                "tmdb_id": 12345}]})
        self.assertIn("Alias One", 结果.文本们())
        self.assertIn("Alias Two", 结果.文本们())
        # tmdb id 是数字，绝不许被当成查询词
        self.assertNotIn("12345", 结果.文本们())

    def test_别名表命中混排观察文本(self):
        # 文件名是 "仙逆 Renegade Immortal"，别名库里存的是单一语言的写法 —— 要能比中。
        结构 = 假结构(标题="仙逆 Renegade Immortal", 类型="tv")
        结果 = 生成查询(结构, 目录链=(), 别名表={"Renegade Immortal": "仙逆"})
        self.assertEqual(映射(结果)["仙逆"][0], 来源_别名)


class 年份与类型测试(unittest.TestCase):

    def test_年份是独立过滤条件不拼进查询词(self):
        结果 = 生成查询(假结构(标题="沙丘", 年份=2021, 类型="movie"), 目录链=("电影",))
        self.assertEqual(结果.年份, 2021)
        for 词 in 结果.查询词们:
            self.assertNotIn("2021", 词.文本)
        self.assertEqual(结果.类型, "movie")

    def test_类型归一(self):
        self.assertEqual(归一类型("电影"), "movie")
        self.assertEqual(归一类型("集"), "tv")
        self.assertEqual(归一类型("剧集"), "tv")
        self.assertEqual(归一类型(None), "unknown")
        self.assertEqual(生成查询(假结构(标题="某剧", 类型="剧集")).类型, "tv")

    def test_季集原样带出供打分判范围(self):
        结果 = 生成查询(假结构(标题="某剧", 类型="tv", 季=2, 集=7))
        self.assertEqual((结果.季, 结果.集), (2, 7))


class 去重与上限测试(unittest.TestCase):

    def test_同一词只出一条并记下其它来源(self):
        结果 = 生成查询(假结构(标题="仙逆", 类型="tv", 季=1, 集=146),
                    目录链=("Season 01", "仙逆.4K高码"))
        词们 = [x for x in 结果.查询词们 if x.文本 == "仙逆"]
        self.assertEqual(len(词们), 1)
        self.assertEqual(词们[0].来源, 来源_文件标题)          # 保留权重更高的来源
        self.assertIn(来源_目录标题, 词们[0].其它来源)

    def test_标点与大小写差异算同一个词(self):
        结果 = 生成查询(假结构(标题="Spider-Man No Way Home", 类型="movie"))
        键 = [x.文本 for x in 结果.查询词们]
        self.assertEqual(len(键), len(set(x.lower().replace("-", "").replace(" ", "")
                                      for x in 键)))

    def test_查询词数量有上限(self):
        目录链 = ("第一层目录名", "第二层目录名", "第三层目录名", "第四层目录名",
                 "第五层目录名", "第六层目录名")
        结果 = 生成查询(假结构(标题="文件里的标题", 类型="tv"), 目录链=目录链)
        self.assertLessEqual(len(结果.查询词们), 最多查询词)

    def test_排序按权重从高到低(self):
        结果 = 生成查询(假结构(标题="某剧", 类型="tv"), 目录链=("某剧目录",),
                    别名表={"某剧": "Alias"})
        权重们 = [x.权重 for x in 结果.查询词们]
        self.assertEqual(权重们, sorted(权重们, reverse=True))


class 结构自带信息测试(unittest.TestCase):
    """P1 的 ``结构结果`` 会多给几样东西（目录链 / 文件标题 / 目录标题），要用上。"""

    def test_结构自带目录链时不必外部再传(self):
        结果 = 生成查询({"标题": "", "类型": "tv", "季": 1, "集": 5,
                     "目录链": ("葬送的芙莉莲", "Season 01")})
        self.assertIn("葬送的芙莉莲", 结果.文本们())
        # 季目录本身不是作品名，不许漏成查询词
        self.assertNotIn("Season 01", 结果.文本们())

    def test_结构额外给的文件标题与目录标题也当查询词(self):
        结果 = 生成查询({"标题": "", "类型": "tv", "文件标题": "Some Show",
                     "目录标题": "某剧"})
        映射表 = 映射(结果)
        self.assertIn("Some Show", 映射表)
        self.assertEqual(映射表["Some Show"][0], 来源_文件标题)
        self.assertIn("某剧", 映射表)
        self.assertEqual(映射表["某剧"][0], 来源_目录标题)


class 鸭子类型与边缘测试(unittest.TestCase):

    def test_dict入参也能用(self):
        结果 = 生成查询({"标题": "沙丘", "年份": 2021, "类型": "movie", "季": None, "集": None},
                    目录链=["电影"])
        self.assertEqual(结果.文本们(), ["沙丘"])
        self.assertEqual(结果.年份, 2021)

    def test_什么信息都没有时不做无用查询(self):
        结果 = 生成查询(假结构(), 目录链=("电影",))
        self.assertTrue(结果.空的(), "通用目录 + 空标题 → 不该硬凑一个查询词")
        self.assertEqual(结果.汇总(), "（没有查询词）")

    def test_全被过滤时兜底不许零查询词(self):
        # 只有集号可搜时也要搜（宁可搜一个可疑词，也不许零召回，⑤ 会用分数压下去）
        结果 = 生成查询(假结构(标题="146", 类型="tv", 季=1, 集=146), 目录链=("Season 01",))
        self.assertEqual(len(结果.查询词们), 1)
        self.assertIn("兜底", 结果.查询词们[0].备注)

    def test_只有标签的文件名不产出查询词(self):
        结果 = 生成查询(假结构(标题="2160p HDR DDP5 1 中字", 类型="unknown"))
        self.assertTrue(结果.空的())

    def test_目录链为空或None都行(self):
        self.assertEqual(生成查询(假结构(标题="沙丘"), 目录链=None).文本们(), ["沙丘"])
        self.assertEqual(生成查询(假结构(标题="沙丘"), 目录链=()).文本们(), ["沙丘"])

    def test_丢弃记录写明原因(self):
        结果 = 生成查询(假结构(标题="沙丘", 年份=2021, 类型="movie"),
                    目录链=("Season 01", "电影", "沙丘.2021.2160p"))
        原因 = {文本: 因 for 文本, 因 in 结果.丢弃们}
        self.assertIn("电影", 原因)
        self.assertIn("2160p", 原因)


if __name__ == "__main__":
    unittest.main()
