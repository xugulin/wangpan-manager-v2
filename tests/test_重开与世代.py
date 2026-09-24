"""重开与世代号：**播放中途重开**不许崩、不许泄漏、不许让老线程碰新资源。

为什么单独开一份测试（真机 coredump 的教训）
============================================
用户真机三次崩溃，两次是 SIGABRT、崩溃线程 `V2-解封装`、栈在 libavcodec 内部
（``Assertion fctx->async_lock failed at libavcodec/pthread_frame.c:171``）。
复现与定位的结论写进了 :mod:`wangpan.player.引擎` 的模块文档，压测脚本是
``工具/重开压力测试.py``。这里把**能在纯逻辑层测的规矩**钉死：

* ``打开`` 每次自增世代号，老线程发现"我不是当前代"就退出（``_该停`` 为真）；
* 跳转**不再由解封装线程冲刷解码器**（那是 abort 的成因），冲刷交给解码线程自己；
* ``停止`` 必须等到线程死透才释放；**等不到就一律不释放**（宁可泄漏，不许悬垂）；
* 重复 ``打开/停止`` 与"两个线程同时重开"都不许留下活着的线程；
* AI 自动重载要**去抖**，并且**在界面不安全时排队**（不硬插一次打开）。

崩溃型的问题（进程级 abort）单测测不出来 —— 那是 ``工具/重开压力测试.py`` 的活。
"""

from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path

from tests.公用 import 临时目录, 造素材

from wangpan.player.引擎 import _播放代, 播放引擎, 播放状态
from wangpan.ui.播放会话 import 播放会话, 媒体信息, 探测结果


class 假输入:
    """假输入：只记录"谁调过我"，不碰任何 libav（纯逻辑测试用）。"""

    def __init__(self) -> None:
        self.跳转过: list[float] = []
        self.关闭次数 = 0
        self.释放新包次数 = 0
        self.中断回调 = None

    def 跳转(self, 秒: float, 流序号=None) -> bool:
        self.跳转过.append(float(秒))
        return True

    def 关闭(self) -> None:
        self.关闭次数 += 1

    def 释放新包(self, 指针) -> None:
        self.释放新包次数 += 1

    def 释放包(self) -> None:
        pass


class 假解码器:
    """假解码器：记录冲刷次数与"是哪个线程冲刷的"。"""

    def __init__(self) -> None:
        self.冲刷次数 = 0
        self.冲刷线程们: list[str] = []
        self.关闭次数 = 0
        self.用锁 = threading.RLock()
        self._已冲刷世代 = 0

    def 冲刷(self) -> None:
        self.冲刷次数 += 1
        self.冲刷线程们.append(threading.current_thread().name)

    def 送包(self, _包) -> int:
        return 0

    def 送空包(self) -> int:
        return 0

    def 收帧(self) -> int:
        return -1

    def 取帧(self):
        return None

    def 关(self) -> None:
        self.关闭次数 += 1


class 跳转不许跨线程冲刷测试(unittest.TestCase):
    """``_执行跳转`` 跑在解封装线程里，它**不许**碰解码器。

    这是 22:04:08 崩溃的根因：``avcodec_flush_buffers`` 与解码线程的
    ``avcodec_send_packet`` 并发 → libav 断言 abort 整个进程。
    """

    def setUp(self):
        self.引擎 = 播放引擎()
        self.代 = _播放代(1)
        self.代.输入 = 假输入()
        self.代.视频 = 假解码器()
        self.代.音频 = 假解码器()
        self.代.时钟 = self.引擎.时钟
        self.引擎._本代 = self.代

    def test_执行跳转只动输入不动解码器(self):
        self.引擎._执行跳转(self.代, 12.0)
        self.assertEqual(self.代.输入.跳转过, [11.95], "跳转要落在目标点稍前一点")
        self.assertEqual(self.代.视频.冲刷次数, 0,
                         "解封装线程绝不许冲刷视频解码器（跨线程 flush = abort）")
        self.assertEqual(self.代.音频.冲刷次数, 0, "音频同理")
        self.assertEqual(self.代.流世代, 1, "跳转要把流世代 +1（通知解码线程自己冲刷）")

    def test_按需冲刷由解码线程自己做_且同世代只做一次(self):
        解码器 = 假解码器()
        self.引擎._按需冲刷(self.代, 解码器, 1)
        self.assertEqual(解码器.冲刷次数, 1)
        self.assertEqual(解码器.冲刷线程们, [threading.current_thread().name])
        self.引擎._按需冲刷(self.代, 解码器, 1)
        self.assertEqual(解码器.冲刷次数, 1, "同一个流世代不许重复冲刷")
        self.引擎._按需冲刷(self.代, 解码器, 2)
        self.assertEqual(解码器.冲刷次数, 2, "新一代流世代要再冲刷一次")


