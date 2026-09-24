"""结构抽取（``wangpan/identify/结构.py``）与归一化（``归一化.py``）的回归测试。

为什么这一层要单独测（而不是只靠 ``工具/识别评测.py``）
==================================================
评测脚本是**尺子**，它只说"91 条里错了几条"，不告诉你"哪条规则被改坏了"。
而这两层是纯规则、门槛又最高（结构 ≥ 99%、标题清洗 ≥ 98%，``docs/识别/00_架构.md`` 第二节），
任何一条规则被顺手改回去，评测分数可能只掉 1~2 条（甚至因为别处补上而不掉），
但某种写法会整类失效。所以这里把**每一种写法 + 每一条防误判**钉成一条断言。

覆盖顺序照抄施工清单（P1 的优先顺序）：
1. 特别篇/NCOP/NCED（全错的那一类）→ 第 0 季 + 摘词；
2. 字幕组方括号 → 组名不许进标题，``[1080p]`` 这类标签要清掉；
3. 打包/更新至/合集/完结 → 集 = 1，上界进 ``集到``；
4. 中文集话 → 阿拉伯数字、无"第"、中文数字都认；
5. 标题残留画质/版本标签 → ``DDP5.1``/``中英双字``/``HD1080P``/广告前缀；
6. 祖父目录 → 路径整条给进来时往上找 2~3 层；
7. 年份防误判 → ``2012.mp4`` / ``1917.2019`` / ``Blade.Runner.2049.2017``。

测试**全部离线**，不碰 TMDB（P1 不接检索）。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.公用 import 项目根

from wangpan.identify import 归一化, 结构
from wangpan.identify.管线 import 识别


def 抽(文件名: str, 父目录: str = "", 目录链=()) -> 结构.结构结果:
    return 结构.抽取(文件名, 父目录名=父目录, 目录链=目录链)


class 归一化测试(unittest.TestCase):
    """① 清洗层：每一步都要能单独说清"为什么这么改"。"""

    def test_全角转半角含中日韩标点(self):
        self.assertEqual(归一化.转半角("Ｓ０１Ｅ０１　第３集"), "S01E01 第3集")
        self.assertEqual(归一化.转半角("【组名】"), "[组名]")
        self.assertEqual(归一化.转半角("（2019）"), "(2019)")
        # 各种横线统一成 '-'：不然"打包区间 01–12"里的破折号会让区间规则漏掉
        self.assertEqual(归一化.转半角("01–12"), "01-12")
        self.assertEqual(归一化.转半角("01—12"), "01-12")

    def test_切扩展名不能把标签当扩展名(self):
        self.assertEqual(归一化.切扩展名("某剧.S01E01.mkv"), ("某剧.S01E01", "mkv"))
        self.assertEqual(归一化.切扩展名("Show.WEB-DL"), ("Show.WEB-DL", ""))
        # DDP5.1 的".1"、HDR10+ 的"+10"都不是扩展名
        self.assertEqual(归一化.切扩展名("某剧.DDP5.1")[0], "某剧.DDP5.1")
        self.assertEqual(归一化.切扩展名("某剧.1080p")[0], "某剧.1080p")
        self.assertEqual(归一化.切扩展名("影片.2019.rmvb"), ("影片.2019", "rmvb"))

    def test_去网址不吃中文也不吃方括号(self):
        # 分享名是 [电影天堂www.dygod.net]流浪地球 —— 网址后面紧跟着中文与右方括号
        self.assertEqual(归一化.去网址("[电影天堂www.dygod.net]流浪地球"), "[电影天堂 ]流浪地球")
        self.assertIn("流浪地球", 归一化.去网址("http://a.com/x 流浪地球"))

    def test_广告与通用目录(self):
        self.assertTrue(归一化.是广告内容("电影天堂www.dygod.net"))
        self.assertTrue(归一化.是广告内容("更多资源关注公众号：影视大全"))
        self.assertFalse(归一化.是广告内容("葬送的芙莉莲"))
        self.assertTrue(归一化.是通用目录("电影"))
        self.assertTrue(归一化.是通用目录("Movies"))
        # 分类目录判据是"完全相同"，不能把真片名当分类目录
        self.assertFalse(归一化.是通用目录("电影天堂"))
        self.assertFalse(归一化.是通用目录("电影人生"))

    def test_发布组尾巴只砍贴在标签后面的那个(self):
        self.assertEqual(归一化.去发布组尾巴("Dune.2024.1080p.BluRay.x264-GROUP"),
                         "Dune.2024.1080p.BluRay.x264")
        self.assertEqual(归一化.去发布组尾巴("Spider-Man.No.Way.Home.2021.1080p"),
                         "Spider-Man.No.Way.Home.2021.1080p")
        self.assertEqual(归一化.去发布组尾巴("三体.Three-Body.S01E05.1080p"),
                         "三体.Three-Body.S01E05.1080p")

    def test_标签块与实词块(self):
        self.assertTrue(归一化.是发布标签("AVC-8bit 1080p AAC"))
        self.assertTrue(归一化.是发布标签("简繁内嵌"))
        self.assertTrue(归一化.是发布标签("BDRip"))
        self.assertFalse(归一化.是发布标签("葬送的芙莉莲"))
        self.assertFalse(归一化.是发布标签("Sousou no Frieren"))

    def test_清标题清不干净宁可为空(self):
        self.assertEqual(归一化.清标题("1080p WEB-DL H265"), "")
        self.assertEqual(归一化.清标题("沙丘 4K HDR 国语 中英双字"), "沙丘")
        self.assertEqual(归一化.清标题("仙逆 高码 SDR 60fps"), "仙逆")

    def test_标题比对键大小写标点不敏感(self):
        self.assertEqual(归一化.标题比对键("Spider-Man"), 归一化.标题比对键("spider man"))


class 中文数字测试(unittest.TestCase):
    def test_中文数字(self):
        self.assertEqual(结构.中文数字("三"), 3)
        self.assertEqual(结构.中文数字("十二"), 12)
        self.assertEqual(结构.中文数字("二十"), 20)
        self.assertEqual(结构.中文数字("二十四"), 24)
        self.assertEqual(结构.中文数字("一百"), 100)
        self.assertEqual(结构.中文数字("146"), 146)
        self.assertIsNone(结构.中文数字("某"))


class 集号写法测试(unittest.TestCase):
    """② 结构抽取：每一种真机写法。"S01E01 家族"之外全是 P0 报告里错的那几类。"""

    def test_集号在最前_只剩画质标签(self):
        r = 抽("146.SDR.8bit.2160p.60fps.DDP5.1.WEB-DL.H265.mp4", "仙逆.4K高码.SDR.60fps")
        self.assertEqual((r.标题, r.季, r.集, r.类型), ("仙逆", 1, 146, "tv"))
        self.assertIsNone(r.年份)

    def test_纯集号文件_标题靠目录兜底(self):
        for 名, 集 in (("01.mp4", 1), ("05.mkv", 5), ("07.mkv", 7), ("146.mp4", 146),
                     ("S01E08.mkv", 8), ("第131集.mp4", 131)):
            with self.subTest(名=名):
                r = 抽(名, "仙逆" if 集 == 146 else "某剧")
                self.assertEqual(r.集, 集)
                self.assertEqual(r.季, 1)              # 有集号、两处都没写季 → 默认 1
                self.assertEqual(r.标题, "仙逆" if 集 == 146 else "某剧")
                self.assertEqual(r.类型, "tv")

    def test_集号在前_后面的文字是单集标题不算片名(self):
        # "03 小标题" / "12 - 某集标题"：切点之前是空的 → 标题自然落到目录名上
        self.assertEqual(抽("03 小标题.mkv", "某剧").标题, "某剧")
        r = 抽("12 - 某集标题.mp4", "某剧")
        self.assertEqual((r.标题, r.集), ("某剧", 12))

    def test_标准季集写法(self):
        r = 抽("Renegade.Immortal.S01E138.2023.2160p.WEB-DL.H265.10bit.DDP2.0-PanWEB.mkv",
             "仙逆.4K高码.SDR.60fps")
        self.assertEqual((r.标题, r.年份, r.季, r.集), ("Renegade Immortal", 2023, 1, 138))
        self.assertEqual(抽("The.Bear.s3e05.1080p.WEB-DL.mkv").季, 3)
        self.assertEqual(抽("The.Bear.s3e05.1080p.WEB-DL.mkv").集, 5)
        r = 抽("Breaking.Bad.1x03.1080p.mkv", "Breaking Bad")
        self.assertEqual((r.季, r.集, r.标题), (1, 3, "Breaking Bad"))
        r = 抽("某剧.03x05.1080p.mkv", "某剧")
        self.assertEqual((r.季, r.集), (3, 5))

    def test_一文件多集的区间(self):
        r = 抽("Friends.S01E01E02.1080p.BluRay.mkv", "Friends")
        self.assertEqual((r.集, r.集到), (1, 2))
        r = 抽("某剧.S01E01-E12.1080p.WEB-DL.H265.mkv", "某剧")
        self.assertEqual((r.集, r.集到, r.季), (1, 12, 1))

    def test_中文集话含中文数字与回(self):
        样例 = (("第03集.mp4", 3), ("第03话.mp4", 3), ("03话.mp4", 3), ("第三集.mp4", 3),
              ("第十二集.mp4", 12), ("某剧.第06回.1080p.mkv", 6), ("狂飙.第01集.1080p.mp4", 1))
        for 名, 集 in 样例:
            with self.subTest(名=名):
                r = 抽(名, "某剧")
                self.assertEqual(r.集, 集)
                self.assertEqual(r.季, 1)              # "第N集"也要给季 = 1
                self.assertNotIn("集", r.标题)

    def test_横线在前的集号写法(self):
        r = 抽("[Lilith-Raws] Mushoku Tensei - 05 [Baha][WEB-DL].mp4")
        self.assertEqual((r.标题, r.集), ("Mushoku Tensei", 5))

    def test_标题里带横线的数字不算集号(self):
        # "Kaijuu 8-gou"（怪兽8号）：数字后面紧跟连字符时，连字符后面必须有空格/结尾
        # 才是"集号 + 分集标题"；否则那就是片名的一部分（8-gou = 8号）
        r = 抽("[Lilith-Raws] Kaijuu 8-gou - 05 [Baha][WEB-DL][1080p].mp4")
        self.assertEqual((r.标题, r.集), ("Kaijuu 8-gou", 5))

    def test_Part点数字是标签不是区间端点(self):
        # "Part.2 - 03"：Part.2 是"第 2 部"（发布标签），不能当成区间 2-03 的第 2 集
        r = 抽("进击的巨人 最终季 Part.2 - 03 [1080p].mp4")
        self.assertEqual((r.标题, r.集, r.集到), ("进击的巨人 最终季", 3, None))

    def test_文件名里的季优先于默认一(self):
        r = 抽("某剧.第3季.第10集.1080p.mkv", "某剧")
        self.assertEqual((r.季, r.集, r.标题, r.类型), (3, 10, "某剧", "tv"))
        r = 抽("庆余年.第二季.第01集.1080p.mp4", "庆余年")
        self.assertEqual((r.季, r.集, r.标题), (2, 1, "庆余年"))
        r = 抽("某剧.S03.1080p.WEB-DL.mkv", "某剧")
        self.assertEqual((r.季, r.集, r.类型), (3, None, "tv"))   # 整季包没有集号 → 集 null

    def test_季目录优先于文件名季(self):
        r = 抽("某剧/第2季/某剧.第05集.1080p.mkv")
        self.assertEqual((r.季, r.集, r.标题), (2, 5, "某剧"))
        r = 抽("庆余年/Season 02/庆余年.S02E05.mkv")
        self.assertEqual((r.季, r.集, r.标题), (2, 5, "庆余年"))


class 字幕组方括号测试(unittest.TestCase):
    def test_组名不进标题_方括号标签清掉(self):
        r = 抽("[Sakurato] Sousou no Frieren [03][AVC-8bit 1080p AAC][CHS].mp4")
        self.assertEqual((r.标题, r.季, r.集), ("Sousou no Frieren", 1, 3))
        self.assertEqual(抽("[喵萌奶茶屋][葬送的芙莉莲][05][1080p][简繁内嵌].mkv").标题,
                         "葬送的芙莉莲")
        r = 抽("[Lilith-Raws] Mushoku Tensei - 05 [Baha][WEB-DL][1080p][AVC AAC][CHT].mp4")
        self.assertEqual((r.标题, r.集), ("Mushoku Tensei", 5))
        r = 抽("[NC-Raws] 咒术回战 - 12 [Bilibili][WEB-DL][1080p][AVC AAC][CHT].mp4")
        self.assertEqual((r.标题, r.集, r.季, r.类型), ("咒术回战", 12, 1, "tv"))

    def test_标题在第一个方括号里时不许当组名砍掉(self):
        r = 抽("[葬送的芙莉莲][05][1080p].mkv")
        self.assertEqual((r.标题, r.集), ("葬送的芙莉莲", 5))

    def test_标题里的连字符不许被当成组名分隔符(self):
        for 名 in ("Spider-Man.No.Way.Home.2021.1080p.WEB-DL.mkv",
                  "三体.Three-Body.S01E05.2023.2160p.WEB-DL.mkv"):
            with self.subTest(名=名):
                r = 抽(名)
                self.assertIn("-", r.标题)

    def test_广告前缀与网址(self):
        r = 抽("[电影天堂www.dygod.net]流浪地球.2019.HD1080P.国语中字.mkv", "电影")
        self.assertEqual((r.标题, r.年份, r.类型), ("流浪地球", 2019, "movie"))
        r = 抽("【更多资源关注公众号：影视大全】奥本海默.2023.2160p.HDR.mkv", "电影")
        self.assertEqual((r.标题, r.年份), ("奥本海默", 2023))


class 特别篇测试(unittest.TestCase):
    """P0 报告里 9/9 全错的那一类：摘词 + 第 0 季。"""

    def test_NCOP_NCED_OVA_SP_特别篇都进第零季(self):
        样例 = ("[Sakurato] Sousou no Frieren NCOP [1080p][BDRip][x264 FLAC].mkv",
              "[Sakurato] Sousou no Frieren NCED [1080p][BDRip][x264 FLAC].mkv",
              "某番.NCOP.1080p.mkv", "[桜都字幕组] 某番 OVA [1080p][简繁].mp4",
              "某剧.SP.1080p.mkv", "[组名] 进击的巨人 特别篇 [BDRip][1080p].mkv")
        for 名 in 样例:
            with self.subTest(名=名):
                r = 抽(名)
                self.assertEqual(r.季, 0)
                self.assertIsNone(r.集)                # 没有编号 → 不是具体某一集
                self.assertEqual(r.类型, "tv")
                self.assertNotIn("NCOP", r.标题)
                self.assertNotIn("OVA", r.标题)
                self.assertNotIn("SP", r.标题)
                self.assertNotIn("特别篇", r.标题)

    def test_特别篇带编号时编号是集号(self):
        r = 抽("[喵萌奶茶屋] 葬送的芙莉莲 SP01 [1080p][简繁].mkv", "葬送的芙莉莲")
        self.assertEqual((r.标题, r.季, r.集), ("葬送的芙莉莲", 0, 1))
        r = 抽("某番.OVA.02.1080p.mkv", "某番")
        self.assertEqual((r.标题, r.季, r.集), ("某番", 0, 2))

    def test_特别篇目录等同于第零季(self):
        r = 抽("某剧/特别篇/某剧.SP01.mkv")
        self.assertEqual((r.标题, r.季, r.集), ("某剧", 0, 1))
        r = 抽("某剧/Specials/某剧.SP01.mkv")
        self.assertEqual((r.标题, r.季), ("某剧", 0))

    def test_搜索词里不许出现特别篇这类词(self):
        r = 抽("某番.NCOP.1080p.mkv", "某番")
        self.assertEqual(r.查询词们()[0], "某番")


class 打包更新测试(unittest.TestCase):
    """集 = 1（从第 1 集开始打包），上界进 集到；合集/完结没有集号 → 集 null。"""

    def test_打包写法集号是一上界进集到(self):
        样例 = (("某剧.E01-E12.1080p.mkv", 12), ("某剧.01-12.1080p.WEB-DL.mkv", 12),
              ("某剧.更新至12.1080p.mp4", 12), ("某剧.更新至第24集.1080p.mp4", 24),
              ("某剧.全12集.1080p.mkv", 12))
        for 名, 上界 in 样例:
            with self.subTest(名=名):
                r = 抽(名, "某剧")
                self.assertEqual((r.标题, r.集, r.集到, r.季, r.类型),
                                 ("某剧", 1, 上界, 1, "tv"))

    def test_合集与完结没有集号季是null(self):
        for 名 in ("某剧.合集.1080p.WEB-DL.mkv", "某剧.完结.1080p.WEB-DL.mkv",
                  "某剧.全集.1080p.mkv"):
            with self.subTest(名=名):
                r = 抽(名, "某剧")
                self.assertEqual((r.标题, r.集, r.季, r.类型), ("某剧", None, None, "tv"))

    def test_S01E01E12这类区间不给季瞎填(self):
        r = 抽("某剧.S01E01-E12.1080p.WEB-DL.H265.mkv", "某剧")
        self.assertEqual(r.季, 1)


class 年份测试(unittest.TestCase):
    """口径：从后往前取"能留下非空标题"的那个。"""

    def test_年份防误判(self):
        self.assertIsNone(抽("2012.mp4", "电影").年份)          # 2012 是片名
        self.assertEqual(抽("2012.mp4", "电影").标题, "2012")
        self.assertEqual(抽("1917.2019.1080p.WEB-DL.H265.mkv", "电影").年份, 2019)
        self.assertEqual(抽("1917.2019.1080p.WEB-DL.H265.mkv", "电影").标题, "1917")
        self.assertEqual(抽("Blade.Runner.2049.2017.1080p.BluRay.mkv", "电影").标题,
                         "Blade Runner 2049")
        self.assertEqual(抽("Blade.Runner.2049.2017.1080p.BluRay.mkv", "电影").年份, 2017)
        self.assertEqual(抽("2001.A.Space.Odyssey.1968.1080p.BluRay.mkv", "电影").标题,
                         "2001 A Space Odyssey")
        self.assertEqual(抽("2001.A.Space.Odyssey.1968.1080p.BluRay.mkv", "电影").年份, 1968)
        self.assertEqual(抽("1984.1984.1080p.mkv", "电影").标题, "1984")
        self.assertEqual(抽("1984.1984.1080p.mkv", "电影").年份, 1984)
        self.assertEqual(抽("2012.2009.1080p.mkv", "电影").年份, 2009)

    def test_名字里同时有年份与裸前导数字时数字是片名(self):
        r = 抽("10.Cloverfield.Lane.2016.1080p.mkv", "电影")
        self.assertEqual((r.标题, r.年份, r.类型, r.季, r.集),
                         ("10 Cloverfield Lane", 2016, "movie", None, None))

    def test_括号里的年份优先(self):
        r = 抽("流浪地球 (2019).mkv", "电影")
        self.assertEqual((r.标题, r.年份, r.类型), ("流浪地球", 2019, "movie"))
        r = 抽("沙丘 (2021)/沙丘 (2021).mkv")
        self.assertEqual((r.标题, r.年份), ("沙丘", 2021))
        r = 抽("Interstellar (2014) [imdbid-tt0816692].mkv", "电影")
        self.assertEqual((r.标题, r.年份), ("Interstellar", 2014))

    def test_分辨率与标签里的数字不是年份(self):
        self.assertIsNone(抽("某剧.05.1080p.WEB-DL.mkv", "某剧").年份)
        self.assertIsNone(抽("仙逆.Renegade.Immortal.146.2160p.WEB-DL.H265.mp4", "仙逆").年份)


class 标题清洗测试(unittest.TestCase):
    def test_画质标签与语言字幕都不进标题(self):
        样例 = (("沙丘.2021.4K.HDR.国语.中英双字.mkv", "沙丘"),
              ("奥本海默.2023.2160p.WEB-DL.H265.10bit.HDR.DDP5.1.Atmos-OurTV.mkv", "奥本海默"),
              ("Spider-Man.No.Way.Home.2021.2160p.WEB-DL.H265.DDP5.1.Atmos-HHWEB.mkv",
               "Spider-Man No Way Home"),
              ("Dune.Part.Two.2024.2160p.WEB-DL.DDP5.1.Atmos.HDR10+.H265-PandaQT.mkv",
               "Dune Part Two"),
              ("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv", "The Matrix"))
        for 名, 标题 in 样例:
            with self.subTest(名=名):
                self.assertEqual(抽(名, "电影").标题, 标题)

    def test_中英混排两种写法都保留(self):
        r = 抽("葬送的芙莉莲 Sousou.no.Frieren - 03 [1080p][简繁].mp4", "葬送的芙莉莲")
        self.assertEqual(r.标题, "葬送的芙莉莲 Sousou no Frieren")
        self.assertEqual(归一化.标题比对键(r.标题),
                         归一化.标题比对键("葬送的芙莉莲 Sousou no Frieren"))
        r = 抽("三体.Three-Body.S01E05.2023.2160p.WEB-DL.mkv", "三体")
        self.assertEqual(r.标题, "三体 Three-Body")
        self.assertEqual(r.查询词们()[0], "三体 Three-Body")
        self.assertIn("三体", r.查询词们())
        self.assertIn("Three-Body", r.查询词们())

    def test_全角名字也认(self):
        r = 抽("［喵萌奶茶屋］ 葬送的芙莉莲 第０３集 ［１０８０Ｐ］.mkv", "葬送的芙莉莲")
        self.assertEqual((r.标题, r.季, r.集), ("葬送的芙莉莲", 1, 3))

    def test_数字不许被当扩展名切掉(self):
        r = 抽("沙丘2.2024.2160p.WEB-DL.H265.mkv", "电影")
        self.assertEqual((r.标题, r.年份), ("沙丘2", 2024))
        r = 抽("流浪地球2.2023.IMAX.2160p.WEB-DL.H265.mkv", "电影")
        self.assertEqual((r.标题, r.年份), ("流浪地球2", 2023))


class 目录链测试(unittest.TestCase):
    """评测报告第 3 条的已知缺口：标题在祖父目录里。"""

    def test_整条路径给进来要往上找标题(self):
        r = 抽("葬送的芙莉莲/Season 01/05.mkv")
        self.assertEqual((r.标题, r.季, r.集, r.类型), ("葬送的芙莉莲", 1, 5, "tv"))
        self.assertEqual(r.目录链, ("Season 01", "葬送的芙莉莲"))

    def test_只给名字时可以显式给目录链(self):
        r = 抽("05.mkv", 目录链=("Season 01", "葬送的芙莉莲"))
        self.assertEqual((r.标题, r.季, r.集), ("葬送的芙莉莲", 1, 5))

    def test_管线接口两种给法结果一致(self):
        甲 = 识别("葬送的芙莉莲/Season 01/05.mkv")
        乙 = 识别("05.mkv", 父目录名="Season 01", 目录链=("Season 01", "葬送的芙莉莲"))
        self.assertEqual((甲.标题, 甲.季, 甲.集), (乙.标题, 乙.季, 乙.集))
        self.assertEqual((甲.标题, 甲.季, 甲.集), ("葬送的芙莉莲", 1, 5))

    def test_分类目录不提供标题(self):
        # 父目录叫"电影"时不能把"电影"当片名
        self.assertEqual(识别("2012.mp4", 父目录名="电影").标题, "2012")

    def test_季目录与特别篇目录不提供标题(self):
        self.assertNotIn("Season", 抽("某剧/Season 01/05.mkv").标题)
        self.assertEqual(抽("某剧/Specials/05.mkv").标题, "某剧")


class 类型与结果模型测试(unittest.TestCase):
    def test_电影与剧集的类型判据(self):
        self.assertEqual(抽("沙丘.2021.2160p.WEB-DL.H265.mkv", "电影").类型, "movie")
        self.assertEqual(抽("电影/2012.mp4", "电影").类型, "movie")
        self.assertEqual(抽("某剧.S03.1080p.WEB-DL.mkv", "某剧").类型, "tv")
        self.assertEqual(抽("某剧.合集.1080p.mkv", "某剧").类型, "tv")

    def test_电影不许有季集(self):
        for 名 in ("沙丘.2021.2160p.mkv", "The.Batman.2022.2160p.WEB-DL.H265.DV.HDR.mkv"):
            with self.subTest(名=名):
                r = 抽(名, "电影")
                self.assertIsNone(r.季)
                self.assertIsNone(r.集)

    def test_空名字返回unknown不炸(self):
        r = 抽("")
        self.assertEqual((r.标题, r.类型, r.季, r.集), ("", "unknown", None, None))

    def test_结果模型字段齐全(self):
        r = 抽("某剧.S01E08.mkv", "某剧")
        for 字段 in ("标题", "年份", "类型", "季", "集", "集到"):
            self.assertTrue(hasattr(r, 字段), 字段)
        self.assertIn("某剧", r.一句话())


class 依赖方向测试(unittest.TestCase):
    """架构文档第四节末：``wangpan/identify`` 不许依赖正在被改造的 scrape 层。"""

    def test_识别包不引用scrape也不引用v8_3(self):
        import re

        包 = 项目根 / "wangpan" / "identify"
        违规: list[str] = []
        导入行 = re.compile(r"^\s*(?:from|import)\s+([\w.]+)")
        for 名字 in ("归一化.py", "结构.py", "管线.py"):
            路径 = 包 / 名字
            self.assertTrue(路径.is_file(), f"缺文件：{路径}")
            for 行号, 行 in enumerate(路径.read_text(encoding="utf-8").splitlines(), 1):
                m = 导入行.match(行)
                if not m:
                    continue
                模块 = m.group(1)
                if 模块.split(".")[0] in ("v8_3",) or 模块.startswith("wangpan.scrape"):
                    违规.append(f"{名字}:{行号} {行.strip()}")
        self.assertEqual(违规, [], "识别层必须能脱离 scrape 层单独跑：\n" + "\n".join(违规))

    def test_识别层能独立导入(self):
        # 不 import 整个 wangpan 包，直接按模块导入：证明它只依赖标准库 + 自己
        import importlib
        for 模块 in ("wangpan.identify.归一化", "wangpan.identify.结构", "wangpan.identify.管线"):
            self.assertIsNotNone(importlib.import_module(模块))

    def test_包内文件都在(self):
        包: Path = 项目根 / "wangpan" / "identify"
        for 名字 in ("__init__.py", "归一化.py", "结构.py", "管线.py"):
            self.assertTrue((包 / 名字).is_file(), 名字)


if __name__ == "__main__":
    unittest.main()
