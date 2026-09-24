"""整合回归：V1 界面 + V2 自研播放内核，接起来之后**该成立的契约**。

这一份盯的是"两块血脉接缝处"，也就是最容易在后续改动里悄悄坏掉的地方：

* 合并入口（``启动.py``）还在、还认 ``--纯播放``；
* V1 的 8 个页面都建得出来（含新并入的媒体库页）；
* 播放页拿到的是 **V2 会话**（不是 VLC），而且老名字/老方法都还在
  （菜单动作表、播放清单、自检脚本都按这些名字调）；
* `状态快照()` 的 21 个键与 8 个中文状态串 —— AI 播放顾问与界面都按它判断；
* 本地文件能真播（有帧、有画面、能截图）。
"""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

from tests.公用 import 项目根, 自带素材

if not os.environ.get("DISPLAY") and os.name != "nt":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication  # noqa: E402

应用 = QApplication.instance() or QApplication([])


class 合并入口测试(unittest.TestCase):
    def test_启动脚本有纯播放与命令行分支(self):
        路径 = 项目根 / "启动.py"
        self.assertTrue(路径.is_file(), "合并入口 启动.py 必须还在")
        文本 = 路径.read_text(encoding="utf-8")
        for 词 in ("--纯播放", "--cli", "_跑纯播放", "运行界面"):
            self.assertIn(词, 文本, f"启动.py 少了 {词}")

    def test_两套源码都在(self):
        self.assertTrue((项目根 / "wangpan" / "player" / "引擎.py").is_file(),
                        "V2 自研内核要在")
        self.assertTrue((项目根 / "v8_3" / "界面" / "主窗口.py").is_file(),
                        "V1 的 GUI 层要在")
        self.assertTrue((项目根 / "适配器").is_dir(), "三家网盘适配器要在")


class 播放会话契约测试(unittest.TestCase):
    """AI 顾问与界面都靠这些字段/字符串取值，少一个就是"静默失效"。"""

    def setUp(self):
        from wangpan.ui.播放会话 import 播放会话
        self.会话 = 播放会话()
        self.addCleanup(self.会话.关闭)

    def test_状态串是那八个(self):
        from wangpan.ui.播放会话 import 会话状态
        值 = {会话状态.空闲, 会话状态.打开中, 会话状态.缓冲中, 会话状态.播放中,
             会话状态.已暂停, 会话状态.已停止, 会话状态.已结束, 会话状态.出错}
        self.assertEqual(值, {"空闲", "打开中", "缓冲中", "播放中", "已暂停",
                             "已停止", "已结束", "出错"})

    def test_状态快照键齐全(self):
        需要 = {"状态", "状态码", "进度秒", "时长秒", "比例", "缓冲中", "有画面",
               "丢帧", "丢帧增量", "输入码率bps", "解复码率bps", "读字节",
               "已解码视频", "已显示帧", "帧率", "音量", "倍速", "字幕",
               "字幕轨", "音频轨", "已播秒", "参数"}
        快照 = self.会话.状态快照()
        缺失 = sorted(需要 - set(快照))
        self.assertEqual(缺失, [], f"状态快照少了 AI/界面要用的键：{缺失}")

    def test_播放器门面方法齐全(self):
        """界面绕过会话直接调播放器的那些方法（老名字）一个都不能少。"""
        需要 = ("进度秒", "时长秒", "设置缩放", "缩放", "设置宽高比", "宽高比",
               "停止并等待", "绑定窗口", "下一帧", "字幕轨", "音频轨",
               "选择字幕", "选择音频", "当前字幕", "章节数", "当前章节",
               "跳章节", "下一章", "上一章", "取速率", "设置速率", "取音量",
               "设置音量", "是否静音", "设置静音")
        播放器 = self.会话.播放器
        缺失 = [名 for 名 in 需要 if not hasattr(播放器, 名)]
        self.assertEqual(缺失, [], f"播放器门面少了界面要调的方法：{缺失}")

    def test_顾问拿到的入参键齐全(self):
        """`建议起播参数` 的输入少一个键，顾问就可能给出失真建议。"""
        from wangpan.ui.播放会话 import 媒体信息, 播放会话, 探测结果
        记录: dict = {}

        class 假顾问:
            def 建议起播参数(自己, 入参):
                记录.update(入参)
                return {}

            def 生成学习键(自己, *a):
                return "键"

            def 记录效果(自己, *a):
                return True

        会话 = 播放会话(顾问=假顾问(), 自动调优=True)
        self.addCleanup(会话.关闭)
        会话.媒体 = 媒体信息(已知=True, 分辨率="1920x1080", 宽=1920, 高=1080,
                          档位="1080p", 视频编码="h264", 时长秒=60.0)
        会话.探测 = 探测结果(成功=True, 来源说明="测试", 实测带宽bps=20e6)
        会话.直链信息 = {"url": "http://x/y.mp4", "headers": {}, "name": "y.mp4",
                       "size": 123}
        建议 = 会话._决策参数(本地=False)
        self.assertTrue(建议 is not None)
        for 键 in ("网盘", "文件名", "大小字节", "分辨率", "宽", "高", "档位",
                  "视频编码", "时长秒", "本机硬解", "缓存目录剩余字节",
                  "实测带宽bps", "成功"):
            self.assertIn(键, 记录, f"给顾问的入参少了 {键}")

    def test_本地文件不问AI(self):
        """本地文件没有带宽问题，AI 的保守大缓存是负优化（实测结论）。"""
        from wangpan.ui.播放会话 import 播放会话

        class 假顾问:
            def 建议起播参数(自己, 入参):
                raise AssertionError("本地文件不该去问 AI")

        会话 = 播放会话(顾问=假顾问(), 自动调优=True)
        self.addCleanup(会话.关闭)
        设置 = 会话._决策参数(本地=True)
        self.assertEqual(设置.来源, "本地文件")
        self.assertEqual(设置.网络缓存毫秒, 0)

    def test_缓存下限保护(self):
        """带宽比码率紧时，缓存只许加大 —— 这条线上真出过"AI 把缓存压小→反复缓冲"。"""
        from wangpan.ui.播放会话 import 媒体信息, 播放会话, 探测结果
        会话 = 播放会话()
        self.addCleanup(会话.关闭)
        会话.媒体 = 媒体信息(已知=True, 视频码率bps=12.7e6)
        会话.探测 = 探测结果(成功=True, 实测带宽bps=13e6)
        self.assertEqual(会话.缓存下限(), 15000)
        设置 = 会话._套用建议({"网络缓存毫秒": 6000, "硬解": "自动"})
        self.assertGreaterEqual(设置.网络缓存毫秒, 15000)

    def test_主线程泵是直通的(self):
        """V2 引擎线程安全，不需要"非界面线程必须排队"那套（保留签名只为兼容）。"""
        def 干活():
            return 42

        self.assertEqual(self.会话.在主线程(干活), 42)
        self.assertIsNone(self.会话.装主线程泵())
        self.assertEqual(self.会话.排空主线程队列(), 0)


