"""Animeko 公益弹幕源单测：**不联网**（注入假传输）。

重点钉住的是"实测得到的字段语义"：
* ``playTime`` 是**毫秒**；
* 白色返回 **-1**（ARGB 全 1）→ 必须按 0xFFFFFF 取，否则弹幕全变黑；
* ``location`` ∈ TOP/BOTTOM/NORMAL；
* 搜索参数名是 **``q``**（不是 query，传错会 400）；
* 按**集号**选到正确的那一集（同一部剧有正片/番外/续作，选错就全是别人的弹幕）；
* 缓存/节流/并发合并都要真的生效（公益服务不能当自家 CDN 刷）。
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path

from tests.公用 import 临时目录

from wangpan.danmaku.模型 import 弹幕模式, 颜色工具
from wangpan.danmaku.源.animeko import (Animeko源, 清理缓存, 解析弹幕, 解析搜索)
from wangpan.danmaku.源.接口 import 应答, 弹幕源错误, 请求, 素材信息


class 假传输:
    """按路径喂 JSON，并记录请求（用来断言参数、缓存、节流）。"""

    def __init__(self, 响应们: dict, 记录: list | None = None, 延迟: float = 0.0) -> None:
        self.响应们 = 响应们
        self.记录 = 记录 if 记录 is not None else []
        self.延迟 = 延迟

    def __call__(self, 请求对象: 请求) -> 应答:
        路径 = 请求对象.地址.split("api.animeko.org", 1)[-1]
        基路径 = 路径.split("?", 1)[0]
        self.记录.append(路径)
        if self.延迟:
            time.sleep(self.延迟)
        for 键, 值 in self.响应们.items():
            if 基路径 == 键 or 基路径.startswith(键):
                return 应答(状态码=200,
                          体=json.dumps(值(路径) if callable(值) else 值))
        return 应答(状态码=404, 体=json.dumps({"错误": "没有这个路径", "路径": 路径}))


搜索响应 = {"items": [
    {"id": 101, "name": "Sousou no Frieren", "nameCn": "葬送的芙莉莲",
     "airDate": "2023-09-29", "score": 8.9, "tags": ["奇幻"], "mainEpisodeCount": 36},
    {"id": 102, "name": "Sousou no Frieren SP", "nameCn": "葬送的芙莉莲 特别篇",
     "airDate": "2024-01-01", "score": 7.5, "tags": [], "mainEpisodeCount": 3},
]}

详情响应 = {
    101: {"id": 101, "name": "Sousou no Frieren", "nameCn": "葬送的芙莉莲",
          "episodes": [
              {"episodeId": 2001, "sort": "1", "name": "冒险的结束"},
              {"episodeId": 2002, "sort": "2", "name": "魔法使"},
              {"episodeId": 2003, "sort": "12", "name": "真正的勇者"},
          ]},
    102: {"id": 102, "name": "Sousou no Frieren SP", "nameCn": "葬送的芙莉莲 特别篇",
          "episodes": [{"episodeId": 3001, "sort": "1", "name": "特别篇 1"}]},
}

弹幕响应 = {"danmakuList": [
    {"id": "d1", "senderId": "s1",
     "danmakuInfo": {"playTime": 213992, "color": -1, "text": "白色是 -1", "location": "NORMAL"}},
    {"id": "d2", "senderId": "s2",
     "danmakuInfo": {"playTime": 1000, "color": 16711680, "text": "红色", "location": "TOP"}},
    {"id": "d3", "senderId": "s3",
     "danmakuInfo": {"playTime": 2000, "color": 255, "text": "蓝色", "location": "BOTTOM"}},
    {"id": "d4", "senderId": "s4",
     "danmakuInfo": {"playTime": 3000, "color": 0, "text": "黑字", "location": "REVERSE"}},
    {"id": "d5", "senderId": "s5",
     "danmakuInfo": {"playTime": -5, "color": -1, "text": "负数时间要丢", "location": "NORMAL"}},
    {"id": "d6", "senderId": "s6",
     "danmakuInfo": {"playTime": 4000, "color": -1, "text": "   ", "location": "NORMAL"}},
    {"id": "d7", "senderId": "s7", "danmakuInfo": {"playTime": "坏值", "color": -1,
                                                 "text": "坏时间要丢", "location": "NORMAL"}},
]}


def 造源(响应们=None, **改动) -> Animeko源:
    临时 = 临时目录()
    参数 = dict(传输=假传输(响应们 or {"/v2/subjects/search": 搜索响应,
                                 "/v2/subjects/101": 详情响应[101],
                                 "/v2/subjects/102": 详情响应[102],
                                 "/v1/danmaku/": 弹幕响应}),
              缓存目录=Path(临时.name) / "缓存", 最小请求间隔秒=0.0)
    参数.update(改动)
    源 = Animeko源(**参数)
    源._测试临时 = 临时          # 防止被 GC 掉
    return 源


class 解析测试(unittest.TestCase):
    def test_颜色与模式映射(self):
        池 = 解析弹幕(弹幕响应["danmakuList"])
        按文本 = {d.文本: d for d in 池.条目们}
        self.assertEqual(按文本["白色是 -1"].颜色, 0xFFFFFF, "-1 要当成白色（ARGB）")
        self.assertEqual(按文本["红色"].颜色, 0xFF0000)
        self.assertEqual(按文本["蓝色"].颜色, 0x0000FF)
        self.assertEqual(按文本["白色是 -1"].模式, 弹幕模式.滚动)
        self.assertEqual(按文本["红色"].模式, 弹幕模式.顶部)
        self.assertEqual(按文本["蓝色"].模式, 弹幕模式.底部)
        self.assertEqual(按文本["黑字"].模式, 弹幕模式.滚动, "不认识的位置退回滚动")

    def test_毫秒与丢弃规则(self):
        池 = 解析弹幕(弹幕响应["danmakuList"])
        文本们 = [d.文本 for d in 池.条目们]
        self.assertIn("白色是 -1", 文本们)
        self.assertEqual([d for d in 池.条目们 if d.文本 == "白色是 -1"][0].毫秒, 213992,
                         "playTime 本来就是毫秒，不要再乘 1000")
        self.assertNotIn("负数时间要丢", 文本们)
        self.assertNotIn("坏时间要丢", 文本们)
        self.assertNotIn("   ", 文本们, "空白文本要丢")
        self.assertEqual(len(池), 4)

    def test_排序(self):
        池 = 解析弹幕(弹幕响应["danmakuList"])
        时间们 = [d.毫秒 for d in 池.条目们]
        self.assertEqual(时间们, sorted(时间们))

    def test_搜索解析(self):
        条目们 = 解析搜索(搜索响应)
        self.assertEqual(len(条目们), 2)
        self.assertEqual(条目们[0]["标题"], "葬送的芙莉莲")
        self.assertEqual(条目们[0]["集数"], 36)
        self.assertEqual(条目们[0]["评分"], 8.9)

    def test_坏数据不炸(self):
        self.assertEqual(len(解析弹幕(None)), 0)
        self.assertEqual(len(解析弹幕([None, 1, "x", {}])), 0)
        self.assertEqual(解析搜索({}), [])


class 请求测试(unittest.TestCase):
    def test_搜索参数名是q(self):
        记录: list = []
        源 = 造源({"/v2/subjects/search": 搜索响应})
        源.传输 = 假传输({"/v2/subjects/search": 搜索响应}, 记录)
        源.搜索("芙莉莲", 上限=3)
        self.assertEqual(len(记录), 1)
        self.assertIn("q=", 记录[0], "参数名必须是 q（传 query 会 400，实测过）")
        self.assertIn("limit=3", 记录[0])

    def test_按集号选对集(self):
        源 = 造源()
        结果们 = 源.匹配(素材信息(文件名="Sousou no Frieren - 02.mkv",
                            标题="葬送的芙莉莲", 集号=2))
        self.assertTrue(结果们)
        最佳 = max(结果们, key=lambda x: x.分数)
        self.assertEqual(最佳.标识, "2002", "第 2 集要选中 ep=2002")
        self.assertEqual(最佳.标题, "葬送的芙莉莲")

    def test_集号对不上就跳过那个作品(self):
        源 = 造源()
        结果们 = 源.匹配(素材信息(文件名="Sousou no Frieren - 99.mkv",
                            标题="葬送的芙莉莲", 集号=99))
        self.assertEqual([r for r in 结果们 if r.标识 == "2001"], [],
                         "没有第 99 集的作品不该被选中")

    def test_取弹幕走对路径(self):
        记录: list = []
        源 = 造源()
        源.传输 = 假传输({"/v1/danmaku/": 弹幕响应}, 记录)
        池 = 源.取弹幕("2001")
        self.assertEqual(len(池), 4)
        self.assertIn("/v1/danmaku/2001", 记录[0])
        self.assertIn("maxCount=8000", 记录[0])
        self.assertIn("toTime=-1", 记录[0])

    def test_没有标识时报错(self):
        with self.assertRaises(弹幕源错误):
            造源().取弹幕("")

    def test_404要抛错而不是当空结果(self):
        源 = 造源({"/v2/subjects/search": 搜索响应})
        with self.assertRaises(弹幕源错误):
            源.取弹幕("999999")


class 缓存与节流测试(unittest.TestCase):
    def test_第二次同地址不发请求(self):
        记录: list = []
        传输 = 假传输({"/v1/danmaku/": 弹幕响应}, 记录)
        源 = Animeko源(传输=传输, 缓存目录=Path(临时目录().name) / "缓存",
                    最小请求间隔秒=0.0)
        self.assertEqual(len(源.取弹幕("2001")), 4)
        self.assertEqual(len(源.取弹幕("2001")), 4)
        self.assertEqual(len(记录), 1, "第二次应该命中缓存")

    def test_TTL过期会重取(self):
        记录: list = []
        传输 = 假传输({"/v1/danmaku/": 弹幕响应}, 记录)
        源 = Animeko源(传输=传输, 缓存目录=Path(临时目录().name) / "缓存",
                    缓存TTL秒=100.0, 最小请求间隔秒=0.0)
        时刻 = [1000.0]
        源._时钟 = lambda: 时刻[0]
        源.取弹幕("2001")
        时刻[0] = 1050.0
        源.取弹幕("2001")
        self.assertEqual(len(记录), 1, "TTL 内不该重取")
        时刻[0] = 1200.0
        源.取弹幕("2001")
        self.assertEqual(len(记录), 2, "TTL 过了要重取")

    def test_TTL为0等于关缓存(self):
        记录: list = []
        源 = Animeko源(传输=假传输({"/v1/danmaku/": 弹幕响应}, 记录),
                    缓存目录=Path(临时目录().name) / "缓存", 缓存TTL秒=0,
                    最小请求间隔秒=0.0)
        源.取弹幕("2001")
        源.取弹幕("2001")
        self.assertEqual(len(记录), 2)

    def test_节流真的会等(self):
        记录: list = []
        源 = Animeko源(传输=假传输({"/v1/danmaku/": 弹幕响应}, 记录),
                    缓存目录=Path(临时目录().name) / "缓存", 最小请求间隔秒=0.25)
        slept: list[float] = []
        源._睡眠 = lambda 秒: slept.append(秒)
        源.取弹幕("2001")
        源.取弹幕("2002")
        self.assertTrue(slept and slept[0] > 0, "第二次请求应该被节流")

    def test_并发同一集只发一次(self):
        记录: list = []
        源 = Animeko源(传输=假传输({"/v1/danmaku/": 弹幕响应}, 记录, 延迟=0.2),
                    缓存目录=Path(临时目录().name) / "缓存", 最小请求间隔秒=0.0)
        结果们: list = []

        def 跑():
            try:
                结果们.append(len(源.取弹幕("2001")))
            except Exception as 错:  # noqa: BLE001
                结果们.append(f"错：{错}")

        线程们 = [threading.Thread(target=跑) for _ in range(6)]
        for t in 线程们:
            t.start()
        for t in 线程们:
            t.join()
        self.assertEqual(len(记录), 1, f"6 个并发只该发 1 次请求，实际 {len(记录)}")
        self.assertTrue(all(x == 4 for x in 结果们), f"每个线程都要拿到结果：{结果们}")

    def test_磁盘缓存能跨实例(self):
        目录 = Path(临时目录().name) / "缓存"
        甲 = Animeko源(传输=假传输({"/v1/danmaku/": 弹幕响应}),
                    缓存目录=目录, 最小请求间隔秒=0.0)
        甲.取弹幕("2001")
        记录: list = []
        乙 = Animeko源(传输=假传输({"/v1/danmaku/": 弹幕响应}, 记录),
                    缓存目录=目录, 最小请求间隔秒=0.0)
        self.assertEqual(len(乙.取弹幕("2001")), 4)
        self.assertEqual(len(记录), 0, "换实例也该命中磁盘缓存")

    def test_坏缓存不影响(self):
        目录 = Path(临时目录().name) / "缓存"
        目录.mkdir(parents=True, exist_ok=True)
        源 = Animeko源(传输=假传输({"/v1/danmaku/": 弹幕响应}),
                    缓存目录=目录, 最小请求间隔秒=0.0)
        键 = None
        源.取弹幕("2001")
        for 文件 in 目录.glob("*.json"):
            文件.write_text("{坏 JSON", encoding="utf-8")
            键 = 文件
        self.assertIsNotNone(键)
        self.assertEqual(len(源.取弹幕("2001")), 4, "缓存坏了要能重新请求")

    def test_清理缓存(self):
        目录 = Path(临时目录().name) / "缓存"
        目录.mkdir(parents=True, exist_ok=True)
        旧 = 目录 / "old.json"
        旧.write_text("{}", encoding="utf-8")
        import os
        os.utime(旧, (time.time() - 40 * 86400,) * 2)
        新 = 目录 / "new.json"
        新.write_text("{}", encoding="utf-8")
        self.assertEqual(清理缓存(目录, 保留天数=30), 1)
        self.assertFalse(旧.is_file())
        self.assertTrue(新.is_file())


class 诊断与开关测试(unittest.TestCase):
    def test_诊断说不需要凭据(self):
        诊断 = 造源().诊断()
        self.assertFalse(诊断["需要凭据"])
        self.assertEqual(诊断["标识"], "animeko")

    def test_关闭后不再请求(self):
        源 = 造源()
        源.关闭()
        with self.assertRaises(弹幕源错误):
            源.取弹幕("2001")

    def test_能匹配且进默认源链(self):
        from wangpan.danmaku.源 import 建默认源
        self.assertTrue(造源().能匹配())
        源们 = 建默认源()
        名字们 = [s.名字 for s in 源们]
        self.assertIn("Animeko 公益弹幕", 名字们)
        self.assertLess(名字们.index("Animeko 公益弹幕"), 名字们.index("弹弹play"),
                        "不需要凭据的源要排在前面（没配 AppId 的用户也能用）")


if __name__ == "__main__":
    unittest.main()
