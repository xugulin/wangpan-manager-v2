"""刮削流水线端到端测试（**不联网**）。

用一个"假 TMDB"（实现跟真客户端一样的接口）+ `file://` 图片地址，
把 `扫描 → 匹配打分 → 取详情 → 挂文件 → 下图 → 落库 → 写 NFO` 整条跑一遍。
这样在没有 API Key 的机器与 CI 上，也能证明流水线是通的。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from urllib.parse import quote

from tests.公用 import 临时目录

from PySide6.QtWidgets import QApplication

from wangpan.scrape.库 import 资料库, 查询条件
from wangpan.scrape.模型 import (人物, 人物工种, 图片, 图片类型, 媒体条目, 媒体类型,
                            匹配候选, 参演, 分级, 季, 集)
from wangpan.scrape.图片 import 图片缓存
from wangpan.scrape.服务 import 刮削服务, 刮削设置
from wangpan.scrape.NFO import 读NFO, 写NFO
from wangpan.scrape.扫描 import 扫描媒体库

应用 = QApplication.instance() or QApplication([])


# ---------------- 假 TMDB ----------------

class 假TMDB:
    """跟真客户端同签名的最小实现：数据是造的，图片走 file://。"""

    def __init__(self, 图片目录: Path, 带集列表: bool = True) -> None:
        self.图片目录 = Path(图片目录)
        self.图片目录.mkdir(parents=True, exist_ok=True)
        self.请求次数 = 0
        self.搜索过的词: list[str] = []
        #: 真 TMDB 的 `/tv/{id}` 里 **seasons[] 只有集数、没有集列表**（集要另外
        #: 打 `/tv/{id}/season/{n}`）。默认 True 是为了兼容老测试；
        #: 验"季集对齐"时必须设 False，否则假客户端会把真实现里的坑盖住。
        self.带集列表 = 带集列表
        self.取过的季: list[tuple[str, int]] = []

    # --- 配置 ---
    def 可用(self) -> bool:
        return True

    def 配置信息(self) -> dict:
        return {"images": {"secure_base_url": self.图片目录.as_uri() + "/",
                          "poster_sizes": ["w500"], "backdrop_sizes": ["w1280"]}}

    def 图片地址(self, 远端路径: str, 尺寸: str = "w500") -> str:
        return f"{self.图片目录.as_uri()}/{尺寸}{远端路径}"

    # --- 搜索 ---
    def 搜电影(self, 标题: str, 年份=None) -> list[匹配候选]:
        self.请求次数 += 1
        self.搜索过的词.append(标题)
        if "沙丘" in 标题 or "Dune" in 标题:
            return [匹配候选(来源标识="438631", 标题="沙丘", 原名="Dune", 年份=2021,
                          类型=媒体类型.电影, 简介="厄拉科斯的故事", 热度=900,
                          海报远端="/dune.jpg")]
        if "银翼" in 标题 or "Blade" in 标题:
            return [匹配候选(来源标识="78", 标题="银翼杀手", 原名="Blade Runner",
                          年份=1982, 类型=媒体类型.电影, 热度=800,
                          海报远端="/br.jpg")]
        return []

    def 搜剧集(self, 标题: str, 年份=None) -> list[匹配候选]:
        self.请求次数 += 1
        self.搜索过的词.append(标题)
        if "怪奇" in 标题 or "Stranger" in 标题:
            return [匹配候选(来源标识="66732", 标题="怪奇物语", 原名="Stranger Things",
                          年份=2016, 类型=媒体类型.剧集, 热度=1500,
                          海报远端="/st.jpg")]
        return []

    # --- 详情 ---
    def 取电影(self, id: str) -> 媒体条目:
        self.请求次数 += 1
        条目 = 媒体条目(类型=媒体类型.电影, 标题="沙丘", 原名="Dune", 年份=2021,
                    简介="厄拉科斯的故事", 时长分钟=155, 评分=8.1, 评分票数=12000,
                    影评人评分=83.0, 状态="Released", 原始语言="en")
        条目.外部ID = {"tmdb": "438631", "imdb": "tt1160419"}
        条目.标签 = ["科幻", "冒险"]
        条目.分级们 = [分级("US", "PG-13"), 分级("HK", "IIA")]
        条目.制片公司 = ["Legendary Pictures"]
        条目.海报 = 图片(图片类型.海报, "/dune.jpg", None, 500, 750, "zh", 8.0, 100)
        条目.背景 = 图片(图片类型.背景, "/dune_bg.jpg", None, 1280, 720, "", 7.0, 50)
        条目.参演们 = [
            参演(人物=人物(标识="1190668", 名字="提莫西·查拉梅"),
                工种=人物工种.演员, 角色="Paul Atreides", 排序=1),
            参演(人物=人物(标识="1373737", 名字="丽贝卡·弗格森"),
                工种=人物工种.演员, 角色="Lady Jessica", 排序=2),
            参演(人物=人物(标识="137427", 名字="丹尼斯·维伦纽瓦"),
                工种=人物工种.导演, 排序=0),
        ]
        for 人 in [x.人物 for x in 条目.参演们]:
            人.头像 = 图片(图片类型.人物, f"/p{人.标识}.jpg", None, 185, 185)
        return 条目

    def 取剧集(self, id: str) -> 媒体条目:
        self.请求次数 += 1
        条目 = 媒体条目(类型=媒体类型.剧集, 标题="怪奇物语", 原名="Stranger Things",
                    年份=2016, 简介="小镇怪事", 评分=8.6, 评分票数=20000,
                    状态="Returning Series", 原始语言="en")
        条目.外部ID = {"tmdb": "66732", "tvdb": "305288"}
        条目.标签 = ["悬疑", "科幻"]
        条目.分级们 = [分级("US", "TV-14")]
        条目.海报 = 图片(图片类型.海报, "/st.jpg", None, 500, 750)
        第一季 = 季(季号=1, 标题="第 1 季", 简介="", 集数=3, 播出日期="2016-07-15")
        if self.带集列表:
            for 号, 名 in ((1, "第一章"), (2, "第二章"), (3, "第三章")):
                第一季.集们.append(集(季号=1, 集号=号, 标题=名, 播出日期="2016-07-15",
                                  时长分钟=48, 评分=8.0))
        条目.季们.append(第一季)
        return 条目

    def 取季(self, 剧id: str, 季号: int) -> 季:
        self.请求次数 += 1
        self.取过的季.append((str(剧id), int(季号)))
        季对象 = 季(季号=季号, 标题=f"第 {季号} 季", 集数=3)
        for 号, 名 in ((1, "第一章"), (2, "第二章"), (3, "第三章")):
            季对象.集们.append(集(季号=季号, 集号=号, 标题=名,
                              播出日期="2016-07-15", 时长分钟=48, 评分=8.0))
        return 季对象

    def 取分级(self, 类型, id: str) -> list[分级]:
        return [分级("US", "PG-13")]

    def 取图片(self, 类型, id: str, 种类: str = "") -> list[图片]:
        return []

    def 下载图片(self, 远端路径: str, 落点: Path, 尺寸: str = "w500"):
        return None

    def 关闭(self) -> None:
        return


