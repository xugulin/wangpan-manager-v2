"""手动资料界面测试（**不联网**：测试图都是现场用 QImage 画出来的纯色图）。

覆盖的是"断网也能入库"这条刚需路径：新建/修改/校验/选图/装载，
以及两个容易出事的点 —— 改已有剧集不能丢季集、两条手动记录不能互相覆盖。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.公用 import 临时目录

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from wangpan.scrape.库 import 资料库
from wangpan.scrape.模型 import 季, 集, 媒体条目, 媒体类型
from wangpan.scrape.图片 import 图片缓存
from wangpan.ui.手动资料 import 手动资料对话框, 手动资料面板

应用 = QApplication.instance() or QApplication([])


def 造图(落点: Path, 宽: int = 60, 高: int = 90, 颜色: str = "#3366ff") -> Path:
    """画一张纯色 PNG 存盘（真图片文件：不联网、也不需要素材库）。"""
    图 = QImage(宽, 高, QImage.Format.Format_RGB32)
    图.fill(QColor(颜色))
    if not 图.save(str(落点), "PNG"):
        raise RuntimeError(f"造图失败：{落点}")
    return 落点


class 手动资料基础(unittest.TestCase):
    """公共脚手架：临时库 + 临时图片缓存 + 一个空白的面板。"""

    def setUp(self) -> None:
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)
        self.库 = 资料库(self.根 / "资料库.db")
        self.addCleanup(self.库.关闭)
        self.缓存 = 图片缓存(self.根 / "缓存" / "图片")
        self.面板 = 手动资料面板(self.库, self.缓存)

    def 存一条剧集(self) -> int:
        """插一条**带季集**的剧集（用来验证"改资料不丢季集"）。"""
        条目 = 媒体条目(
            类型=媒体类型.剧集, 标题="老剧名", 年份=2015, 来源="tmdb",
            外部ID={"tmdb": "777"},
            季们=[季(季号=1, 标题="第一季",
                     集们=[集(季号=1, 集号=1, 标题="试播集"),
                         集(季号=1, 集号=2, 标题="第二集")]),
                季(季号=2, 标题="第二季",
                     集们=[集(季号=2, 集号=1, 标题="回归")])])
        return self.库.存条目(条目)


class 新建与保存测试(手动资料基础):
    def test_新建保存后读回一致(self):
        self.面板.标题框.setText("  我的片子  ")
        self.面板.年份框.setValue(2019)
        self.面板.评分框.setValue(8.5)
        self.面板.影评人框.setValue(72.0)
        self.面板.时长框.setValue(118)
        self.面板.标签框.setText("动作, 科幻，动作")
        self.面板.国家框.setCurrentText("CN")
        self.面板.分级值框.setText("辅导级12")
        self.assertTrue(self.面板.添加分级())

        媒体id = self.面板.保存()
        self.assertIsNotNone(媒体id)
        条目 = self.库.取媒体(媒体id)
        self.assertEqual(条目.标题, "我的片子", "标题要去掉首尾空白")
        self.assertEqual(条目.年份, 2019)
        self.assertAlmostEqual(条目.评分, 8.5, places=3)
        self.assertAlmostEqual(条目.影评人评分, 72.0, places=3)
        self.assertEqual(条目.时长分钟, 118)
        self.assertEqual(条目.标签, ["动作", "科幻"], "重复标签只留一个")
        self.assertEqual([一条.可读() for 一条 in 条目.分级们], ["CN:辅导级12"])
        self.assertIs(条目.类型, 媒体类型.电影, "新建默认就是电影")
        self.assertEqual(条目.来源, "手动")
        self.assertEqual(self.库.统计().媒体数, 1)

    def test_保存会发出已保存信号(self):
        收到: list[int] = []
        self.面板.已保存.connect(收到.append)
        self.面板.标题框.setText("信号片")
        媒体id = self.面板.保存()
        self.assertEqual(收到, [媒体id])
        self.assertEqual(self.面板.当前媒体id(), 媒体id)

    def test_标题为空不保存(self):
        提示: list[str] = []
        self.面板.校验失败.connect(提示.append)
        self.面板.标题框.setText("   ")
        媒体id = self.面板.保存()
        self.assertIsNone(媒体id, "标题空着不该写库")
        self.assertEqual(self.库.统计().媒体数, 0)
        self.assertTrue(提示, "要让宿主知道为什么没保存")
        self.assertIn("标题", 提示[0])
        self.assertIn("标题不能为空", self.面板.提示标签.text())

    def test_年份为零表示未知(self):
        self.面板.标题框.setText("没年份")
        self.面板.年份框.setValue(0)
        条目 = self.库.取媒体(self.面板.保存())
        self.assertIsNone(条目.年份, "0 = 未知，库里存 NULL 而不是 0")

    def test_评分越界被拒绝保存(self):
        # 把控件上限放开到 99，模拟"从代码里塞了个越界值"：校验要拦住它
        self.面板.评分框.setMaximum(99.0)
        self.面板.评分框.setValue(99.0)
        self.面板.标题框.setText("越界片")
        self.assertIsNone(self.面板.保存(), "越界的评分要拒绝保存（不是悄悄写进去）")
        self.assertEqual(self.库.统计().媒体数, 0)
        self.assertIn("评分", self.面板.提示标签.text())

    def test_影评人评分是百分制(self):
        self.面板.影评人框.setMaximum(999.0)
        self.面板.影评人框.setValue(85.0)
        self.面板.标题框.setText("百分制")
        条目 = self.库.取媒体(self.面板.保存())
        self.assertAlmostEqual(条目.影评人评分, 85.0, places=3,
                               msg="85 分是合法的影评人评分（0–100）")
        # 反过来：超过 100 也要被拦（它和 0–10 的"评分"不是一套量纲）
        self.面板.标题框.setText("超百分")
        self.面板.影评人框.setValue(999.0)
        self.assertIsNone(self.面板.保存())
        self.assertIn("百分制", self.面板.提示标签.text())

    def test_组装条目时把越界值夹住(self):
        """控件挡住手滑（夹住）+ 校验再兜一层（拒绝），两层都不许把 8.5 分制的 85 分写进库。"""
        self.面板.评分框.setMaximum(99.0)
        self.面板.影评人框.setMaximum(999.0)
        self.面板.评分框.setValue(99.0)
        self.面板.影评人框.setValue(999.0)
        条目 = self.面板.组装条目()
        self.assertAlmostEqual(条目.评分, 10.0, places=3)
        self.assertAlmostEqual(条目.影评人评分, 100.0, places=3)

    def test_年份框范围挡住手滑(self):
        self.assertEqual(self.面板.年份框.minimum(), 0)
        self.assertEqual(self.面板.年份框.maximum(), 2100)
        self.面板.年份框.setValue(99999)
        self.assertLessEqual(self.面板.年份框.value(), 2100)

    def test_简介与状态语言都写进库(self):
        self.面板.标题框.setText("多行简介")
        self.面板.简介框.setPlainText("第一行\n第二行")
        self.面板.状态框.setText("Released")
        self.面板.语言框.setText("ja")
        条目 = self.库.取媒体(self.面板.保存())
        self.assertEqual(条目.简介, "第一行\n第二行")
        self.assertEqual(条目.状态, "Released")
        self.assertEqual(条目.原始语言, "ja")

    def test_外部ID去掉首尾空白(self):
        self.面板.标题框.setText("有外部id")
        self.面板.tmdb框.setText("  12345  ")
        self.面板.imdb框.setText(" tt0000001 ")
        self.面板.tvdb框.setText("  ")
        条目 = self.库.取媒体(self.面板.保存())
        self.assertEqual(条目.外部ID["tmdb"], "12345")
        self.assertEqual(条目.外部ID["imdb"], "tt0000001")
        self.assertEqual(条目.外部ID["tvdb"], "")

    def test_标签解析中英文逗号并且去重(self):
        self.面板.标签框.setText("动作， 科幻 ,动作,  ,剧情")
        self.assertEqual(self.面板.组装条目().标签, ["动作", "科幻", "剧情"])

    def test_两条手动记录不会互相覆盖(self):
        """手动条目常常没有外部 id：两条共用空键时，后存的不能把前一条盖掉。"""
        self.面板.标题框.setText("第一条")
        第一 = self.面板.保存()
        self.面板.新建()
        self.面板.标题框.setText("第二条")
        第二 = self.面板.保存()

        self.assertNotEqual(第一, 第二)
        self.assertEqual(self.库.统计().媒体数, 2, "两条记录必须都在")
        self.assertEqual(self.库.取媒体(第一).标题, "第一条")
        self.assertEqual(self.库.取媒体(第二).标题, "第二条")

    def test_连续保存同一条不会变成两条(self):
        self.面板.标题框.setText("改两次")
        第一 = self.面板.保存()
        self.面板.评分框.setValue(7.0)
        第二 = self.面板.保存()
        self.assertEqual(第一, 第二, "第二次保存应该还是同一条")
        self.assertEqual(self.库.统计().媒体数, 1)
        self.assertAlmostEqual(self.库.取媒体(第一).评分, 7.0, places=3)

    def test_没填外部ID时给内部编号并提示(self):
        self.面板.标题框.setText("第一条")
        self.面板.保存()
        self.面板.新建()
        self.面板.标题框.setText("第二条")
        self.面板.保存()
        self.assertIn("内部编号", self.面板.提示标签.text(), "分配了编号就要说清楚")
        self.assertTrue(self.面板.tmdb框.text().startswith("本地-"),
                        "编号会显示在外部 ID 框里（用户看得见、也改得动）")

    def test_有未保存改动(self):
        self.assertFalse(self.面板.有未保存改动(), "空白新建状态不算有改动")
        self.面板.标题框.setText("填了标题")
        self.assertTrue(self.面板.有未保存改动())
        self.assertIsNotNone(self.面板.保存())
        self.assertFalse(self.面板.有未保存改动(), "刚保存完就是干净的")
        self.面板.简介框.setPlainText("又改了")
        self.assertTrue(self.面板.有未保存改动())

    def test_装载后改动的检测(self):
        媒体id = self.存一条剧集()
        面板 = 手动资料面板(self.库, self.缓存, 媒体id)
        self.assertFalse(面板.有未保存改动(), "刚装载完不该算有改动")
        面板.标题框.setText("改过了")
        self.assertTrue(面板.有未保存改动())
        面板.保存()
        self.assertFalse(面板.有未保存改动())

    def test_点按钮走一遍(self):
        """真点按钮（不是直调方法）：验证 clicked 的 bool 参数没把槽函数打歪。"""
        self.面板.分级值框.setText("PG")
        self.面板.添加分级按钮.click()
        self.assertEqual(self.面板.分级表.count(), 1)
        self.面板.分级表.setCurrentRow(0)
        self.面板.删除分级按钮.click()
        self.assertEqual(self.面板.分级表.count(), 0)

        self.assertTrue(self.面板.海报框.选本地文件(造图(self.根 / "点按钮.png")))
        self.assertIsNotNone(self.面板.海报框.取图片())
        self.面板.海报框.清除按钮.click()
        self.assertIsNone(self.面板.海报框.取图片(), "清除按钮要真的清掉")

        self.面板.标题框.setText("点按钮保存")
        self.面板.保存按钮.click()
        self.assertEqual(self.库.统计().媒体数, 1)

    def test_点保存按钮标题为空时提示不保存(self):
        self.面板.保存按钮.click()
        self.assertEqual(self.库.统计().媒体数, 0)
        self.assertIn("标题", self.面板.提示标签.text())


class 修改已有条目测试(手动资料基础):
    def test_改剧集标题后季集还在(self):
        媒体id = self.存一条剧集()
        面板 = 手动资料面板(self.库, self.缓存, 媒体id)
        self.assertEqual(面板.标题框.text(), "老剧名")
        面板.标题框.setText("新剧名")
        新id = 面板.保存()

        self.assertEqual(新id, 媒体id, "改的是同一条记录")
        条目 = self.库.取媒体(媒体id)
        self.assertEqual(条目.标题, "新剧名")
        self.assertEqual(条目.来源, "手动")
        self.assertEqual(len(条目.季们), 2, "季不能丢")
        self.assertEqual([len(一季.集们) for 一季 in 条目.季们], [2, 1], "集不能丢")
        self.assertEqual(条目.季们[0].集们[1].标题, "第二集")
        self.assertEqual(条目.汇总集数(), 3)
        self.assertEqual(self.库.统计().媒体数, 1, "不能变成两条")

    def test_改条目保留原本的外部ID与文件路径(self):
        条目 = 媒体条目(类型=媒体类型.电影, 标题="有文件的片子", 来源="tmdb",
                    外部ID={"tmdb": "55", "imdb": "tt55"},
                    文件路径=Path("/tmp/不存在但只是记录/片子.mkv"))
        媒体id = self.库.存条目(条目)
        面板 = 手动资料面板(self.库, self.缓存, 媒体id)
        面板.简介框.setPlainText("改一下简介")
        面板.保存()
        读回 = self.库.取媒体(媒体id)
        self.assertEqual(读回.外部ID["imdb"], "tt55")
        self.assertEqual(len(self.库.文件们(媒体id)), 1, "文件记录不该消失")

    def test_装载不存在的id返回False(self):
        self.assertFalse(self.面板.装载(999999))
        self.assertIn("没有", self.面板.提示标签.text())

    def test_装载把标题填进控件(self):
        self.面板.标题框.setText("装载用")
        self.面板.标签框.setText("悬疑, 犯罪")
        媒体id = self.面板.保存()
        self.面板.新建()
        self.assertEqual(self.面板.标题框.text(), "")

        新面板 = 手动资料面板(self.库, self.缓存, 媒体id)
        self.assertTrue(新面板.装载(媒体id))
        self.assertEqual(新面板.标题框.text(), "装载用")
        self.assertEqual(新面板.标签框.text(), "悬疑, 犯罪")
        self.assertEqual(新面板.当前媒体id(), 媒体id)

    def test_装载后新建会清空(self):
        self.面板.标题框.setText("先有")
        媒体id = self.面板.保存()
        self.assertTrue(self.面板.装载(媒体id))
        self.面板.新建()
        self.assertEqual(self.面板.标题框.text(), "")
        self.assertIsNone(self.面板.当前媒体id(), "新建状态不该还挂着上一条的 id")


class 本地图片测试(手动资料基础):
    def test_选本地图片进缓存并写进条目(self):
        原图 = 造图(self.根 / "自备海报.png", 60, 90, "#ff8800")
        self.assertTrue(self.面板.海报框.选本地文件(原图))
        记住的 = self.面板.海报框.取图片()
        self.assertEqual(记住的.来源, "手动")
        self.assertEqual((记住的.宽, 记住的.高), (60, 90), "尺寸从图里读出来")
        self.面板.标题框.setText("有海报的片子")

        媒体id = self.面板.保存()
        条目 = self.库.取媒体(媒体id)
        self.assertIsNotNone(条目.海报, "海报要写进条目")
        本地 = Path(条目.海报.本地路径)
        self.assertTrue(本地.is_file(), f"缓存里该有这张图：{本地}")
        self.assertGreater(本地.stat().st_size, 0, "缓存文件不能是空的")
        self.assertNotEqual(本地, 原图, "库里记的应该是缓存路径，不是原图路径")
        self.assertEqual(条目.海报.类型.value, "poster")

    def test_清除海报后为None(self):
        self.面板.标题框.setText("先有海报")
        self.assertTrue(self.面板.海报框.选本地文件(造图(self.根 / "a.png")))
        self.assertTrue(self.面板.海报框.清除())
        self.assertIsNone(self.面板.海报框.取图片())
        条目 = self.库.取媒体(self.面板.保存())
        self.assertIsNone(条目.海报, "清掉之后库里就不该有海报")

    def test_选不存在的文件不崩(self):
        self.assertFalse(self.面板.海报框.选本地文件(self.根 / "根本没有.png"))
        self.assertIsNone(self.面板.海报框.取图片())

    def test_三张图各归各位(self):
        self.assertTrue(self.面板.海报框.选本地文件(造图(self.根 / "p.png", 60, 90)))
        self.assertTrue(self.面板.背景框.选本地文件(造图(self.根 / "b.png", 120, 68)))
        self.assertTrue(self.面板.标志框.选本地文件(造图(self.根 / "l.png", 80, 40)))
        self.面板.标题框.setText("三张图")
        条目 = self.库.取媒体(self.面板.保存())
        self.assertIsNotNone(条目.海报)
        self.assertIsNotNone(条目.背景)
        self.assertIsNotNone(条目.标志)
        self.assertEqual(条目.海报.本地路径.name.startswith("本地_"), True)

    def test_装载带海报的条目会预览(self):
        self.面板.标题框.setText("带图装载")
        self.面板.海报框.选本地文件(造图(self.根 / "c.png"))
        媒体id = self.面板.保存()
        新面板 = 手动资料面板(self.库, self.缓存, 媒体id)
        self.assertTrue(新面板.海报框.有图())
        self.assertIsNotNone(新面板.海报框.取图片())
        self.assertIn("✅", 新面板.海报框.状态标签.text())


class 分级与对话框测试(手动资料基础):
    def test_分级增删(self):
        self.面板.国家框.setCurrentText("HK")
        self.面板.分级值框.setText("IIB")
        self.assertTrue(self.面板.添加分级())
        self.面板.国家框.setCurrentText("US")
        self.面板.分级值框.setText("PG-13")
        self.assertTrue(self.面板.添加分级())
        self.assertEqual(self.面板.分级表.count(), 2)

        self.面板.分级表.setCurrentRow(0)
        self.assertTrue(self.面板.删除选中分级())
        self.assertEqual(self.面板.分级表.count(), 1)

        self.面板.标题框.setText("分级片")
        条目 = self.库.取媒体(self.面板.保存())
        self.assertEqual([一条.可读() for 一条 in 条目.分级们], ["US:PG-13"])
        self.assertEqual(条目.取分级().值, "PG-13")

    def test_空分级值不加(self):
        self.面板.分级值框.setText("   ")
        self.assertFalse(self.面板.添加分级())
        self.assertEqual(self.面板.分级表.count(), 0)

    def test_同名分级不重复添加(self):
        self.面板.国家框.setCurrentText("JP")
        self.面板.分级值框.setText("G")
        self.assertTrue(self.面板.添加分级())
        self.面板.国家框.setCurrentText("JP")
        self.面板.分级值框.setText("G")
        self.assertFalse(self.面板.添加分级())
        self.assertEqual(self.面板.分级表.count(), 1)

    def test_对话框保存后取条目(self):
        对话 = 手动资料对话框(self.库, self.缓存)
        self.addCleanup(对话.deleteLater)
        self.assertIsNone(对话.取条目(), "还没保存时应该是 None")
        对话.标题框.setText("对话框新建")          # 字段访问直接转给面板
        媒体id = 对话.保存()
        self.assertIsNotNone(媒体id)
        条目 = 对话.取条目()
        self.assertIsNotNone(条目)
        self.assertEqual(条目.标题, "对话框新建")
        self.assertEqual(self.库.取媒体(媒体id).标题, "对话框新建")

    def test_对话框取消后取条目仍是None(self):
        对话 = 手动资料对话框(self.库, self.缓存)
        self.addCleanup(对话.deleteLater)
        对话.面板.标题框.setText("没点保存")
        对话.reject()
        self.assertIsNone(对话.取条目(), "取消 = 没有结果")
        self.assertEqual(self.库.统计().媒体数, 0, "取消不该写库")

    def test_对话框编辑已有条目(self):
        媒体id = self.存一条剧集()
        对话 = 手动资料对话框(self.库, self.缓存, 媒体id)
        self.addCleanup(对话.deleteLater)
        self.assertEqual(对话.标题框.text(), "老剧名")
        对话.标题框.setText("对话框改名")
        self.assertEqual(对话.保存(), 媒体id)
        读回 = self.库.取媒体(媒体id)
        self.assertEqual(读回.标题, "对话框改名")
        self.assertEqual(读回.汇总集数(), 3, "对话框保存也不许丢季集")


class 不联网测试(手动资料基础):
    def test_源码里没有任何联网调用(self):
        """这条路径的卖点就是"断网也能用"：源码里不该出现网络库或 URL。"""
        import wangpan.ui.手动资料 as 模块
        源码 = Path(模块.__file__).read_text(encoding="utf-8")
        for 词 in ("urllib", "requests", "http.client", "://"):
            self.assertNotIn(词, 源码, f"手动资料不该出现 {词}")


if __name__ == "__main__":
    unittest.main()