class 世代失效测试(unittest.TestCase):
    """世代号：老线程必须"发现自己不是当前代"就退出。"""

    def setUp(self):
        self.引擎 = 播放引擎()
        self.代 = _播放代(1)
        self.引擎._本代 = self.代

    def test_当前代不叫停(self):
        self.assertFalse(self.引擎._该停(self.代))

    def test_换了一代老线程立刻该停(self):
        self.引擎._代 = 2
        self.引擎._本代 = _播放代(2)
        self.assertTrue(self.引擎._该停(self.代),
                        "老线程必须立刻知道自己不是当前代（否则会去碰新资源）")

    def test_停止标志也能叫停(self):
        self.代.停.set()
        self.assertTrue(self.引擎._该停(self.代))

    def test_没有当前代时一律该停(self):
        self.引擎._本代 = None
        self.assertTrue(self.引擎._该停(self.代))
        self.assertTrue(self.引擎._该停(None))


class 停止必须等线程死透测试(unittest.TestCase):
    """**释放永远排在"线程确认死透"之后** —— 这条规矩不许被改回去。"""

    def _造卡住的代(self, 卡多久: float = 5.0):
        引擎 = 播放引擎()
        代 = _播放代(7)
        代.输入 = 假输入()
        代.视频 = 假解码器()
        代.音频 = 假解码器()
        代.时钟 = 引擎.时钟

        def 卡住():
            # 故意**不理会**停止标志：模拟"卡在网络读里"的线程。
            # 老代码这时候会 clear 掉停止标志并继续，然后照样释放输入 —— 悬垂指针。
            截止 = time.time() + 卡多久
            while time.time() < 截止:
                time.sleep(0.02)

        线程 = threading.Thread(target=卡住, name="V2-假卡住", daemon=True)
        线程.start()
        代.线程们 = [线程]
        引擎._本代 = 代
        引擎._代 = 7
        return 引擎, 代, 线程

    def test_停止超时就一个字节都不释放(self):
        引擎, 代, 线程 = self._造卡住的代(卡多久=1.2)
        日志: list[str] = []
        引擎._日志 = 日志.append
        干净 = 引擎.停止(超时秒=0.2)
        self.assertFalse(干净, "线程没死透时 停止() 必须如实返回 False")
        self.assertEqual(代.输入.关闭次数, 0,
                         "线程还活着就关输入 = 悬垂指针（真机 abort 的最后一环）")
        self.assertEqual(代.视频.关闭次数, 0, "解码器同样不许释放")
        self.assertEqual(引擎.退役代数(), 1, "这一代要被退役保管（不是丢掉、不是释放）")
        self.assertTrue(any("停止超时" in 行 for 行 in 日志),
                        "超时不许静默：必须在日志里说清楚")
        self.assertTrue(any("不释放" in 行 for 行 in 日志), "要说清代价：资源不释放")
        线程.join(timeout=5.0)

    def test_线程死了以后补做释放(self):
        引擎, 代, 线程 = self._造卡住的代(卡多久=0.3)
        输入对象, 解码器对象 = 代.输入, 代.视频      # 释放后 代.输入 会被清空，先留引用
        日志: list[str] = []
        引擎._日志 = 日志.append
        self.assertFalse(引擎.停止(超时秒=0.1))
        线程.join(timeout=5.0)
        self.assertFalse(线程.is_alive())
        self.assertTrue(引擎.停止(), "线程已经退出，这次必须干净")
        self.assertEqual(引擎.退役代数(), 0, "退役的代要被补做释放，不能一直漏")
        self.assertEqual(输入对象.关闭次数, 1, "补释放时关一次（且只有一次）")
        self.assertEqual(解码器对象.关闭次数, 1)