def 造库(根: Path, 图片目录: Path) -> None:
    """造一个像真实媒体库的目录（电影文件夹 + 剧集季目录），并准备假 CDN 的图。"""
    # 电影：文件夹 + 多版本
    (根 / "Movies" / "沙丘 (2021)").mkdir(parents=True, exist_ok=True)
    for 名 in ("沙丘 (2021) - 2160p.mkv", "沙丘 (2021) - 1080p.mkv"):
        (根 / "Movies" / "沙丘 (2021)" / 名).write_bytes(b"x" * (21 * 1024 * 1024))
    # 剧集：Season 01 + 3 集 + 一个特别篇
    for 季名 in ("Season 01", "Season 00"):
        (根 / "Shows" / "怪奇物语 (2016)" / 季名).mkdir(parents=True, exist_ok=True)
    for 号 in (1, 2, 3):
        (根 / "Shows" / "怪奇物语 (2016)" / "Season 01" /
         f"怪奇物语 S01E{号:02d}.mkv").write_bytes(b"y" * (25 * 1024 * 1024))
    (根 / "Shows" / "怪奇物语 (2016)" / "Season 00" /
     "怪奇物语 S00E01.mkv").write_bytes(b"z" * (25 * 1024 * 1024))
    # 假 CDN 的图片（file:// 也能被 urllib 读，这样不必联网）
    (图片目录).mkdir(parents=True, exist_ok=True)
    from PySide6.QtGui import QColor, QImage
    for 名, 宽, 高, 色 in (("w500/dune.jpg", 500, 750, (150, 90, 40)),
                        ("w1280/dune_bg.jpg", 1280, 720, (30, 40, 60)),
                        ("w185/p1190668.jpg", 185, 185, (200, 160, 120)),
                        ("w185/p1373737.jpg", 185, 185, (180, 140, 200)),
                        ("w185/p137427.jpg", 185, 185, (120, 160, 200)),
                        ("w500/st.jpg", 500, 750, (40, 60, 120))):
        路径 = 图片目录 / 名
        路径.parent.mkdir(parents=True, exist_ok=True)
        图 = QImage(宽, 高, QImage.Format.Format_RGB32)
        图.fill(QColor(*色))
        图.save(str(路径))


