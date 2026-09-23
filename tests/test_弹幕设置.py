"""弹幕设置面板测试（离屏，不联网）。

覆盖三件事：
1. 观感/时间轴/过滤三类控件**立刻**把值写进配置与规则（"取配置()永远是最新的"）；
2. 连续拖动只发有限次信号（60ms 合并），但第一次必须立刻发（点了就有反应）；
3. 规则的导入导出往返一致，坏文件只提示、不崩、不改当前规则。
"""

from __future__ import annotations

import json
import time
import unittest
from dataclasses import asdict
from pathlib import Path

from tests.公用 import 临时目录

from PySide6.QtWidgets import QApplication

from wangpan.danmaku.模型 import 弹幕配置
from wangpan.danmaku.过滤 import 过滤规则, 默认规则
from wangpan.ui.弹幕设置 import 弹幕设置面板

应用 = QApplication.instance() or QApplication([])


def 等信号(条件, 超时毫秒: int = 800) -> bool:
    """泵事件直到条件成立。

    面板的信号走 60ms 合并，光 sleep 不泵事件永远等不到定时器回调。
    """
    截止 = time.time() + 超时毫秒 / 1000.0
    while time.time() < 截止:
        应用.processEvents()
        if 条件():
            return True
        time.sleep(0.005)
    return bool(条件())


class 弹幕设置基础(unittest.TestCase):
    def setUp(self) -> None:
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)
        self.面板 = 弹幕设置面板()
        self.addCleanup(self.面板.deleteLater)


class 构造与装载测试(弹幕设置基础):
    def test_单独构造不崩(self):
        面板 = 弹幕设置面板()
        self.addCleanup(面板.deleteLater)
        self.assertIsInstance(面板.取配置(), 弹幕配置)
        self.assertIsInstance(面板.取规则(), 过滤规则)
        self.assertTrue(面板.取配置().显示)

    def test_传入配置与规则会被复制(self):
        配置 = 弹幕配置()
        规则 = 默认规则()
        面板 = 弹幕设置面板(配置=配置, 规则=规则)
        self.addCleanup(面板.deleteLater)
        面板.速度滑块.setValue(400)
        面板.屏蔽词框.setPlainText("加群")
        self.assertEqual(配置.速度像素每秒, 280.0, "面板改的是自己的副本，宿主那份不动")
        self.assertEqual(规则.屏蔽词, [], "同上：规则也要复制")
        self.assertEqual(面板.取配置().速度像素每秒, 400.0)
        self.assertEqual(面板.取规则().屏蔽词, ["加群"])

    def test_装载配置回填控件且不发信号(self):
        配置信号: list = []
        规则信号: list = []
        self.面板.配置变化.connect(配置信号.append)
        self.面板.规则变化.connect(规则信号.append)
        新配置 = 弹幕配置().复制(速度像素每秒=333.0, 不透明度=0.5, 字号比例=0.05,
                            显示区比例=0.3, 安全间距像素=20.0, 显示=False,
                            允许底部=True, 时间轴偏移毫秒=-1500)
        self.面板.装载配置(新配置)
        self.面板.装载规则(默认规则().复制(屏蔽词=["加群"], 最短文本=2))

        self.assertEqual(配置信号, [], "装载不是用户操作，不该发信号")
        self.assertEqual(规则信号, [])
        self.assertEqual(self.面板.取配置(), 新配置, "取配置要等于装载进来的那份")
        self.assertEqual(self.面板.速度滑块.value(), 333)
        self.assertEqual(self.面板.安全间距框.value(), 20)
        self.assertFalse(self.面板.显示勾.isChecked())
        self.assertTrue(self.面板.底部勾.isChecked())
        self.assertEqual(self.面板.偏移滑块.value(), -1500)
        self.assertEqual(self.面板.屏蔽词框.toPlainText(), "加群")
        self.assertEqual(self.面板.最短文本框.value(), 2)

    def test_装载后控件改动能盖掉旧值(self):
        self.面板.装载配置(弹幕配置().复制(速度像素每秒=100.0))
        self.面板.速度滑块.setValue(500)
        self.assertEqual(self.面板.取配置().速度像素每秒, 500.0)

    def test_界面上没暴露的配置字段不会丢(self):
        """界面只摆了一部分字段；冷门字段（停留时长/抖动/同屏上限…）必须原样留着，
        否则"进设置页看一眼"就会把引擎精调过的参数打回默认值。"""
        原 = 弹幕配置().复制(固定停留毫秒=8000, 抖动比例=0.2, 同屏上限=120,
                          回溯装填毫秒=15000, 加速上限倍率=2.5, 描边宽度比例=0.2,
                          轨道间距比例=1.4, 长弹幕加速=False, 加速指数=0.5,
                          跳转重填阈值毫秒=5000)
        self.面板.装载配置(原)
        self.面板.速度滑块.setValue(500)
        现在 = self.面板.取配置()
        self.assertEqual(现在.速度像素每秒, 500.0, "界面上的字段要跟着改")
        self.assertEqual(现在.固定停留毫秒, 8000)
        self.assertEqual(现在.同屏上限, 120)
        self.assertEqual(现在.回溯装填毫秒, 15000)
        self.assertFalse(现在.长弹幕加速)
        self.assertAlmostEqual(现在.抖动比例, 0.2, places=4)
        self.assertAlmostEqual(现在.描边宽度比例, 0.2, places=4)
        self.assertAlmostEqual(现在.加速上限倍率, 2.5, places=4)


