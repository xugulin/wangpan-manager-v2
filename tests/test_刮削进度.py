"""刮削进度测试（**不联网**）：进度必须真的动，而且要按"视频单元"动。

为什么要专门测这个
==================
用户真机截图：``0/1（0%）｜成功 0｜待确认 0｜失败 0｜用时 0.0s`` —— 其实后台已经
把海报和剧集都入库了，用户以为卡死。两个原因，各钉一条：

1. **分母**：进度原来按"任务"（= 一部剧一个文件夹）计数，16 集的剧从头到尾就是
   ``0/1``；现在按视频单元计数，一部 16 集的剧要看到 1/16、2/16…16/16。
2. **分子与计数**：`已完成` 原来在"任务"层才 +1，`成功/需要确认/失败` **根本没被
   更新过**（所以明明入库了还显示"成功 0"）；现在按"一个视频处理完"推进，三个计数
   用"前后差值"同步（见 ``刮削服务._同步计数``）。

界面那一行字（"已处理 X/Y｜成功 a｜待确认 b｜失败 c"）由
:func:`wangpan.ui.媒体库页.进度文案` 渲染，这里直接单测它，不用真开进度框。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.公用 import 临时目录

from wangpan.scrape.图片 import 图片缓存
from wangpan.scrape.库 import 资料库
from wangpan.scrape.模型 import 媒体条目, 媒体类型, 匹配候选, 季, 集
from wangpan.scrape.服务 import 刮削服务, 刮削设置


class 假TMDB:
    """最小可用的假客户端：认两部片子（一部 16 集的剧 + 一部电影），别的一律没有。"""

    剧标识 = "999"
    电影标识 = "500"

    def __init__(self) -> None:
        self.请求次数 = 0
        self.搜过的词: list[str] = []
        self.取过的季: list[tuple[str, int]] = []

    def 可用(self) -> bool:
        return True

    def 配置信息(self) -> dict:
        return {"images": {"secure_base_url": "https://image.tmdb.org/t/p/"}}

    def 搜电影(self, 标题: str, 年份=None) -> list[匹配候选]:
        self.请求次数 += 1
        self.搜过的词.append(标题)
        if "进度电影" in 标题:
            return [匹配候选(来源标识=self.电影标识, 标题="进度电影", 年份=2019,
                          类型=媒体类型.电影, 热度=400)]
        return []

    def 搜剧集(self, 标题: str, 年份=None) -> list[匹配候选]:
        self.请求次数 += 1
        self.搜过的词.append(标题)
        if "进度剧" in 标题:
            return [匹配候选(来源标识=self.剧标识, 标题="进度剧", 原名="Progress Show",
                          年份=2020, 类型=媒体类型.剧集, 热度=900)]
        return []

    def 取电影(self, id: str) -> 媒体条目:
        self.请求次数 += 1
        条目 = 媒体条目(类型=媒体类型.电影, 标题="进度电影", 年份=2019, 评分=7.0)
        条目.外部ID = {"tmdb": self.电影标识}
        return 条目

    def 取剧集(self, id: str) -> 媒体条目:
        self.请求次数 += 1
        条目 = 媒体条目(类型=媒体类型.剧集, 标题="进度剧", 原名="Progress Show",
                    年份=2020, 评分=8.0)
        条目.外部ID = {"tmdb": self.剧标识}
        条目.季们.append(季(季号=1, 标题="第 1 季", 集数=16))
        return 条目

    def 取季(self, 剧id: str, 季号: int) -> 季:
        self.请求次数 += 1
        self.取过的季.append((str(剧id), int(季号)))
        季对象 = 季(季号=int(季号), 标题=f"第 {季号} 季", 集数=16)
        for 号 in range(1, 17):
            季对象.集们.append(集(季号=int(季号), 集号=号, 标题=f"第 {号} 集"))
        return 季对象

    def 关闭(self) -> None:
        return None


#: 造一部剧要几集（够长才看得出"按集推进"，也别太长拖慢测试）
集数 = 16


def 造剧目录(根: Path, 名字: str = "进度剧 (2020)", 集数_: int = 集数,
          目录名: str = "") -> Path:
    目录 = 根 / (目录名 or 名字)
    目录.mkdir(parents=True, exist_ok=True)
    for 号 in range(1, 集数_ + 1):
        (目录 / f"{名字} S01E{号:02d}.mkv").write_bytes(b"v" * 4096)
    return 目录


class 进度测试基类(unittest.TestCase):
    def setUp(self) -> None:
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)
        self.媒体 = self.根 / "媒体"
        self.媒体.mkdir(parents=True, exist_ok=True)
        self.库 = 资料库(self.根 / "资料库.db")
        self.addCleanup(self.库.关闭)
        self.客户端 = 假TMDB()
        self.快照们: list = []
        self.日志行: list[str] = []
        self.服务 = 刮削服务(
            self.库, self.客户端, 图片缓存(self.根 / "图缓存"),
            刮削设置(最小文件字节=0, 本批上限=10, 下载图片=False,
                 抓季集详情=True),
            进度回调=self.快照们.append, 日志回调=self.日志行.append)
        self.addCleanup(self.服务.关闭)

    def _续跑段(self) -> list:
        """只要"续跑"那一段的快照（前面登记阶段也在报进度，分母含义不同）。"""
        起点 = len(self.快照们)
        self.结果 = self.服务.续跑()
        return self.快照们[起点:]


class 续跑进度测试(进度测试基类):
    def test_一部16集的剧要看到1到16递增(self):
        造剧目录(self.媒体)
        self.服务.扫库([self.媒体])
        快照们 = self._续跑段()
        self.assertTrue(快照们, "续跑必须回调进度（一次都没有 = 界面还是死的）")
        总数们 = {x.总数 for x in 快照们}
        self.assertEqual(总数们, {集数},
                         f"分母必须是真实待处理的视频数 {集数}，而不是任务数 1：{总数们}")
        已完成们 = [x.已完成 for x in 快照们]
        self.assertEqual(已完成们[0], 0)
        self.assertEqual(已完成们[-1], 集数, f"跑完必须走满：{已完成们}")
        self.assertEqual(已完成们, sorted(已完成们), f"进度不许倒退：{已完成们}")
        # 用户要看的就是 "1/16、2/16 … 16/16" 这一串
        self.assertEqual(sorted(set(已完成们)), list(range(0, 集数 + 1)),
                         f"应当是逐集推进：{已完成们}")
        self.assertEqual(self.服务.进度.成功, 1)
        self.assertEqual(self.服务.进度.需要确认, 0)
        self.assertEqual(self.服务.进度.失败, 0)
        self.assertEqual(self.服务.进度.任务总数, 1)
        self.assertEqual(self.服务.进度.任务已完成, 1)

    def test_成功待确认失败三个计数会被同步(self):
        """截图里"成功 0"其实已经入库了 —— 计数必须跟着结果走。"""
        造剧目录(self.媒体)
        # 再造一个认不出来的目录：它会被"丢弃"，于是"处理过"但不"成功"
        (self.媒体 / "查无此片").mkdir(parents=True, exist_ok=True)
        for 号 in (1, 2):
            (self.媒体 / "查无此片" / f"查无此片 S01E{号:02d}.mkv").write_bytes(b"w" * 4096)
        self.服务.扫库([self.媒体])
        快照们 = self._续跑段()
        最后 = 快照们[-1]
        self.assertEqual(self.服务.进度.成功, 1, "进度剧应当自动入库")
        self.assertEqual(最后.总数, 集数 + 2, "两个任务的视频数都要算进分母")
        self.assertEqual(最后.已完成, 最后.总数,
                         "即使任务被丢弃/跳过，也算'处理过了'（进度不能停在半路）")
        self.assertEqual(self.服务.进度.失败, 0)
        self.assertEqual(self.结果.跳过, 1, "认不出来的那部应当被丢弃（跳过）")

    def test_刮路径也按视频数报进度(self):
        造剧目录(self.媒体)
        目录 = self.媒体 / "进度剧 (2020)"
        结果 = self.服务.刮路径(目录)
        self.assertEqual(结果.成功, 1, f"应当成功：{结果.摘要()}｜{' / '.join(self.日志行[-5:])}")
        快照们 = self.快照们
        self.assertTrue(快照们)
        self.assertEqual(快照们[0].总数, 集数, "单刮一个剧文件夹也是 16 集，不是 1")
        self.assertEqual(快照们[-1].已完成, 快照们[-1].总数)
        self.assertEqual(self.服务.进度.成功, 1)

    def test_登记阶段的分母就是真实待处理条数(self):
        """登记（扫库）那一刻就要把分母设对：这是"识别+落库"之前用户看到的第一眼。"""
        造剧目录(self.媒体)
        self.服务.扫库([self.媒体])
        快照们 = self.快照们
        self.assertTrue(快照们, "登记阶段也要报进度")
        self.assertEqual(快照们[0].总数, 集数,
                         "第一眼就必须是 0/16，而不是 0/0 或 0/1")
        self.assertEqual(快照们[0].已完成, 0)
        self.assertEqual(快照们[-1].已完成, 集数, "登记完成后进度要走满")

    def test_电影多版本也算多个视频单元(self):
        目录 = self.媒体 / "进度电影 (2019)"
        目录.mkdir(parents=True, exist_ok=True)
        for 名 in ("进度电影 (2019) - 2160p.mkv", "进度电影 (2019) - 1080p.mkv"):
            (目录 / 名).write_bytes(b"m" * 4096)
        self.服务.扫库([self.媒体])
        快照们 = self._续跑段()
        self.assertEqual(快照们[0].总数, 2, "两个版本 = 两个视频单元")
        self.assertEqual(快照们[-1].已完成, 2)
        self.assertEqual(self.服务.进度.成功, 1)


class 进度文案测试(unittest.TestCase):
    """进度框上那一行字（用户实际看到的东西）。"""

    def test_文案带已处理与三个计数(self):
        from wangpan.scrape.服务 import 刮削进度
        from wangpan.ui.媒体库页 import 进度文案
        进度 = 刮削进度(总数=16, 已完成=3, 成功=1, 需要确认=1, 失败=0, 当前="进度剧 S01E03.mkv")
        文案 = 进度文案(进度)
        self.assertIn("已处理 3/16", 文案)
        self.assertIn("19%", 文案)
        self.assertIn("成功 1", 文案)
        self.assertIn("待确认 1", 文案)
        self.assertIn("失败 0", 文案)
        self.assertIn("进度剧 S01E03.mkv", 文案)

    def test_总数未知时不显示假的百分比(self):
        from wangpan.scrape.服务 import 刮削进度
        from wangpan.ui.媒体库页 import 进度文案
        文案 = 进度文案(刮削进度(), 阶段="正在扫描…")
        self.assertIn("已处理 0/0", 文案)
        self.assertIn("正在扫描…", 文案)

    def test_进度对象的快照是副本(self):
        from wangpan.scrape.服务 import 刮削进度
        进度 = 刮削进度(总数=10, 已完成=2)
        快照 = 进度.快照()
        进度.已完成 = 9
        self.assertEqual(快照.已完成, 2, "快照必须是副本（后台线程还在改原对象）")

    def test_推进不越过总数(self):
        from wangpan.scrape.服务 import 刮削进度
        进度 = 刮削进度(总数=3)
        进度.推进(10)
        self.assertEqual(进度.已完成, 3, "百分比不许超过 100%")
        进度.推进(-5)
        self.assertEqual(进度.已完成, 3, "负数不该让进度倒退")


if __name__ == "__main__":
    unittest.main()
