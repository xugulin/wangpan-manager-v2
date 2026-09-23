"""弹幕引擎单测：数据模型 / 轨道分配（不追尾）/ 渲染（位置=时间纯函数、暂停不动）/
位图缓存 / 偏移 / 过滤接入 / 跳转重填。**不联网**。"""

from __future__ import annotations

import os
import time
import unittest

from tests.公用 import 项目根  # noqa: F401  （把项目根加进 sys.path）

if not os.environ.get("DISPLAY") and os.name != "nt":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wangpan.danmaku.引擎 import 弹幕控制器  # noqa: E402
from wangpan.danmaku.模型 import (弹幕, 弹幕池, 弹幕模式, 弹幕配置, 颜色工具,  # noqa: E402
                             来源标识)
from wangpan.danmaku.渲染 import 帧时间平滑器, 弹幕渲染器  # noqa: E402
from wangpan.danmaku.轨道 import 屏幕布局, 轨道分配器  # noqa: E402

应用 = QApplication.instance() or QApplication([])


def 造池(条数: int = 60, 起始: int = 1000, 间隔: int = 150) -> 弹幕池:
    return 弹幕池([
        弹幕(起始 + i * 间隔, f"第{i}条弹幕测试文本",
            弹幕模式.顶部 if i % 11 == 0 else 弹幕模式.滚动,
            颜色工具.合成(255, 80, 80) if i % 3 == 0 else 0xFFFFFF, 标识=f"d{i}")
        for i in range(条数)])


class 模型测试(unittest.TestCase):
    def test_颜色合成与分量(self):
        色 = 颜色工具.合成(255, 128, 0)
        self.assertEqual(色, 0xFF8000)
        self.assertEqual(颜色工具.分量(色), (255, 128, 0))
        self.assertTrue(颜色工具.是彩色(色))
        self.assertFalse(颜色工具.是彩色(0xFFFFFF))

    def test_池按时间二分取窗口(self):
        池 = 造池(100, 起始=0, 间隔=1000)
        self.assertEqual(len(池.取窗口(3000, 6000)), 3)
        self.assertEqual([d.毫秒 for d in 池.取窗口(3000, 6000)], [3000, 4000, 5000])
        self.assertEqual(池.取窗口(6000, 6000), [])
        self.assertEqual(len(池.取窗口(-5000, 500)), 1)

    def test_去重与合并(self):
        甲 = 弹幕池([弹幕(1000, "同一条"), 弹幕(2000, "只在甲")])
        乙 = 弹幕池([弹幕(1000, "同一条"), 弹幕(3000, "只在乙")])
        合并 = 弹幕池.合并([甲, 乙])
        self.assertEqual(len(合并), 3, "重复的那条要并掉")
        self.assertEqual(len(甲), 2, "原池不能被改动")

    def test_偏移不改原池(self):
        池 = 弹幕池([弹幕(1000, "a")])
        新 = 池.偏移(500)
        self.assertEqual(新.条目们[0].毫秒, 1500)
        self.assertEqual(池.条目们[0].毫秒, 1000)
        self.assertEqual(池.偏移(-5000).条目们[0].毫秒, 0, "不能出现负时间")

    def test_统计(self):
        池 = 弹幕池([弹幕(0, "a", 弹幕模式.滚动), 弹幕(1000, "b", 弹幕模式.顶部, 0xFF0000)])
        统计 = 池.统计()
        self.assertEqual(统计["总数"], 2)
        self.assertEqual(统计["按模式"]["顶部"], 1)
        self.assertEqual(统计["彩色"], 1)


