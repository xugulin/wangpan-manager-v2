"""⑦ 学习层（``wangpan/identify/学习.py``）的单测：**记录 → 查询 → 打分联动**，不联网。

为什么这层必须有测试
====================
学习库是**唯一会随使用变强的信号**（命中 +45 分、而且**命中即免 margin 直接自动入库**，
见 ``决策.门槛.学习库免间距``）。它同时也是最危险的一处：库一旦脏了
（把 ``媒体`` / ``来自：分享`` 这种容器目录记成作品），错误会在**用户看不见的地方**
自动入库。所以这里钉的不是"功能能跑"，而是四类硬约束：

1. **记什么**：目录 → id、别名 → id 都记；分类目录 / 季目录 / 标签 / 没有作品名证据时
   一律不记（宁可不学，也不许污染库）；
2. **存取安全**：原子写（写坏了也不毁库，不留半截文件、不留临时文件残渣）、
   坏文件当空库、老形状（整份就是别名表）当别名、版本号能补能留；
3. **与上游一致**：本模块写出去的文件，⑤ 打分与 ③ 候选**都认**（形状兼容），
   "本模块说命中 ⟺ 打分说命中"（判定一致，避免"评测说有、线上没有"）；
4. **联动与反证**：两个咬得很紧的候选，一个有学习命中 → 必须变成自动入库；
   但**年份/类型反证仍然压得住**（库可能是脏的，反证比记忆硬）。

全部用临时目录 + 构造数据，**一个请求都不发**。
"""

from __future__ import annotations

import json
import os
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from unittest import mock

from tests.公用 import 临时目录

from wangpan.identify import 学习
from wangpan.identify.学习 import (记确认, 查询, 命中表, 读库, 读原始, 清空, 统计,
                             默认学习库路径, 结构版本, 空库字典)
from wangpan.identify.打分 import 学习库, 学习命中, 打分, 权重表
from wangpan.identify.决策 import 决策, 档_自动, 档_待确认
from wangpan.identify.检索 import 检索候选
from wangpan.identify.候选 import 生成查询, 别名命中, 来源_别名


# ============================ 构造数据的小工具 ============================


@dataclass
class 假结构:
    """P1 结构结果的替身（只带学习层与打分要用的字段）。"""

    标题: str = ""
    年份: Optional[int] = None
    类型: str = "unknown"
    季: Optional[int] = None
    集: Optional[int] = None
    集到: Optional[int] = None
    文件标题: str = ""
    目录标题: str = ""
    标题候选们: list = field(default_factory=list)
    目录链: tuple = ()


def 造候选(标识: str, 标题: str, *, 类型: str = "tv", 年份=None, 原名: str = "",
         热度: float = 10.0, 票数: int = 100, 来源=None) -> 检索候选:
    return 检索候选(标识=标识, 类型=类型, 标题=标题, 原名=原名 or 标题, 年份=年份,
                热度=热度, 票数=票数,
                来源=dict({标题: 80.0} if 来源 is None else 来源))


class 学习基类(unittest.TestCase):
    """每个用例一个临时库文件（**绝不碰 数据/识别学习.json**）。"""

    def setUp(self) -> None:
        self._文件夹 = 临时目录()
        self.addCleanup(self._文件夹.cleanup)
        self.根 = Path(self._文件夹.name)
        self.库 = self.根 / "识别学习.json"

    def 记(self, 样本: str, 标识: str = "223911", 类型: str = "tv", **关键字) -> dict:
        return 记确认(样本=样本, 选中=标识, 类型=类型, 库路径=self.库, **关键字)


# ============================ 一、库文件形状与容错 ============================