class 真素材重开测试(unittest.TestCase):
    """真解码器上反复重开：线程必须归零、世代号必须自增、不许留退役代。"""

    @classmethod
    def setUpClass(cls):
        cls.临时 = 临时目录()
        cls.素材 = 造素材(cls.临时.name, 秒=2.0, 带声音=True)

    @classmethod
    def tearDownClass(cls):
        cls.临时.cleanup()

    def _引擎(self):
        日志: list[str] = []
        引擎 = 播放引擎(日志回调=日志.append)
        self.addCleanup(引擎.停止)
        return 引擎, 日志

    def test_重复打开停止十二轮不泄漏不崩(self):
        引擎, 日志 = self._引擎()
        上一代 = 0
        for 轮 in range(12):
            引擎.打开(str(self.素材))
            self.assertEqual(引擎.代数, 上一代 + 1, f"第 {轮 + 1} 次打开要自增世代号")
            上一代 = 引擎.代数
            引擎.播放()
            self.assertTrue(引擎.存活线程名(), "起播以后必须有活着的线程")
            time.sleep(0.12)
            引擎.停止()
            self.assertEqual(引擎.存活线程名(), [],
                             f"第 {轮 + 1} 次停止后线程必须归零（老线程不许残留）")
            self.assertEqual(引擎.退役代数(), 0,
                             f"第 {轮 + 1} 次停止后不该有退役代（本地文件没有卡死的理由）")
            self.assertIsNone(引擎.输入, "停止后公开视图必须是空的")
            self.assertIsNone(引擎._本代, "停止后不该还有“当前代”")
        self.assertGreater(引擎.统计.已解视频帧, 0, "12 轮里总得真解出过帧")

    def test_重开时老线程会自己退出(self):
        引擎, _日志 = self._引擎()
        引擎.打开(str(self.素材))
        引擎.播放()
        截止 = time.time() + 5.0
        while time.time() < 截止 and 引擎.统计.已解视频帧 < 2:
            time.sleep(0.05)
        老代 = 引擎._本代
        老线程们 = list(老代.线程们)
        # 直接再打开一次：老的一代必须被停干净
        引擎.打开(str(self.素材))
        self.assertEqual(引擎.代数, 老代.编号 + 1)
        for 线程 in 老线程们:
            self.assertFalse(线程.is_alive(),
                             f"老线程 {线程.name} 必须已经退出（它不能再碰任何 libav）")
        self.assertIsNot(引擎._本代, 老代, "当前代必须是新的那一代")
        self.assertTrue(引擎._该停(老代), "对老代来说永远该停")

    def test_两个线程同时重开只留下一个当前代(self):
        """真机 22:04:08 同一秒里两条参数更新 = 两个线程同时重开。"""
        引擎, 日志 = self._引擎()
        错误: list[str] = []

        def 狂重开():
            for _ in range(4):
                try:
                    引擎.打开(str(self.素材))
                    引擎.播放()
                    time.sleep(0.05)
                    引擎.停止()
                except Exception as 错:  # noqa: BLE001
                    错误.append(f"{type(错).__name__}: {错}")

        线程们 = [threading.Thread(target=狂重开, name=f"压力-重开{i}", daemon=True)
                 for i in range(2)]
        for 线程 in 线程们:
            线程.start()
        for 线程 in 线程们:
            线程.join(timeout=60.0)
        self.assertEqual(错误, [], "并发重开不该抛异常")
        self.assertEqual(引擎.存活线程名(), [], "并发重开后不许有活着的线程")
        self.assertEqual(引擎.退役代数(), 0,
                         "本地文件没有理由停不下来（有退役代就说明 join 超时了）")
        self.assertGreater(引擎.代数, 1, "确实重开了多次")


class _假统计:
    def __init__(self):
        self.当前时间秒 = 30.0
        self.总时长秒 = 1200.0
        self.状态 = 播放状态.播放中


class _假引擎:
    """给"自动重载安全化"测试用的假引擎：只记录打开/播放/跳转。"""

    def __init__(self):
        self.统计 = _假统计()
        self.状态 = 播放状态.播放中
        self.打开次数 = 0
        self.播放次数 = 0
        self.跳转们: list[float] = []

    def 打开(self, 地址, 选项=None, 请求头=None, **kw):  # noqa: N802
        self.打开次数 += 1

    def 播放(self) -> None:
        self.播放次数 += 1

    def 跳转(self, 秒: float) -> None:
        self.跳转们.append(float(秒))

    def 停止(self) -> bool:
        return True