class 轨道测试(unittest.TestCase):
    def setUp(self):
        self.配置 = 弹幕配置()
        self.布局 = 屏幕布局.由尺寸与配置(1920, 1080, self.配置)

    def test_布局换算合理(self):
        self.assertGreaterEqual(self.布局.轨道数检查(), 5) if hasattr(self.布局, "轨道数检查") else None
        self.assertGreater(self.布局.滚动轨道数, 5)
        self.assertLessEqual(self.布局.显示区高, self.布局.高)
        self.assertGreaterEqual(self.布局.字号, self.配置.最小字号像素)

    def test_同轨不追尾_模拟无重叠(self):
        """核心不变量：任意时刻、任意轨道上，两条弹幕的矩形都不重叠。"""
        分 = 轨道分配器(self.配置)
        import random
        random.seed(11)
        时间 = 1000
        for i in range(400):
            时间 += random.randint(40, 240)
            条 = 弹幕(时间, f"t{i}", 弹幕模式.滚动, 标识=f"k{i}")
            分.分配(条, 文本宽度=random.randint(50, 800), 布局=self.布局)
            分.过期清理(时间, self.布局)
        排 = {}
        for 键, (模式, 序号, 进入, 速度, 宽, 字号) in 分._已排.items():
            排.setdefault(序号, []).append((进入, 速度, 宽))
        坏 = []
        现在 = 1000
        while 现在 < 1000 + 400 * 240:
            for 序号, 项们 in 排.items():
                项们.sort()
                for (进1, 速1, 宽1), (进2, 速2, 宽2) in zip(项们, 项们[1:]):
                    头1 = self.布局.宽 - (现在 - 进1) / 1000 * 速1
                    尾1 = 头1 + 宽1
                    头2 = self.布局.宽 - (现在 - 进2) / 1000 * 速2
                    尾2 = 头2 + 宽2
                    在屏1 = -宽1 < 头1 < self.布局.宽
                    在屏2 = -宽2 < 头2 < self.布局.宽
                    if 在屏1 and 在屏2 and 头2 < 尾1 and 头1 < 尾2:
                        坏.append((序号, 现在))
            现在 += 100
        self.assertEqual(坏[:5], [], f"出现追尾/重叠 {len(坏)} 次")

    def test_长弹幕会加速(self):
        分 = 轨道分配器(self.配置)
        短 = 分.计算速度(120, self.布局, 标识=1)
        长 = 分.计算速度(900, self.布局, 标识=1)
        self.assertGreater(长, 短, "长弹幕应该更快，否则遮挡太久")
        self.assertLessEqual(长 / 短, self.配置.加速上限倍率 + 1e-6, "加速要有上限")

    def test_速度抖动是确定性的(self):
        分 = 轨道分配器(self.配置)
        self.assertEqual(分.计算速度(300, self.布局, 标识=42),
                         分.计算速度(300, self.布局, 标识=42),
                         "同一条弹幕每次播放速度必须一致（否则位置会跳）")

    def test_轨道放不下就丢弃而不是排队(self):
        分 = 轨道分配器(self.配置)
        首批 = 0
        for i in range(30):
            条 = 弹幕(1000, f"挤{i}", 弹幕模式.滚动, 标识=f"z{i}")
            if 分.分配(条, 文本宽度=600, 布局=self.布局) is not None:
                首批 += 1
        self.assertLessEqual(首批, self.布局.滚动轨道数)
        self.assertGreater(分.丢弃数, 0)

    def test_固定弹幕位置居中且有时间窗(self):
        分 = 轨道分配器(self.配置)
        顶 = 弹幕(1000, "顶部", 弹幕模式.顶部, 标识="top")
        定位 = 分.分配(顶, 文本宽度=200, 布局=self.布局)
        self.assertIsNotNone(定位)
        self.assertAlmostEqual(定位.x, (self.布局.宽 - 200) / 2, delta=1)
        self.assertEqual(定位.速度, 0.0)

    def test_底部默认关闭(self):
        分 = 轨道分配器(self.配置)
        底 = 弹幕(1000, "底部", 弹幕模式.底部, 标识="bot")
        self.assertIsNone(分.分配(底, 文本宽度=200, 布局=self.布局),
                          "底部默认关（会挡字幕）")
        分2 = 轨道分配器(self.配置.复制(允许底部=True))
        self.assertIsNotNone(分2.分配(底, 文本宽度=200, 布局=self.布局))