class 库文件测试(学习基类):

    def test_默认路径与打分是同一个_但测试进程里被引到临时目录(self):
        """生产路径上两个模块必须指向**同一个文件**；测试进程里必须指向临时目录。"""
        from wangpan.identify.打分 import 默认学习库路径 as 打分默认
        self.assertEqual(默认学习库路径().name, "识别学习.json")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("V2_识别学习库", None)
            self.assertEqual(默认学习库路径(), 打分默认(),
                             "没有覆盖变量时，学习与打分必须是同一个文件")
        self.assertNotEqual(默认学习库路径(), 打分默认(),
                            "测试进程必须被 tests/公用.py 引到临时目录（否则会写用户的数据）")
        self.assertIn("v2测试", str(默认学习库路径()))

    def test_空库字典带版本与三个节点(self):
        数据 = 空库字典()
        self.assertEqual(数据["版本"], 结构版本)
        for 节点 in ("别名", "目录", "作品"):
            self.assertEqual(数据[节点], {})
        # 空库必须被 ⑤ 打分认成"空的"（中性），而不是"有内容但没命中"
        self.assertTrue(学习库.从字典(数据).空的())

    def test_文件不存在当空库(self):
        self.assertEqual(读原始(self.库)["别名"], {})
        self.assertTrue(读库(self.库).空的())
        self.assertEqual(统计(self.库)["别名数"], 0)

    def test_坏文件当空库(self):
        """半截 JSON / 顶层是数组 / 二进制 / 目录：**一律当空库**，绝不抛。"""
        for 内容 in ("{半截", "", "[]", "123", "null", "\x00\x01\x02"):
            with self.subTest(内容=内容):
                self.库.write_text(内容, encoding="utf-8", errors="ignore")
                原始 = 读原始(self.库)
                self.assertEqual(原始["别名"], {})
                self.assertTrue(读库(self.库).空的())
                self.assertEqual(原始["版本"], 结构版本)
        self.库.unlink()
        self.库.mkdir()
        self.assertTrue(读库(self.库).空的(), "路径是目录时也要当空库，不许抛")

    def test_老形状整份就是别名表(self):
        """P2 的探测脚本吃过这种形状（整个文件就是一张别名表），要认。"""
        self.库.write_text(json.dumps({"仙逆": "Renegade Immortal"},
                                    ensure_ascii=False), encoding="utf-8")
        原始 = 读原始(self.库)
        self.assertEqual(原始["别名"], {"仙逆": "Renegade Immortal"})
        self.assertEqual(原始["目录"], {})
        self.assertEqual(原始["版本"], 结构版本, "老库缺版本号要补上当前版本")

    def test_版本号写入与保留(self):
        self.记("/媒体/剧集/某剧/Season 01/05.mkv", "209867")
        原文 = json.loads(self.库.read_text(encoding="utf-8"))
        self.assertEqual(原文["版本"], 结构版本)
        self.assertTrue(原文["更新时间"], "要写更新时间（排查'这条是哪次确认的'）")
        # 未来版本号要**原样保留**（不许被当前版本号覆盖，否则迁移无从判断）
        原文["版本"] = 结构版本 + 7
        self.库.write_text(json.dumps(原文, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(读原始(self.库)["版本"], 结构版本 + 7)
        记确认(样本="/媒体/剧集/某剧/Season 01/05.mkv", 选中="209867",
             库路径=self.库)
        self.assertEqual(读原始(self.库)["版本"], 结构版本 + 7)

    def test_清空后仍是合法空库(self):
        self.记("/媒体/剧集/某剧/Season 01/05.mkv", "209867")
        self.assertGreater(统计(self.库)["别名数"], 0)
        清空(self.库)
        self.assertTrue(读库(self.库).空的())
        self.assertEqual(读原始(self.库)["版本"], 结构版本, "清空要保留版本号")
        self.assertTrue(查询(假结构(标题="某剧", 类型="tv"), [造候选("209867", "某剧")],
                          库路径=self.库) == [])

    def test_写库失败不毁库_不可序列化(self):
        self.记("/媒体/剧集/某剧/Season 01/05.mkv", "209867")
        之前 = self.库.read_text(encoding="utf-8")
        with self.assertRaises(TypeError):
            学习.写库({"别名": {"某剧": {1, 2, 3}}}, self.库)      # set 不能序列化
        self.assertEqual(self.库.read_text(encoding="utf-8"), 之前,
                         "序列化失败必须连一个字节都不动原库")
        self.assertEqual(list(self.根.glob("*.tmp")), [], "不许留临时文件残渣")

    def test_写库失败不毁库_替换那一步炸(self):
        self.记("/媒体/剧集/某剧/Season 01/05.mkv", "209867")
        之前 = self.库.read_text(encoding="utf-8")
        with mock.patch.object(学习.os, "replace", side_effect=OSError("磁盘满了")):
            with self.assertRaises(OSError):
                学习.写库({"别名": {"别的": {"tmdb_id": "1"}}}, self.库)
        self.assertEqual(self.库.read_text(encoding="utf-8"), 之前,
                         "替换失败时旧库必须完好（用户几十次确认不能白点）")
        self.assertEqual(list(self.根.glob("*.tmp")), [], "临时文件要清掉")
        self.assertTrue(读库(self.库).别名, "库还能正常读")


# ============================ 二、记什么：目录 / 别名 ============================


class 记确认测试(学习基类):

    def test_目录键与别名键都落(self):
        """真机形态：目录是干净的作品名，文件名只剩集号。"""
        结果 = self.记("/来自：分享/仙逆.4K高码.SDR.60fps")
        self.assertIn("仙逆.4K高码.SDR.60fps", 结果["目录"])
        self.assertIn("仙逆", 结果["别名"])
        self.assertIn("tv:223911", 结果["作品"])
        数据 = 读原始(self.库)["目录"]["仙逆.4K高码.SDR.60fps"]
        self.assertEqual(数据["tmdb_id"], "223911")
        self.assertEqual(数据["类型"], "tv")
        self.assertTrue(数据["标题"], "值里的标题一定要非空（否则会被当查询词乱搜）")

    def test_同一部剧的两种写法都记(self):
        """``Renegade.Immortal.S01E138…`` 在 ``仙逆.4K高码.SDR.60fps`` 里：
        文件名英文、目录名中文，两个名字都要能当"看过的名字"。"""
        结果 = self.记("/来自：分享/仙逆.4K高码.SDR.60fps/"
                    "Renegade.Immortal.S01E138.2023.2160p.WEB-DL.H265-PanWEB.mkv")
        self.assertEqual(结果["别名"], ["Renegade Immortal", "仙逆"])
        self.assertEqual(结果["目录"], ["仙逆.4K高码.SDR.60fps"])

    def test_分类目录与分享目录绝不入库(self):
        """``媒体``/``剧集``/``来自：分享``/``Season 01`` 一旦入库就会误命中别的作品。"""
        记确认(样本="/来自：分享/仙逆.4K高码.SDR.60fps", 选中="223911",
             类型="tv", 库路径=self.库)
        记确认(样本="/媒体/剧集/葬送的芙莉莲/Season 01/05.mkv", 选中="209867",
             类型="tv", 库路径=self.库)
        记确认(样本="/媒体/某电影.2020.mkv", 选中="999", 类型="movie", 库路径=self.库)
        原始 = 读原始(self.库)
        禁词 = {"媒体", "剧集", "来自 分享", "来自：分享", "Season 01", "电影"}
        self.assertFalse(禁词 & set(原始["目录"]), f"目录键里混进了容器名：{原始['目录']}")
        self.assertFalse(禁词 & set(原始["别名"]), f"别名键里混进了容器名：{原始['别名']}")
        self.assertIn("某电影", 原始["别名"], "真正的作品名还是要记下来")
        self.assertEqual(原始["目录"], {"仙逆.4K高码.SDR.60fps": mock.ANY,
                                  "葬送的芙莉莲": mock.ANY})

    def test_季目录不当目录键(self):
        self.记("/媒体/剧集/葬送的芙莉莲/Season 02/07.mkv", "209867")
        原始 = 读原始(self.库)
        self.assertEqual(list(原始["目录"]), ["葬送的芙莉莲"])
        self.assertNotIn("Season 02", 原始["目录"])

    def test_没有作品名证据时只写作品档案(self):
        """``第03集.mp4`` 孤零零一个文件：没有名字可记，**宁可不记**。"""
        结果 = self.记("第03集.mp4", "209867")
        self.assertEqual(结果["目录"], [])
        self.assertEqual(结果["别名"], [])
        self.assertTrue(结果["跳过"], "要说明为什么没记")
        原始 = 读原始(self.库)
        self.assertEqual(原始["别名"], {})
        self.assertIn("tv:209867", 原始["作品"], "作品档案（纯 id）无风险，照写")

    def test_结构给了就不用再抽一次(self):
        """调用方已经算过结构（真机在刮削线程里）时不许重复解析。"""
        结构 = 假结构(标题="仙逆", 类型="tv", 季=1, 集=146, 文件标题="仙逆",
                   目录标题="仙逆", 目录链=("仙逆.4K高码.SDR.60fps",))
        结果 = 记确认(样本="/来自：分享/仙逆.4K高码.SDR.60fps/146.mp4",
                    结构=结构, 选中="223911", 库路径=self.库)
        self.assertEqual(结果["目录"], ["仙逆.4K高码.SDR.60fps"])
        self.assertEqual(结果["别名"], ["仙逆"])

    def test_重复确认累加次数_改指留痕(self):
        self.记("/媒体/剧集/某剧/Season 01/01.mkv", "100")
        self.记("/媒体/剧集/某剧/Season 01/02.mkv", "100")
        self.assertEqual(读原始(self.库)["别名"]["某剧"]["次数"], 2)
        self.记("/媒体/剧集/某剧/Season 01/03.mkv", "200")
        值 = 读原始(self.库)["别名"]["某剧"]
        self.assertEqual(值["tmdb_id"], "200")
        self.assertEqual(值["次数"], 1, "改指之后次数重新算")
        self.assertEqual(值["旧id们"], ["100"], "改指要留审计痕迹（学习库最怕脏）")

    def test_缺id要报错(self):
        with self.assertRaises(ValueError):
            记确认(样本="/媒体/剧集/某剧/01.mkv", 选中="", 库路径=self.库)
        with self.assertRaises(ValueError):
            记确认(选中="100", 库路径=self.库)

    def test_选中可以是匹配候选或字典(self):
        class 假匹配候选:
            来源标识 = "223911"
            标题 = "仙逆"
            原名 = "Renegade Immortal"
            年份 = 2023
            类型 = "tv"

        记确认(样本="/来自：分享/仙逆.4K高码.SDR.60fps/146.mp4",
             选中=假匹配候选(), 库路径=self.库)
        值 = 读原始(self.库)["别名"]["仙逆"]
        self.assertEqual(值["tmdb_id"], "223911")
        self.assertEqual(值["年份"], 2023)
        self.assertEqual(值["标题"], "仙逆")
        记确认(样本="/来自：分享/仙逆.4K高码.SDR.60fps/147.mp4",
             选中={"tmdb_id": "223911", "类型": "tv"}, 库路径=self.库)
        self.assertEqual(读原始(self.库)["别名"]["仙逆"]["次数"], 2)


# ============================ 三、查询：目录命中 / 别名命中 ============================


class 查询测试(学习基类):

    def test_目录命中_同目录第二集零成本(self):
        self.记("/来自：分享/仙逆.4K高码.SDR.60fps/146.SDR.8bit.2160p.mp4")
        结构 = 假结构(标题="仙逆", 类型="tv", 季=1, 集=147, 文件标题="",
                   目录标题="仙逆", 目录链=("仙逆.4K高码.SDR.60fps",))
        候选 = 造候选("223911", "仙逆", 年份=2023)
        命中 = 查询(结构, [候选], 库路径=self.库)
        self.assertEqual(len(命中), 1)
        self.assertEqual(命中[0].来源, "目录")
        self.assertEqual(命中[0].键, "仙逆.4K高码.SDR.60fps")
        self.assertEqual(命中[0].加权, 权重表["学习库"], "强命中 = 满权重（45 分）")
        self.assertEqual(命中表(结构, [候选], 库路径=self.库), {"223911": 45.0})

    def test_别名命中_同名作品第二次零成本(self):
        self.记("/来自：分享/仙逆.4K高码.SDR.60fps/"
              "Renegade.Immortal.S01E138.2023.2160p.WEB-DL.H265-PanWEB.mkv")
        for 标题, 目录链 in (("Renegade Immortal", ()),
                          ("仙逆", ("别的目录",))):
            结构 = 假结构(标题=标题, 类型="tv", 季=1, 集=138, 目录链=目录链)
            命中 = 查询(结构, [造候选("223911", "仙逆", 原名="Renegade Immortal")],
                      库路径=self.库)
            self.assertEqual(len(命中), 1, f"「{标题}」应该靠别名命中")
            self.assertEqual(命中[0].来源, "别名")
            self.assertEqual(命中[0].加权, 45.0)

    def test_别的作品不会蹭到命中(self):
        self.记("/来自：分享/仙逆.4K高码.SDR.60fps/146.mp4")
        结构 = 假结构(标题="仙逆", 类型="tv", 目录链=("仙逆.4K高码.SDR.60fps",))
        别的 = 造候选("292307", "仙逆合集篇", 年份=2023)
        self.assertEqual(查询(结构, [别的], 库路径=self.库), [],
                        "值不指向这条候选就不算命中（id 对不上、标题也对不上）")

    def test_空库与坏库查询都是空(self):
        self.assertEqual(查询(假结构(标题="仙逆", 类型="tv"),
                          [造候选("223911", "仙逆")], 库路径=self.库), [])
        self.库.write_text("{坏", encoding="utf-8")
        self.assertEqual(查询(假结构(标题="仙逆", 类型="tv"),
                          [造候选("223911", "仙逆")], 库路径=self.库), [])

    def test_查询与打分的命中判定完全一致(self):
        """**这是本模块最重要的回归线**：两边判定不一致 = "评测说有、线上没有"。"""
        self.记("/来自：分享/仙逆.4K高码.SDR.60fps/"
              "Renegade.Immortal.S01E138.2023.2160p.WEB-DL.H265-PanWEB.mkv")
        self.记("/媒体/剧集/葬送的芙莉莲/Season 01/05.mkv", "209867")
        库对象 = 读库(self.库)
        样本们 = [
            (假结构(标题="仙逆", 类型="tv", 季=1, 集=147,
                  目录链=("仙逆.4K高码.SDR.60fps",)), "223911", "仙逆"),
            (假结构(标题="Renegade Immortal", 类型="tv", 季=1, 集=139), "223911", "仙逆"),
            (假结构(标题="仙逆", 类型="movie"), "292307", "仙逆合集篇"),
            (假结构(标题="葬送的芙莉莲", 类型="tv", 季=1, 集=6,
                  目录链=("Season 01", "葬送的芙莉莲")), "209867", "葬送的芙莉莲"),
            (假结构(标题="无职转生", 类型="tv"), "94664", "无职转生～到了异世界就拿出真本事～"),
            (假结构(标题="", 类型="unknown"), "209867", "葬送的芙莉莲"),
        ]
        for 结构, 标识, 标题 in 样本们:
            候选 = 造候选(标识, 标题)
            系数, 说明, 中性 = 学习命中(结构, 候选, 库对象)
            命中 = 查询(结构, [候选], 库=库对象)
            with self.subTest(标题=标题):
                self.assertEqual(bool(命中), (not 中性 and 系数 > 0),
                                 f"查询与打分判定不一致：{说明}")
                if 命中:
                    self.assertEqual(命中[0].加权, 系数 * 权重表["学习库"])


# ============================ 四、与上游形状兼容 ============================


class 形状兼容测试(学习基类):

    def test_打分读学习库认得本模块写的文件(self):
        self.记("/来自：分享/仙逆.4K高码.SDR.60fps/146.mp4")
        from wangpan.identify.打分 import 读学习库
        库对象 = 读学习库(self.库)
        self.assertIn("仙逆", 库对象.别名)
        self.assertIn("仙逆.4K高码.SDR.60fps", 库对象.目录)
        self.assertEqual(库对象.作品["tv:223911"]["tmdb_id"], "223911")

    def test_候选别名命中认得本模块写的值(self):
        self.记("/来自：分享/仙逆.4K高码.SDR.60fps/146.mp4")
        别名表 = 读原始(self.库)["别名"]
        命中 = 别名命中(["仙逆"], 别名表)
        self.assertIn(("仙逆", "仙逆"), 命中)

    def test_生成查询把学习到的别名当最高权重查询词(self):
        self.记("/来自：分享/仙逆.4K高码.SDR.60fps/"
              "Renegade.Immortal.S01E138.2023.2160p.WEB-DL-PanWEB.mkv")
        别名表 = 读原始(self.库)["别名"]
        for 标题 in ("Renegade Immortal", "仙逆"):
            结构 = 假结构(标题=标题, 类型="tv", 季=1, 集=139)
            查询集 = 生成查询(结构, 别名表=别名表)
            文本们 = [x.文本 for x in 查询集.查询词们]
            with self.subTest(标题=标题):
                self.assertIn(标题, 文本们, "记过的名字要出查询词")
                self.assertEqual(查询集.查询词们[0].来源, 来源_别名,
                                 "别名权重最高（100），要排在文件标题/目录标题前面")
                self.assertEqual(查询集.查询词们[0].权重, 100.0)

    def test_值里的标题一定非空且绝不把id当查询词(self):
        """值里没有可认的标题键时，``候选._拉起别名`` 会把 tmdb_id/"tv" 当查询词。"""
        self.记("/来自：分享/仙逆.4K高码.SDR.60fps/146.mp4")
        别名表 = 读原始(self.库)["别名"]
        词们 = [文本 for 文本, _ in 别名命中(["仙逆"], 别名表)]
        self.assertNotIn("223911", 词们)
        self.assertNotIn("tv", 词们)
        for 节点 in ("别名", "目录"):
            for 键, 值 in 读原始(self.库)[节点].items():
                self.assertTrue(str(值.get("标题") or "").strip(), f"{节点}「{键}」的标题空了")


# ============================ 五、联动：命中抬高分数，但反证仍压得住 ============================


class 打分联动测试(学习基类):

    def _两个咬得很紧的候选(self, *, 学习候选年份=2024, 学习候选类型="tv"):
        """标题像、分数咬得很紧的两条候选（真机《狂飙》《三体》那类 margin < 25 的长相）。"""
        甲 = 造候选("1", "某剧", 类型=学习候选类型, 年份=学习候选年份,
                  原名="某剧", 热度=30, 票数=500, 来源={"某剧": 80.0})
        乙 = 造候选("2", "某剧 外传", 类型="tv", 年份=2024,
                  原名="某剧 外传", 热度=30, 票数=500, 来源={"某剧": 80.0})
        return 甲, 乙

    def test_空库时是待确认(self):
        结构 = 假结构(标题="某剧", 类型="tv", 季=1, 集=5)
        甲, 乙 = self._两个咬得很紧的候选()
        判定 = 决策(结构, 打分(结构, [甲, 乙]))
        self.assertEqual(判定.档位, 档_待确认, f"前提：空库时它拿不准（{判定.可读()}）")

    def test_学习命中把它送进自动档(self):
        结构 = 假结构(标题="某剧", 类型="tv", 季=1, 集=5, 目录链=("某剧.4K高码",))
        self.记("/媒体/剧集/某剧.4K高码/05.mkv", "1", 类型="tv")
        甲, 乙 = self._两个咬得很紧的候选()
        库对象 = 读库(self.库)
        结果 = 打分(结构, [甲, 乙], 学习库=库对象)
        判定 = 决策(结构, 结果)
        self.assertEqual(判定.档位, 档_自动, f"学习命中该直接自动入库：{判定.可读()}")
        self.assertGreater(判定.分数, 95.0)
        self.assertIn("学习库命中", 判定.原因)
        # 第二名的分数没变，分数差就是"学习库"那一项带来的
        self.assertAlmostEqual(判定.分数 - 结果.亚军().总分, 结果.间距(), places=6)

    def test_学习命中仍压不住年份反证(self):
        """库可能是脏的（同一目录换过内容 / 当年点错），年份差 ≥3 是反证，必须交人工。"""
        结构 = 假结构(标题="某剧", 类型="tv", 季=1, 集=5, 年份=2024,
                   目录链=("某剧.4K高码",))
        self.记("/媒体/剧集/某剧.4K高码/05.mkv", "1", 类型="tv")
        甲, 乙 = self._两个咬得很紧的候选(学习候选年份=2019)
        结果 = 打分(结构, [甲, 乙], 学习库=读库(self.库))
        self.assertGreater(结果.首选().取("学习库").系数, 0, "前提：学习库确实命中了")
        self.assertLess(结果.首选().取("年份").系数, 0, "前提：年份确实是反证")
        判定 = 决策(结构, 结果)
        self.assertEqual(判定.档位, 档_待确认)
        self.assertIn("年份是反证", 判定.原因)

    def test_学习命中仍压不住类型反证(self):
        结构 = 假结构(标题="某剧", 类型="tv", 季=1, 集=5, 目录链=("某剧.4K高码",))
        self.记("/媒体/剧集/某剧.4K高码/05.mkv", "1", 类型="tv")
        甲, 乙 = self._两个咬得很紧的候选(学习候选类型="movie")
        结果 = 打分(结构, [甲, 乙], 学习库=读库(self.库))
        self.assertGreater(结果.首选().取("学习库").系数, 0, "前提：学习库确实命中了")
        判定 = 决策(结构, 结果)
        self.assertEqual(判定.档位, 档_待确认)
        self.assertIn("反证", 判定.原因)

    def test_学习命中之后覆盖率提升可复现(self):
        """同一批样本：空库 0 条自动，写库之后 2 条自动（"越用越准"的最小复现）。"""
        样本们 = [
            (假结构(标题="某剧", 类型="tv", 季=1, 集=5, 目录链=("某剧.4K高码",)),
             "/媒体/剧集/某剧.4K高码/05.mkv", 造候选("1", "某剧", 类型="tv", 年份=2024,
                                             来源={"某剧": 80.0})),
            (假结构(标题="另剧", 类型="tv", 季=1, 集=3, 目录链=("另剧",)),
             "/媒体/剧集/另剧/03.mkv", 造候选("2", "另剧", 类型="tv", 年份=2023,
                                           来源={"另剧": 80.0})),
        ]
        空库自动 = 0
        for 结构, _, 候选 in 样本们:
            甲 = 候选
            乙 = 造候选("99", 候选.标题 + " 外传", 类型="tv", 年份=候选.年份,
                      来源={候选.标题: 80.0})
            if 决策(结构, 打分(结构, [甲, 乙])).档位 == 档_自动:
                空库自动 += 1
        for 结构, 路径, 候选 in 样本们:
            self.记(路径, 候选.标识, 类型="tv")
        有库自动 = 0
        for 结构, _, 候选 in 样本们:
            甲 = 候选
            乙 = 造候选("99", 候选.标题 + " 外传", 类型="tv", 年份=候选.年份,
                      来源={候选.标题: 80.0})
            if 决策(结构, 打分(结构, [甲, 乙], 学习库=读库(self.库))).档位 == 档_自动:
                有库自动 += 1
        self.assertEqual(空库自动, 0, "前提：空库时这两条都拿不准")
        self.assertEqual(有库自动, 2, "写库之后两条都要自动入库")


# ============================ 六、服务层闭环（真确认动作 → 学习样本） ============================


class 假TMDB:
    """够用就行的假客户端（不会有人点确认，所以只提供"取详情"这一路）。"""

    def __init__(self) -> None:
        self.取的详情: list[str] = []

    def 可用(self) -> bool:
        return True

    def 配置信息(self) -> dict:
        return {"images": {"secure_base_url": "https://image.tmdb.org/t/p/"}}

    def 图片地址(self, 远端路径: str, 尺寸: str = "w500") -> str:
        return f"https://image.tmdb.org/t/p/{尺寸}{远端路径}"

    def 搜电影(self, 标题, 年份=None):
        from wangpan.scrape.模型 import 匹配候选, 媒体类型
        return [匹配候选(来源标识="101", 标题=标题, 原名="Alpha", 年份=年份,
                      类型=媒体类型.电影, 热度=120),
                匹配候选(来源标识="202", 标题=标题 + " 2", 原名="Beta", 年份=年份,
                      类型=媒体类型.电影, 热度=118),
                匹配候选(来源标识="303", 标题=标题, 原名="Gamma", 年份=年份,
                      类型=媒体类型.电影, 热度=117)]

    def 搜剧集(self, 标题, 年份=None):
        from wangpan.scrape.模型 import 媒体类型
        return [匹配候选(来源标识=c.来源标识, 标题=c.标题, 原名=c.原名, 年份=c.年份,
                      类型=媒体类型.剧集, 热度=c.热度) for c in self.搜电影(标题, 年份)]

    def 取电影(self, id):
        from wangpan.scrape.模型 import 媒体类型, 媒体条目
        self.取的详情.append(str(id))
        条目 = 媒体条目(类型=媒体类型.电影, 标题=f"取到的{id}", 年份=2024)
        条目.外部ID = {"tmdb": str(id)}
        return 条目

    def 取剧集(self, id):
        from wangpan.scrape.模型 import 媒体类型, 季, 集
        条目 = self.取电影(id)
        条目.类型 = 媒体类型.剧集
        条目.季们 = [季(季号=1, 集数=1, 集们=[集(季号=1, 集号=1)])]
        return 条目

    def 取分级(self, *a):
        return []

    def 取图片(self, *a, **k):
        return []

    def 下载图片(self, *a, **k):
        return None

    def 关闭(self) -> None:
        return


class 服务闭环测试(unittest.TestCase):
    """``服务.py`` 那几行接线：**人工确认成功之后**才写学习样本，写失败不影响入库。"""

    def setUp(self) -> None:
        self._文件夹 = 临时目录()
        self.addCleanup(self._文件夹.cleanup)
        self.根 = Path(self._文件夹.name)
        self.学习库 = self.根 / "识别学习.json"
        补 = mock.patch.object(学习, "默认学习库路径", lambda: self.学习库)
        补.start()
        self.addCleanup(补.stop)

        from wangpan.scrape.库 import 资料库
        from wangpan.scrape.服务 import 刮削服务, 刮削设置
        self.媒体目录 = self.根 / "媒体" / "某剧.4K高码.SDR.60fps"
        self.媒体目录.mkdir(parents=True, exist_ok=True)
        self.文件 = self.媒体目录 / "146.SDR.8bit.2160p.60fps.DDP5.1.WEB-DL.H265.mp4"
        self.文件.write_bytes(b"x" * (2 * 1024 * 1024))
        self.资料库 = 资料库(self.根 / "库.db")
        self.addCleanup(self.资料库.关闭)
        self.日志: list[str] = []
        self.服务 = 刮削服务(self.资料库, 假TMDB(), None,
                       刮削设置(最小文件字节=1024, 下载图片=False),
                       日志回调=self.日志.append)
        self.addCleanup(self.服务.关闭)

    def test_采纳之后写入学习样本(self):
        from wangpan.scrape.模型 import 匹配候选, 媒体类型
        结果 = self.服务.采纳候选(self.文件, 匹配候选(来源标识="223911", 标题="某剧",
                                            类型=媒体类型.剧集, 年份=2024))
        self.assertEqual(结果.失败, 0, f"采纳不该失败：{结果.错误们}")
        原始 = 读原始(self.学习库)
        self.assertEqual(原始["目录"]["某剧.4K高码.SDR.60fps"]["tmdb_id"], "223911")
        self.assertEqual(原始["别名"]["某剧"]["tmdb_id"], "223911")
        self.assertTrue(any("识别学习" in x for x in self.日志),
                        f"要留一行日志：{self.日志}")

    def test_没有人工确认时不写学习样本(self):
        """自动匹配 / 续跑（没有强制 id）不是"人工确认"，不该污染学习库。"""
        self.服务.刮路径(self.文件)
        self.assertFalse(self.学习库.exists(),
                         "自动流程不该写学习库（只有人工点过的才算数）")

    def test_写学习库失败不影响采纳(self):
        from wangpan.scrape.模型 import 匹配候选, 媒体类型
        with mock.patch.object(学习, "记确认", side_effect=RuntimeError("学习库炸了")):
            结果 = self.服务.采纳候选(self.文件, 匹配候选(来源标识="223911", 标题="某剧",
                                               类型=媒体类型.剧集, 年份=2024))
        self.assertEqual(结果.失败, 0, f"学习库写不动绝不能影响入库：{结果.错误们}")
        self.assertEqual(结果.成功, 1)
        self.assertTrue(any("记录失败" in x or "不影响入库" in x for x in self.日志),
                        f"软失败要留日志：{self.日志}")


if __name__ == "__main__":
    unittest.main()