class 观感测试(弹幕设置基础):
    def test_速度滑块改配置并立刻发信号(self):
        收到: list = []
        self.面板.配置变化.connect(收到.append)
        self.面板.速度滑块.setValue(400)
        self.assertTrue(收到, "第一次改动要立刻发（不能等防抖）")
        self.assertIsInstance(收到[0], 弹幕配置)
        self.assertEqual(收到[0].速度像素每秒, 400.0)
        self.assertEqual(self.面板.取配置().速度像素每秒, 400.0, "取配置不用等信号")
        self.assertIn("400", self.面板.速度值标签.text())

    def test_速度滑块范围是60到600(self):
        self.assertEqual(self.面板.速度滑块.minimum(), 60)
        self.assertEqual(self.面板.速度滑块.maximum(), 600)
        self.面板.速度滑块.setValue(9999)
        self.assertEqual(self.面板.速度滑块.value(), 600)

    def test_字号比例换算文案(self):
        说明 = self.面板.字号值标签.text()
        self.assertIn("1080p", 说明, "比例太抽象，要告诉用户 1080p 下是多少像素")
        self.assertIn("px", 说明)
        self.assertIn("40", 说明, "默认 3.7% × 1080 ≈ 40px")
        self.面板.字号滑块.setValue(60)          # 6% → 64.8 → 65px
        self.assertIn("65", self.面板.字号值标签.text())
        self.assertAlmostEqual(self.面板.取配置().字号比例, 0.06, places=4)

    def test_连续性滑块都写进配置(self):
        self.面板.不透明度滑块.setValue(50)
        self.面板.显示区滑块.setValue(30)
        self.面板.安全间距框.setValue(22)
        配置 = self.面板.取配置()
        self.assertAlmostEqual(配置.不透明度, 0.5, places=3)
        self.assertAlmostEqual(配置.显示区比例, 0.3, places=3)
        self.assertAlmostEqual(配置.安全间距像素, 22.0, places=3)
        self.assertIn("50%", self.面板.不透明度值标签.text())
        self.assertIn("30%", self.面板.显示区值标签.text())

    def test_允许底部默认关闭并提示挡字幕(self):
        self.assertFalse(self.面板.底部勾.isChecked(), "底部弹幕会挡字幕，默认必须关")
        self.assertFalse(self.面板.取配置().允许底部)
        self.assertIn("字幕", self.面板.底部提示.text())
        self.面板.底部勾.setChecked(True)
        self.assertTrue(self.面板.取配置().允许底部)

    def test_显示开关与彩色顶部底部(self):
        self.面板.显示勾.setChecked(False)
        self.面板.彩色勾.setChecked(False)
        self.面板.顶部勾.setChecked(False)
        配置 = self.面板.取配置()
        self.assertFalse(配置.显示)
        self.assertFalse(配置.显示彩色)
        self.assertFalse(配置.允许顶部)

    def test_连续拖动只发有限次信号(self):
        """拖一次滑块最多惊动宿主两次（第一次立刻 + 60ms 合并的尾一次）。"""
        收到: list = []
        self.面板.配置变化.connect(收到.append)
        滑块 = self.面板.速度滑块
        for 值 in range(300, 340):           # 模拟拖动中的 40 次变化
            滑块.setValue(值)
        self.assertEqual(len(收到), 1, "拖动过程中只该有第一次的立即发送")
        等信号(lambda: len(收到) > 1, 400)   # 松开后尾部的合并发送
        self.assertLessEqual(len(收到), 3, "40 次变化不该发 40 个信号")
        self.assertGreaterEqual(len(收到), 1)
        self.assertEqual(收到[-1].速度像素每秒, 339.0, "尾一次带的必须是最新值")


