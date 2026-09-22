"""命名解析的回归测试（**全部离线**）。

覆盖三块：
1. 官方命名规范里的示例（电影/剧集/多版本/多段/特别篇/番外/外挂字幕）；
2. 原型踩过的 5 个坑（每个坑一条专门的回归测试，防止改回去）；
3. 路径级猜测 :func:`~wangpan.scrape.命名解析.猜剧集路径`（真的要建目录，验证"往上找"的逻辑）。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.公用 import 临时目录

from wangpan.scrape.命名解析 import (是同一集的不同版本, 是视频文件, 猜剧集路径, 提取发布标签,
                              解析, 解析季目录, 转半角)


class 官方示例测试(unittest.TestCase):
    """官方文档里给出的命名例子，一个一个对拍。"""

    def test_电影_下划线片名与年份(self):
        结果 = 解析("Best_Movie_Ever (2019).mp4")
        self.assertEqual(结果.类型, "电影")
        self.assertEqual(结果.标题, "Best Movie Ever")
        self.assertEqual(结果.年份, 2019)
        self.assertEqual(结果.季, None)
        self.assertEqual(结果.集, None)

    def test_电影_ID标签与版本名(self):
        结果 = 解析("Movie (2021) [imdbid-tt12801262] - 2160p.mp4", 父目录名="Movie (2021)")
        self.assertEqual(结果.标题, "Movie")
        self.assertEqual(结果.年份, 2021)
        self.assertEqual((结果.数据源, 结果.数据源ID), ("imdb", "tt12801262"))
        self.assertEqual(结果.版本, "2160p")

    def test_电影_ID标签_等号写法(self):
        结果 = 解析("Movie (2021) [tmdbid=123].mkv")
        self.assertEqual((结果.数据源, 结果.数据源ID), ("tmdb", "123"))
        self.assertEqual(结果.标题, "Movie")

    def test_电影_ID标签_tvdb(self):
        结果 = 解析("Movie (2021) [TVDBID-4321].mkv")
        self.assertEqual((结果.数据源, 结果.数据源ID), ("tvdb", "4321"))

    def test_剧集_一文件多集(self):
        结果 = 解析("Series Name A S01E01-E02.mkv", 父目录名="Series Name A")
        self.assertEqual(结果.类型, "集")
        self.assertEqual((结果.季, 结果.集, 结果.集到), (1, 1, 2))
        self.assertEqual(结果.标题, "Series Name A")

    def test_剧集_一文件多集_连写(self):
        结果 = 解析("Series Name S01E01E02.mkv")
        self.assertEqual((结果.季, 结果.集, 结果.集到), (1, 1, 2))

    def test_剧集_一文件多集_短横线数字(self):
        结果 = 解析("Series Name S01E05-06.mkv")
        self.assertEqual((结果.季, 结果.集, 结果.集到), (1, 5, 6))

    def test_剧集_1x01记法(self):
        结果 = 解析("Show Name - 1x01 - Pilot.mkv")
        self.assertEqual(结果.类型, "集")
        self.assertEqual((结果.季, 结果.集), (1, 1))
        self.assertEqual(结果.标题, "Show Name")

    def test_剧集_第01集记法(self):
        结果 = 解析("名侦探柯南 第01集.mkv")
        self.assertEqual(结果.类型, "集")
        self.assertEqual(结果.集, 1)
        self.assertEqual(结果.标题, "名侦探柯南")

    def test_剧集_第2季第01集(self):
        结果 = 解析("某剧 第2季 第01集.mkv")
        self.assertEqual((结果.季, 结果.集), (2, 1))
        self.assertEqual(结果.标题, "某剧")

    def test_多段_cd1(self):
        结果 = 解析("Movie Name-cd1.mkv")
        self.assertEqual(结果.段, 1)
        self.assertEqual(结果.标题, "Movie Name")

    def test_多段_各种写法(self):
        for 名字, 期望 in (("Movie Name-cd2.mkv", 2), ("Movie.Name.dvd1.mkv", 1),
                        ("Movie Name part3.mkv", 3), ("Movie Name_pt4.mkv", 4),
                        ("Movie Name disc5.mkv", 5), ("Movie Name-disk6.mkv", 6)):
            with self.subTest(名字=名字):
                self.assertEqual(解析(名字).段, 期望)

    def test_三维_带格式优先(self):
        结果 = 解析("Awesome 3D Movie (2022).3D.FTAB.mp4")
        self.assertEqual(结果.三维, "FTAB")
        self.assertEqual(结果.标题, "Awesome 3D Movie")     # 片名里的 3D 不能被吃掉
        self.assertEqual(结果.年份, 2022)

    def test_三维_孤立(self):
        结果 = 解析("Awesome Movie (2022) 3D.mkv")
        self.assertEqual(结果.三维, "3D")
        self.assertEqual(结果.标题, "Awesome Movie")

    def test_三维_各种格式(self):
        for 标记 in ("HSBS", "FSBS", "HTAB", "FTAB", "MVC"):
            with self.subTest(标记=标记):
                结果 = 解析(f"Movie (2021).3d.{标记}.mkv")
                self.assertEqual(结果.三维, 标记)

    def test_日期式命名(self):
        结果 = 解析("Show Name 2021-05-01 Title.mkv")
        self.assertEqual(结果.类型, "集")
        self.assertEqual(结果.季, 2021)
        self.assertEqual(结果.集, 501)              # Plex 惯例：月日 → MMDD
        self.assertEqual(结果.标题, "Show Name")
        self.assertIn("日期:2021-05-01", 结果.标记)

    def test_一句话包含关键信息(self):
        文本 = 解析("Series Name A S01E01-E02.mkv").一句话()
        for 片段 in ("集", "Series Name A", "S01", "E01-E02"):
            self.assertIn(片段, 文本)


class 季目录测试(unittest.TestCase):
    def test_标准写法(self):
        for 名字, 期望 in (("Season 01", 1), ("Season 1", 1), ("Season.01", 1),
                        ("season 12", 12)):
            with self.subTest(名字=名字):
                self.assertEqual(解析季目录(名字), (期望, False))

    def test_缩写写法要能认(self):
        """官方不推荐 `S01`，但真实文件里到处都是，必须能认。"""
        for 名字, 期望 in (("S01", 1), ("s1", 1), ("S 02", 2), ("S12", 12)):
            with self.subTest(名字=名字):
                self.assertEqual(解析季目录(名字), (期望, False))

    def test_纯数字目录(self):
        self.assertEqual(解析季目录("5"), (5, False))
        self.assertEqual(解析季目录("0"), (0, True))

    def test_特别篇写法(self):
        for 名字 in ("Season 00", "Specials", "Extras", "特别篇", "SP"):
            with self.subTest(名字=名字):
                self.assertEqual(解析季目录(名字), (0, True))

    def test_中文季(self):
        self.assertEqual(解析季目录("第2季"), (2, False))

    def test_不是季目录(self):
        for 名字 in ("Movies", "2019", "", "Season", "Specials 1"):
            with self.subTest(名字=名字):
                self.assertEqual(解析季目录(名字), (None, False))

    def test_年份目录不是季号(self):
        """`2019` 是年份目录，不是第 2019 季（纯数字只认 1~2 位）。"""
        self.assertEqual(解析季目录("2019")[0], None)


class 五个坑的回归测试(unittest.TestCase):
    """原型实测踩过的坑，一条都不许再犯。"""

    def test_坑1_图片文件不是一集(self):
        结果 = 解析("S01E01 Some Episode-thumb.jpg")
        self.assertEqual(结果.类型, "非视频")
        self.assertNotEqual(结果.类型, "集")

    def test_坑1_字幕文件不是一集(self):
        结果 = 解析("Series Name A (2021) S01E01 Title.ja.ass")
        self.assertEqual(结果.类型, "非视频")
        self.assertIn("ja", 结果.标记)
        self.assertEqual(结果.标题, "Series Name A")
        # 季集还是要解析出来：外挂字幕要靠它找到对应的那一集
        self.assertEqual((结果.季, 结果.集), (1, 1))

    def test_坑1_图片扩展名全部挡住(self):
        for 后缀 in ("jpg", "jpeg", "png", "webp", "nfo", "srt"):
            with self.subTest(后缀=后缀):
                self.assertEqual(解析(f"Movie (2021).{后缀}").类型, "非视频")
                self.assertFalse(是视频文件(f"Movie (2021).{后缀}"))

    def test_坑2_绝对集号兜底_带前导零(self):
        结果 = 解析("剧名 07.mkv")
        self.assertEqual(结果.集, 7)
        self.assertEqual(结果.标题, "剧名")

    def test_坑2_发布标签里的数字不算集号(self):
        结果 = 解析("Movie.2021.1080p.WEB-DL.x264.mkv")
        self.assertIsNone(结果.集)
        self.assertEqual(结果.类型, "电影")
        self.assertEqual(结果.年份, 2021)

    def test_坑2_分辨率里的数字不算年份也不算集号(self):
        结果 = 解析("Movie.1920x1080.mkv")
        self.assertIsNone(结果.集)
        self.assertNotEqual(结果.年份, 1920)

    def test_坑2_多段的数字不算集号(self):
        结果 = 解析("Movie Name-cd1.mkv")
        self.assertIsNone(结果.集)
        self.assertEqual(结果.段, 1)

    def test_坑2_3D标记里的字母数字不算集号(self):
        结果 = 解析("Movie (2021).3D.FTAB.1080p.mkv")
        self.assertIsNone(结果.集)
        self.assertEqual(结果.三维, "FTAB")

    def test_坑2_没有零填充的末尾数字不当集号(self):
        """`Movie 3.mkv` 是第三部电影，不是第 3 集；`Movie 03.mkv` 才是集。"""
        self.assertEqual(解析("Movie 3.mkv").类型, "电影")
        self.assertEqual(解析("Movie 03.mkv").类型, "集")

    def test_坑2_括号里的数字算集号(self):
        结果 = 解析("剧名 (07).mkv")
        self.assertEqual(结果.集, 7)

    def test_坑3_片名里的3D不被带格式的3D误伤(self):
        结果 = 解析("Awesome 3D Movie (2022).3D.FTAB.mp4")
        self.assertEqual(结果.标题, "Awesome 3D Movie")
        self.assertEqual(结果.三维, "FTAB")

    def test_坑4_多版本判定不看标题(self):
        甲, 乙 = 解析("S01E01 - 1080p.mkv"), 解析("S01E01 - 2160p.mkv")
        self.assertEqual(甲.标题, "")          # 标签清掉后标题就是空的
        self.assertEqual(乙.标题, "")
        self.assertTrue(是同一集的不同版本("S01E01 - 1080p.mkv", "S01E01 - 2160p.mkv"))

    def test_坑5_标题清不干净也不退回原始文件名(self):
        结果 = 解析("S01E01 - 1080p.mkv")
        self.assertEqual(结果.标题, "")
        self.assertNotIn("1080p", 结果.标题)

    def test_坑5_同一集两个版本不会被当成两部片子(self):
        self.assertTrue(是同一集的不同版本("Show.S01E01.1080p.BluRay.x264-GRP.mkv",
                                    "Show.S01E01.2160p.WEB-DL.x265-GRP.mkv"))

    def test_多版本_不同集不算(self):
        self.assertFalse(是同一集的不同版本("S01E01.mkv", "S01E02.mkv"))
        self.assertFalse(是同一集的不同版本("S01E01.mkv", "S02E01.mkv"))

    def test_多版本_不同电影不算(self):
        self.assertFalse(是同一集的不同版本("沙丘 (2021).mkv", "沙丘2 (2024).mkv"))

    def test_多版本_同一部电影的两个版本算(self):
        self.assertTrue(是同一集的不同版本("Movie (2021) [imdbid-tt1] - 1080p.mp4",
                                    "Movie (2021) [imdbid-tt1] - 2160p.mp4"))

    def test_多版本_多段是同一个文件的两半(self):
        self.assertFalse(是同一集的不同版本("Movie Name-cd1.mkv", "Movie Name-cd2.mkv"))

    def test_多版本_非视频不参与(self):
        self.assertFalse(是同一集的不同版本("S01E01.ja.ass", "S01E01.zh.ass"))


class 番外测试(unittest.TestCase):
    def test_番外后缀(self):
        结果 = 解析("Making of The Best Movie Ever-behindthescenes.mp4")
        self.assertTrue(结果.是番外)
        self.assertEqual(结果.标题, "Making of The Best Movie Ever")

    def test_番外后缀_各种写法(self):
        for 名字 in ("X-trailer.mkv", "X.trailer.mkv", "X_sample.mkv", "X-deleted.mkv",
                    "X-featurette.mkv", "X-short.mkv"):
            with self.subTest(名字=名字):
                self.assertTrue(解析(名字).是番外)

    def test_番外后缀不能误伤普通片名(self):
        """`The Other Guys` 里的 Other 前面是空格，不是分隔符 —— 不能判成番外。"""
        self.assertFalse(解析("The Other Guys (2010).mkv").是番外)
        self.assertFalse(解析("Another Movie (2010).mkv").是番外)

    def test_番外目录(self):
        self.assertTrue(解析("Making Of.mkv", 父目录名="Behind The Scenes").是番外)
        self.assertTrue(解析("Clip.mkv", 季目录名="Featurettes").是番外)


class 标记测试(unittest.TestCase):
    def test_字幕标记_default_forced(self):
        结果 = 解析("Show S01E01.zh.forced.ass")
        self.assertIn("zh", 结果.标记)
        self.assertIn("forced", 结果.标记)
        self.assertEqual(结果.类型, "非视频")

    def test_字幕标记_sdh_hi(self):
        结果 = 解析("Show S01E01.en.sdh.srt")
        self.assertIn("en", 结果.标记)
        self.assertIn("sdh", 结果.标记)

    def test_三字母语言码(self):
        self.assertIn("eng", 解析("Movie.2021.eng.srt").标记)

    def test_片名里的语言码不会被误当标记(self):
        """`It.1990.mkv` 的 It 是片名（第 0 段永远不当标记）。"""
        结果 = 解析("It.1990.mkv")
        self.assertEqual(结果.标题, "It")
        self.assertNotIn("it", 结果.标记)

    def test_hd不是语言标记(self):
        self.assertNotIn("hd", 解析("Movie.2021.1080p.HD.mkv").标记)


class 发布标签测试(unittest.TestCase):
    def test_完整发布名(self):
        标签 = 提取发布标签("Movie.2021.1080p.BluRay.x265.DTS-HD.MA.10bit-GROUP.mkv")
        self.assertEqual(标签["清晰度"], "1080p")
        self.assertEqual(标签["来源"], "BluRay")
        self.assertEqual(标签["编码"], "x265")
        self.assertEqual(标签["音轨"], "DTS-HD MA")
        self.assertEqual(标签["发布组"], "GROUP")
        self.assertIn("10bit", 标签["其它"])

    def test_编码同族归一(self):
        for 写法 in ("x264", "H264", "h.264", "AVC"):
            with self.subTest(写法=写法):
                self.assertEqual(提取发布标签(f"Movie.2021.1080p.{写法}.mkv")["编码"], "x264")
        self.assertEqual(提取发布标签("Movie.2021.2160p.HEVC.mkv")["编码"], "x265")

    def test_没有标签就是空(self):
        标签 = 提取发布标签("Movie Name (2019).mkv")
        self.assertEqual(标签["清晰度"], "")
        self.assertEqual(标签["来源"], "")
        self.assertEqual(标签["编码"], "")
        self.assertEqual(标签["音轨"], "")
        self.assertEqual(标签["发布组"], "")
        self.assertEqual(标签["其它"], [])

    def test_片名里的连字符不被当发布组(self):
        """`Spider-Man.mkv` 没有别的发布标签 → 不许把 `Man` 当发布组（否则标题被砍成 Spider）。"""
        self.assertEqual(提取发布标签("Spider-Man.mkv")["发布组"], "")
        self.assertEqual(解析("Spider-Man.mkv").标题, "Spider-Man")

    def test_发布组只在有别的标签时才认(self):
        self.assertEqual(提取发布标签("Movie.2021.1080p.BluRay.x264-SPARKS.mkv")["发布组"],
                     "SPARKS")
        self.assertEqual(解析("Movie.2021.1080p.BluRay.x264-SPARKS.mkv").标题, "Movie")


class 标题清理测试(unittest.TestCase):
    def test_番剧方括号组前缀(self):
        结果 = 解析("[Nekomoe kissaten][Movie Title][1080p][BDRip].mkv")
        self.assertEqual(结果.标题, "Movie Title")

    def test_片名本身在方括号里不能删掉(self):
        """`[REC]` 是片名：只有一组方括号时不许当发布组前缀删掉。"""
        self.assertEqual(解析("[REC] (2007).mkv").标题, "REC")
        self.assertEqual(解析("[REC] 2 [2009].mkv").标题, "REC 2")

    def test_目录名兜底标题(self):
        self.assertEqual(解析("随便什么名.mkv", 父目录名="Movie Name (2019)").标题, "随便什么名")
        self.assertEqual(解析("无标题.mkv", 父目录名="Movie Name (2019)").标题, "无标题")

    def test_转半角(self):
        self.assertEqual(转半角("ＡＢＣ１２３（２０１９）"), "ABC123(2019)")


class 猜剧集路径测试(unittest.TestCase):
    def _建(self, 相对: str) -> Path:
        with 临时目录() as 名:
            pass
        raise AssertionError

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

    def test_标准剧集结构(self):
        路径 = self._放("Series Name A (2021)/Season 01/Series Name A S01E01.mkv")
        结果 = 猜剧集路径(路径)
        self.assertEqual(结果.类型, "集")
        self.assertEqual(结果.标题, "Series Name A")
        self.assertEqual(结果.年份, 2021)
        self.assertEqual((结果.季, 结果.集), (1, 1))

    def test_文件名没剧名时用目录兜底(self):
        路径 = self._放("Series Name A (2021)/Season 02/S02E05.mkv")
        结果 = 猜剧集路径(路径)
        self.assertEqual(结果.标题, "Series Name A")
        self.assertEqual((结果.季, 结果.集), (2, 5))

    def test_特别篇目录(self):
        路径 = self._放("Series Name A (2021)/Specials/S00E01.mkv")
        结果 = 猜剧集路径(路径)
        self.assertEqual(结果.季, 0)
        self.assertTrue(结果.特别篇)

    def test_Season_00也算特别篇(self):
        路径 = self._放("Series Name A (2021)/Season 00/S00E03.mkv")
        结果 = 猜剧集路径(路径)
        self.assertTrue(结果.特别篇)

    def test_番外子目录不丢季号(self):
        路径 = self._放("Series Name A (2021)/Season 01/Featurettes/Making of X-behindthescenes.mkv")
        结果 = 猜剧集路径(路径)
        self.assertTrue(结果.是番外)
        self.assertEqual(结果.季, 1)              # 番外目录在季目录下面，季号不能丢
        self.assertEqual(结果.标题, "Making of X")   # 番外保留自己的名字

    def test_番外文件没有自己名字时用剧名(self):
        路径 = self._放("Series Name A (2021)/Season 01/Featurettes/S01E05 - Featurette.mkv")
        结果 = 猜剧集路径(路径)
        self.assertTrue(结果.是番外)
        self.assertEqual(结果.标题, "Series Name A")
        self.assertEqual(结果.集, 5)

    def test_电影目录(self):
        路径 = self._放("Movie Name (2019)/Movie Name (2019).mkv")
        结果 = 猜剧集路径(路径)
        self.assertEqual(结果.类型, "电影")
        self.assertEqual(结果.标题, "Movie Name")
        self.assertEqual(结果.年份, 2019)

    def test_没有季目录的剧集(self):
        路径 = self._放("Series Name A (2021)/Series Name A S01E01-E02.mkv")
        结果 = 猜剧集路径(路径)
        self.assertEqual(结果.类型, "集")
        self.assertEqual(结果.标题, "Series Name A")
        self.assertEqual((结果.季, 结果.集, 结果.集到), (1, 1, 2))

    def test_季目录传目录本身(self):
        路径 = self._放("Series Name A (2021)/Season 01/x.mkv")
        结果 = 猜剧集路径(路径.parent.parent)
        self.assertEqual(结果.类型, "剧集")
        self.assertEqual(结果.标题, "Series Name A")
        self.assertEqual(结果.年份, 2021)
        self.assertEqual(结果.季, 1)

    def test_有特别篇目录的整部剧不是特别篇(self):
        self._放("Series Name A (2021)/Season 01/a.mkv")
        self._放("Series Name A (2021)/Specials/b.mkv")
        结果 = 猜剧集路径(self.根 / "Series Name A (2021)")
        self.assertFalse(结果.特别篇)
        self.assertEqual(结果.季, 1)


if __name__ == "__main__":
    unittest.main()
