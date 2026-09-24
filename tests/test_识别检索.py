"""④ 检索（``wangpan/identify/检索.py``）的单测。

两条路线一起上，各管一段：

* **真 HTTP 路线**：用 ``tests/假TMDB服务.py`` 起一个环回服务，再拿**真的**
  :class:`~wangpan.scrape.tmdb.TMDB客户端`（真 urllib、真鉴权头、真 JSON 解析）去搜。
  这一路证明"多查询真的发出去了、返回真的解析成了候选"；
* **注入假客户端路线**：数调用次数、按查询词给不同结果、按查询词抛异常 ——
  这些"真服务做不到"的场景（限流、单查询失败、缓存命中）只能靠它。

覆盖：多查询合并去重、一条候选被两个查询词搜到的来源证据、单查询报错不影响其它、
缓存命中不再调客户端、类型偏好（unknown 两个接口都搜）、年份软过滤回退、
落盘缓存与跨天过期、客户端没鉴权时的软失败。
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from tests.公用 import 临时目录
from tests.假TMDB服务 import 假TMDB服务

from wangpan.identify.检索 import (检索, 检索候选, 检索缓存, 归一类型偏好, 归一查询词们,
                             默认落盘缓存路径, 类型_电影, 类型_剧集)
from wangpan.scrape.模型 import 匹配候选, 媒体类型
from wangpan.scrape.tmdb import TMDB客户端, TMDB配置, 请求失败


def 造候选(标识: str, 标题: str, 年份=None, 类型=类型_电影, 热度: float = 10.0) -> 匹配候选:
    return 匹配候选(来源标识=标识, 标题=标题, 原名=标题, 年份=年份,
                类型=媒体类型.电影 if 类型 == 类型_电影 else 媒体类型.剧集,
                热度=热度, 额外={"评分": 7.5, "票数": 100, "原始语言": "zh"})


@dataclass
class 假客户端:
    """数调用次数、按 (查询词, 类型) 给不同结果、按查询词抛错的假 TMDB 客户端。

    ``配置`` 故意做成可选：真客户端有 ``配置``（缓存命名空间要用它），
    假客户端没有时 :func:`~wangpan.identify.检索._命名空间` 会退回类名，不该炸。
    """

    表: dict = field(default_factory=dict)          # (查询词, 类型) → list[匹配候选]
    报错们: set = field(default_factory=set)        # {(查询词, 类型)}
    可用与否: bool = True
    语言: str = "zh-CN"
    调用: list = field(default_factory=list)

    class _配置:
        language = "zh-CN"
        接口基地址 = ""

    配置 = _配置()

    def 可用(self) -> bool:
        return self.可用与否

    def _搜(self, 类型: str, 标题: str, 年份=None):
        self.调用.append((类型, 标题, 年份))
        if (标题, 类型) in self.报错们:
            raise 请求失败(429, f"假限流：{标题}")
        return list(self.表.get((标题, 类型), []))

    def 搜电影(self, 标题: str, 年份=None):
        return self._搜(类型_电影, 标题, 年份)

    def 搜剧集(self, 标题: str, 年份=None):
        return self._搜(类型_剧集, 标题, 年份)


class 与真客户端打通测试(unittest.TestCase):
    """真 HTTP + 真 urllib + 真鉴权头（假服务顶替网络对面）。"""

    def setUp(self):
        self.临时 = 临时目录()
        self.服务 = 假TMDB服务(Path(self.临时.name) / "图")
        self.服务.启动()
        # 缓存 TTL=0：单测不许往 数据/缓存/tmdb 里写文件（那会污染真机缓存）。
        self.客户端 = TMDB客户端(TMDB配置(
            token="fake-token", language="zh-CN", 缓存TTL秒=0, 接口基地址=self.服务.接口基地址,
            缓存目录=Path(self.临时.name) / "tmdb缓存"))

    def tearDown(self):
        self.客户端.关闭()
        self.服务.停止()
        self.临时.cleanup()

    def test_多查询双接口真发请求并合并去重(self):
        结果 = 检索(self.客户端, ["沙丘", "Dune"], "unknown", 缓存=检索缓存())
        # 2 个查询词 × 2 个接口 = 4 次真请求（服务端记录里能数出来）
        搜索请求 = [x for x in self.服务.记录 if "/search/" in x]
        self.assertEqual(len(搜索请求), 4)
        self.assertEqual(结果.请求次数, 4)
        # 假服务的 3 条候选在电影/剧集两个接口各回一遍 → 3+3 条唯一候选
        self.assertEqual(len(结果.候选们), 6)
        类型们 = sorted({x.类型 for x in 结果.候选们})
        self.assertEqual(类型们, [类型_电影, 类型_剧集])
        # 每条候选都被**两个**查询词搜到 → 来源证据有两条（这是⑤打分最关键的输入）
        for 候选 in 结果.候选们:
            self.assertEqual(len(候选.来源), 2, 候选.可读())
            self.assertIn("沙丘", 候选.来源)
            self.assertIn("Dune", 候选.来源)
        self.assertFalse(结果.有错())
        # 传进来的查询词顺序不影响：证据按权重排
        self.assertEqual(结果.命中们[0].查询词, "沙丘")

    def test_候选字段映射正确(self):
        结果 = 检索(self.客户端, ["沙丘"], "movie", 缓存=检索缓存())
        第一条 = 结果.候选们[0]
        self.assertTrue(第一条.标识)
        self.assertEqual(第一条.类型, 类型_电影)
        self.assertEqual(第一条.标题, "沙丘")
        self.assertEqual(第一条.年份, 2021)
        self.assertGreater(第一条.热度, 0)
        self.assertEqual(第一条.票数, 1200)          # 来自 匹配候选.额外
        self.assertIsNotNone(第一条.原始)             # 原对象留着给⑤打分用


class 合并去重测试(unittest.TestCase):

    def test_一条候选被两个查询词搜到只出现一次且记录来源(self):
        客户端 = 假客户端(表={
            ("仙逆", 类型_剧集): [造候选("223911", "仙逆", 2023, 类型_剧集, 热度=50.0)],
            ("Renegade Immortal", 类型_剧集): [
                造候选("223911", "仙逆", 2023, 类型_剧集, 热度=50.0),
                造候选("999", "别的剧", 2020, 类型_剧集, 热度=1.0)],
        })
        结果 = 检索(客户端, ["仙逆", "Renegade Immortal"], "tv", 缓存=检索缓存())
        self.assertEqual(len(结果.候选们), 2)
        仙逆 = next(x for x in 结果.候选们 if x.标识 == "223911")
        self.assertEqual(len(仙逆.来源), 2)
        # 被两个查询词搜到 → 权重取更高的那个（证据更强 → 排在前面）
        self.assertEqual(仙逆.来源["仙逆"], 1.0)
        self.assertGreaterEqual(仙逆.最高权重(), 1.0)
        self.assertEqual(结果.候选们[0].标识, "223911")

    def test_同id不同接口算两条(self):
        客户端 = 假客户端(表={
            ("沙丘", 类型_电影): [造候选("438631", "沙丘", 2021, 类型_电影)],
            ("沙丘", 类型_剧集): [造候选("438631", "沙丘", 2021, 类型_剧集)],
        })
        结果 = 检索(客户端, ["沙丘"], "unknown", 缓存=检索缓存())
        # id 相同但一个是电影一个是剧集 → **不能**合并（它们是两个不同的条目）
        self.assertEqual(len(结果.候选们), 2)
        self.assertEqual({x.类型 for x in 结果.候选们}, {类型_电影, 类型_剧集})

    def test_没有id的候选按标题年份类型合并(self):
        客户端 = 假客户端(表={
            ("某剧", 类型_剧集): [造候选("", "某剧", 2020, 类型_剧集)],
            ("某剧 别名", 类型_剧集): [造候选("", "某剧", 2020, 类型_剧集)],
        })
        结果 = 检索(客户端, ["某剧", "某剧 别名"], "tv", 缓存=检索缓存())
        self.assertEqual(len(结果.候选们), 1)
        self.assertEqual(len(结果.候选们[0].来源), 2)

    def test_类型偏好决定搜哪个接口(self):
        客户端 = 假客户端(表={
            ("某剧", 类型_剧集): [造候选("1", "某剧", 2020, 类型_剧集)],
            ("某剧", 类型_电影): [造候选("2", "某剧电影版", 2020, 类型_电影)],
        })
        检索(客户端, ["某剧"], "tv", 缓存=检索缓存())
        self.assertEqual([x[0] for x in 客户端.调用], [类型_剧集])
        客户端.调用.clear()
        检索(客户端, ["某剧"], "movie", 缓存=检索缓存())
        self.assertEqual([x[0] for x in 客户端.调用], [类型_电影])
        客户端.调用.clear()
        检索(客户端, ["某剧"], "unknown", 缓存=检索缓存())
        self.assertEqual(sorted(x[0] for x in 客户端.调用), [类型_电影, 类型_剧集])

    def test_类型偏好归一(self):
        self.assertEqual(归一类型偏好("tv"), [类型_剧集])
        self.assertEqual(归一类型偏好("电影"), [类型_电影])
        self.assertEqual(sorted(归一类型偏好("unknown")), [类型_电影, 类型_剧集])
        self.assertEqual(sorted(归一类型偏好(None)), [类型_电影, 类型_剧集])

    def test_查询词入参容忍多种形状(self):
        self.assertEqual([x.文本 for x in 归一查询词们("沙丘")], ["沙丘"])
        self.assertEqual([x.文本 for x in 归一查询词们(["沙丘", "沙丘", " Dune "])],
                         ["沙丘", "Dune"])
        self.assertEqual(归一查询词们([]), [])


class 失败要软测试(unittest.TestCase):

    def test_单个查询词报错不影响其它(self):
        客户端 = 假客户端(表={("好词", 类型_剧集): [造候选("1", "某剧", 2020, 类型_剧集)]},
                      报错们={("坏词", 类型_剧集)})
        结果 = 检索(客户端, ["坏词", "好词"], "tv", 缓存=检索缓存())
        self.assertEqual([x.标识 for x in 结果.候选们], ["1"])
        self.assertEqual(len(结果.错误们), 1)
        self.assertIn("坏词", 结果.错误们[0].查询词)
        self.assertIn("429", 结果.错误们[0].原因)
        # 出错的那次也要留下命中记录（真网探测要把它打出来给人看）
        坏 = next(x for x in 结果.命中们 if x.查询词 == "坏词")
        self.assertTrue(坏.错误)

    def test_双接口时一个接口报错另一个照常(self):
        客户端 = 假客户端(表={("某剧", 类型_电影): [造候选("1", "某剧", 2020, 类型_电影)]},
                      报错们={("某剧", 类型_剧集)})
        结果 = 检索(客户端, ["某剧"], "unknown", 缓存=检索缓存())
        self.assertEqual([x.标识 for x in 结果.候选们], ["1"])
        self.assertEqual(len(结果.错误们), 1)

    def test_客户端没鉴权时不发请求(self):
        客户端 = 假客户端(可用与否=False)
        结果 = 检索(客户端, ["沙丘"], "movie", 缓存=检索缓存())
        self.assertEqual(客户端.调用, [])
        self.assertEqual(结果.候选们, [])
        self.assertIn("没配鉴权", 结果.错误们[0].原因)

    def test_没有查询词时不炸(self):
        结果 = 检索(假客户端(), [], "unknown", 缓存=检索缓存())
        self.assertEqual(结果.候选们, [])
        self.assertIn("没有查询词", 结果.错误们[0].原因)


class 年份软过滤测试(unittest.TestCase):

    class _带年份客户端(假客户端):
        def _搜(self, 类型: str, 标题: str, 年份=None):
            self.调用.append((类型, 标题, 年份))
            if 年份:                       # 带年份一律搜不到（模拟 first_air_date_year 硬过滤）
                return []
            return [造候选("1", 标题, 2020, 类型)]

    def test_带年份搜不到时退回不带年份(self):
        客户端 = self._带年份客户端()
        结果 = 检索(客户端, ["某剧"], "tv", 过滤年份=2021, 缓存=检索缓存())
        self.assertEqual([x.标识 for x in 结果.候选们], ["1"])
        self.assertEqual([x[2] for x in 客户端.调用], [2021, None])
        self.assertEqual(结果.年份回退数, 1)
        self.assertTrue(结果.命中们[0].年份回退)

    def test_可以不退回(self):
        客户端 = self._带年份客户端()
        结果 = 检索(客户端, ["某剧"], "tv", 过滤年份=2021, 缓存=检索缓存(), 重试不带年份=False)
        self.assertEqual(结果.候选们, [])
        self.assertEqual([x[2] for x in 客户端.调用], [2021])


class 缓存测试(unittest.TestCase):

    def test_缓存命中不再调客户端(self):
        客户端 = 假客户端(表={("沙丘", 类型_电影): [造候选("1", "沙丘", 2021, 类型_电影)]})
        缓存 = 检索缓存()
        第一次 = 检索(客户端, ["沙丘"], "movie", 缓存=缓存)
        调用数 = len(客户端.调用)
        self.assertEqual(调用数, 1)
        第二次 = 检索(客户端, ["沙丘"], "movie", 缓存=缓存)
        self.assertEqual(len(客户端.调用), 调用数, "缓存命中后不许再打客户端")
        self.assertEqual(第二次.请求次数, 0)
        self.assertEqual(第二次.缓存命中, 1)
        self.assertEqual([x.标识 for x in 第二次.候选们], ["1"])
        self.assertTrue(第二次.命中们[0].来自缓存)
        self.assertEqual([x.标识 for x in 第一次.候选们], ["1"])

    def test_缓存按类型与年份分区(self):
        客户端 = 假客户端(表={
            ("沙丘", 类型_电影): [造候选("1", "沙丘", 2021, 类型_电影)],
            ("沙丘", 类型_剧集): [造候选("2", "沙丘剧版", 2021, 类型_剧集)]})
        缓存 = 检索缓存()
        检索(客户端, ["沙丘"], "movie", 缓存=缓存)
        检索(客户端, ["沙丘"], "tv", 缓存=缓存)
        self.assertEqual(len(客户端.调用), 2, "电影与剧集是两次不同的请求，不许互相顶替")

    def test_不同语言不共用缓存(self):
        甲 = 假客户端(表={("沙丘", 类型_电影): [造候选("1", "沙丘", 2021)]})
        乙 = 假客户端(表={("沙丘", 类型_电影): [造候选("1", "Dune", 2021)]})
        乙.配置 = type("配置", (), {"language": "en-US", "接口基地址": ""})()
        缓存 = 检索缓存()
        检索(甲, ["沙丘"], "movie", 缓存=缓存)
        结果 = 检索(乙, ["沙丘"], "movie", 缓存=缓存)
        self.assertEqual(len(乙.调用), 1)
        self.assertEqual(结果.候选们[0].标题, "Dune")

    def test_落盘缓存跨对象命中(self):
        with 临时目录() as 目录:
            路径 = Path(目录) / "识别检索缓存.json"
            客户端 = 假客户端(表={("沙丘", 类型_电影): [造候选("1", "沙丘", 2021)]})
            检索(客户端, ["沙丘"], "movie", 缓存=检索缓存(落盘=路径))
            self.assertTrue(路径.is_file())
            客户端2 = 假客户端()
            结果 = 检索(客户端2, ["沙丘"], "movie", 缓存=检索缓存(落盘=路径))
            self.assertEqual(客户端2.调用, [], "落盘缓存命中就不该再打客户端")
            self.assertEqual([x.标识 for x in 结果.候选们], ["1"])
            self.assertEqual(结果.候选们[0].标题, "沙丘")

    def test_落盘缓存跨天失效(self):
        时刻 = [1_700_000_000.0]                    # 固定"现在"，好让过期可测
        客户端 = 假客户端(表={("沙丘", 类型_电影): [造候选("1", "沙丘", 2021)]})
        with 临时目录() as 目录:
            路径 = Path(目录) / "识别检索缓存.json"
            检索(客户端, ["沙丘"], "movie", 缓存=检索缓存(落盘=路径, 现在=lambda: 时刻[0]))
            时刻[0] += 86400 * 2                    # 两天后再来
            客户端2 = 假客户端(表={("沙丘", 类型_电影): [造候选("1", "沙丘", 2021)]})
            检索(客户端2, ["沙丘"], "movie", 缓存=检索缓存(落盘=路径, 现在=lambda: 时刻[0]))
            self.assertEqual(len(客户端2.调用), 1, "过期了就该重新打接口")

    def test_坏缓存文件当没有(self):
        with 临时目录() as 目录:
            路径 = Path(目录) / "识别检索缓存.json"
            路径.write_text("{ 这不是 JSON", encoding="utf-8")
            客户端 = 假客户端(表={("沙丘", 类型_电影): [造候选("1", "沙丘", 2021)]})
            结果 = 检索(客户端, ["沙丘"], "movie", 缓存=检索缓存(落盘=路径))
            self.assertEqual(len(结果.候选们), 1)

    def test_默认落盘路径在数据目录(self):
        self.assertEqual(默认落盘缓存路径().name, "识别检索缓存.json")
        self.assertEqual(默认落盘缓存路径().parent.name, "数据")


class 与候选串起来测试(unittest.TestCase):
    """③ → ④ 的接口契约：候选集合直接喂给检索，证据要能合到一起。

    这条链是 P2 的交付物本身（架构文档第三节的 ③④ 两格），所以单独钉一条：
    上游给 ``查询集合``，下游直接吃，不许中间还要谁写一层适配。
    """

    def test_候选生成到检索整条链(self):
        from wangpan.identify.候选 import 生成查询
        客户端 = 假客户端(表={
            ("仙逆", 类型_剧集): [造候选("223911", "仙逆", 2023, 类型_剧集)],
            ("Renegade Immortal", 类型_剧集): [造候选("223911", "仙逆", 2023, 类型_剧集)],
        })
        查询集 = 生成查询({"标题": "仙逆 Renegade Immortal", "年份": None, "类型": "tv",
                       "季": 1, "集": 146}, 目录链=("仙逆",))
        self.assertIn("仙逆", 查询集.文本们())
        self.assertIn("Renegade Immortal", 查询集.文本们())
        结果 = 检索(客户端, 查询集, 查询集.类型, 过滤年份=查询集.年份, 缓存=检索缓存())
        self.assertEqual(len(结果.候选们), 1)
        # 同一部剧被两个查询词搜到 → 一条候选、两条来源证据
        self.assertEqual(len(结果.候选们[0].来源), 2)
        self.assertEqual(结果.候选们[0].标识, "223911")

    def test_没有查询词时不打客户端(self):
        from wangpan.identify.候选 import 生成查询
        客户端 = 假客户端()
        查询集 = 生成查询({"标题": "", "类型": "unknown"}, 目录链=("电影",))
        self.assertTrue(查询集.空的())
        结果 = 检索(客户端, 查询集, 查询集.类型, 缓存=检索缓存())
        self.assertEqual(客户端.调用, [])
        self.assertEqual(结果.候选们, [])


class 序列化测试(unittest.TestCase):

    def test_候选来回转换不丢字段(self):
        原 = 检索候选(标识="1", 类型=类型_剧集, 标题="某剧", 原名="Some Show", 年份=2020,
                   简介="简介", 海报远端="/p.jpg", 热度=3.5, 票数=42, 评分=8.1,
                   原始语言="zh", 来源={"某剧": 80.0, "Some Show": 56.0})
        回来 = 检索候选.从字典(原.到字典())
        self.assertEqual(回来.标识, "1")
        self.assertEqual(回来.类型, 类型_剧集)
        self.assertEqual(回来.年份, 2020)
        self.assertEqual(回来.来源, 原.来源)
        self.assertEqual(回来.来源查询们(), ["某剧", "Some Show"])

    def test_从字典吃坏数据不炸(self):
        结果 = 检索候选.从字典({"年份": "不知道", "来源": "不是字典"})
        self.assertIsNone(结果.年份)
        self.assertEqual(结果.来源, {})


if __name__ == "__main__":
    unittest.main()