class 时间轴测试(弹幕设置基础):
    def test_加半秒与减半秒(self):
        self.面板.加半秒按钮.click()
        self.assertEqual(self.面板.取配置().时间轴偏移毫秒, 500)
        self.assertIn("+500", self.面板.偏移值标签.text())
        self.面板.加半秒按钮.click()
        self.assertEqual(self.面板.取配置().时间轴偏移毫秒, 1000)
        self.面板.减半秒按钮.click()
        self.面板.减半秒按钮.click()
        self.面板.减半秒按钮.click()
        self.assertEqual(self.面板.取配置().时间轴偏移毫秒, -500)
        self.assertEqual(self.面板.偏移滑块.value(), -500)

    def test_归零按钮(self):
        self.面板.偏移滑块.setValue(-1234)
        self.assertEqual(self.面板.取配置().时间轴偏移毫秒, -1234)
        self.面板.归零按钮.click()
        self.assertEqual(self.面板.取配置().时间轴偏移毫秒, 0)
        self.assertIn("不偏移", self.面板.偏移值标签.text())

    def test_偏移滑块范围与夹住(self):
        self.assertEqual(self.面板.偏移滑块.minimum(), -30000)
        self.assertEqual(self.面板.偏移滑块.maximum(), 30000)
        self.assertEqual(self.面板.调偏移(999999), 30000, "越界要被夹住")
        self.assertEqual(self.面板.取配置().时间轴偏移毫秒, 30000)

    def test_偏移标签带秒数(self):
        self.面板.偏移滑块.setValue(2500)
        self.assertIn("+2500", self.面板.偏移值标签.text())
        self.assertIn("+2.5", self.面板.偏移值标签.text())


class 过滤与规则测试(弹幕设置基础):
    def test_多行解析忽略空行与空白(self):
        self.面板.屏蔽词框.setPlainText("  加群  \n\n关注\n\n   \n")
        self.assertEqual(self.面板.取规则().屏蔽词, ["加群", "关注"])
        self.面板.发送者框.setPlainText("甲\n\n 乙 ")
        self.assertEqual(self.面板.取规则().屏蔽发送者, ["甲", "乙"])
        self.面板.关键词框.setPlainText("哈哈\n\n 233 ")
        self.assertEqual(self.面板.取规则().只显示含关键词, ["哈哈", "233"])
        self.面板.正则框.setPlainText("^\\d+$\n\nabc")
        self.assertEqual(self.面板.取规则().正则们, ["^\\d+$", "abc"])

    def test_规则变化信号(self):
        收到: list = []
        self.面板.规则变化.connect(收到.append)
        self.面板.屏蔽词框.setPlainText("加群")
        self.assertTrue(收到, "第一次改动立刻发")
        self.assertIsInstance(收到[0], 过滤规则)
        self.assertEqual(收到[0].屏蔽词, ["加群"])

    def test_非法正则标红提示但不崩(self):
        self.面板.正则框.setPlainText("([\n好人")
        提示 = self.面板.正则提示.text()
        self.assertIn("用不了", 提示)
        self.assertIn("([", 提示)
        self.assertEqual(self.面板.取规则().正则们, ["([", "好人"],
                         "坏正则留在规则里（判定时会被跳过），不阻止其它设置保存")
        self.assertIn("border", self.面板.正则框.styleSheet(), "标红：给输入框加红边框")
        # 改回合法正则：提示要恢复（不能一直红着）
        self.面板.正则框.setPlainText("好人")
        self.assertNotIn("用不了", self.面板.正则提示.text())

    def test_四个屏蔽勾写进规则(self):
        for 勾 in (self.面板.屏蔽顶部勾, self.面板.屏蔽底部勾,
                   self.面板.屏蔽滚动勾, self.面板.屏蔽彩色勾):
            勾.setChecked(True)
        规则 = self.面板.取规则()
        self.assertTrue(规则.屏蔽顶部)
        self.assertTrue(规则.屏蔽底部)
        self.assertTrue(规则.屏蔽滚动)
        self.assertTrue(规则.屏蔽彩色)
        self.assertTrue(规则.有实质规则())

    def test_长度与时间窗写进规则(self):
        self.面板.最短文本框.setValue(2)
        self.面板.最长文本框.setValue(30)
        self.面板.最小秒框.setValue(3.5)
        self.面板.最大秒框.setValue(120.0)
        规则 = self.面板.取规则()
        self.assertEqual(规则.最短文本, 2)
        self.assertEqual(规则.最长文本, 30)
        self.assertAlmostEqual(规则.最小秒, 3.5, places=3)
        self.assertAlmostEqual(规则.最大秒, 120.0, places=3)

    def test_去重勾(self):
        self.assertTrue(self.面板.去重勾.isChecked(), "默认去重")
        self.面板.去重勾.setChecked(False)
        self.assertFalse(self.面板.取规则().去重)

    def test_启用正则过滤属于配置(self):
        配置信号: list = []
        规则信号: list = []
        self.面板.配置变化.connect(配置信号.append)
        self.面板.规则变化.connect(规则信号.append)
        self.面板.启用正则勾.setChecked(False)
        self.assertTrue(配置信号, "它在数据模型里是配置字段")
        self.assertEqual(规则信号, [])
        self.assertFalse(self.面板.取配置().启用正则过滤)


