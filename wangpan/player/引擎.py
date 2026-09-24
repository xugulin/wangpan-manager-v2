"""播放引擎：**我们自己的播放器循环**（解封装 → 解码 → 音频主时钟 → 视频按时呈现）。

线程模型（三个线程 + 两个有界队列，背压天然存在，不会把内存吃光）
================================================================
::

    解封装线程 ──[视频包队列]──> 视频线程 ──> 最新帧（只保留最新一张，供界面取）
              └─[音频包队列]──> 音频线程 ──> 音频设备（PulseAudio）──> 音频主时钟

* **音频是主时钟**：音频线程每写出去一块，就把"已播到哪儿"记进 :class:`音频时钟`
  （再减去设备延迟）；
* **视频跟着时钟走**：早到的帧等一等，晚到超过 ``丢帧阈值`` 的帧直接丢（并计数）；
* 暂停 = 三个线程都停在同一处（音频不写、视频不呈现、解封装不读），**不是**忙等；
* 跳转 = 停一下 → 冲刷队列与解码器 → ``avformat_seek_file`` → 复位时钟 → 继续。

世代号（generation）—— 为什么必须有
==================================
"播放中途重开"是**用户真机三次崩溃**的现场：22:04:08 的 core 是 SIGABRT、
崩溃线程 `V2-解封装`、栈在 libavcodec 内部（``+0x902967 → +0x7d392 → +0x272a2``），
断言原文 ``Assertion fctx->async_lock failed at libavcodec/pthread_frame.c:171``。

复现出来的两条**硬结论**（见 ``工具/重开压力测试.py``）：

1. **跨线程冲刷**：``_执行跳转`` 跑在解封装线程里，却对视频/音频线程**正在使用**的
   ``AVCodecContext`` 调 ``avcodec_flush_buffers``。libav 的解码上下文（尤其帧级多线程
   解码器）不是线程安全的 —— 冲刷与 ``avcodec_send_packet`` 一重叠，
   内部帧线程的 ``async_lock`` 断言就失败 → ``abort()``。
   AI"换参数重开"每次紧跟一次 ``跳转(位置)``，所以它成了触发点。
2. **老线程摸到新世代资源**：``打开()`` 先 ``停止()``；老 ``停止()`` 在 join 超时后
   ``self._停.clear()`` 就返回，还照样把 ``self.输入/self.视频`` 换成新的 ——
   于是**老线程继续跑**，并且它每轮循环都去读 ``self.输入``/``self.视频``
   （现在是新一代的对象）→ 两个线程同时用一个 ``AVFormatContext``/``AVCodecContext``。

所以这一层的铁律有两条，改代码时不许破坏：

* **每一次 ``打开()`` 自增一个世代号**，线程启动时**把这一代的对象用参数带进去**，
  循环里发现"我不是当前代了"就立刻退出，**退出前一个 libav 调用都不许再发**；
* **释放永远排在"线程确认死透"之后**：join 超时就不释放（把整代资源退役保管），
  宁可泄漏一次，也绝不在有人还在用的时候 ``avformat_close_input``/``avcodec_free_context``。

为什么要自己写（而不是用现成播放器）
====================================
这一步之后，"画面在哪个窗口、由谁画、什么时候画"**完全由我们决定**：
界面只是从 :meth:`播放引擎.取最新帧` 拿一张图自己画 —— 不存在"第三方播放器自己开窗口"
这一类问题。硬解、字幕、直链这些都在这个骨架上加，不再受别人的平台规则牵制。
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..ffmpeg import 绑定 as B
from ..ffmpeg.绑定 import 常量
from . import 解码, 网络
from .音频输出 import 音频输出设备, 打开输出
from .解封装 import 输入

__all__ = ["播放引擎", "播放状态", "引擎统计"]

#: 队列深度（包数）。128 个包 ≈ 一两秒，够抗抖动、又不至于堆内存
视频队列深度 = 96
音频队列深度 = 192
#: 视频比音频早到多少算"该等"（秒）—— 提前量，避免刚好卡在边界上抖
视频提前量 = 0.004
#: 帧比时钟晚多少就直接丢（秒）
丢帧阈值 = 0.080
#: 解码线程等不到数据时的轮询间隔（秒）
轮询间隔 = 0.004


class 播放状态:
    空闲 = "空闲"
    播放中 = "播放中"
    暂停 = "暂停"
    结束 = "结束"
    出错 = "出错"


@dataclass
class 引擎统计:
    已解视频帧: int = 0
    已丢视频帧: int = 0
    已解音频块: int = 0
    已播音频秒: float = 0.0
    读入字节: int = 0
    当前时间秒: float = 0.0
    总时长秒: float = 0.0
    状态: str = 播放状态.空闲
    音视频: str = ""
    硬解: str = ""
    音频设备: str = ""
    帧率: float = 0.0
    码率bps: float = 0.0
    硬解帧: int = 0
    输出尺寸: str = ""

    def 摘要(self) -> str:
        return (f"{self.状态}｜{self.当前时间秒:.1f}/{self.总时长秒:.1f}s"
                f"｜{self.帧率:.0f}fps｜丢帧 {self.已丢视频帧}/{self.已解视频帧}"
                f"｜{self.码率bps / 1e6:.1f}Mbps｜{self.硬解}"
                f"（硬解帧 {self.硬解帧}）"
                + (f"｜输出 {self.输出尺寸}" if self.输出尺寸 else "")
                + f"｜{self.音频设备}")


class 音频时钟:
    """音频主时钟：已写样本数 − 设备延迟 = "现在听到的是第几秒"。"""

    def __init__(self, 采样率: int = 解码.输出采样率) -> None:
        self.采样率 = 采样率
        self._基准秒 = 0.0          # 这一段的起点（跳转会重置）
        self._样本 = 0
        self._起点时刻 = time.monotonic()
        self._锁 = threading.Lock()
        self.暂停中 = False
        self._暂停时刻 = 0.0
        #: 音频**放完了**（EOF + 队列空 + 冲刷完）—— 这时时钟必须改由真实时间推进，
        #: 否则它会停在最后一个样本上，而片尾几帧的时间戳可能比它更大 →
        #: 视频线程永远在等一个不会到来的时刻（Windows CI 上实测到的死等：帧卡在 1）
        self.音频已完 = False

    def 记写入(self, 样本数: int) -> None:
        with self._锁:
            self._样本 += int(样本数)

    def 重置(self, 起点秒: float, 倍速: float = 1.0) -> None:
        with self._锁:
            self._基准秒 = float(起点秒)
            self.倍速 = max(0.1, min(8.0, float(倍速 or 1.0)))
            self._样本 = 0
            self._起点时刻 = time.monotonic()
            self.音频已完 = False

    #: 倍速（>1 快放）。音频是"按原速重采样后以倍速写出去"，
    #: 所以时钟要用同一个倍速折算 —— 否则画面会比声音慢/快（实测过）。
    倍速 = 1.0

    def 现在秒(self, 设备延迟: float = 0.0) -> float:
        with self._锁:
            按样本 = self._样本 / self.采样率 * self.倍速
            按时间 = (time.monotonic() - self._起点时刻) * self.倍速
            if self.音频已完:
                # 音频没了，就别再被"已写样本"卡住：让真实时间继续往前走
                按样本 = max(按样本, 按时间)
            else:
                # 护栏：时钟**不许超过真实经过的时间**（设备不阻塞时会冲向片尾）
                按样本 = min(按样本, 按时间 + 0.5)
            return (self._基准秒 + 按样本
                    - max(0.0, 设备延迟) * self.倍速)


class 系统时钟:
    """没有音轨时用的主时钟：按系统单调时间走（暂停要靠调用方冻结）。

    为什么必须有它（实测踩到）：纯视频文件（或音轨被禁用）里，音频线程根本不存在，
    音频时钟永远停在 0 → 视频线程永远"等时钟" → **画面一帧都不出**。
    所以"主时钟"要能在音频/系统之间切换，而不是写死成音频。
    """

    def __init__(self) -> None:
        self._基准秒 = 0.0
        self._起点时刻 = time.monotonic()
        self._暂停累计 = 0.0
        self._暂停开始 = 0.0
        self._暂停中 = False

    def 记写入(self, _样本数: int) -> None:      # 与音频时钟同接口
        return

    def 重置(self, 起点秒: float, 倍速: float = 1.0) -> None:
        self._基准秒 = float(起点秒)
        self.倍速 = max(0.1, min(8.0, float(倍速 or 1.0)))
        self._起点时刻 = time.monotonic()
        self._暂停累计 = 0.0
        self._暂停中 = False

    def 暂停(self, 暂停: bool) -> None:
        if 暂停 and not self._暂停中:
            self._暂停中 = True
            self._暂停开始 = time.monotonic()
        elif not 暂停 and self._暂停中:
            self._暂停中 = False
            self._暂停累计 += time.monotonic() - self._暂停开始

    倍速 = 1.0

    def 现在秒(self, 设备延迟: float = 0.0) -> float:
        跑到 = time.monotonic() - self._起点时刻 - self._暂停累计
        if self._暂停中:
            跑到 = self._暂停开始 - self._起点时刻 - self._暂停累计
        return (self._基准秒 + max(0.0, 跑到) * self.倍速
                - max(0.0, 设备延迟) * self.倍速)


class _播放代:
    """**一代播放**的全部资源与标志（``打开()`` 一次 = 一代）。

    为什么要把这些东西打包成一个对象，而不是继续挂在引擎上（``self.输入``）：
    引擎上的字段永远是"**当前**那一代"的。老线程如果每轮循环都去读 ``self.输入``，
    那么新一代一建好，它就悄悄摸到了**别人的** ``AVFormatContext``/解码器 ——
    两个线程共用一个 libav 上下文，内部断言就会 abort 掉整个进程（真机 coredump 实证）。
    所以线程启动时**把这一代的对象作为参数带进去**，此后只认自己手里这一份。

    同理，``停``/``队列们``/``线程们`` 也必须是每一代自己的：
    共用的事件会出现"新一代刚 clear 掉停止标志，老线程又继续跑"这种事（老代码正是如此）。
    """

    __slots__ = ("编号", "输入", "视频", "音频", "输出", "时钟", "停", "暂停", "结束",
                 "视频队列", "音频队列", "线程们", "跳转请求", "跳转锁",
                 "流世代", "退役", "开播时刻")

    def __init__(self, 编号: int, *, 输入=None, 视频=None, 音频=None,
                 输出=None, 时钟=None) -> None:
        self.编号 = int(编号)
        self.输入 = 输入
        self.视频 = 视频
        self.音频 = 音频
        self.输出 = 输出
        self.时钟 = 时钟
        #: 这一代的"请停下来"（**只由这一代的线程读，绝不复用**）
        self.停 = threading.Event()
        #: 暂停（也是每一代自己的：绝不让新一代 clear 掉老线程还在等的那个事件）
        self.暂停 = threading.Event()
        #: 解封装走到头了
        self.结束 = threading.Event()
        #: 队列也是每一代自己的：否则老线程会往新队列里塞旧包
        self.视频队列: queue.Queue = queue.Queue(maxsize=视频队列深度)
        self.音频队列: queue.Queue = queue.Queue(maxsize=音频队列深度)
        self.线程们: list[threading.Thread] = []
        self.跳转请求: Optional[float] = None
        self.跳转锁 = threading.Lock()
        #: **流世代**（跳转世代）：每次跳转 +1。解码线程看到带新流世代的包时，
        #: 由**它自己**冲刷自己的解码器 —— 冲刷必须是"谁用谁冲刷"，
        #: 绝不能由解封装线程代劳（那正是 22:04:08 崩溃的成因）。
        self.流世代 = 0
        #: 已经退役（停止超时、资源不敢释放）的一代，只用来记账与日志
        self.退役 = False
        self.开播时刻 = time.monotonic()


class 播放引擎:
    """一个文件的播放：打开 → 起播 → （暂停/跳转/音量）→ 停止。"""

    def __init__(self, 日志回调: Optional[Callable[[str], None]] = None) -> None:
        self._日志 = 日志回调 or (lambda _t: None)
        self.输入: Optional[输入] = None
        self.视频: Optional[解码.视频解码器] = None
        self.音频: Optional[解码.音频解码器] = None
        self.输出: Optional[音频输出设备] = None
        self.时钟 = 音频时钟()
        self.统计 = 引擎统计()
        self.状态 = 播放状态.空闲
        self.音量 = 1.0
        self.倍速 = 1.0
        self._单帧模式 = False
        self._锁 = threading.RLock()
        self._最新帧: Optional[解码.解码帧] = None
        self._帧序号 = 0
        #: 当前世代号（每次 ``打开`` +1；仅用于日志与"我是不是当前代"的核对）
        self._代 = 0
        self._本代: Optional[_播放代] = None
        #: 停止超时后**不释放**、留在这里保管的一代（宁可泄漏也不悬垂）
        self._退役们: list[_播放代] = []
        #: ``打开``/``播放``/``停止`` 的串行锁：真机 22:04:08 同一秒里连发了两条
        #: 参数更新，也就是**两个线程同时在重开**。不串行化的话会出现
        #: "A 刚建好一代、B 正在停止它、A 又在上面起线程"这种没人能推理的局面。
        self._开停锁 = threading.RLock()
        self._读入累计 = 0
        self._码率基点 = (0.0, 0)
        self._输出尺寸 = (0, 0)
        #: 这一路要不要强制软解（由 :meth:`打开` 的 ``不要硬解`` 设定）
        self._不要硬解 = False

    # ---------------- 世代相关的只读入口（给界面/工具/测试看） ----------------

    @property
    def 代数(self) -> int:
        """已经开到第几代（每次 ``打开`` 自增一次）。"""
        return int(self._代)

    @property
    def _线程们(self) -> list[threading.Thread]:
        """当前这一代的线程列表（旧代码/诊断工具按这个名字读）。"""
        return list(self._本代.线程们) if self._本代 is not None else []

    @property
    def _视频队列(self) -> queue.Queue:
        代 = self._本代
        return 代.视频队列 if 代 is not None else queue.Queue(maxsize=视频队列深度)

    @property
    def _音频队列(self) -> queue.Queue:
        代 = self._本代
        return 代.音频队列 if 代 is not None else queue.Queue(maxsize=音频队列深度)

    @property
    def _停(self) -> threading.Event:
        代 = self._本代
        if 代 is not None:
            return 代.停
        事件 = threading.Event()
        事件.set()
        return 事件

    @property
    def _结束(self) -> threading.Event:
        代 = self._本代
        if 代 is not None:
            return 代.结束
        return threading.Event()

    @property
    def _暂停(self) -> threading.Event:
        代 = self._本代
        if 代 is not None:
            return 代.暂停
        return threading.Event()

    def 存活线程名(self) -> list[str]:
        """当前代 + 退役代里**还活着**的线程名（压测/自检用它判"有没有停干净"）。"""
        结果 = []
        for 代 in ([self._本代] if self._本代 is not None else []) + list(self._退役们):
            for 线程 in 代.线程们:
                if 线程.is_alive():
                    结果.append(线程.name)
        return 结果

    def 退役代数(self) -> int:
        """有几代因为"线程没停干净"而没能释放（正常播放应该永远是 0）。"""
        return len(self._退役们)


    # ---------------- 打开 / 收尾 ----------------

    def 打开(self, 地址: str, 选项: Optional[dict] = None,
           请求头: Optional[dict] = None, *,
           不要硬解: bool = False,
           缓冲字节: int = 0,
           超时毫秒: int = 0) -> None:
        """打开一路源（本地文件 / 网盘直链）。

        :param 请求头: 网盘直链常要的 UA/Referer/Cookie；网络地址会自动补上
            重连/超时/连接复用等选项（见 :func:`wangpan.player.网络.网络选项`）。
        :param 不要硬解: 这一路强制软解（AI 播放顾问/界面在硬解花屏时用）。
        :param 缓冲字节: 覆盖默认读缓冲（AI 给出的"网络缓存毫秒"换算而来）；
            0 = 用 libavformat 默认。
        :param 超时毫秒: 覆盖默认读写超时；0 = 用 :data:`网络.默认超时毫秒`。

        重开的规矩（**这是本文件最重要的一段**）：

        * 第一步永远是 :meth:`停止`，并且**确认它返回干净**才往下走；
        * 然后自增**世代号**，把这一代的资源装进一个全新的 :class:`_播放代` ——
          老线程即使还在跑，手里也只有**它自己那一代**的对象，碰不到新的；
        * 整个过程拿 ``_开停锁``（真机 22:04:08 同一秒里有两条参数更新 =
          两个线程同时在重开，不串行化就会变成"没人能推理的局面"）。
        """
        with self._开停锁:
            # ① 先把老的一代停干净。返回 False = 有线程没在超时内退出 ——
            #    它们的那一代已经被"退役保管"（绝不释放），这里必须如实记一笔。
            if not self.停止():
                self._日志("[播放] 上一代没能在超时内停干净（那一代已退役保管、不释放）；"
                         "这一路用**全新的一代**继续，老线程碰不到新的输入/解码器")
            self._代 += 1
            代 = _播放代(self._代)
            self._本代 = 代
            self._清公开字段()      # 建立期间公开视图一律为空：界面读到 None 就不会摸半成品
            选项 = dict(选项 or {})
            self._不要硬解 = bool(不要硬解)
            if 网络.是网络地址(地址):
                额外 = {}
                if int(缓冲字节) > 0:
                    额外["缓冲字节"] = int(缓冲字节)
                if int(超时毫秒) > 0:
                    额外["超时毫秒"] = int(超时毫秒)
                自动 = 网络.网络选项(地址, 请求头, **额外)
                自动.update(选项)
                选项 = 自动
                头 = dict(请求头 or {})
                if 头.get("Cookie"):
                    # Cookie 不打印全文（可能含登录态），只报"带没带"
                    self._日志(f"[网络] 走网络播放：{网络.摘要(地址)}"
                             f"（带 {len(头)} 个头，含 Cookie）")
                else:
                    self._日志(f"[网络] 走网络播放：{网络.摘要(地址)}"
                             + (f"（带 {len(头)} 个头）" if 头 else ""))
            try:
                代.输入 = self._打开带降级(地址, 选项)
            except BaseException:
                # 打开失败：这一代还没有任何线程，撤掉是安全的
                self._本代 = None
                self.状态 = 播放状态.空闲
                self.统计.状态 = self.状态
                raise
            self.输入 = 代.输入
            # 让网络 I/O 能被"停止/换世代"打断（否则关闭时正在读 → 悬垂指针 → 段错误）
            try:
                代.输入.装中断回调(
                    lambda: 代.停.is_set() or self._本代 is not 代)
            except Exception as 错:  # noqa: BLE001
                self._日志(f"[网络] 中断回调没装上（不影响播放）：{错}")
            self.统计.总时长秒 = 代.输入.时长秒
            self.统计.音视频 = "、".join(流.一句话() for 流 in 代.输入.流们)
            self._日志(f"[播放] 已打开（第 {代.编号} 代）：{self.统计.音视频}"
                     f"｜时长 {代.输入.时长秒:.1f}s")
            try:
                if 代.输入.视频流 is not None:
                    代.视频 = 解码.视频解码器(代.输入.绑定, 代.输入.视频流)
                    代.视频.允许硬解 = not self._不要硬解
                    if self._输出尺寸 != (0, 0):
                        代.视频.设置输出尺寸(*self._输出尺寸)
                    代.视频.打开()
                    self.统计.硬解 = 代.视频.硬解
                    self.统计.帧率 = 代.输入.视频流.帧率
                if 代.输入.音频流 is not None:
                    代.音频 = 解码.音频解码器(代.输入.绑定, 代.输入.音频流)
                    代.音频.打开()
                    代.音频.设置倍速(self.倍速)      # 沿用当前倍速（只登记，音频线程应用）
                    代.输出 = 打开输出(解码.输出采样率, 解码.输出声道数)
                    self.统计.音频设备 = 代.输出.名字
                    代.时钟 = 音频时钟(解码.输出采样率)
                    if getattr(代.输出, "失败原因", ""):
                        self._日志(f"[音频] {代.输出.失败原因}")
                else:
                    # 没有音轨 → 用系统时钟当主时钟（否则视频永远等不到时钟，画面不出）
                    代.时钟 = 系统时钟()
                    self.统计.音频设备 = "（这个文件没有音轨，用系统时钟）"
            except BaseException:
                # 解码器/输出建了一半就失败：这一代**没有任何线程**，按顺序释放是安全的
                代.停.set()
                self._释放一代(代)
                self._本代 = None
                self._清公开字段()
                self.状态 = 播放状态.空闲
                self.统计.状态 = self.状态
                raise
            self.视频, self.音频, self.输出, self.时钟 = (
                代.视频, 代.音频, 代.输出, 代.时钟)
            if 代.时钟 is not None:
                代.时钟.倍速 = self.倍速
            self.状态 = 播放状态.暂停
            self.统计.状态 = self.状态

    def _打开带降级(self, 地址: str, 选项: dict):
        """打开输入；网络选项太"激进"时**降级重试**。

        实测（本地 HTTP/1.0 服务器）：`multiple_requests=1`（复用连接）会被
        "回完就关连接"的服务器坑到，`avformat_open_input` 直接报"文件结束"。
        真实网盘 CDN 也常这么干。所以策略是：
        ① 先用完整选项（带重连/复用/超时）；
        ② 失败就退到"最小集"（只保留请求头与超时），能播最重要；
        ③ 两次都失败才把第一次的错误抛出去（保留最原始的诊断信息）。
        """
        第一次错误: Optional[Exception] = None
        try:
            return 输入.打开(地址, 选项 or None)
        except Exception as 错:  # noqa: BLE001
            第一次错误 = 错
        if not 选项:
            raise 第一次错误
        最小 = {k: v for k, v in 选项.items()
              if k in ("headers", "user_agent", "rw_timeout", "buffer_size")}
        if 最小 == 选项:
            raise 第一次错误
        self._日志(f"[网络] 带完整选项打不开（{第一次错误}），改用最小选项重试")
        try:
            return 输入.打开(地址, 最小 or None)
        except Exception:  # noqa: BLE001
            raise 第一次错误

    def 播放(self) -> None:
        """起播（或从暂停继续）。第一次调用会拉起三个线程。

        线程**只认自己这一代**：``目标=函数, args=(代,)`` —— 这样即使之后有人
        ``打开`` 了新一代，老线程也只是"发现自己不是当前代"然后退出，
        绝不会有半路摸到新世代输入的那种事。
        """
        with self._开停锁:
            代 = self._本代
            if 代 is None or 代.输入 is None:
                raise RuntimeError("还没打开文件（先 打开()）")
            if not 代.线程们:
                代.停.clear()
                代.结束.clear()
                造 = [("解封装", self._解封装循环), ("视频", self._视频循环)]
                if 代.音频 is not None and 代.输出 is not None:
                    造.append(("音频", self._音频循环))
                for 名, 函数 in 造:
                    线程 = threading.Thread(target=函数, args=(代,),
                                          name=f"V2-{名}", daemon=True)
                    线程.start()
                    代.线程们.append(线程)
            代.暂停.clear()
            if isinstance(代.时钟, 系统时钟):
                代.时钟.暂停(False)
            if 代.输出 is not None:
                try:
                    代.输出.恢复()          # PulseAudio 没有 pause，这里保留钩子
                except Exception:  # noqa: BLE001
                    pass
            self.状态 = 播放状态.播放中
            self.统计.状态 = self.状态

    def 暂停切换(self) -> bool:
        """返回是否处于暂停状态。

        ⚠️ 这里**不拿 `_开停锁`**：它只动"这一代自己的事件"，不碰任何 libav 资源，
        拿了反而会让界面在"后台线程正在重开（要联网几秒）"时卡住按键。
        """
        代 = self._本代
        if self.状态 == 播放状态.播放中:
            if 代 is not None:
                代.暂停.set()
            if isinstance(self.时钟, 系统时钟):
                self.时钟.暂停(True)
            self.状态 = 播放状态.暂停
        elif self.状态 == 播放状态.暂停:
            if 代 is not None:
                代.暂停.clear()
            if isinstance(self.时钟, 系统时钟):
                self.时钟.暂停(False)
            self.状态 = 播放状态.播放中
        self.统计.状态 = self.状态
        return self.状态 == 播放状态.暂停

    def 设置暂停(self, 暂停: bool) -> None:
        if 暂停 and self.状态 == 播放状态.播放中:
            self.暂停切换()
        elif not 暂停 and self.状态 == 播放状态.暂停:
            self.播放()

    def 跳转(self, 秒: float) -> None:
        """**只登记请求**，真正的 seek 由解封装线程做（它才是输入的唯一使用者）。"""
        代 = self._本代
        if 代 is None:
            return
        with 代.跳转锁:
            代.跳转请求 = max(0.0, float(秒))

    def 设置音量(self, 音量: float) -> None:
        self.音量 = max(0.0, min(1.5, float(音量)))

    def 设置倍速(self, 倍速: float) -> float:
        """设置播放倍速（0.25~4.0），返回真正生效的值。

        实现方式：**音频按"倍速 × 采样率"重采样**（等于把音频变短），视频时钟也按
        同一个倍速折算 —— 两边用同一个系数，音画就不会散。
        注意：这会改变音调（和 libvlc/VLC 的倍速行为一致）而不是保持音调。

        ⚠️ 这里**只登记**（``音频.设置倍速`` 现在就是"登记请求"）：
        重建重采样器会把音频线程正在用的 ``swr`` 释放掉（use-after-free），
        所以由音频线程在自己的安全点上做（见 :meth:`解码.音频解码器._应用倍速请求`）。
        """
        倍速 = max(0.25, min(4.0, float(倍速 or 1.0)))
        self.倍速 = 倍速
        if isinstance(self.时钟, (音频时钟, 系统时钟)):
            self.时钟.倍速 = 倍速
        代 = self._本代
        if 代 is not None and 代.音频 is not None:
            try:
                代.音频.设置倍速(倍速)
            except Exception:  # noqa: BLE001
                pass
        self._日志(f"[播放] 倍速 {倍速:.2f}×")
        return 倍速

    def 逐帧(self) -> bool:
        """暂停 + 前进一帧（"逐帧看"）。返回是否真的前进了一帧。

        注意：**光"收帧"不够** —— 解码是"喂包才有帧"，暂停时队列里可能已经空了，
        所以要"没帧就喂一个包"，最多试几十次（B 帧重排时前面几个包本来就不出帧）。

        ⚠️ 这是**界面线程直接碰解码器**的唯一入口，所以它必须与视频线程互斥：
        解码器自带一把可重入锁（见 :class:`解码._基础解码器`），
        ``送包/收帧/取帧`` 都在锁里，这里就不用再自己加锁了。
        """
        代 = self._本代
        if 代 is None or 代.输入 is None or 代.视频 is None:
            return False
        if self.状态 == 播放状态.播放中:
            self.设置暂停(True)
        for _尝试 in range(40):
            帧 = 代.视频.解码一帧()
            if 帧 is not None:
                if 帧.时间秒 >= 0:
                    self.统计.当前时间秒 = 帧.时间秒
                self.统计.已解视频帧 += 1
                self.统计.硬解帧 = int(getattr(代.视频, "硬解帧数", 0))
                with self._锁:
                    self._最新帧 = 帧
                    self._帧序号 += 1
                return True
            try:
                条目 = 代.视频队列.get_nowait()
            except queue.Empty:
                return False
            if 条目 == "冲刷" or (isinstance(条目, tuple) and 条目[0] == "冲刷"):
                代.视频.送空包()
                continue
            if isinstance(条目, tuple) and len(条目) == 3:
                _包, 新包, 条目流世代 = 条目
            else:
                _包, 新包 = 条目
                条目流世代 = 代.流世代
            if 条目流世代 != getattr(代.视频, "_已冲刷世代", 0):
                # 逐帧也要遵守"跳转后先冲刷"：由**本线程**冲刷自己的解码器
                代.视频.冲刷()
                代.视频._已冲刷世代 = 条目流世代
            try:
                代.视频.送包(新包)
            finally:
                代.输入.释放新包(新包)
        return False

    def 章节们(self) -> list:
        return list(getattr(self.输入, "章节们", []) or []) if self.输入 else []

    def 跳章节(self, 序号: int) -> bool:
        """跳到第 N 个章节（0 起）；没有章节表就返回 False。"""
        章节们 = self.章节们()
        if not 章节们 or not (0 <= 序号 < len(章节们)):
            return False
        self.跳转(章节们[序号].起始秒)
        return True

    def 当前章节(self) -> int:
        """当前时间落在第几个章节（-1 = 没有章节表）。"""
        章节们 = self.章节们()
        if not 章节们:
            return -1
        现在 = self.统计.当前时间秒
        for 项 in reversed(章节们):
            if 现在 >= 项.起始秒:
                return 项.序号
        return 0

    def 设置输出尺寸(self, 宽: int, 高: int) -> None:
        """告诉解码器界面需要多大（**性能关键**：4K 全尺寸每帧要搬 25 MB）。

        界面在 resize 时调用；解码器只缩不放，且会缓存缩放器。
        """
        self._输出尺寸 = (max(0, int(宽 or 0)), max(0, int(高 or 0)))
        if self.视频 is not None:
            self.视频.设置输出尺寸(*self._输出尺寸)

    def 停止(self, 超时秒: float = 6.0) -> bool:
        """停下来，并且**等到线程真的死透**；返回是否干净（True = 已释放）。

        为什么要"等到死透"（老代码在这里只 join 3 秒，超时后 ``_停.clear()`` 就返回了，
        还照样把 ``self.输入`` 换成新的 —— 那是崩溃的最后一块拼图）：
        线程可能正卡在 ``av_read_frame`` 或 ``avcodec_send_packet`` 里，
        这时候释放 ``AVFormatContext``/``AVCodecContext`` 就是**悬垂指针**。

        所以现在的规矩是：

        1. 先把"这一代不再有效"立起来（``_本代 = None`` + ``代.停``），
           线程在每一个循环点都会看到，并且**退出前不再发任何 libav 调用**；
        2. join 等它们真死。等不到就**一个字节都不释放**，把整代资源放进 ``_退役们`` 保管
           （宁可泄漏一次，也绝不悬垂），并把超时写进日志 —— 不许静默继续；
        3. 只有确认全死了才释放，顺序固定：队列里的包 → 音频输出 → 解码器 → 格式上下文。
        """
        with self._开停锁:
            self._收退役()               # 顺手把之前退役、现在死透的那些真正释放掉
            代 = self._本代
            self._本代 = None            # ① 从这一刻起，老线程全部是"非当前代"
            self._清公开字段()
            if 代 is None:
                return True
            代.停.set()                  # 让阻塞中的 av_read_frame 立刻返回（中断回调）
            代.暂停.clear()              # 暂停中的线程也要醒过来退出
            截止 = time.monotonic() + max(0.1, float(超时秒))
            剩下: list[threading.Thread] = []
            for 线程 in list(代.线程们):
                线程.join(timeout=max(0.0, 截止 - time.monotonic()))
                if 线程.is_alive():
                    剩下.append(线程)
            代.线程们 = 剩下
            if 剩下:
                代.退役 = True
                self._退役们.append(代)
                self._日志("[播放] ⚠️ 停止超时：" + "、".join(t.name for t in 剩下)
                         + f" 等了 {超时秒:.1f}s 还没退出。第 {代.编号} 代的输入/解码器"
                           "**一律不释放**（整体退役保管）—— 绝不冒“读还没返回就关输入”的"
                           "悬垂指针风险；新一代用全新的对象播放，两边互不相碰。")
                if self.状态 != 播放状态.结束:
                    self.状态 = 播放状态.空闲
                self.统计.状态 = self.状态
                return False
            self._释放一代(代)           # ③ 确认没人用了，才按顺序释放
            if self.状态 != 播放状态.结束:
                self.状态 = 播放状态.空闲
            self.统计.状态 = self.状态
            return True

    def _清公开字段(self) -> None:
        """把"当前资源"的公开视图清空（界面读到 None 就不会去摸正在死的那一代）。"""
        self.输入 = None
        self.视频 = None
        self.音频 = None
        self.输出 = None

    def _释放一代(self, 代: _播放代) -> None:
        """释放一代的 libav 资源。

        **调用前提：这一代的线程已经全部死透（或从未启动）** —— 这是整个加固里最硬的规矩：
        释放永远排在"线程确认死透"之后。
        顺序也有讲究：先排空队列（把我们 ``复制包`` 出来的引用还掉，
        它们指向的缓冲还在输入里）→ 再关音频输出 → 再关解码器（用流参数建的）
        → 最后关格式上下文（流参数的所有者）。反过来就会出现
        "解码器还引用着已经没了的流参数"。
        """
        self._排空队列(代)
        for 部件 in (代.输出, 代.视频, 代.音频):
            try:
                if 部件 is not None and hasattr(部件, "关"):
                    部件.关()
            except Exception:  # noqa: BLE001
                pass
        try:
            if 代.输入 is not None:
                代.输入.关闭()
        except Exception:  # noqa: BLE001
            pass
        代.输出 = 代.视频 = 代.音频 = 代.输入 = None

    def _收退役(self) -> None:
        """把之前退役（停止超时、不敢释放）、现在线程已经死透的那些代真正释放掉。

        为什么要补这一步：退役是"保命"不是"放弃"。线程晚几百毫秒退出是常事
        （比如卡在一次网络写里），下次 ``打开``/``停止`` 时把它们收掉，就不会一直漏。
        """
        if not self._退役们:
            return
        留: list[_播放代] = []
        for 代 in self._退役们:
            代.线程们 = [t for t in 代.线程们 if t.is_alive()]
            if 代.线程们:
                留.append(代)
                continue
            self._日志(f"[播放] 退役的第 {代.编号} 代线程已经退出，现在补做释放")
            self._释放一代(代)
        self._退役们 = 留

    # ---------------- 界面取帧 ----------------

    def 取最新帧(self) -> Optional[解码.解码帧]:
        with self._锁:
            return self._最新帧

    def 取帧序号(self) -> int:
        with self._锁:
            return self._帧序号

    # ---------------- 三个循环 ----------------

    def _排空队列(self, 本代: Optional[_播放代] = None) -> None:
        """丢掉队列里还没解码的包 —— **必须释放**（它们是我们复制出来的引用）。"""
        代 = 本代 if 本代 is not None else self._本代
        if 代 is None:
            return
        for 队列 in (代.视频队列, 代.音频队列):
            while True:
                try:
                    条目 = 队列.get_nowait()
                except queue.Empty:
                    break
                self._放队列失败丢弃(代, 条目)

    def _该停(self, 本代: Optional[_播放代] = None) -> bool:
        """返回 True = 这个线程该退出了。

        判据有两个（缺一不可）：
        ① 自己那一代的"停"事件被立起来（用户点了停止/关窗）；
        ② **自己已经不是当前代了** —— 这是世代号的核心作用：
           老线程发现"有人开了新一代"就必须立刻退出，而且退出前不许再发任何 libav 调用
           （老线程手里那套资源马上就要被释放，再碰一下就是悬垂指针）。
        """
        代 = 本代 if 本代 is not None else self._本代
        if 代 is None:
            return True
        return 代.停.is_set() or 代 is not self._本代

    def _等一等(self, 本代: _播放代) -> bool:
        """暂停时在这里睡；返回 False 表示该退出（停止/换世代）。"""
        次数 = 0
        while 本代.暂停.is_set() and not self._该停(本代):
            time.sleep(0.01)
            次数 += 1
            if 次数 == 1:
                self._日志("[播放] 已暂停")
        return not self._该停(本代)

    def _解封装循环(self, 本代: _播放代) -> None:
        """解封装线程：**输入的唯一使用者**（读包/跳转都只在这里发生）。

        ``本代`` 是启动时带进来的：所有资源都从它取，绝不读 ``self.输入`` ——
        否则新一代一建好，这个老线程就会去读**别人的**格式上下文
        （两个线程共用一个 libav 上下文 = 内部断言 abort）。
        """
        try:
            while not self._该停(本代):
                if not self._等一等(本代):
                    return
                跳转 = self._取跳转请求(本代)
                包 = 本代.输入.读包()
                if 跳转 is not None:
                    # 跳转：先丢弃当前这一包，做完整冲刷，再继续读
                    if 包 is not None:
                        本代.输入.释放包()
                    self._执行跳转(本代, 跳转)
                    continue
                if 包 is None:
                    # EOF：给两个解码器送空包，把 B 帧等缓存吐干净
                    if 本代.视频 is not None:
                        self._放队列(本代, 本代.视频队列, ("冲刷", 本代.流世代), 强制=True)
                    if 本代.音频 is not None:
                        self._放队列(本代, 本代.音频队列, ("冲刷", 本代.流世代), 强制=True)
                    本代.结束.set()
                    return
                大小 = int(包["大小"])
                self._读入累计 += 大小
                self.统计.读入字节 += 大小
                if 本代.视频 is not None and 包["流序号"] == 本代.输入.视频流.序号:
                    新包 = 本代.输入.复制包()
                    本代.输入.释放包()
                    self._放队列(本代, 本代.视频队列, (包, 新包, 本代.流世代))
                elif (本代.音频 is not None
                      and 包["流序号"] == 本代.输入.音频流.序号):
                    新包 = 本代.输入.复制包()
                    本代.输入.释放包()
                    self._放队列(本代, 本代.音频队列, (包, 新包, 本代.流世代))
                else:
                    本代.输入.释放包()
                self._刷码率()
        except BaseException as 错:  # noqa: BLE001
            # 一个线程里的异常绝不该被吞掉（会变成"看起来在播、其实什么都没发生"）
            if not self._该停(本代):
                self._出错(f"解封装线程：{错!r}")

    def _放队列(self, 本代: _播放代, 队列: queue.Queue, 条目: tuple,
               强制: bool = False) -> None:
        """放进队列（条目 = ``(元信息, 包指针, 流世代)``）；队列满就等（背压），超时丢掉。

        ``强制`` 用于 EOF 的 ``("冲刷", 流世代)`` 标记：那是控制信号（不带包），
        给它更长的耐心（2 秒）把它送进去；实在送不进去就丢掉 ——
        **绝不能像老代码那样无限阻塞**（暂停时解码线程不消费，解封装线程会卡死在
        ``put()`` 里，连"停止"都要靠 join 超时才能脱身）。
        """
        截止 = time.time() + (2.0 if 强制 else 0.5)
        while not self._该停(本代):
            try:
                队列.put(条目, timeout=0.05)
                return
            except queue.Full:
                if time.time() > 截止:
                    self._放队列失败丢弃(本代, 条目)
                    return

    def _放队列失败丢弃(self, 本代: _播放代, 条目) -> None:
        """队列塞不进去时把包还掉（**必须**，否则每次背压都漏一个包的引用）。"""
        if isinstance(条目, tuple) and len(条目) == 3:
            try:
                本代.输入.释放新包(条目[1])
            except Exception:  # noqa: BLE001
                pass

    def _取跳转请求(self, 本代: _播放代) -> Optional[float]:
        with 本代.跳转锁:
            值 = 本代.跳转请求
            本代.跳转请求 = None
            return 值

    def _执行跳转(self, 本代: _播放代, 秒: float) -> None:
        """跳转。**由解封装线程执行**（输入只归它用），并且**不碰解码器**。

        为什么把 ``部件.冲刷()`` 从这里拿掉（这是 22:04:08 崩溃的根因）：
        冲刷是 ``avcodec_flush_buffers``，而 ``AVCodecContext`` 正被视频/音频线程用着
        （``avcodec_send_packet``/``receive_frame``）—— libav 的解码上下文不是线程安全的，
        两边一重叠，帧级多线程解码器的 ``async_lock`` 断言失败 → ``abort()``，
        整进程 SIGABRT（真机 core：崩溃线程就是 ``V2-解封装``）。
        现在的做法：这里只把**流世代 +1**，让"谁用谁冲刷" ——
        解码线程拿到带新流世代的包时，由**它自己**冲刷自己的解码器。
        """
        起点 = max(0.0, 秒 - 0.05)
        好 = 本代.输入.跳转(起点)
        self._排空队列(本代)
        with 本代.跳转锁:
            本代.流世代 += 1
        本代.时钟.重置(秒)
        with self._锁:
            self._最新帧 = None
        self.统计.当前时间秒 = 秒
        self._日志(f"[播放] 跳到 {秒:.1f}s（{'成功' if 好 else '容器不支持精确跳转'}）"
                 f"｜流世代 {本代.流世代}（冲刷交给解码线程自己做）")

    def _刷码率(self) -> None:
        现在 = time.monotonic()
        上次时刻, 上次字节 = self._码率基点
        if 现在 - 上次时刻 >= 1.0:
            增量 = self.统计.读入字节 - 上次字节
            self.统计.码率bps = 增量 * 8 / max(0.001, 现在 - 上次时刻)
            self._码率基点 = (现在, self.统计.读入字节)

    def _视频循环(self, 本代: _播放代) -> None:
        try:
            while not self._该停(本代):
                if not self._等一等(本代):
                    return
                try:
                    条目 = 本代.视频队列.get(timeout=0.2)
                except queue.Empty:
                    continue
                if isinstance(条目, tuple) and 条目 and 条目[0] == "冲刷":
                    # EOF 的"送空包"标记（控制信号，没有包指针）
                    流世代 = int(条目[1]) if len(条目) > 1 else 本代.流世代
                    self._按需冲刷(本代, 本代.视频, 流世代)
                    本代.视频.送空包()
                    self._收视频帧(本代)
                    continue
                _包, 新包, 流世代 = 条目
                if self._该停(本代):
                    # 停下来时不要再把包喂给解码器，但**必须**把它还掉（我们复制出来的）
                    本代.输入.释放新包(新包)
                    return
                self._按需冲刷(本代, 本代.视频, 流世代)
                try:
                    本代.视频.送包(新包)
                finally:
                    本代.输入.释放新包(新包)
                self._收视频帧(本代)
        except BaseException as 错:  # noqa: BLE001
            if not self._该停(本代):
                self._出错(f"视频线程：{错!r}")

    def _按需冲刷(self, 本代: _播放代, 解码器, 流世代: int) -> None:
        """解码线程在**自己的线程里**应用"跳转冲刷"。

        为什么必须由本线程做：``avcodec_flush_buffers`` 与 ``avcodec_send_packet``
        并发就是 abort（真机 coredump）。这里保证同一把锁（解码器内部那把）之下、
        同一个线程里，冲刷一定发生在"新世代的第一个包"之前。
        """
        if 解码器 is None:
            return
        if int(流世代) == int(getattr(解码器, "_已冲刷世代", 0)):
            return
        解码器.冲刷()
        解码器._已冲刷世代 = int(流世代)

    def _收视频帧(self, 本代: _播放代) -> None:
        提前帧: Optional[解码.解码帧] = None
        while True:
            if self._该停(本代):
                return
            with 本代.视频.用锁:
                代码 = 本代.视频.收帧()
                if 代码 < 0:
                    break
                帧 = 本代.视频.取帧()
            if 帧 is None:
                continue
            self.统计.已解视频帧 += 1
            self.统计.硬解帧 = int(getattr(本代.视频, "硬解帧数", 0))
            if 帧.宽 and not self.统计.输出尺寸:
                self.统计.输出尺寸 = f"{帧.宽}x{帧.高}"
            if 帧.时间秒 < 0:
                帧.时间秒 = self.统计.当前时间秒
            if not self._等到该显示(本代, 帧):
                self.统计.已丢视频帧 += 1
                continue
            with self._锁:
                self._最新帧 = 帧
                self._帧序号 += 1
            self.统计.当前时间秒 = 帧.时间秒
        if 提前帧 is not None:
            with self._锁:
                self._最新帧 = 提前帧

    def _等到该显示(self, 本代: _播放代, 帧: 解码.解码帧) -> bool:
        """按音频时钟等（或直接丢）。返回 False = 这帧太晚了，丢掉。

        ⚠️ 里面有**看门狗**：如果时钟 1 秒都没往前走（音频早放完了、或者设备出问题），
        就放行这一帧并告警 —— 宁可时序有点偏，也绝不能死等在这里
        （Windows CI 上实测到过：帧卡在 1，视频线程一直在 sleep）。
        """
        延迟 = 本代.输出.延迟秒() if 本代.输出 is not None else 0.0
        上次时钟 = 本代.时钟.现在秒(延迟)
        上次前进时刻 = time.monotonic()
        等待起点 = 上次前进时刻
        while not self._该停(本代):
            if 本代.暂停.is_set():
                time.sleep(0.01)
                上次前进时刻 = time.monotonic()
                上次时钟 = 本代.时钟.现在秒(延迟)
                continue
            现在 = 本代.时钟.现在秒(延迟)
            if 现在 > 上次时钟 + 1e-4:
                上次时钟 = 现在
                上次前进时刻 = time.monotonic()
            差 = 帧.时间秒 - 现在
            # 排查用：把"视频线程眼里的数字"记下来（只出几帧时靠它定位，别猜）
            self._等帧调试 = (round(帧.时间秒, 3), round(现在, 3), round(差, 3),
                          getattr(self, "_等帧轮数", 0))
            self._等帧轮数 = getattr(self, "_等帧轮数", 0) + 1
            if 差 > 视频提前量:
                if time.monotonic() - 上次前进时刻 > 1.0:
                    self._日志("[播放] 提示：时钟 1 秒没动（音频可能已放完），"
                             "为免卡住先放行这一帧")
                    self._时钟卡住次数 = getattr(self, "_时钟卡住次数", 0) + 1
                    return True
                time.sleep(min(0.02, max(0.002, 差 - 视频提前量)))
                continue
            if 差 < -丢帧阈值:
                return False
            return True
        return False

    def _音频循环(self, 本代: _播放代) -> None:
        try:
            while not self._该停(本代):
                if not self._等一等(本代):
                    return
                try:
                    条目 = 本代.音频队列.get(timeout=0.2)
                except queue.Empty:
                    continue
                if isinstance(条目, tuple) and 条目 and 条目[0] == "冲刷":
                    流世代 = int(条目[1]) if len(条目) > 1 else 本代.流世代
                    self._按需冲刷(本代, 本代.音频, 流世代)
                    本代.音频.送空包()
                    self._收音频块(本代)
                    # 音频到此为止：时钟改由真实时间推进（否则视频会死等，见 音频时钟.音频已完）
                    if isinstance(本代.时钟, 音频时钟):
                        本代.时钟.音频已完 = True
                    continue
                包, 新包, 流世代 = 条目
                if self._该停(本代):
                    本代.输入.释放新包(新包)
                    return
                self._按需冲刷(本代, 本代.音频, 流世代)
                try:
                    本代.音频.送包(新包)
                finally:
                    本代.输入.释放新包(新包)
                self._收音频块(本代)
        except BaseException as 错:  # noqa: BLE001
            if not self._该停(本代):
                self._出错(f"音频线程：{错!r}")

    def _收音频块(self, 本代: _播放代) -> None:
        while True:
            if self._该停(本代):
                return
            with 本代.音频.用锁:
                代码 = 本代.音频.收帧()
                if 代码 < 0:
                    break
                块 = 本代.音频.取块()
            if 块 is None:
                continue
            self.统计.已解音频块 += 1
            数据 = self._按音量缩放(块.数据)
            try:
                本代.输出.写(数据)
            except Exception as 错:  # noqa: BLE001
                self._日志(f"[音频] 写入失败：{错}")
                return
            本代.时钟.记写入(块.样本数)
            self.统计.已播音频秒 = 本代.时钟.现在秒(0.0)

    def _按音量缩放(self, 数据: bytes) -> bytes:
        """S16 音量缩放（纯 Python 但只做整数乘法，48kHz 立体声完全够快）。"""
        音量 = self.音量
        if abs(音量 - 1.0) < 0.001 or not 数据:
            return 数据
        样本数 = len(数据) // 2
        数组 = (ctypes.c_int16 * 样本数).from_buffer_copy(数据)
        for i in range(样本数):
            值 = int(数组[i] * 音量)
            数组[i] = -32768 if 值 < -32768 else (32767 if 值 > 32767 else 值)
        return bytes(数组)

    def _出错(self, 文本: str) -> None:
        self.统计.状态 = 播放状态.出错
        self.状态 = 播放状态.出错
        self._日志(f"[播放] 出错：{文本}")

    # ---------------- 结束判定 ----------------

    def 是否结束(self) -> bool:
        """文件放完了吗（解封装到头 + 队列空 + 音频写完了）。"""
        代 = self._本代
        if 代 is None or not 代.结束.is_set():
            return False
        if not 代.视频队列.empty() or not 代.音频队列.empty():
            return False
        if 代.输出 is not None and 代.时钟.现在秒(0.0) < max(
                0.0, self.统计.总时长秒 - 0.3):
            return False
        if self.状态 != 播放状态.结束:
            self.状态 = 播放状态.结束
            self.统计.状态 = self.状态
            self._日志("[播放] 播放结束")
        return True

    def 截图(self, 路径: str) -> bool:
        """把当前帧存成 PNG（画面是我们自己的数据，截图不需要问播放器）。

        ⚠️ 目录不存在要先建出来：`QImage.save` 失败是**静默**的（只返回 False），
        在全新检出的目录里"截图没成功"就是这么来的（Windows CI 实测）。
        """
        帧 = self.取最新帧()
        if 帧 is None:
            return False
        try:
            目标 = Path(路径)
            if 目标.parent and not 目标.parent.exists():
                目标.parent.mkdir(parents=True, exist_ok=True)
            图 = 帧.转QImage()
            好 = bool(图.save(str(目标)))
            if not 好:
                self._日志(f"[播放] 截图保存失败（{目标}）：检查目录权限/路径")
            return 好
        except Exception as 错:  # noqa: BLE001
            self._日志(f"[播放] 截图异常：{错}")
            return False
