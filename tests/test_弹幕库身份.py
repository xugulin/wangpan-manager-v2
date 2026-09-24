"""②弹幕用**资料库身份**：库里有的用库身份，库里没有才回落文件名，集号取库里的季集。

用户原话
========
「获取弹幕应该通过刮削来获取准确的信息来获取对应的弹幕（因为用户会乱放文件夹乱改名，
所以文件名不可信）」。

真机实例：光鸭上那一集被改成 ``146.SDR.8bit.2160p.60fps.AAC2.0.WEB-DL.H265.mp4``，
目录名也不带剧名。刮削之后库里那条记录上是 ``仙逆 / Renegade Immortal / tv#223911 /
S01E146`` —— 弹幕必须按**这些**去搜，而不是按那个文件名。

这里全部用**假客户端**（假的 HTTP 传输 + 临时资料库），一次真网都不打。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.公用 import 临时目录

from wangpan.danmaku.库身份 import 取库身份, 库路径们, 造素材
from wangpan.danmaku.源.接口 import 素材信息, 应答, 请求
from wangpan.danmaku.源.弹弹play import 弹弹play源
from wangpan.danmaku.源.animeko import Animeko源
from wangpan.scrape.库 import 资料库
from wangpan.scrape.模型 import 媒体条目, 媒体类型, 季, 集

#: 真机上那一集的文件名（**故意带一个错集号 146**：库里那一集其实是 S01E07）
改名文件 = "146.SDR.8bit.2160p.60fps.AAC2.0.WEB-DL.H265.mp4"
#: 它在光鸭里的库路径写法
网盘标识 = "guangya"
远端目录 = "/来自：分享/仙逆.4K高码.SDR.60fps"
库路径 = f"{网盘标识}:{远端目录}/{改名文件}"


class 假传输:
    """记录请求、按预置回答（**不打真网**）。"""

    def __init__(self, 回答=None) -> None:
        self.请求们: list[请求] = []
        self.回答 = 回答

    def __call__(self, 请: 请求) -> 应答:
        self.请求们.append(请)
        体 = self.回答(请) if callable(self.回答) else self.回答
        if isinstance(体, 应答):
            return 体
        return 应答(200, json.dumps(体 if 体 is not None else {}, ensure_ascii=False))

    @property
    def 路径们(self) -> list[str]:
        return [str(x.地址) for x in self.请求们]

    def 最后一个体(self) -> dict:
        return json.loads((self.请求们[-1].体 or b"{}").decode("utf-8"))

    def 查询(self, 序: int = -1) -> dict:
        """把某个请求的**查询参数**读出来（搜索是 GET）。"""
        from urllib.parse import parse_qsl, urlsplit
        return dict(parse_qsl(urlsplit(str(self.请求们[序].地址)).query))


def 搜索响应(标题: str, 集们: dict) -> dict:
    """``GET /api/v2/search/episodes`` 形状（``集们`` = {集标题: episodeId}）。"""
    return {"success": True, "errorCode": 0, "errorMessage": "", "animes": [
        {"animeId": 5, "animeTitle": 标题, "type": "tvseries",
         "episodes": [{"episodeId": 编号, "episodeTitle": 名}
                      for 名, 编号 in 集们.items()]}]}


def 匹配响应(标题: str, 集标题: str = "第146话") -> dict:
    return {"success": True, "errorCode": 0, "errorMessage": "",
            "isMatched": True,
            "matches": [{"episodeId": 999, "animeId": 7, "animeTitle": 标题,
                         "episodeTitle": 集标题, "type": "tvseries"}]}


def 造资料库(根: Path, 标题="仙逆", 原名="Renegade Immortal", 集号=7,
          标识="223911") -> 资料库:
    """造一个临时资料库：库里**有一条刮好的记录**，那一集就是 ``库路径``。"""
    库 = 资料库(根 / "资料库.db")
    条目 = 媒体条目(类型=媒体类型.剧集, 标题=标题, 原名=原名, 年份=2023,
                来源="tmdb")
    条目.外部ID = {"tmdb": 标识}
    条目.季们 = [季(季号=1, 标题="第 1 季", 集数=200, 集们=[
        集(季号=1, 集号=集号, 标题="那一年", 文件路径=Path(库路径))])]
    库.存条目(条目)
    return 库


class 库身份测试(unittest.TestCase):
    def setUp(self) -> None:
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)
        self.库 = 造资料库(self.根)
        self.addCleanup(self.库.关闭)

    def test_按库路径找到剧集身份(self):
        身份 = 取库身份([库路径], self.库)
        self.assertIsNotNone(身份)
        self.assertEqual(身份.标识, "223911")
        self.assertEqual(身份.标题, "仙逆")
        self.assertEqual(身份.原名, "Renegade Immortal")
        self.assertEqual(身份.年份, 2023)
        self.assertEqual((身份.季号, 身份.集号), (1, 7), "季集必须取库里的")
        self.assertEqual(身份.搜索词们(), ["仙逆", "Renegade Immortal"])

    def test_路径写法几种都认(self):
        """播放页手上可能只有"直链/本地路径"或"网盘标识+远端路径"，都要能查到。"""
        候选 = 库路径们(Path("/tmp/直链缓存.mp4"), 文件名=改名文件,
                     远端路径=f"{远端目录}/{改名文件}", 网盘标识=网盘标识)
        self.assertIn(库路径, 候选)
        self.assertIsNotNone(取库身份(候选, self.库))

    def test_库里有的用库身份(self):
        素材, 说明 = 造素材(Path("/tmp/直链.mp4"), 文件名=改名文件,
                        远端路径=f"{远端目录}/{改名文件}", 网盘标识=网盘标识,
                        资料库=self.库)
        self.assertTrue(素材.来自资料库)
        self.assertEqual(素材.标题, "仙逆")
        self.assertEqual(素材.原名, "Renegade Immortal")
        self.assertEqual((素材.季号, 素材.集号), (1, 7),
                         "集号必须用库里的（文件名里那个 146 不算数）")
        self.assertEqual(素材.搜索词们, ["仙逆", "Renegade Immortal"])
        self.assertIn("用资料库身份", 说明)

    def test_库里没有回落文件名并说明可能不准(self):
        日志行: list[str] = []
        素材, 说明 = 造素材(Path("/tmp/直链.mp4"), 文件名=改名文件,
                        远端路径="/别的目录/别的文件.mp4", 网盘标识=网盘标识,
                        资料库=self.库, 日志回调=日志行.append)
        self.assertFalse(素材.来自资料库)
        self.assertEqual(素材.文件名, 改名文件)
        self.assertIn("退回用文件名", 说明)
        self.assertIn("可能匹配到别的片子", 说明)
        self.assertTrue(any("退回用文件名" in x for x in 日志行),
                        f"日志必须说明这是兜底：{日志行}")

    def test_资料库打不开也不炸(self):
        class 坏库:
            def 按路径找集(self, _路径):
                raise RuntimeError("库坏了")

            def 按路径找媒体(self, _路径):
                raise RuntimeError("库坏了")

        素材, 说明 = 造素材(Path("/tmp/x.mp4"), 文件名=改名文件, 资料库=坏库())
        self.assertFalse(素材.来自资料库)
        self.assertIn("退回用文件名", 说明)


class 弹弹play用库身份测试(unittest.TestCase):
    def setUp(self) -> None:
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)
        self.库 = 造资料库(self.根)
        self.addCleanup(self.库.关闭)

    def _源(self, 假: 假传输) -> 弹弹play源:
        return 弹弹play源("测试AppId", "测试密钥", 传输=假, 读环境=False,
                        配置路径=self.根 / "没有.json", 缓存目录=self.根 / "缓存",
                        缓存TTL秒=0, 最小请求间隔秒=0.0)

    def _素材(self, 资料库=None):
        return 造素材(Path("/tmp/直链.mp4"), 文件名=改名文件,
                    远端路径=f"{远端目录}/{改名文件}", 网盘标识=网盘标识,
                    资料库=资料库 if 资料库 is not None else self.库)[0]

    def test_有库身份时走作品名搜索而不是文件名match(self):
        假 = 假传输(搜索响应("仙逆", {"第7话": 71, "第8话": 72}))
        源 = self._源(假)
        候选 = 源.匹配(self._素材())
        self.assertEqual([c.标识 for c in 候选], ["71"], "按库里的集号 7 挑集")
        self.assertTrue(all("/api/v2/search/episodes" in x for x in 假.路径们),
                        f"应该走搜索接口：{假.路径们}")
        self.assertFalse(any("/api/v2/match" in x for x in 假.路径们),
                         "有库身份就不该再按文件名 match")
        # 第一发按 TMDB id 精确查（库里给的），后面才是名字
        首次 = 假.查询(0)
        self.assertEqual(首次.get("tmdbId"), "223911")
        self.assertEqual(首次.get("tmdbIdType"), "0")
        self.assertEqual(首次.get("episode"), "7",
                         "搜索时给的集号必须是库里的 7，不是文件名里的 146")

    def test_中文名搜不到会用原名再试(self):
        from urllib.parse import parse_qsl, urlsplit

        def 回答(请):
            查询 = dict(parse_qsl(urlsplit(str(请.地址)).query))
            if 查询.get("tmdbId"):
                return {"success": True, "errorCode": 0, "errorMessage": "",
                        "animes": []}          # TMDB 源里没有它
            if "Renegade" in str(查询.get("anime") or ""):
                return 搜索响应("Renegade Immortal", {"第7话": 71})
            return {"success": True, "errorCode": 0, "errorMessage": "", "animes": []}

        假 = 假传输(回答)
        源 = self._源(假)
        候选 = 源.匹配(self._素材())
        self.assertEqual([c.标识 for c in 候选], ["71"])
        试过的词 = [假.查询(序).get("anime") for 序 in range(len(假.请求们))]
        self.assertEqual([x for x in 试过的词 if x], ["仙逆", "Renegade Immortal"],
                         "中文名与原名都要试")

    def test_按库身份搜不到也不回落文件名(self):
        """搜不到宁可"没找到"，也不许拿乱改的文件名去 match 出一部别的片子。"""
        假 = 假传输({"success": True, "errorCode": 0, "errorMessage": "", "animes": []})
        源 = self._源(假)
        self.assertEqual(源.匹配(self._素材()), [])
        self.assertFalse(any("/api/v2/match" in x for x in 假.路径们),
                         f"不许回落按文件名匹配：{假.路径们}")

    def test_库里没有时才按文件名match(self):
        假 = 假传输(匹配响应("别的片子"))
        源 = self._源(假)
        素材 = 素材信息(文件名=改名文件, 来自资料库=False)
        候选 = 源.匹配(素材)
        self.assertEqual([c.标识 for c in 候选], ["999"])
        self.assertTrue(any("/api/v2/match" in x for x in 假.路径们))
        体 = 假.最后一个体()
        self.assertEqual(体["fileName"], "146.SDR.8bit.2160p.60fps.AAC2.0.WEB-DL.H265")
        self.assertEqual(体["matchMode"], "fileNameOnly")


class Animeko用库身份测试(unittest.TestCase):
    def setUp(self) -> None:
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)
        self.库 = 造资料库(self.根)
        self.addCleanup(self.库.关闭)

    def _源(self, 假: 假传输) -> Animeko源:
        return Animeko源(传输=假, 缓存目录=self.根 / "animeko缓存", 缓存TTL秒=0,
                        最小请求间隔秒=0.0)

    def _素材(self):
        return 造素材(Path("/tmp/直链.mp4"), 文件名=改名文件,
                    远端路径=f"{远端目录}/{改名文件}", 网盘标识=网盘标识,
                    资料库=self.库)[0]

    def test_查词只用库里的中文名与原名(self):
        def 回答(请: 请求) -> dict:
            地址 = str(请.地址)
            if "/subjects/search" in 地址:
                词 = 地址.split("q=", 1)[1].split("&", 1)[0]
                from urllib.parse import unquote
                词 = unquote(词)
                if 词 in ("仙逆", "Renegade Immortal"):
                    return {"items": [{"id": 1, "name": "Renegade Immortal",
                                       "nameCn": "仙逆", "type": "tv"}]}
                return {"items": []}
            if "/v2/subjects/1" in 地址:
                return {"id": 1, "name": "Renegade Immortal", "nameCn": "仙逆",
                        "episodes": [{"id": 7001, "episodeId": 7001, "sort": 7,
                                      "name": "第7集"}]}
            if "/v1/danmaku/" in 地址:
                return {"danmakuList": []}
            return {}

        假 = 假传输(回答)
        源 = self._源(假)
        候选 = 源.匹配(self._素材())
        self.assertTrue(候选, f"应当能按库身份搜到作品：{假.路径们}")
        self.assertEqual(候选[0].标识, "7001", "集号取库里的 7 对应的那一集")
        搜过的词 = [x for x in 假.路径们 if "search" in x]
        from urllib.parse import unquote
        搜过的词 = [unquote(x) for x in 搜过的词]
        self.assertTrue(any("仙逆" in x for x in 搜过的词),
                        f"必须先搜中文名：{搜过的词}")
        self.assertFalse(any("146.SDR" in x for x in 假.路径们),
                         f"不许拿改名后的文件名去搜：{假.路径们}")

    def test_库里没有时用文件名兜底(self):
        假 = 假传输({"items": []})
        源 = self._源(假)
        素材 = 素材信息(文件名="葬送的芙莉莲 S01E01.mkv", 标题="葬送的芙莉莲",
                    季号=1, 集号=1, 来自资料库=False)
        源.匹配(素材)
        搜过的词 = [x for x in 假.路径们 if "search" in x]
        self.assertTrue(any("%E8%91%AC" in x or "芙莉莲" in x for x in 搜过的词),
                        f"库里没有时按文件名解析出来的标题搜：{搜过的词}")


class 播放页接线测试(unittest.TestCase):
    """播放页「找弹幕」真的走"资料库身份优先"（不是只在库里那层对）。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.应用 = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)
        self.库 = 造资料库(self.根)
        self.addCleanup(self.库.关闭)

    def test_装载弹幕用库身份造素材(self):
        from wangpan.danmaku.引擎 import 装载结果
        from wangpan.ui.播放页 import 播放页

        捕获: dict = {}

        class 假弹幕:
            def 装载(自己, 素材, 源们=None, 自动阈值=82.0):
                捕获["素材"] = 素材
                捕获["源数"] = len(list(源们 or []))
                return 装载结果(成功=True, 条数=3, 来源="假源")

        class 假引擎:
            输入 = type("输入", (), {"地址": "/tmp/直链.mp4"})()
            统计 = type("统计", (), {"总时长秒": 1440.0})()

        class 假会话:
            网盘标识 = 网盘标识
            远端路径 = f"{远端目录}/{改名文件}"

        页 = 播放页()
        self.addCleanup(页.关闭)
        页.引擎 = 假引擎()
        页.会话 = 假会话()
        页.弹幕 = 假弹幕()
        提示们: list[str] = []
        页._提示 = 提示们.append
        # 播放页自己会 new 一个真资料库（用户那份）——把"打开资料库"换成临时库
        import wangpan.danmaku.库身份 as 库身份模块
        原 = 库身份模块.打开资料库
        库身份模块.打开资料库 = lambda 路径=None: self.库
        self.addCleanup(lambda: setattr(库身份模块, "打开资料库", 原))

        页.装载弹幕()
        素材 = 捕获.get("素材")
        self.assertIsNotNone(素材, f"没有走到装载：{提示们}")
        self.assertTrue(素材.来自资料库, "播放页必须先查资料库身份")
        self.assertEqual(素材.标题, "仙逆")
        self.assertEqual((素材.季号, 素材.集号), (1, 7), "季集取库里的")
        self.assertEqual(捕获.get("源数"), 3, "三个源都要传给匹配（Animeko/弹弹play/本地）")
        self.assertTrue(any("资料库身份" in x for x in 提示们),
                        f"提示里要说明依据：{提示们}")


if __name__ == "__main__":
    unittest.main()