class 规则导入导出测试(弹幕设置基础):
    def _配一套规则(self) -> None:
        self.面板.屏蔽词框.setPlainText("加群\n关注")
        self.面板.正则框.setPlainText("\\d{3,}")
        self.面板.发送者框.setPlainText("水军甲")
        self.面板.关键词框.setPlainText("哈哈")
        self.面板.屏蔽顶部勾.setChecked(True)
        self.面板.屏蔽彩色勾.setChecked(True)
        self.面板.最短文本框.setValue(3)
        self.面板.最长文本框.setValue(50)
        self.面板.最小秒框.setValue(5.0)
        self.面板.最大秒框.setValue(600.0)
        self.面板.去重勾.setChecked(False)

    def test_导出是合法JSON(self):
        文本 = self.面板.导出规则()
        数据 = json.loads(文本)
        self.assertIsInstance(数据, dict)
        self.assertIn("规则", 数据)

    def test_导出导入往返后规则一致(self):
        self._配一套规则()
        文本 = self.面板.导出规则()
        另一个 = 弹幕设置面板()
        self.addCleanup(另一个.deleteLater)
        self.assertTrue(另一个.导入规则(文本))
        self.assertEqual(asdict(另一个.取规则()), asdict(self.面板.取规则()))
        self.assertEqual(另一个.屏蔽词框.toPlainText().splitlines(), ["加群", "关注"])
        self.assertTrue(另一个.屏蔽顶部勾.isChecked())
        self.assertFalse(另一个.去重勾.isChecked())

    def test_导入坏JSON返回False且规则不变(self):
        规则前 = asdict(self.面板.取规则())
        self.assertFalse(self.面板.导入规则("{这不是 JSON"))
        self.assertEqual(asdict(self.面板.取规则()), 规则前, "坏文件不该改掉当前规则")
        self.assertIn("导入失败", self.面板.规则提示标签.text())
        self.assertFalse(self.面板.导入规则(""))
        self.assertFalse(self.面板.导入规则("[1, 2, 3]"), "顶层不是对象也要拒绝")

    def test_导出到文件再导入(self):
        落点 = self.根 / "子目录" / "弹幕规则.json"
        self._配一套规则()
        self.assertTrue(self.面板.导出到文件(落点))
        self.assertTrue(落点.is_file(), "没写文件出来？")
        数据 = json.loads(落点.read_text(encoding="utf-8"))
        self.assertIn("规则", 数据)

        另一个 = 弹幕设置面板()
        self.addCleanup(另一个.deleteLater)
        self.assertTrue(另一个.从文件导入(落点))
        self.assertEqual(asdict(另一个.取规则()), asdict(self.面板.取规则()))

    def test_从文件导入坏文件不崩(self):
        坏 = self.根 / "坏.json"
        坏.write_text("{不是 JSON", encoding="utf-8")
        self.assertFalse(self.面板.从文件导入(坏))
        self.assertFalse(self.面板.从文件导入(self.根 / "根本没有.json"))
        self.assertIn("读不了文件", self.面板.规则提示标签.text())

    def test_导入会发规则变化信号(self):
        文本 = self.面板.导出规则()
        另一个 = 弹幕设置面板()
        self.addCleanup(另一个.deleteLater)
        收到: list = []
        另一个.规则变化.connect(收到.append)
        self.assertTrue(另一个.导入规则(文本))
        self.assertTrue(收到, "导入是明确的用户动作，立刻发")
        self.assertEqual(收到[-1].去重, self.面板.取规则().去重)


class 离屏渲染测试(弹幕设置基础):
    def test_面板离屏渲染不是空白(self):
        面板 = 弹幕设置面板()
        self.addCleanup(面板.deleteLater)
        面板.resize(820, 900)
        面板.show()
        应用.processEvents()
        图 = 面板.grab().toImage()
        self.assertGreater(图.width(), 10)
        self.assertGreater(图.height(), 10)

        颜色计数: dict[str, int] = {}
        for x in range(0, 图.width(), 7):
            for y in range(0, 图.height(), 7):
                名 = 图.pixelColor(x, y).name()
                颜色计数[名] = 颜色计数.get(名, 0) + 1
        背景 = max(颜色计数, key=lambda 键: 颜色计数[键])
        非背景 = sum(数 for 色, 数 in 颜色计数.items() if 色 != 背景)
        self.assertGreater(非背景, 0, f"面板画出来是一张纯色图（{背景}），控件没渲染出来")
        面板.hide()


if __name__ == "__main__":
    unittest.main()
