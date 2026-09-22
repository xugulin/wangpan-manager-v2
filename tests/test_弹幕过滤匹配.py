"""匹配与过滤测试：相似度打分、自动决策、过滤规则与导入导出。

测什么、为什么这么测
====================
* 相似度**不写脆断言**：真实名字千奇百怪，钉死"必须 87.3 分"只会让以后调权重时
  到处改测试。这里钉的是**区间与相对关系**（同年份差别要扣分但仍合理、不同季必须
  明显低于同名、无关的要很低），并把参考值写在断言的提示里；
* 决策钉**边界语义**：≥ 阈值且领先够多才自动采纳；唯一候选算"没有竞争"；
  前两名挨得近就必须 `需要人工确认`；
* 过滤钉**原因与统计对得上**：界面上要显示"为什么这条没显示"，原因字段是给人看的，
  不能和实际丢弃的原因不一致；
* 全程离线：这里的"源"是 :class:`假源`，一个字节都不发。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.公用 import 临时目录

from wangpan.danmaku.模型 import 弹幕, 弹幕池, 弹幕模式, 来源标识
from wangpan.danmaku.过滤 import 默认规则, 过滤器, 过滤规则, 统计键们
from wangpan.danmaku.匹配 import (从标题取季号, 取采纳的弹幕, 文件名相似度, 找弹幕,
                             数字们, 规范化名字, 打分, 排序候选, 自动决策,
                             最长公共子序列长度, 最长公共子串长度)
from wangpan.danmaku.源.接口 import 弹幕源, 弹幕源错误, 匹配结果, 素材信息


# ---------------------------------------------------------------------------
# 假的源（不联网）
# ---------------------------------------------------------------------------

class 假源(弹幕源):
    """只回答预置候选的源；``抛错`` 用来测"一个源挂了不影响别人"。"""

    def __init__(self, 标识: str = "假源", 候选们=(), 名字: str = "假源",
                 抛错: Exception | None = None, 弹幕们=()) -> None:
        self.标识 = 标识
        self.名字 = 名字
        self.候选们 = list(候选们)
        self.抛错 = 抛错
        self.弹幕们 = list(弹幕们)
        self.取过: list[str] = []

    def 能匹配(self) -> bool:
        return True

    def 匹配(self, 素材: 素材信息) -> list[匹配结果]:
        if self.抛错 is not None:
            raise self.抛错
        return list(self.候选们)

    def 取弹幕(self, 标识: str) -> 弹幕池:
        self.取过.append(标识)
        if not self.弹幕们:
            raise 弹幕源错误(f"{self.名字} 没有弹幕")
        return 弹幕池(list(self.弹幕们))


def 素材(文件名="影片.mkv", **改动) -> 素材信息:
    参数 = dict(文件名=文件名)
    参数.update(改动)
    return 素材信息(**参数)


# ---------------------------------------------------------------------------
# 相似度
# ---------------------------------------------------------------------------

class 相似度测试(unittest.TestCase):
    def test_完全相同是100(self):
        self.assertEqual(文件名相似度("影片", "影片"), 100.0)
        self.assertEqual(文件名相似度("影片.mkv", "影片"), 100.0, "去扩展名后应该一样")
        self.assertEqual(文件名相似度("某番 第二季", "某番 第2季"), 100.0,
                         "中文数字与阿拉伯数字要归一化")

    def test_大小写与标点空格不影响(self):
        值 = 文件名相似度("Attack on Titan", "attack on  titan!")
        self.assertGreaterEqual(值, 95.0, f"只有大小写与标点差异，不该低：{值}")

    def test_发布标签重的文件名仍然给高分(self):
        """真实文件名带一堆标签；正确的候选被压到及格线以下就等于匹配不可用。"""
        for 甲, 乙 in (("[VCB-Studio] 进击的巨人 [01][1080p][CHS]", "进击的巨人"),
                      ("【幻樱字幕组】★4月新番【进击的巨人 第二季】【01】【GB_MP4】【1280X720】",
                       "进击的巨人 第二季"),
                      ("进击的巨人 第01话 [1080p]", "进击的巨人")):
            值 = 文件名相似度(甲, 乙)
            self.assertGreaterEqual(值, 60.0, f"《{甲}》 vs 《{乙}》 只给了 {值}")

    def test_年份差一年扣分但仍合理(self):
        值 = 文件名相似度("进击的巨人 2013", "进击的巨人 2014")
        self.assertLess(值, 100.0, "年份不同不能给满分")
        self.assertGreaterEqual(值, 60.0, f"只差一年不该判成无关：{值}")
        # 年份一样时应该明显更高
        self.assertGreater(文件名相似度("进击的巨人 2013", "进击的巨人 2013"), 值)

    def test_续集与不同季要能区分(self):
        同名 = 文件名相似度("某番 第二季", "某番 第二季")
        不同季 = 文件名相似度("某番 第一季", "某番 第二季")
        self.assertEqual(同名, 100.0)
        self.assertLess(不同季, 65.0, f"不同季的分数必须压下来，否则会带错集：{不同季}")
        self.assertGreater(不同季, 30.0, f"毕竟是同一部番，别压到无关档：{不同季}")
        # 第三季 / 第二季 之间也一样要区分
        self.assertLess(文件名相似度("某番 第二季", "某番 第三季"), 65.0)

    def test_无关的分数很低(self):
        值 = 文件名相似度("孤独的美食家", "进击的巨人")
        self.assertLess(值, 20.0, f"毫无关系的两个名字不该有分：{值}")

    def test_空的一边是0(self):
        self.assertEqual(文件名相似度("", "进击的巨人"), 0.0)
        self.assertEqual(文件名相似度("进击的巨人", ""), 0.0)
        self.assertEqual(文件名相似度(None, None), 0.0)

    def test_一方包含另一方有加成(self):
        值 = 文件名相似度("进击的巨人", "进击的巨人 最终季")
        self.assertGreaterEqual(值, 75.0, f"名字被完整包含，至少该给 75：{值}")
        self.assertLess(值, 100.0)

    def test_相似度是对称的(self):
        对们 = [("进击的巨人 2013", "进击的巨人 2014"), ("[VCB-Studio] 甲 [01]", "甲"),
              ("甲 第二季", "乙")]
        for 甲, 乙 in 对们:
            self.assertEqual(文件名相似度(甲, 乙), 文件名相似度(乙, 甲))

    def test_规范化与序号抽取(self):
        self.assertEqual(规范化名字("[VCB-Studio] 某番 第01话 [1080p][x265].mkv"),
                         "vcb studio 某番 第1话")
        self.assertEqual(规范化名字("某番.第2季.1080p.WEB-DL"), "某番 第2季")
        self.assertEqual(文件名相似度("某番 第01话", "某番 第1话"), 100.0,
                         "前导零不该影响结果")
        self.assertEqual(数字们("某番 第二季 2013"), {2, 2013})
        self.assertEqual(数字们("一人之下"), set(), "片名里的'一'不该被当成数字")
        self.assertEqual(从标题取季号("进击的巨人 第二季"), 2)
        self.assertEqual(从标题取季号("某番 Season 3"), 3)
        self.assertEqual(从标题取季号("某番 S02"), 2)
        self.assertIsNone(从标题取季号("进击的巨人"))
        self.assertEqual(最长公共子序列长度("abcd", "acbd"), 3)
        self.assertEqual(最长公共子串长度("xxabcyy", "zzabczz"), 3)


# ---------------------------------------------------------------------------
# 打分与排序
# ---------------------------------------------------------------------------

class 打分测试(unittest.TestCase):
    def setUp(self) -> None:
        self.素材 = 素材(文件名="[VCB-Studio] 进击的巨人 [01][1080p][CHS].mkv",
                       标题="进击的巨人", 集号=1, 季号=1, 时长秒=1440.0)

    def test_完全一致的候选接近满分(self):
        候 = 匹配结果("101", "进击的巨人", "第1话 绝望", "1", "tvseries")
        分 = 打分(候, self.素材)
        self.assertGreaterEqual(分, 95.0, f"名字与集号都对上了：{分}")

    def test_集号一致加分不一致扣分(self):
        对 = 匹配结果("101", "进击的巨人", "第1话", "1")
        错 = 匹配结果("102", "进击的巨人", "第2话", "2")
        无 = 匹配结果("103", "进击的巨人", "第1话", "")
        self.assertGreater(打分(对, self.素材), 打分(错, self.素材))
        self.assertGreaterEqual(打分(对, self.素材) - 打分(错, self.素材), 10.0,
                              "集号不对是很强的负信号，差距要看得出来")
        self.assertGreaterEqual(打分(对, self.素材), 打分(无, self.素材))

    def test_季号不一致扣分(self):
        第二季 = 匹配结果("201", "进击的巨人 第二季", "第1话", "1")
        素材2 = 素材(文件名="进击的巨人 第二季 第01话.mkv", 标题="进击的巨人 第二季",
                   集号=1, 季号=2)
        self.assertGreater(打分(第二季, 素材2), 90.0)
        素材1 = 素材(文件名="进击的巨人 第一季 第01话.mkv", 标题="进击的巨人 第一季",
                   集号=1, 季号=1)
        self.assertLess(打分(第二季, 素材1), 打分(第二季, 素材2))

    def test_无关候选分数很低(self):
        候 = 匹配结果("301", "孤独的美食家", "第1话", "1")
        self.assertLess(打分(候, self.素材), 35.0)

    def test_打分夹在0到100之间(self):
        for 标题 in ("", "完全不相干的节目名字", "进击的巨人"):
            分 = 打分(匹配结果("1", 标题, "", "9"), self.素材)
            self.assertGreaterEqual(分, 0.0)
            self.assertLessEqual(分, 100.0)

    def test_排序候选降序且填好分数理由(self):
        候选们 = [匹配结果("1", "孤独的美食家", "第1话", "1"),
                匹配结果("2", "进击的巨人", "第1话", "1"),
                匹配结果("3", "进击的巨人 第二季", "第1话", "1")]
        排好 = 排序候选(候选们, self.素材)
        self.assertEqual([候.标识 for 候 in 排好], ["2", "3", "1"])
        self.assertEqual([候.分数 for 候 in 排好], sorted([候.分数 for 候 in 排好],
                                                     reverse=True))
        self.assertTrue(all(候.理由 for 候 in 排好), "每个候选都要有理由")
        self.assertIn("名字相似", 排好[0].理由)
        # 传进去的对象不该被改（调用方可能还要用原样的候选）
        self.assertEqual([候.分数 for 候 in 候选们], [0.0, 0.0, 0.0])


# ---------------------------------------------------------------------------
# 自动决策
# ---------------------------------------------------------------------------

class 决策测试(unittest.TestCase):
    def setUp(self) -> None:
        self.素材 = 素材(文件名="进击的巨人 第01话.mkv", 标题="进击的巨人", 集号=1)

    def test_高分且差距大就自动采纳(self):
        候选们 = [匹配结果("1", "进击的巨人", "第1话", "1"),
                匹配结果("2", "孤独的美食家", "第1话", "1")]
        决策 = 自动决策(候选们, self.素材)
        self.assertFalse(决策.需要人工确认)
        self.assertIsNotNone(决策.采纳)
        self.assertEqual(决策.采纳.标识, "1")
        self.assertIn("自动采纳", 决策.说明)
        self.assertEqual(len(决策.候选们), 2)

    def test_两个候选接近就要人工确认(self):
        候选们 = [匹配结果("1", "进击的巨人", "第1话", "1"),
                匹配结果("2", "进击的巨人", "第1话", "1")]
        决策 = 自动决策(候选们, self.素材)
        self.assertTrue(决策.需要人工确认)
        self.assertIsNone(决策.采纳)
        self.assertIn("拿不准", 决策.说明)

    def test_没有候选要人工确认(self):
        决策 = 自动决策([], self.素材)
        self.assertTrue(决策.需要人工确认)
        self.assertIsNone(决策.采纳)
        self.assertEqual(决策.候选们, [])
        self.assertIn("没有任何候选", 决策.说明)

    def test_唯一候选高分就采纳(self):
        """只有一个候选时没有"第二名"可比 —— 弹弹play 的 hash 命中通常就一个。"""
        决策 = 自动决策([匹配结果("1", "进击的巨人", "第1话", "1")], self.素材)
        self.assertFalse(决策.需要人工确认)
        self.assertEqual(决策.采纳.标识, "1")
        self.assertIn("唯一候选", 决策.说明)

    def test_分数不够高要人工确认(self):
        # 阈值设到 100 以上：两个候选就算都很高也不该自动采纳
        决策 = 自动决策([匹配结果("1", "进击的巨人", "第1话", "1"),
                      匹配结果("2", "进击的巨人", "第2话", "2")],
                      self.素材, 自动阈值=100.5, 差距阈值=12.0)
        self.assertTrue(决策.需要人工确认)
        self.assertIn("自动阈值", 决策.说明)

    def test_阈值可以调(self):
        候选们 = [匹配结果("1", "进击的巨人", "第1话", "1"),
                匹配结果("2", "进击的巨人 第二季", "第1话", "1")]
        松 = 自动决策(候选们, self.素材, 自动阈值=30.0, 差距阈值=1.0)
        紧 = 自动决策(候选们, self.素材, 自动阈值=99.9, 差距阈值=40.0)
        self.assertFalse(松.需要人工确认)
        self.assertTrue(紧.需要人工确认)
        self.assertEqual(松.采纳.标识, 紧.候选们[0].标识)


# ---------------------------------------------------------------------------
# 找弹幕（把各源串起来）
# ---------------------------------------------------------------------------

class 找弹幕测试(unittest.TestCase):
    def setUp(self) -> None:
        self.素材 = 素材(文件名="进击的巨人 第01话.mkv", 标题="进击的巨人", 集号=1)

    def test_合并各源的候选并决策(self):
        甲 = 假源("源甲", [匹配结果("1", "进击的巨人", "第1话", "1")], 名字="源甲")
        乙 = 假源("源乙", [匹配结果("9", "孤独的美食家", "第1话", "1")], 名字="源乙")
        决策 = 找弹幕(self.素材, [甲, 乙])
        self.assertFalse(决策.需要人工确认)
        self.assertEqual(决策.采纳.标识, "1")
        self.assertEqual(len(决策.候选们), 2)

    def test_候选记住自己来自哪个源(self):
        """标识只在源内唯一，要回去取弹幕就得知道是哪个源给的。"""
        甲 = 假源("源甲", [匹配结果("1", "进击的巨人", "第1话", "1")], 名字="源甲")
        乙 = 假源("源乙", [匹配结果("1", "进击的巨人", "第1话", "1")], 名字="源乙")
        决策 = 找弹幕(self.素材, [甲, 乙])
        self.assertEqual([候.原始["源"] for 候 in 决策.候选们], ["源甲", "源乙"])

    def test_一个源挂了不影响别的源(self):
        坏 = 假源("坏源", 名字="坏源", 抛错=RuntimeError("网络断了"))
        好 = 假源("好源", [匹配结果("1", "进击的巨人", "第1话", "1")], 名字="好源")
        with self.assertLogs("wangpan.danmaku.匹配", level="WARNING"):
            决策 = 找弹幕(self.素材, [坏, 好])
        self.assertFalse(决策.需要人工确认)
        self.assertEqual(决策.采纳.标识, "1")
        self.assertIn("坏源", 决策.说明)

    def test_不能匹配的源不问(self):
        class 本地式(假源):
            def 能匹配(self) -> bool:
                return False

        不问 = 本地式("不问", [匹配结果("1", "进击的巨人")])
        决策 = 找弹幕(self.素材, [不问])
        self.assertTrue(决策.需要人工确认)
        self.assertIn("没有可用的匹配源", 决策.说明)

    def test_没有源也是人工确认(self):
        决策 = 找弹幕(self.素材, [])
        self.assertTrue(决策.需要人工确认)

    def test_按来源取弹幕(self):
        甲条 = 弹幕(1000, "来自甲", 弹幕模式.滚动, 0xFFFFFF, 25, "甲", 来源标识.弹弹play, "1")
        乙条 = 弹幕(2000, "来自乙", 弹幕模式.顶部, 0xFFFFFF, 25, "乙", 来源标识.本地, "1")
        甲 = 假源("源甲", [匹配结果("1", "进击的巨人", "第1话", "1")], 名字="源甲", 弹幕们=[甲条])
        乙 = 假源("源乙", [匹配结果("1", "进击的巨人 第二季", "第1话", "1")], 名字="源乙",
                弹幕们=[乙条])
        决策 = 找弹幕(self.素材, [甲, 乙])
        self.assertEqual(决策.采纳.原始["源"], "源甲", "分数高的那个该被采纳")
        池 = 取采纳的弹幕(决策, [甲, 乙])
        self.assertEqual([条.文本 for 条 in 池], ["来自甲"], "应该去候选自己那个源取")
        self.assertEqual(乙.取过, [], "不该去另一个源取")

    def test_取弹幕先找对的源再兜底(self):
        甲 = 假源("源甲", [匹配结果("1", "进击的巨人", "第1话", "1")], 名字="源甲")
        乙 = 假源("源乙", [匹配结果("1", "进击的巨人", "第1话", "1")], 名字="源乙",
                弹幕们=[弹幕(1000, "乙的弹幕", 弹幕模式.滚动)])
        决策 = 找弹幕(self.素材, [甲])
        决策.采纳.原始["源"] = "不存在的源"
        with self.assertLogs("wangpan.danmaku.匹配", level="WARNING"):
            池 = 取采纳的弹幕(决策, [甲, 乙])
        self.assertEqual([条.文本 for 条 in 池], ["乙的弹幕"])

    def test_没有采纳就不能取弹幕(self):
        决策 = 自动决策([], self.素材)
        with self.assertRaises(弹幕源错误):
            取采纳的弹幕(决策, [假源("源甲")])
        with self.assertRaises(弹幕源错误):
            取采纳的弹幕(自动决策([匹配结果("1", "进击的巨人")], self.素材), [])


# ---------------------------------------------------------------------------
# 过滤
# ---------------------------------------------------------------------------

def 造池() -> 弹幕池:
    """7 条样例：能一次覆盖屏蔽词/正则/发送者/模式/彩色/时间窗/长度/去重。

    最后那条 "普通的一条" 是**真重复**（时间、文本、模式都一样，来源不同）——
    去重键就是这三样（见 模型.弹幕.去重键）。
    """
    return 弹幕池([
        弹幕(1000, "加群一起玩", 弹幕模式.滚动, 0xFFFFFF, 25, "甲"),
        弹幕(2000, "HELLO World", 弹幕模式.顶部, 0xFFFFFF, 25, "乙"),
        弹幕(3000, "彩色弹幕", 弹幕模式.底部, 0xFF0000, 25, "丙"),
        弹幕(4000, "。", 弹幕模式.滚动, 0xFFFFFF, 25, "丁"),
        弹幕(5000, "普通的一条", 弹幕模式.滚动, 0xFFFFFF, 25, "戊"),
        弹幕(5000, "普通的一条", 弹幕模式.滚动, 0xFFFFFF, 25, "戊", 来源标识.本地, "另一个源"),
        弹幕(7000, "很长很长很长很长很长很长很长的一条弹幕", 弹幕模式.滚动, 0xFFFFFF, 25, "己"),
    ])


class 过滤测试(unittest.TestCase):
    def test_默认规则只去重不屏蔽(self):
        规则 = 默认规则()
        self.assertEqual(规则.屏蔽词, [])
        self.assertEqual(规则.正则们, [])
        self.assertTrue(规则.去重)
        self.assertFalse(规则.有实质规则())
        新, 统计 = 过滤器(规则).应用(造池())
        self.assertEqual(统计["进入"], 7)
        self.assertEqual(统计["留下"], 6, "只该去掉重复的那条")
        self.assertEqual(统计["按原因"]["重复"], 1)
        self.assertTrue(all(值 == 0 for 键, 值 in 统计["按原因"].items() if 键 != "重复"))

    def test_屏蔽词(self):
        池, 统计 = 过滤器(过滤规则(屏蔽词=["加群"], 去重=False)).应用(造池())
        self.assertNotIn("加群一起玩", [条.文本 for 条 in 池])
        self.assertEqual(统计["按原因"]["屏蔽词"], 1)
        self.assertEqual(统计["留下"], 6)

    def test_正则大小写不敏感(self):
        过滤 = 过滤器(过滤规则(正则们=["^hello"], 去重=False))
        池, 统计 = 过滤.应用(造池())
        self.assertNotIn("HELLO World", [条.文本 for 条 in 池])
        self.assertEqual(统计["按原因"]["正则"], 1)
        self.assertEqual(统计["留下"], 6)

    def test_非法正则吞掉不崩且记下来(self):
        with self.assertLogs("wangpan.danmaku.过滤", level="WARNING") as 捕获:
            编 = 过滤器(过滤规则(正则们=["([", "^普通", "a{2,1}"], 去重=False))
        self.assertEqual(len(捕获.records), 2, "两个坏正则都要写进日志")
        self.assertEqual(编.坏正则, ["([", "a{2,1}"])
        池, 统计 = 编.应用(造池())
        self.assertEqual(统计["按原因"]["正则"], 2, "能用的那条正则还要继续生效")
        self.assertEqual(统计["坏正则"], 编.坏正则)
        self.assertEqual(统计["留下"], 5)

    def test_屏蔽发送者(self):
        池, 统计 = 过滤器(过滤规则(屏蔽发送者=["戊"], 去重=False)).应用(造池())
        self.assertEqual(统计["按原因"]["发送者"], 2)
        self.assertNotIn("戊", [条.发送者 for 条 in 池])

    def test_只显示含关键词(self):
        池, 统计 = 过滤器(过滤规则(只显示含关键词=["普通"], 去重=False)).应用(造池())
        self.assertEqual([条.文本 for 条 in 池], ["普通的一条", "普通的一条"])
        self.assertEqual(统计["按原因"]["不含关键词"], 5)

    def test_屏蔽三种模式(self):
        总数 = {弹幕模式.滚动: 0, 弹幕模式.顶部: 0, 弹幕模式.底部: 0}
        for 条 in 造池():
            总数[条.模式] += 1
        for 字段, 模式, 文本 in (("屏蔽顶部", 弹幕模式.顶部, "HELLO World"),
                              ("屏蔽底部", 弹幕模式.底部, "彩色弹幕"),
                              ("屏蔽滚动", 弹幕模式.滚动, "加群一起玩")):
            池, 统计 = 过滤器(过滤规则(去重=False, **{字段: True})).应用(造池())
            self.assertNotIn(文本, [条.文本 for 条 in 池], 字段)
            self.assertEqual(统计["按原因"][字段], 总数[模式], 字段)
            self.assertTrue(all(条.模式 is not 模式 for 条 in 池), 字段)

    def test_屏蔽彩色(self):
        池, 统计 = 过滤器(过滤规则(屏蔽彩色=True, 去重=False)).应用(造池())
        self.assertEqual(统计["按原因"]["屏蔽彩色"], 1)
        self.assertTrue(all(条.颜色 == 0xFFFFFF for 条 in 池))

    def test_时间窗(self):
        池, 统计 = 过滤器(过滤规则(最小秒=2.0, 最大秒=6.0, 去重=False)).应用(造池())
        self.assertEqual([条.秒 for 条 in 池], [2.0, 3.0, 4.0, 5.0, 5.0])
        self.assertEqual(统计["按原因"]["时间窗"], 2)
        # 最大秒 = 0 表示不限
        池2, 统计2 = 过滤器(过滤规则(最大秒=0, 最小秒=6.5, 去重=False)).应用(造池())
        self.assertEqual([条.秒 for 条 in 池2], [7.0])
        self.assertEqual(统计2["按原因"]["时间窗"], 6)

    def test_长度限制(self):
        池, 统计 = 过滤器(过滤规则(最短文本=3, 最长文本=6, 去重=False)).应用(造池())
        self.assertEqual([条.文本 for 条 in 池],
                         ["加群一起玩", "彩色弹幕", "普通的一条", "普通的一条"])
        self.assertEqual(统计["按原因"]["文本太短"], 1)
        self.assertEqual(统计["按原因"]["文本太长"], 2)

    def test_去重可以关掉(self):
        开, 统计开 = 过滤器(过滤规则(去重=True)).应用(造池())
        关, 统计关 = 过滤器(过滤规则(去重=False)).应用(造池())
        self.assertEqual((len(开), len(关)), (6, 7))
        self.assertEqual((统计开["按原因"]["重复"], 统计关["按原因"]["重复"]), (1, 0))

    def test_过滤产出新池不改原池(self):
        原 = 造池()
        前 = len(原)
        新, _ = 过滤器(过滤规则(屏蔽词=["加群"])).应用(原)
        self.assertEqual(len(原), 前, "原池不能被改（弹幕是不可变的视图）")
        self.assertLess(len(新), 前)
        self.assertEqual(新.来源说明, 原.来源说明)

    def test_命中原因与统计对得上(self):
        """原因必须说清楚是哪条规则挡的（界面要拿它解释，说错比不说更糟）。"""
        规则 = 过滤规则(屏蔽词=["加群"], 正则们=["^hello"], 屏蔽发送者=["丁"],
                    屏蔽彩色=True, 最小秒=1.0, 最长文本=12, 去重=False)
        过滤 = 过滤器(规则)
        池, 统计 = 过滤.应用(造池())
        期望 = {"加群一起玩": "屏蔽词：加群",
              "HELLO World": "正则：^hello",
              "彩色弹幕": "屏蔽彩色",
              "。": "发送者：丁",
              "普通的一条": "",                       # 长度 5 ≤ 12，留
              "很长很长很长很长很长很长很长的一条弹幕": "文本太长"}
        for 条 in 造池():
            self.assertIn(期望[条.文本], 过滤.命中原因(条), 条.文本)
        # 每条留下的弹幕，命中原因必须是空串
        for 条 in 池:
            self.assertEqual(过滤.命中原因(条), "")
        self.assertEqual([条.文本 for 条 in 池], ["普通的一条", "普通的一条"])
        self.assertEqual(统计["进入"], 7)
        self.assertEqual(统计["留下"], len(池))
        self.assertEqual(统计["进入"] - 统计["留下"], sum(统计["按原因"].values()))

    def test_统计结构固定(self):
        新, 统计 = 过滤器().应用(造池())
        self.assertEqual(sorted(统计), sorted(["进入", "留下", "按原因", "坏正则"]))
        self.assertEqual(sorted(统计["按原因"]), sorted(统计键们))
        self.assertEqual(统计["进入"], 7)
        self.assertEqual(统计["留下"], len(新))

    def test_命中原因对单条也能用(self):
        过滤 = 过滤器(过滤规则(屏蔽词=["加群"]))
        self.assertEqual(过滤.命中原因(弹幕(0, "加群吧")), "屏蔽词：加群")
        self.assertEqual(过滤.命中原因(弹幕(0, "普通")), "")

    def test_导出导入往返一致(self):
        规则 = 过滤规则(屏蔽词=["加群", "广告"], 正则们=["^\\d+$"], 屏蔽发送者=["某人"],
                    只显示含关键词=[], 屏蔽顶部=True, 屏蔽彩色=True, 最小秒=1.5,
                    最大秒=600.0, 最短文本=2, 最长文本=80, 去重=False)
        过滤 = 过滤器(规则)
        文本 = 过滤.导出()
        self.assertEqual(json.loads(文本)["版本"], 1)
        回来 = 过滤器.导入(文本)
        self.assertEqual(回来.规则, 规则)
        self.assertEqual(回来.坏正则, [])
        # 往返后行为也要一致
        甲, _ = 过滤.应用(造池())
        乙, _ = 回来.应用(造池())
        self.assertEqual([条.文本 for 条 in 甲], [条.文本 for 条 in 乙])

    def test_导入坏JSON给默认规则(self):
        with self.assertLogs("wangpan.danmaku.过滤", level="WARNING"):
            for 坏 in ("{不是 JSON", "", "null", "[1,2,3]", '"字符串"'):
                过滤 = 过滤器.导入(坏)
                self.assertEqual(过滤.规则, 默认规则(), 坏)
                self.assertEqual(过滤.坏正则, [])

    def test_导入坏字段用默认值(self):
        文本 = json.dumps({"规则": {"屏蔽词": "不是数组", "最小秒": "很久",
                                 "去重": "false", "屏蔽顶部": 1, "最长文本": 30,
                                 "不认识的字段": 5}}, ensure_ascii=False)
        with self.assertLogs("wangpan.danmaku.过滤", level="WARNING"):
            过滤 = 过滤器.导入(文本)
        self.assertEqual(过滤.规则.屏蔽词, [])
        self.assertEqual(过滤.规则.最小秒, 0.0)
        self.assertFalse(过滤.规则.去重, "字符串 'false' 要当假")
        self.assertTrue(过滤.规则.屏蔽顶部)
        self.assertEqual(过滤.规则.最长文本, 30)

    def test_导入顶层扁平写法也行(self):
        过滤 = 过滤器.导入(json.dumps({"屏蔽词": ["广告"]}, ensure_ascii=False))
        self.assertEqual(过滤.规则.屏蔽词, ["广告"])

    def test_导出的规则能存成文件再读回来(self):
        """界面上"另存规则/载入规则"就是这么干的。"""
        with 临时目录() as 目录:
            路径 = Path(目录) / "过滤规则.json"
            路径.write_text(过滤器(过滤规则(屏蔽词=["广告", "加群"], 屏蔽顶部=True)).导出(),
                          encoding="utf-8")
            回来 = 过滤器.导入(路径.read_text(encoding="utf-8"))
        self.assertEqual(回来.规则.屏蔽词, ["广告", "加群"])
        self.assertTrue(回来.规则.屏蔽顶部)

    def test_改了规则以后正则要重新编译(self):
        """界面绑的是同一个规则对象，用户改完要立刻生效。"""
        规则 = 过滤规则(去重=False)
        过滤 = 过滤器(规则)
        self.assertEqual(过滤.坏正则, [])
        规则.正则们.append("([")
        with self.assertLogs("wangpan.danmaku.过滤", level="WARNING"):
            池, 统计 = 过滤.应用(造池())
        self.assertEqual(过滤.坏正则, ["(["])
        self.assertEqual(len(池), 7, "坏正则不该过滤掉任何东西")
        规则.正则们.append("^普通")
        self.assertEqual(过滤.坏正则, ["(["])
        with self.assertLogs("wangpan.danmaku.过滤", level="WARNING"):
            池2, 统计2 = 过滤.应用(造池())
        self.assertEqual(统计2["按原因"]["正则"], 2)


class 过滤与匹配联动测试(unittest.TestCase):
    def test_过滤后的池能直接给界面用(self):
        """一条链：找弹幕 → 取弹幕 → 过滤 → 按时间取窗口（引擎就是这么用的）。"""
        条们 = [弹幕(1000, "加群", 弹幕模式.滚动, 0xFFFFFF, 25, "甲"),
              弹幕(2000, "正常的一条", 弹幕模式.滚动, 0xFFFFFF, 25, "乙"),
              弹幕(2000, "正常的一条", 弹幕模式.滚动, 0xFFFFFF, 25, "乙", 来源标识.本地, "9")]
        源 = 假源("源甲", [匹配结果("1", "进击的巨人", "第1话", "1")], 名字="源甲", 弹幕们=条们)
        信息 = 素材(文件名="进击的巨人 第01话.mkv", 标题="进击的巨人", 集号=1)
        决策 = 找弹幕(信息, [源])
        池 = 取采纳的弹幕(决策, [源])
        干净, 统计 = 过滤器(过滤规则(屏蔽词=["加群"])).应用(池)
        self.assertEqual(len(干净), 1, "屏蔽词挡掉一条，重复再去掉一条")
        self.assertEqual(统计["按原因"]["屏蔽词"], 1)
        self.assertEqual(统计["按原因"]["重复"], 1)
        self.assertEqual([条.文本 for 条 in 干净.取窗口(1500, 3500)], ["正常的一条"])

    def test_过滤不会改变来源信息(self):
        来源 = 来源标识.弹弹play
        池 = 弹幕池([弹幕(1000, "你好", 弹幕模式.滚动, 0xFFFFFF, 25, "甲", 来源, "7")])
        新, _ = 过滤器(过滤规则(最短文本=1)).应用(池)
        self.assertIs(新.条目们[0].来源, 来源)
        self.assertEqual(新.条目们[0].标识, "7")


if __name__ == "__main__":
    unittest.main()