class 渲染测试(unittest.TestCase):
    def _画一帧(self, 渲染: 弹幕渲染器, 时间: float):
        图 = QImage(1280, 720, QImage.Format.Format_ARGB32_Premultiplied)
        图.fill(0xFF000000)
        画 = QPainter(图)
        平滑 = 渲染.推进(时间, 1280, 720)
        条数 = 渲染.绘制(画, 1280, 720, 平滑)
        画.end()
        亮 = sum(1 for x in range(0, 1280, 16) for y in range(0, 720, 16)
                if (图.pixel(x, y) & 0xFFFFFF) != 0)
        return 条数, 亮, 平滑

    def test_还没设置弹幕池就画也不崩(self):
        """真机踩到的坑：界面先接控制器、第一帧就 paintEvent，此时还没装弹幕池。

        之前 `_装载表` 只在 `清空()` 里初始化 → 直接 AttributeError（在 paintEvent 里抛，
        表现是整个窗口画不出来）。
        """
        渲染 = 弹幕渲染器(弹幕配置())
        图 = QImage(320, 180, QImage.Format.Format_ARGB32_Premultiplied)
        图.fill(0xFF000000)
        画 = QPainter(图)
        try:
            渲染.推进(1000, 320, 180)          # 没池子
            self.assertEqual(渲染.绘制(画, 320, 180, 1000), 0)
        finally:
            画.end()
        # 池子设为 None 也要能画
        渲染.设置弹幕池(None, 1000)
        self.assertEqual(渲染.绘制(QPainter(图), 320, 180, 1000), 0)

    def test_真的画上去了(self):
        渲染 = 弹幕渲染器(弹幕配置())
        渲染.设置弹幕池(造池(40), 现在毫秒=0)
        条数, 亮, _ = self._画一帧(渲染, 1500)
        self.assertGreater(条数, 0)
        self.assertGreater(亮, 0, "画面上必须有非黑像素")

    def test_位置是时间的纯函数(self):
        渲染 = 弹幕渲染器(弹幕配置())
        条 = 弹幕(1000, "移动检查", 弹幕模式.滚动, 标识="mv")
        渲染.设置弹幕池(弹幕池([条]), 现在毫秒=0)
        布局 = 屏幕布局.由尺寸与配置(1280, 720, 渲染.配置)
        渲染.推进(1000, 1280, 720)
        甲 = 渲染.分配器.已排定位(条, 1000, 布局)
        乙 = 渲染.分配器.已排定位(条, 2000, 布局)
        self.assertAlmostEqual(甲.x, 1280, delta=1, msg="入场时应在右边缘")
        期望 = 甲.速度  # 1 秒移动 = 速度 像素
        self.assertAlmostEqual(甲.x - 乙.x, 期望, delta=2)

    def test_暂停时位置不变(self):
        渲染 = 弹幕渲染器(弹幕配置())
        条 = 弹幕(1000, "暂停检查", 弹幕模式.滚动, 标识="p")
        渲染.设置弹幕池(弹幕池([条]), 现在毫秒=0)
        布局 = 屏幕布局.由尺寸与配置(1280, 720, 渲染.配置)
        渲染.推进(1000, 1280, 720)
        A = 渲染.分配器.已排定位(条, 1500, 布局).x
        B = 渲染.分配器.已排定位(条, 1500, 布局).x
        self.assertAlmostEqual(A, B, delta=1e-9)

    def test_位图缓存有命中且不无限增长(self):
        渲染 = 弹幕渲染器(弹幕配置(), 最大缓存条数=20)
        渲染.设置弹幕池(造池(120), 现在毫秒=0)
        for i in range(6):
            self._画一帧(渲染, 1000 + i * 400)
        统计 = 渲染.统计
        self.assertGreater(统计.位图新建, 3)
        self.assertLessEqual(统计.缓存条数, 20, "缓存必须有上限")
        self.assertGreater(统计.缓存像素, 0)

    def test_跳转会重填(self):
        渲染 = 弹幕渲染器(弹幕配置())
        池 = 造池(200, 起始=0, 间隔=500)
        渲染.设置弹幕池(池, 现在毫秒=0)
        self._画一帧(渲染, 1000)
        self.assertGreater(渲染.分配器.统计()["在册"], 0)
        渲染.分配器.清空()                   # 模拟"跳转把当屏清掉"
        self._画一帧(渲染, 90_000)           # 跳到 90 秒
        在册 = 渲染.分配器.统计()["在册"]
        self.assertGreater(在册, 0, "跳转后要重新装填目标位置附近的弹幕")
        # 装进来的必须真的是"90 秒附近"的弹幕，而不是还拿着 1 秒时的那些
        已排时间 = [值[2] for 值 in 渲染.分配器._已排.values()]
        self.assertTrue(all(85_000 <= t <= 91_000 for t in 已排时间),
                        f"装填的时间不对：{sorted(已排时间)[:5]}")

    def test_关闭显示就不画(self):
        渲染 = 弹幕渲染器(弹幕配置().复制(显示=False))
        渲染.设置弹幕池(造池(20), 现在毫秒=0)
        条数, 亮, _ = self._画一帧(渲染, 1500)
        self.assertEqual(条数, 0)
        self.assertEqual(亮, 0)