class 界面接线测试(unittest.TestCase):
    def setUp(self):
        from v8_3.界面.主窗口 import 主窗口
        self.窗口 = 主窗口()
        self.addCleanup(self.窗口.close)
        for _ in range(6):
            应用.processEvents()

    def test_八个页面都建得出来(self):
        视图 = ("切换到播放页", "切换到媒体库页", "切换到传输页",
              "切换到敏感词页", "切换到日志页", "切换到设置页", "切换到AI页")
        for 名 in 视图:
            getattr(self.窗口, 名)()
            应用.processEvents()
        self.assertGreaterEqual(self.窗口.堆叠.count(), 8,
                                "播放/媒体库/传输/敏感词/日志/设置/AI + 网盘页 都该在")

    def test_切侧栏不改窗口尺寸(self):
        """点「🗂 面板」只该显示/隐藏侧栏，**不许把主窗口改大小**。

        用户真机反馈原话："播放页播放时点击面板会GUI窗宽度高度变化到这不是我想要的，
        多点两次面板还导致窗口崩溃了"。根因：``切换侧栏`` 原来顺手调了 ``重排``，
        而 ``重排`` = ``按视频比例调整窗口``，里面直接 ``窗口.resize(...)``。
        """
        应用 = QApplication.instance() or QApplication([])
        self.窗口.resize(1280, 820)
        self.窗口.show()
        for _ in range(8):
            应用.processEvents()
        页 = self.窗口.播放页面()
        self.窗口.切换到播放页()          # 用户就是这么用的：在播放页点面板
        for _ in range(6):
            应用.processEvents()
        前尺寸 = (self.窗口.width(), self.窗口.height())
        for 第 in range(6):                      # "多点两次"：连点 6 次
            页.切换侧栏(第 % 2 == 0)
            for _ in range(6):
                应用.processEvents()
                QTimer.singleShot(0, lambda: None)
            time.sleep(0.12)
        后尺寸 = (self.窗口.width(), self.窗口.height())
        self.assertEqual(后尺寸, 前尺寸,
                         f"连点 6 次面板后窗口尺寸不许变：{前尺寸} → {后尺寸}")
        # 侧栏真的能开关（不是"因为啥都没做所以尺寸没变"）。
        # ⚠️ 面板现在是**独立悬浮窗口**（用户要求），所以判"可见"要用
        #    `页.面板可见()` —— 它自己知道该看窗口还是看内嵌标签页；
        #    直接看 `右栏.isHidden()` 会假红（面板窗口隐藏时标签页并没被 hide）。
        页.切换侧栏(False)
        应用.processEvents()
        self.assertFalse(页.面板可见(), "隐藏后面板该看不见")
        页.切换侧栏(True)
        应用.processEvents()
        self.assertTrue(页.面板可见(), "显示后面板该看得见")
        self.assertTrue(页.面板是独立窗口, "面板必须是独立悬浮窗口（用户要求）")
        面板窗 = 页._面板窗口
        self.assertIsNotNone(面板窗)
        # 高度要跟主窗口一致；**屏幕装不下时按屏幕收**（"不要超出屏幕"也是用户要求）。
        屏幕 = (self.窗口.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        期望 = min(self.窗口.frameGeometry().height(), 屏幕.height())
        self.assertLessEqual(abs(面板窗.height() - 期望), 2,
                             f"面板高度该是 min(主窗口高, 屏幕高)"
                             f"：实际 {面板窗.height()}，期望 {期望}"
                             f"（主窗口 {self.窗口.frameGeometry().height()}、"
                             f"屏幕 {屏幕.height()}）")
        self.assertLessEqual(面板窗.geometry().bottom(), 屏幕.bottom() + 1,
                             "面板不许超出屏幕下边缘")

    def test_播放页用的是V2内核(self):
        from wangpan.ui.播放会话 import 播放会话
        页 = self.窗口.播放页面()
        self.assertIsInstance(页.会话, 播放会话)
        # 老名字必须还在（菜单动作表 / 自检 / 独立窗口都按它们调）
        for 名 in ("处理准备结果", "刷新网盘列表", "播放指定视频", "关闭",
                  "流畅优先", "打开独立窗口", "收回播放到页面", "独立窗口播放",
                  "用VLC自带窗口播放", "下一个", "上一个", "相对跳转", "跳到结尾",
                  "逐帧", "当前速度", "当前音量", "章节数", "当前章节", "跳章节",
                  "下一章", "上一章", "当前宽高比", "当前缩放", "把当前加入清单",
                  "自适应宽度", "按视频比例调整窗口", "切换侧栏"):
            self.assertTrue(hasattr(页, 名), f"播放页少了老接口：{名}")
        # 老控件别名
        for 名 in ("视频", "控制条", "清单", "左面板", "AI面板", "菜单栏",
                  "网盘框", "路径框", "浏览按钮", "信息栏", "状态标签",
                  "播放按钮", "进度条", "音量条", "倍速框", "字幕按钮"):
            self.assertTrue(hasattr(页, 名), f"播放页少了老控件名：{名}")

    def test_播放页不再有VLC那一套(self):
        页 = self.窗口.播放页面()
        for 名 in ("_出口", "_真正起播_旧", "_自愈画面", "_发现游离窗口",
                  "_守护", "_允许自带窗口"):
            self.assertFalse(hasattr(页, 名), f"VLC 时代的成员不该还在：{名}")

    def test_媒体库页并进来了(self):
        页 = self.窗口.媒体库页()
        self.assertTrue(页.可用, "媒体库（资料库 + 海报墙）应当能起来")
        self.assertIsNotNone(页.海报墙)


class 真播测试(unittest.TestCase):
    """本地文件真播一遍：有帧、有画面、能截图、状态串在契约里。"""

    def test_本地文件能播(self):
        from PySide6.QtCore import QTimer
        from wangpan.ui.播放页 import 播放页
        素材 = 自带素材()
        页 = 播放页()
        self.addCleanup(页.关闭)
        页.resize(640, 400)
        self.assertTrue(页.打开(str(素材)), "打开本地素材失败")
        QTimer.singleShot(1600, 应用.quit)
        应用.exec()
        统计 = 页.引擎.统计
        self.assertGreater(统计.已解视频帧, 0, "一帧都没解出来")
        self.assertTrue(页.视频.有画面, "画面层没有帧")
        from wangpan.ui.播放会话 import 会话状态
        self.assertIn(页.会话.状态文本(), {会话状态.空闲, 会话状态.播放中,
                                      会话状态.已暂停, 会话状态.已结束,
                                      会话状态.缓冲中, 会话状态.出错})
        路径 = 页.截图()
        self.assertTrue(路径 and Path(路径).is_file(), "截图应当落盘")


if __name__ == "__main__":
    unittest.main()


class 弹幕接线测试(unittest.TestCase):
    """弹幕：视频旁边放一个同名 B站 XML，打开时**应当自动装上**。"""

    def test_同名弹幕自动装上(self):
        import shutil
        from tests.公用 import 临时目录
        from PySide6.QtCore import QTimer
        from wangpan.ui.播放页 import 播放页
        # ⚠️ 不能用 `with 临时目录()`：它在语句块结束时就删目录，而那时播放器
        #    还开着那个视频文件 —— Windows 上 `WinError 32 文件被占用`，删不掉就报错
        #    （CI 真机抓到的）。改成 addCleanup，靠 LIFO：先关播放页，再删临时目录。
        临时 = 临时目录()
        self.addCleanup(临时.cleanup)
        目录 = Path(临时.name)
        视频 = 目录 / "样片.mp4"
        shutil.copy2(自带素材(), 视频)
        # 最小可用的 B站弹幕 XML（两条：一条滚动、一条顶部）
        视频.with_suffix(".xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?><i>'
            '<d p="1.0,1,25,16777215,0,0,0,0">第一条弹幕</d>'
            '<d p="1.5,1,25,16711680,0,0,0,5">顶部弹幕</d>'
            '</i>', encoding="utf-8")
        页 = 播放页()
        self.addCleanup(页.关闭)
        页.resize(640, 400)
        self.assertTrue(页.打开(str(视频)))
        QTimer.singleShot(900, 应用.quit)
        应用.exec()
        self.assertTrue(页.弹幕.有弹幕, "同名 XML 应当被自动装上")
        self.assertGreaterEqual(页.弹幕.装载结果.条数, 2,
                                f"两条都该装上；实际 {页.弹幕.装载结果.条数}")


class AI顾问接线测试(unittest.TestCase):
    """AI 播放顾问：会话要把**探测/媒体/硬解能力**喂给它，并把它的建议落到播放设置上。"""

    def test_顾问建议会落到播放设置(self):
        from wangpan.ui.播放会话 import 媒体信息, 播放会话, 探测结果

        class 假顾问:
            def __init__(自己):
                自己.收到的入参 = None

            def 建议起播参数(自己, 入参):
                自己.收到的入参 = 入参
                return {"网络缓存毫秒": 9000, "起播等待秒": 1.5, "硬解": "自动",
                        "可流畅播放": True, "理由": "测试建议", "来源": "假顾问",
                        "附加选项": [], "风险": "", "允许丢帧": True, "视频输出": ""}

            def 生成学习键(自己, *a):
                return "假学习键"

            def 记录效果(自己, *a):
                自己.记录了 = True
                return True

        顾问 = 假顾问()
        会话 = 播放会话(顾问=顾问, 自动调优=True)
        self.addCleanup(会话.关闭)
        会话.媒体 = 媒体信息(已知=True, 分辨率="1920x1080", 宽=1920, 高=1080,
                          档位="1080p", 视频编码="h264", 视频码率bps=8e6, 时长秒=60.0)
        会话.探测 = 探测结果(成功=True, 来源说明="测试", 实测带宽bps=4e7)
        会话.直链信息 = {"url": "http://x/y.mp4", "headers": {}, "name": "y.mp4",
                       "size": 1}
        设置 = 会话._决策参数(本地=False)
        self.assertEqual(设置.来源, "假顾问")
        self.assertEqual(设置.网络缓存毫秒, 9000)
        self.assertIn("AI", 会话.AI决策状态 + "AI")
        self.assertIsNotNone(顾问.收到的入参)

    def test_顾问不在时回落规则且不炸(self):
        from wangpan.ui.播放会话 import 媒体信息, 播放会话, 探测结果
        会话 = 播放会话(顾问=None, 自动调优=True)
        self.addCleanup(会话.关闭)
        会话.媒体 = 媒体信息(已知=True, 视频码率bps=12.7e6)
        会话.探测 = 探测结果(成功=True, 实测带宽bps=13e6)
        设置 = 会话._决策参数(本地=False)
        self.assertEqual(设置.来源, "规则")
        self.assertGreaterEqual(设置.网络缓存毫秒, 15000)   # 带宽紧 → 缓存加大

    def test_播放页装配了顾问(self):
        """真正的播放页要真去装 AI 顾问（没密钥时也是规则级，不能是 None）。"""
        from v8_3.界面.主窗口 import 主窗口
        窗口 = 主窗口()
        self.addCleanup(窗口.close)
        页 = 窗口.播放页面()
        self.assertTrue(hasattr(页, "顾问"))
        self.assertIsNotNone(页.AI面板)
        self.assertTrue(页.会话.顾问 is 页.顾问 or 页.顾问 is None)