class 自动重载安全化测试(unittest.TestCase):
    """AI"换参数重开"要走安全路径：去抖 + 等安全点排队（不硬插）。"""

    def _会话(self, 引擎=None):
        引擎 = 引擎 or _假引擎()
        会话 = 播放会话(引擎=引擎)
        会话.直链信息 = {"url": "https://example.invalid/a.mp4", "headers": {},
                     "name": "a.mp4", "size": 0}
        会话.媒体 = 媒体信息(已知=True, 视频码率bps=19.1e6, 总码率bps=19.1e6,
                        时长秒=1200.0, 分辨率="3840x1632", 视频编码="hevc")
        会话.探测 = 探测结果(成功=True, 实测带宽bps=63.0e6)
        return 会话, 引擎

    def test_同一秒里的第二条参数更新只记参数不重开(self):
        会话, 引擎 = self._会话()
        日志: list[str] = []
        会话._日志 = 日志.append
        会话.应用新参数({"网络缓存毫秒": 9000, "理由": "第一次"})
        self.assertEqual(引擎.打开次数, 1, "第一次要真的重开")
        会话.应用新参数({"网络缓存毫秒": 3000, "理由": "同一秒的第二条"})
        self.assertEqual(引擎.打开次数, 1, "去抖：同一秒的第二条不许再重开一次")
        self.assertTrue(any("去抖" in 行 for 行 in 日志), "去抖要写日志，不许静默吞掉")
        self.assertIsNotNone(会话._待重载, "参数要排队留着，不能丢")

    def test_界面不安全时排队_到安全点再做(self):
        会话, 引擎 = self._会话()
        会话._日志 = lambda _t: None
        会话._上次重载时刻 = time.monotonic() - 100.0     # 不考虑去抖
        安全 = {"值": False}
        会话.安全点查询 = lambda: 安全["值"]
        会话.应用新参数({"网络缓存毫秒": 9000, "理由": "拖动中"})
        self.assertEqual(引擎.打开次数, 0, "正在拖进度条时绝不许硬插一次重开")
        self.assertIsNotNone(会话._待重载)
        self.assertFalse(会话.推进待办(), "还没到安全点，待办不动")
        self.assertEqual(引擎.打开次数, 0)
        安全["值"] = True
        self.assertTrue(会话.推进待办(), "到安全点后由界面把待办做掉")
        self.assertEqual(引擎.打开次数, 1)
        self.assertIsNone(会话._待重载)
        self.assertEqual(引擎.跳转们, [30.0], "重开要跳回原来的位置")

    def test_不重载时不碰引擎(self):
        会话, 引擎 = self._会话()
        会话._日志 = lambda _t: None
        会话.应用新参数({"理由": "什么都不改"}, 自动重载=False)
        self.assertEqual(引擎.打开次数, 0)

    def test_播放已经停了就不许替用户重新打开(self):
        """排队中的重开绝不能"把用户刚停掉的播放又开起来"。"""
        会话, 引擎 = self._会话()
        会话._日志 = lambda _t: None
        会话._上次重载时刻 = time.monotonic() - 100.0
        安全 = {"值": False}
        会话.安全点查询 = lambda: 安全["值"]
        会话.应用新参数({"网络缓存毫秒": 9000, "理由": "先排队"})
        self.assertIsNotNone(会话._待重载)
        安全["值"] = True
        引擎.状态 = 播放状态.空闲              # 用户点了停止
        self.assertFalse(会话.推进待办())
        self.assertEqual(引擎.打开次数, 0, "停了就不许自动重开")
        self.assertIsNone(会话._待重载, "作废的待办要清掉")

    def test_关闭时清掉排队中的重开(self):
        会话, 引擎 = self._会话()
        会话._日志 = lambda _t: None
        会话.应用新参数({"网络缓存毫秒": 9000, "理由": "排队"})
        会话.关闭()
        self.assertIsNone(会话._待重载)
        self.assertFalse(会话.推进待办())


if __name__ == "__main__":
    unittest.main()