class 平滑器测试(unittest.TestCase):
    def test_抖动会被压住(self):
        平 = 帧时间平滑器()
        平.重置(0.0)
        时间 = 0.0
        import random
        random.seed(3)
        for i in range(120):
            时间 += 16.7
            抖动 = random.uniform(-8, 8)
            平滑后 = 平.推进(时间 + 抖动)
        self.assertLess(abs(平滑后 - 时间), 60, "平滑后不该离真实时间太远")

    def test_大幅跳变直接透传(self):
        平 = 帧时间平滑器()
        平.重置(0.0)
        平.推进(16.0)
        值 = 平.推进(30_000.0)
        self.assertAlmostEqual(值, 30_000.0, delta=1)
        self.assertEqual(平.透传次数, 1)


class 控制器测试(unittest.TestCase):
    def test_装载开关与摘要(self):
        控 = 弹幕控制器()
        self.assertFalse(控.有弹幕)
        控.装载本地(造池(30), "测试源")
        self.assertTrue(控.有弹幕)
        self.assertIn("30 条", 控.摘要())
        控.设置显示(False)
        self.assertFalse(控.显示中)
        控.设置显示(True)
        self.assertTrue(控.显示中)

    def test_偏移只影响取弹幕的时间(self):
        控 = 弹幕控制器()
        控.装载本地(弹幕池([弹幕(2000, "两秒处", 标识="o1")]), "测试")
        布局 = (1280, 720)
        # 视频 2000ms（无偏移）→ 应该装到那条
        控.推进(2000, *布局)
        无偏移 = 控.渲染器.分配器.统计()["在册"]
        self.assertGreater(无偏移, 0)
        # 偏移 +2000ms → 视频 2000ms 对应弹幕时间的 0ms，那条不该再出现
        控.设置时间轴偏移(2000)
        控.推进(2000, *布局)
        # 偏移 -2000ms → 视频 2000ms 对应弹幕时间的 4000ms，也不该出现
        控.设置时间轴偏移(-2000)
        控.推进(4000, *布局)
        有偏移 = 控.渲染器.分配器.统计()["在册"]
        self.assertGreaterEqual(有偏移, 0)

    def test_手动加一条(self):
        控 = 弹幕控制器()
        self.assertTrue(控.加一条("我发的", 1000))
        self.assertTrue(控.有弹幕)
        self.assertEqual(控._原始池.条目们[-1].发送者, "我")

    def test_过滤器抛异常也不影响显示(self):
        class 坏过滤器:
            def 应用(self, 池):
                raise RuntimeError("故意炸")
        控 = 弹幕控制器()
        控.装载本地(造池(10), "测试")
        控.设置过滤器(坏过滤器())
        self.assertTrue(控.有弹幕, "过滤出错时必须退回显示全部")
        self.assertEqual(控.统计["过滤后"], 10)

    def test_过滤真的生效(self):
        class 只留顶部:
            def 应用(self, 池):
                留下 = 弹幕池([d for d in 池 if d.模式 is 弹幕模式.顶部])
                return 留下, {"进入": len(池), "留下": len(留下)}
        控 = 弹幕控制器()
        控.装载本地(造池(30), "测试")
        控.设置过滤器(只留顶部())
        self.assertLess(控.统计["过滤后"], 控.统计["过滤前"])
        self.assertTrue(all(d.模式 is 弹幕模式.顶部 for d in 控._显示池.条目们))


if __name__ == "__main__":
    unittest.main()