class 流水线测试(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.临时 = 临时目录()
        cls.根 = Path(cls.临时.name)
        cls.媒体 = cls.根 / "媒体"
        cls.图床 = cls.根 / "假CDN"
        造库(cls.媒体, cls.图床)

    @classmethod
    def tearDownClass(cls):
        cls.临时.cleanup()

    def _服务(self, 写NFO: bool = False, 设置改动: dict | None = None):
        库 = 资料库(self.根 / f"库_{self.id().split('.')[-1]}.db")
        self.addCleanup(库.关闭)
        缓存 = 图片缓存(self.根 / "图缓存")
        客户端 = 假TMDB(self.图床)
        设置 = 刮削设置(本批上限=10, 写NFO=写NFO, 最小文件字节=1024)
        for 键, 值 in (设置改动 or {}).items():
            setattr(设置, 键, 值)
        self._日志行: list[str] = []
        服务 = 刮削服务(库, 客户端, 缓存, 设置, 日志回调=self._日志行.append)
        self.addCleanup(服务.关闭)
        return 库, 客户端, 服务, 缓存

    def test_扫描能认出电影与剧集(self):
        结果 = 扫描媒体库(self.媒体, 最小文件字节=1024)
        self.assertGreaterEqual(len(结果.条目们), 3)
        电影 = [x for x in 结果.条目们 if x.类型 is 媒体类型.电影]
        剧集 = [x for x in 结果.条目们 if x.类型 is 媒体类型.剧集]
        self.assertEqual(len(电影), 1, "沙丘那一个文件夹应该合成一部电影")
        self.assertEqual(len(电影[0].视频们), 2, "两个版本要归到同一部")
        self.assertEqual(len(剧集), 4, "3 集 + 1 个特别篇（S00E01）")
        特别篇 = [x for x in 剧集 if x.特别篇]
        self.assertEqual(len(特别篇), 1)

    def test_电影全流程_落库与图片(self):
        库, 客户端, 服务, 缓存 = self._服务()
        服务.扫库([self.媒体])
        # 任务按"剧/电影文件夹"归并：沙丘 1 个 + 怪奇物语 1 个 = 2 个
        # （一集一个任务会让 20 集的剧搜 20 次，白撞限流）
        self.assertEqual(len(库.取任务("待处理")), 2)
        结果 = 服务.续跑()
        self.assertGreaterEqual(
            结果.成功, 2,
            f"应该至少刮成电影+剧集：{结果.摘要()}"
            + "｜日志：" + " / ".join(self._日志行[-8:]))
        行 = 库.列表(查询条件(关键词="沙丘"))
        self.assertTrue(行, "沙丘应该入库")
        条目 = 库.取媒体(int(行[0]["id"]))
        self.assertEqual(条目.类型, 媒体类型.电影)
        self.assertEqual(条目.年份, 2021)
        self.assertAlmostEqual(条目.评分, 8.1, delta=0.01)
        self.assertAlmostEqual(条目.影评人评分, 83.0, delta=0.1, msg="0–100 量纲要保住")
        self.assertIn("US:PG-13", [g.可读() for g in 条目.分级们])
        self.assertEqual(条目.导演()[0].人物.名字, "丹尼斯·维伦纽瓦")
        self.assertGreaterEqual(len(条目.演员()), 2)
        self.assertIn("科幻", 条目.标签)
        # 图片真的下到本地缓存了（file:// 假 CDN）
        self.assertIsNotNone(条目.海报, "海报必须入库")
        self.assertTrue(条目.海报.可用(), f"海报文件应存在：{条目.海报.本地路径}")
        self.assertTrue(Path(条目.海报.本地路径).stat().st_size > 0)
        self.assertIsNotNone(条目.背景)
        self.assertTrue(条目.背景.可用())
        # 多版本文件都挂在同一部作品上
        self.assertGreaterEqual(len(库.文件们(int(行[0]["id"]))), 2)

    def test_剧集把文件挂到集上(self):
        库, 客户端, 服务, 缓存 = self._service_剧集()
        行 = 库.列表(查询条件(关键词="怪奇"))
        self.assertTrue(行, "剧集应该入库")
        条目 = 库.取媒体(int(行[0]["id"]))
        self.assertEqual(条目.类型, 媒体类型.剧集)
        self.assertGreaterEqual(len(条目.季们), 1)
        第一季 = next((s for s in 条目.季们 if s.季号 == 1), None)
        self.assertIsNotNone(第一季)
        self.assertEqual(len(第一季.集们), 3)
        挂了文件的 = [c for c in 第一季.集们 if c.文件路径 is not None]
        self.assertEqual(len(挂了文件的), 3, "三集都该挂上本地文件")
        for 集对象 in 挂了文件的:
            self.assertTrue(Path(集对象.文件路径).is_file())

    def _service_剧集(self):
        库 = 资料库(self.根 / "库_剧集.db")
        self.addCleanup(库.关闭)
        缓存 = 图片缓存(self.根 / "图缓存")
        客户端 = 假TMDB(self.图床)
        服务 = 刮削服务(库, 客户端, 缓存,
                    刮削设置(本批上限=10, 最小文件字节=1024, 抓季集详情=False))
        self.addCleanup(服务.关闭)
        剧目录 = self.媒体 / "Shows"
        服务.扫库([剧目录])
        服务.续跑()
        return 库, 客户端, 服务, 缓存

    def test_写NFO与读回一致(self):
        库, 客户端, 服务, 缓存 = self._服务(写NFO=True)
        服务.刮路径(self.媒体 / "Movies" / "沙丘 (2021)")
        nfo = self.媒体 / "Movies" / "沙丘 (2021)" / "沙丘 (2021).nfo"
        self.assertTrue(nfo.is_file(), "开了写 NFO 就该写出来")
        读回 = 读NFO(nfo)
        self.assertIsNotNone(读回)
        self.assertEqual(读回.标题, "沙丘")
        self.assertEqual(读回.年份, 2021)
        self.assertAlmostEqual(读回.评分, 8.1, delta=0.1)
        self.assertIn("科幻", 读回.标签)
        self.assertEqual(读回.外部ID.get("tmdb"), "438631")

    def test_没有客户端时也能用本地NFO入库(self):
        """没配 TMDB 时：扫描 + 本地 NFO 也要能进库（不能因为没网就什么都做不了）。"""
        库 = 资料库(self.根 / "库_离线.db")
        self.addCleanup(库.关闭)
        目录 = self.根 / "离线媒体" / "银翼杀手 (1982)"
        目录.mkdir(parents=True, exist_ok=True)
        (目录 / "银翼杀手 (1982).mkv").write_bytes(b"v" * (22 * 1024 * 1024))
        写NFO(媒体条目(类型=媒体类型.电影, 标题="银翼杀手", 年份=1982, 评分=8.1,
                    简介="复制人", 标签=["科幻"],
                    参演们=[参演(人物=人物(名字="哈里森·福特"),
                              工种=人物工种.演员, 角色="Deckard")],
                    外部ID={"tmdb": "78"}), 目录 / "银翼杀手 (1982).nfo")
        服务 = 刮削服务(库, None, 图片缓存(self.根 / "图缓存2"),
                    刮削设置(最小文件字节=1024))
        self.addCleanup(服务.关闭)
        结果 = 服务.刮路径(目录)
        self.assertGreaterEqual(结果.跳过 + 结果.成功 + 结果.需要确认, 1)
        行 = 库.列表(查询条件(关键词="银翼"))
        self.assertTrue(行, "本地 NFO 也要能入库")
        条目 = 库.取媒体(int(行[0]["id"]))
        self.assertEqual(条目.年份, 1982)
        self.assertEqual(条目.演员()[0].人物.名字, "哈里森·福特")


if __name__ == "__main__":
    unittest.main()


class 季集对齐测试(unittest.TestCase):
    """剧集必须"挂得上文件"——这是真机测真 API 才暴露的坑。

    真 TMDB 的 `/tv/{id}` 里 ``seasons[]`` **只有 episode_count，没有集列表**，
    集要另外打 `/tv/{id}/season/{n}`。原来的流水线只取剧集详情就落库，
    结果"剧集入库了、海报也下了，但一集文件都没挂上"（海报墙点进去播不了）。
    这里的假客户端刻意照真 API 的形状来（`带集列表=False`），把这个坑钉死。
    """

    @classmethod
    def setUpClass(cls):
        cls.临时 = 临时目录()
        cls.根 = Path(cls.临时.name)
        cls.媒体 = cls.根 / "媒体"
        cls.图床 = cls.根 / "假CDN"
        造库(cls.媒体, cls.图床)

    @classmethod
    def tearDownClass(cls):
        cls.临时.cleanup()

    def _服务(self, 带集列表: bool, 抓季集详情: bool, 名字: str):
        库 = 资料库(self.根 / f"库_{名字}.db")
        self.addCleanup(库.关闭)
        客户端 = 假TMDB(self.图床, 带集列表=带集列表)
        服务 = 刮削服务(库, 客户端, 图片缓存(self.根 / f"图_{名字}"),
                    刮削设置(最小文件字节=1024, 抓季集详情=抓季集详情))
        self.addCleanup(服务.关闭)
        return 库, 客户端, 服务

    def test_真API形状的剧集也要挂上文件(self):
        库, 客户端, 服务 = self._服务(带集列表=False, 抓季集详情=True, 名字="对齐")
        服务.扫库([self.媒体 / "Shows" / "怪奇物语 (2016)"])
        结果 = 服务.续跑()
        self.assertEqual(结果.失败, 0, f"不该失败：{结果.错误们}")
        行们 = 库.列表(查询条件(关键词="怪奇"))
        self.assertTrue(行们)
        条目 = 库.取媒体(int(行们[0]["id"]))
        第一季 = next(s for s in 条目.季们 if s.季号 == 1)
        self.assertEqual(len(第一季.集们), 3, "集列表要靠逐季详情补上")
        self.assertEqual(第一季.集们[0].标题, "第一章")
        挂了 = [c for c in 第一季.集们 if c.文件路径]
        self.assertEqual(len(挂了), 3, "本地三集都要挂上")
        # 本地有 S00E01（特别篇）也有 S01E01~03 → 只抓这两季，第 2/3 季不碰
        self.assertEqual(客户端.取过的季, [("66732", 0), ("66732", 1)],
                         "只该抓本地有文件的那几季")
        特别篇 = next(s for s in 条目.季们 if s.季号 == 0)
        self.assertEqual(特别篇.标题, "特别篇")
        self.assertTrue(any(c.文件路径 for c in 特别篇.集们),
                        "特别篇的文件也要挂上（本地确实有）")
        文件们 = {Path(r["路径"]).name for r in 库.文件们(int(行们[0]["id"]))}
        self.assertIn("怪奇物语 S01E01.mkv", 文件们)

    def test_关掉逐季详情就用本地文件名兜底(self):
        库, 客户端, 服务 = self._服务(带集列表=False, 抓季集详情=False, 名字="兜底")
        服务.扫库([self.媒体 / "Shows" / "怪奇物语 (2016)"])
        服务.续跑()
        行们 = 库.列表(查询条件(关键词="怪奇"))
        条目 = 库.取媒体(int(行们[0]["id"]))
        第一季 = next(s for s in 条目.季们 if s.季号 == 1)
        挂了 = [c for c in 第一季.集们 if c.文件路径]
        self.assertEqual(len(挂了), 3, "不抓季详情也必须把本地文件挂到集上")
        self.assertEqual(客户端.取过的季, [], "关了就不该打季详情的请求")
        self.assertTrue(all(c.标题 for c in 挂了), "标题要有（本地文件名兜底）")

    def test_只抓本地有的季_不多打请求(self):
        """20 季的剧不该全抓：只有本地有文件的季才值得打一次详情。"""
        目录 = self.根 / "多季剧" / "某剧 (2020)" / "Season 02"
        目录.mkdir(parents=True, exist_ok=True)
        (目录 / "某剧 S02E01.mkv").write_bytes(b"m" * (2 * 1024 * 1024))
        库 = 资料库(self.根 / "库_多季.db")
        self.addCleanup(库.关闭)
        客户端 = 假TMDB(self.图床)
        服务 = 刮削服务(库, 客户端, 图片缓存(self.根 / "图_多季"),
                    刮削设置(最小文件字节=1024, 抓季集详情=True))
        self.addCleanup(服务.关闭)
        结果 = 服务.刮路径(目录 / "某剧 S02E01.mkv")
        self.assertGreaterEqual(结果.跳过 + 结果.成功 + 结果.需要确认, 1)
