"""③"扫描时直接对文件识别，不要对文件夹识别" —— 混合目录不再糊成一部。

用户原话
========
「扫描时最好直接对文件进行识别，不要对文件夹进行识别（避免用户乱放文件夹）」。

原来扫描是"按目录归并"（一个目录 = 一部作品）。用户把若干部不同的剧/电影混放在同一个
目录里时，归并的后果有两个（这里各钉一条）：

* 归成一部（几张卡变一张，点进去混着别人的集）；
* 更糟：``扫描一个目录`` 一旦认出剧集，同一个目录里**没有季集信息的电影会被整批丢掉**
  （测试里那条"老路会丢文件"就是这个）。

这一组测试用**假 TMDB**（不联网），把同一批文件的"老路（``扫描媒体库`` + ``扫库``）"与
"新路（``枚举视频单元`` + ``扫库按文件``）"跑出来对比，证明老路糊/丢、新路各归各的。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.公用 import 临时目录

from wangpan.scrape.库 import 资料库
from wangpan.scrape.模型 import 媒体条目, 媒体类型, 匹配候选, 季, 集
from wangpan.scrape.图片 import 图片缓存
from wangpan.scrape.服务 import 刮削服务, 刮削设置
from wangpan.scrape.扫描 import 枚举视频单元, 扫描媒体库

#: 三个不同作品混放在同一个目录里（用户"乱放"的典型）
混合目录文件 = {
    "Dune.2021.2160p.mkv": ("438631", "沙丘", 2021, "movie"),
    "Blade.Runner.1982.1080p.mkv": ("78", "银翼杀手", 1982, "movie"),
    "Stranger.Things.S01E01.mkv": ("66732", "怪奇物语", 2016, "tv"),
    "Stranger.Things.S01E02.mkv": ("66732", "怪奇物语", 2016, "tv"),
}


class 假TMDB:
    """认三部作品：沙丘（电影）/ 银翼杀手（电影）/ 怪奇物语（剧）。"""

    表 = {
        "沙丘": ("438631", "沙丘", "Dune", 2021, "movie"),
        "银翼杀手": ("78", "银翼杀手", "Blade Runner", 1982, "movie"),
        "怪奇物语": ("66732", "怪奇物语", "Stranger Things", 2016, "tv"),
        "Dune": ("438631", "沙丘", "Dune", 2021, "movie"),
        "Blade Runner": ("78", "银翼杀手", "Blade Runner", 1982, "movie"),
        "Stranger Things": ("66732", "怪奇物语", "Stranger Things", 2016, "tv"),
    }

    def __init__(self) -> None:
        self.请求次数 = 0

    def 可用(self) -> bool:
        return True

    def 配置信息(self) -> dict:
        return {"images": {"secure_base_url": "https://image.tmdb.org/t/p/"}}

    def _候选(self, 标题: str) -> list[匹配候选]:
        self.请求次数 += 1
        for 键, (标识, 标题中, 原名, 年份, 类型) in self.表.items():
            if 键.lower() in str(标题).lower():
                return [匹配候选(
                    来源标识=标识, 标题=标题中, 原名=原名, 年份=年份,
                    类型=媒体类型.电影 if 类型 == "movie" else 媒体类型.剧集,
                    热度=500)]
        return []

    def 搜电影(self, 标题: str, 年份=None) -> list[匹配候选]:
        return [x for x in self._候选(标题) if x.类型 is 媒体类型.电影]

    def 搜剧集(self, 标题: str, 年份=None) -> list[匹配候选]:
        return [x for x in self._候选(标题) if x.类型 is 媒体类型.剧集]

    def 取电影(self, id: str) -> 媒体条目:
        self.请求次数 += 1
        for 标识, 标题中, 原名, 年份, 类型 in self.表.values():
            if 标识 == str(id) and 类型 == "movie":
                条目 = 媒体条目(类型=媒体类型.电影, 标题=标题中, 原名=原名, 年份=年份)
                条目.外部ID = {"tmdb": 标识}
                return 条目
        return 媒体条目(类型=媒体类型.电影, 标题="?", 外部ID={"tmdb": str(id)})

    def 取剧集(self, id: str) -> 媒体条目:
        self.请求次数 += 1
        条目 = 媒体条目(类型=媒体类型.剧集, 标题="怪奇物语", 原名="Stranger Things",
                    年份=2016)
        条目.外部ID = {"tmdb": "66732"}
        条目.季们.append(季(季号=1, 标题="第 1 季", 集数=8))
        return 条目

    def 取季(self, 剧id: str, 季号: int) -> 季:
        self.请求次数 += 1
        季对象 = 季(季号=int(季号), 标题=f"第 {季号} 季", 集数=8)
        for 号 in range(1, 9):
            季对象.集们.append(集(季号=int(季号), 集号=号, 标题=f"第{号}集"))
        return 季对象

    def 关闭(self) -> None:
        return None


def 造混合目录(根: Path, 目录名: str = "乱放") -> Path:
    目录 = 根 / 目录名
    目录.mkdir(parents=True, exist_ok=True)
    for 名 in 混合目录文件:
        (目录 / 名).write_bytes(b"v" * 4096)
    return 目录


class 混合目录对比测试(unittest.TestCase):
    """同一批文件：老路（按目录归并）糊/丢，新路（按文件识别）各归各。"""

    def setUp(self) -> None:
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)
        self.媒体 = self.根 / "媒体"
        self.目录 = 造混合目录(self.媒体)

    def _服务(self, 名字: str) -> 刮削服务:
        库 = 资料库(self.根 / f"库_{名字}.db")
        self.addCleanup(库.关闭)
        服务 = 刮削服务(库, 假TMDB(), 图片缓存(self.根 / f"图_{名字}"),
                    刮削设置(最小文件字节=0, 本批上限=50, 下载图片=False),
                    日志回调=lambda _t: None)
        self.addCleanup(服务.关闭)
        self.库 = 库
        return 服务

    def test_枚举视频单元是一个文件一个(self):
        结果 = 枚举视频单元(self.媒体, 最小文件字节=0)
        self.assertEqual(len(结果.条目们), 4, "四个视频 = 四个单元（不是一部作品一个）")
        self.assertEqual({x.视频们[0].name for x in 结果.条目们},
                         set(混合目录文件), "每个单元只带自己那一个文件")
        self.assertEqual(结果.剧集数, 1, "只有怪奇物语那个目录算剧")

    def test_老路按目录归并会丢掉电影(self):
        """钉住"改造前的毛病"：同一个目录里认出剧集后，电影文件整批消失。"""
        结果 = 扫描媒体库(self.媒体, 最小文件字节=0)
        视频们 = {v.name for x in 结果.条目们 for v in x.视频们}
        self.assertIn("Stranger.Things.S01E01.mkv", 视频们)
        self.assertNotIn("Dune.2021.2160p.mkv", 视频们,
                         "老路确实会丢掉没有季集信息的电影（这就是要改的原因）")

    def test_新路各归各的(self):
        服务 = self._服务("新")
        数量, _摘要 = 服务.扫库按文件([self.媒体])
        self.assertEqual(数量, 4)
        任务 = 服务.库.取任务("待处理", 20)
        self.assertEqual(len(任务), 3, f"应当是三部作品各自的任务：{[r['路径'] for r in 任务]}")
        结果 = 服务.续跑()
        self.assertEqual(结果.失败, 0, f"不该失败：{结果.错误们}")
        self.assertEqual(self.库.计数(), 3, "库里应当恰好 3 条媒体（不是 1 条）")
        标题们 = sorted(str(行["标题"]) for 行 in self.库.列表())
        self.assertEqual(标题们, ["怪奇物语", "沙丘", "银翼杀手"],
                         "三部作品各一条，标题来自识别结果（不是目录名）")
        # 每个作品的**文件表**只包含自己的文件（没有互相串）
        归属 = {}
        for 行 in self.库.列表():
            归属[str(行["标题"])] = {Path(r["路径"]).name
                                for r in self.库.文件们(int(行["id"]))}
        self.assertEqual(归属["沙丘"], {"Dune.2021.2160p.mkv"})
        self.assertEqual(归属["银翼杀手"], {"Blade.Runner.1982.1080p.mkv"})
        self.assertEqual(归属["怪奇物语"],
                         {"Stranger.Things.S01E01.mkv", "Stranger.Things.S01E02.mkv"})
        # 怪奇物语的两集要挂在"集"上（剧集形态没坏）
        行 = next(x for x in self.库.列表() if str(x["标题"]) == "怪奇物语")
        条目 = self.库.取媒体(int(行["id"]))
        挂了 = [c for 季对象 in 条目.季们 for c in 季对象.集们 if c.文件路径]
        self.assertEqual(len(挂了), 2, "两集都要挂上文件")

    def test_一个作品的目录行为不变(self):
        """单作品目录（正常整理过的库）走新路也要跟老路一样：一张卡、全部集挂上。"""
        目录 = self.根 / "正常" / "怪奇物语 (2016)" / "Season 01"
        目录.mkdir(parents=True, exist_ok=True)
        for 号 in range(1, 4):
            (目录 / f"怪奇物语 S01E{号:02d}.mkv").write_bytes(b"v" * 4096)
        服务 = self._服务("正常")
        服务.扫库按文件([self.根 / "正常"])
        self.assertEqual(len(服务.库.取任务("待处理", 20)), 1, "一部剧仍然只登记一个任务")
        结果 = 服务.续跑()
        self.assertEqual(结果.失败, 0, f"不该失败：{结果.错误们}")
        self.assertEqual(self.库.计数(), 1)
        行 = self.库.列表()[0]
        条目 = self.库.取媒体(int(行["id"]))
        self.assertEqual(条目.标题, "怪奇物语")
        挂了 = [c for 季对象 in 条目.季们 for c in 季对象.集们 if c.文件路径]
        self.assertEqual(len(挂了), 3, f"三集都该挂上：{挂了}")

    def test_没有客户端时退回按目录归并(self):
        """没配 TMDB 时识别不了作品身份 → 退回老路（保守，不许把任务搞丢）。"""
        库 = 资料库(self.根 / "库_离线.db")
        self.addCleanup(库.关闭)
        服务 = 刮削服务(库, None, 图片缓存(self.根 / "图_离线"),
                    刮削设置(最小文件字节=0, 下载图片=False))
        self.addCleanup(服务.关闭)
        数量, _ = 服务.扫库按文件([self.媒体])
        self.assertEqual(数量, 4)
        self.assertEqual(len(库.取任务("待处理", 20)), 1,
                         "离线时按目录归并（这个目录里只有剧集被认出来 → 1 个任务）")


class 单个文件重刮测试(unittest.TestCase):
    """混合目录里"直接右键这一个文件重刮"也不许把同目录别人的文件带进来。

    这条钉的是 ``_造单元(文件)`` 的兜底：父目录"按目录归并"的结果里**根本没有这个文件**
    （同一个目录里认出剧集时电影被整批丢掉），所以要按这个文件单独造单元。
    """

    def setUp(self) -> None:
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)
        self.媒体 = self.根 / "媒体"
        self.目录 = 造混合目录(self.媒体)
        self.库 = 资料库(self.根 / "库_单文件.db")
        self.addCleanup(self.库.关闭)
        self.服务 = 刮削服务(self.库, 假TMDB(), 图片缓存(self.根 / "图_单文件"),
                        刮削设置(最小文件字节=0, 下载图片=False))
        self.addCleanup(self.服务.关闭)

    def test_只刮这一个电影文件(self):
        结果 = self.服务.刮路径(self.目录 / "Dune.2021.2160p.mkv")
        self.assertEqual(结果.失败, 0, f"不该失败：{结果.错误们}")
        self.assertEqual(结果.成功, 1, f"应当成功：{结果.摘要()}")
        self.assertEqual(self.库.计数(), 1)
        行 = self.库.列表()[0]
        条目 = self.库.取媒体(int(行["id"]))
        # 标题：`刮路径` 这条老路是"本地优先"（文件名解析出来的标题盖过在线的），
        # 所以这里断言"认对了哪一部作品"用 TMDB id，而不是中文标题。
        self.assertEqual(str(条目.外部ID.get("tmdb")), "438631", "应当认成《沙丘》")
        self.assertEqual(条目.年份, 2021)
        文件们 = {Path(str(r["路径"])).name for r in self.库.文件们(int(行["id"]))}
        self.assertEqual(文件们, {"Dune.2021.2160p.mkv"},
                         "只该带上自己这一个文件（同目录的剧集/别的电影不许跟进来）")


class 网盘按文件测试(unittest.TestCase):
    """网盘那条路（界面喂"一集一个"单元进来）也要按文件识别后归并。"""

    def setUp(self) -> None:
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)
        self.库 = 资料库(self.根 / "资料库.db")
        self.addCleanup(self.库.关闭)

    def test_同一个网盘目录里两部作品分成两条(self):
        from wangpan.scrape.扫描 import 造远端单元
        文件们 = ["/分享/乱放/Dune.2021.2160p.mkv",
                "/分享/乱放/Blade.Runner.1982.1080p.mkv"]
        单元们 = 造远端单元("guangya", "/分享/乱放", 文件们)
        self.assertEqual(len(单元们), 2)
        服务 = 刮削服务(self.库, 假TMDB(), 图片缓存(self.根 / "图"),
                    刮削设置(最小文件字节=0, 下载图片=False),
                    远端单元={})
        self.addCleanup(服务.关闭)
        登记, _ = 服务.记远端单元按文件(单元们)
        self.assertEqual(登记, 2, "两部电影 = 两条任务")
        结果 = 服务.续跑()
        self.assertEqual(结果.失败, 0, f"不该失败：{结果.错误们}")
        self.assertEqual(self.库.计数(), 2)
        self.assertEqual(sorted(str(x["标题"]) for x in self.库.列表()),
                         ["沙丘", "银翼杀手"])


if __name__ == "__main__":
    unittest.main()
