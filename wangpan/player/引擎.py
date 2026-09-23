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
        self._线程们: list[threading.Thread] = []
        self._视频队列: queue.Queue = queue.Queue(maxsize=视频队列深度)
        self._音频队列: queue.Queue = queue.Queue(maxsize=音频队列深度)
        self._停 = threading.Event()          # 停止（收尾）
        self._暂停 = threading.Event()        # 暂停
        self._跳转请求: Optional[float] = None
        self._跳转锁 = threading.Lock()
        self._结束 = threading.Event()
        self._读入累计 = 0
        self._码率基点 = (0.0, 0)
        self._输出尺寸 = (0, 0)
        #: 这一路要不要强制软解（由 :meth:`打开` 的 ``不要硬解`` 设定）
        self._不要硬解 = False

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
        """
        self.停止()
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
        self.输入 = self._打开带降级(地址, 选项)
        # 让网络 I/O 能被"停止"打断（否则关闭时正在读 → 悬垂指针 → 段错误）
        try:
            self.输入.装中断回调(lambda: self._停.is_set())
        except Exception as 错:  # noqa: BLE001
            self._日志(f"[网络] 中断回调没装上（不影响播放）：{错}")
        self.统计.总时长秒 = self.输入.时长秒
        self.统计.音视频 = "、".join(流.一句话() for 流 in self.输入.流们)
        self._日志(f"[播放] 已打开：{self.统计.音视频}｜时长 {self.输入.时长秒:.1f}s")
        self.视频 = None
        self.音频 = None
        if self.输入.视频流 is not None:
            self.视频 = 解码.视频解码器(self.输入.绑定, self.输入.视频流)
            self.视频.允许硬解 = not getattr(self, "_不要硬解", False)
            if self._输出尺寸 != (0, 0):
                self.视频.设置输出尺寸(*self._输出尺寸)
            self.视频.打开()
            self.统计.硬解 = self.视频.硬解
            self.统计.帧率 = self.输入.视频流.帧率
        if self.输入.音频流 is not None:
            self.音频 = 解码.音频解码器(self.输入.绑定, self.输入.音频流)
            self.音频.打开()
            self.输出 = 打开输出(解码.输出采样率, 解码.输出声道数)
            self.统计.音频设备 = self.输出.名字
            self.时钟 = 音频时钟(解码.输出采样率)
            if getattr(self.输出, "失败原因", ""):
                self._日志(f"[音频] {self.输出.失败原因}")
        else:
            # 没有音轨 → 用系统时钟当主时钟（否则视频永远等不到时钟，画面不出）
            self.时钟 = 系统时钟()
            self.统计.音频设备 = "（这个文件没有音轨，用系统时钟）"
        self.状态 = 播放状态.暂停

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
        """起播（或从暂停继续）。第一次调用会拉起三个线程。"""
        if self.输入 is None:
            raise RuntimeError("还没打开文件（先 打开()）")
        with self._锁:
            if not self._线程们:
                self._停.clear()
                self._结束.clear()
                造 = [("解封装", self._解封装循环), ("视频", self._视频循环)]
                if self.音频 is not None and self.输出 is not None:
                    造.append(("音频", self._音频循环))
                for 名, 函数 in 造:
                    线程 = threading.Thread(target=函数, name=f"V2-{名}", daemon=True)
                    线程.start()
                    self._线程们.append(线程)
            self._暂停.clear()
            if isinstance(self.时钟, 系统时钟):
                self.时钟.暂停(False)
            if self.输出 is not None:
                try:
                    self.输出.恢复()          # PulseAudio 没有 pause，这里保留钩子
                except Exception:  # noqa: BLE001
                    pass
            self.状态 = 播放状态.播放中
            self.统计.状态 = self.状态

    def 暂停切换(self) -> bool:
        """返回是否处于暂停状态。"""
        if self.状态 == 播放状态.播放中:
            self._暂停.set()
            if isinstance(self.时钟, 系统时钟):
                self.时钟.暂停(True)
            self.状态 = 播放状态.暂停
        elif self.状态 == 播放状态.暂停:
            self._暂停.clear()
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
        with self._跳转锁:
            self._跳转请求 = max(0.0, float(秒))

    def 设置音量(self, 音量: float) -> None:
        self.音量 = max(0.0, min(1.5, float(音量)))

    def 设置倍速(self, 倍速: float) -> float:
        """设置播放倍速（0.25~4.0），返回真正生效的值。

        实现方式：**音频按"倍速 × 采样率"重采样**（等于把音频变短），视频时钟也按
        同一个倍速折算 —— 两边用同一个系数，音画就不会散。
        注意：这会改变音调（和 libvlc/VLC 的倍速行为一致）而不是保持音调。
        """
        倍速 = max(0.25, min(4.0, float(倍速 or 1.0)))
        self.倍速 = 倍速
        if isinstance(self.时钟, (音频时钟, 系统时钟)):
            self.时钟.倍速 = 倍速
        if self.音频 is not None:
            try:
                self.音频.设置倍速(倍速)
            except Exception:  # noqa: BLE001
                pass
        self._日志(f"[播放] 倍速 {倍速:.2f}×")
        return 倍速

    def 逐帧(self) -> bool:
        """暂停 + 前进一帧（"逐帧看"）。返回是否真的前进了一帧。

        注意：**光"收帧"不够** —— 解码是"喂包才有帧"，暂停时队列里可能已经空了，
        所以要"没帧就喂一个包"，最多试几十次（B 帧重排时前面几个包本来就不出帧）。
        """
        if self.输入 is None or self.视频 is None:
            return False
        if self.状态 == 播放状态.播放中:
            self.设置暂停(True)
        for _尝试 in range(40):
            帧 = self.视频.解码一帧()
            if 帧 is not None:
                if 帧.时间秒 >= 0:
                    self.统计.当前时间秒 = 帧.时间秒
                self.统计.已解视频帧 += 1
                self.统计.硬解帧 = int(getattr(self.视频, "硬解帧数", 0))
                with self._锁:
                    self._最新帧 = 帧
                    self._帧序号 += 1
                return True
            try:
                条目 = self._视频队列.get_nowait()
            except queue.Empty:
                return False
            if 条目 == "冲刷":
                self.视频.送空包()
                continue
            _包, 新包 = 条目
            try:
                self.视频.送包(新包)
            finally:
                self.输入.释放新包(新包)
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

    def 停止(self) -> None:
        self._停.set()          # 先把中断标志立起来：正在阻塞的 av_read_frame 会立刻返回
        self._暂停.clear()
        剩下 = []
        for 线程 in list(self._线程们):
            线程.join(timeout=3.0)
            if 线程.is_alive():
                剩下.append(线程.name)
        self._线程们.clear()
        if 剩下:
            # 宁可留着守护线程（进程退出时自然结束），也不要"读还没返回就关输入"
            self._日志("[播放] 提示：还有线程没停干净（" + "、".join(剩下)
                     + "），这次不强制关闭输入（避免悬垂指针崩溃）")
            self._停.clear()
            self.输入 = None
            return
        self._排空队列()
        try:
            if self.输出 is not None:
                self.输出.关()
        except Exception:  # noqa: BLE001
            pass
        self.输出 = None
        for 部件 in (self.视频, self.音频):
            try:
                if 部件 is not None:
                    部件.关()
            except Exception:  # noqa: BLE001
                pass
        self.视频 = self.音频 = None
        try:
            if self.输入 is not None:
                self.输入.关闭()
        except Exception:  # noqa: BLE001
            pass
        self.输入 = None
        if self.状态 != 播放状态.结束:
            self.状态 = 播放状态.空闲
        self.统计.状态 = self.状态

    # ---------------- 界面取帧 ----------------

    def 取最新帧(self) -> Optional[解码.解码帧]:
        with self._锁:
            return self._最新帧

    def 取帧序号(self) -> int:
        with self._锁:
            return self._帧序号

    # ---------------- 三个循环 ----------------

    def _排空队列(self) -> None:
        """丢掉队列里还没解码的包 —— **必须释放**（它们是我们复制出来的引用）。"""
        for 队列 in (self._视频队列, self._音频队列):
            while True:
                try:
                    条目 = 队列.get_nowait()
                except queue.Empty:
                    break
                if isinstance(条目, tuple) and len(条目) == 2 and self.输入 is not None:
                    try:
                        self.输入.释放新包(条目[1])
                    except Exception:  # noqa: BLE001
                        pass

    def _该停(self) -> bool:
        return self._停.is_set()

    def _等一等(self) -> bool:
        """暂停时在这里睡；返回 False 表示该退出（停止）。"""
        次数 = 0
        while self._暂停.is_set() and not self._该停():
            time.sleep(0.01)
            次数 += 1
            if 次数 == 1:
                self._日志("[播放] 已暂停")
        return not self._该停()

    def _解封装循环(self) -> None:
        try:
            while not self._该停():
                if not self._等一等():
                    return
                跳转 = self._取跳转请求()
                包 = self.输入.读包()
                if 跳转 is not None:
                    # 跳转：先丢弃当前这一包，做完整冲刷，再继续读
                    if 包 is not None:
                        self.输入.释放包()
                    self._执行跳转(跳转)
                    continue
                if 包 is None:
                    # EOF：给两个解码器送空包，把 B 帧等缓存吐干净
                    if self.视频 is not None:
                        self._视频队列.put("冲刷")
                    if self.音频 is not None:
                        self._音频队列.put("冲刷")
                    self._结束.set()
                    return
                大小 = int(包["大小"])
                self._读入累计 += 大小
                self.统计.读入字节 += 大小
                if self.视频 is not None and 包["流序号"] == self.输入.视频流.序号:
                    新包 = self.输入.复制包()
                    self.输入.释放包()
                    self._放队列(self._视频队列, (包, 新包))
                elif (self.音频 is not None
                      and 包["流序号"] == self.输入.音频流.序号):
                    新包 = self.输入.复制包()
                    self.输入.释放包()
                    self._放队列(self._音频队列, (包, 新包))
                else:
                    self.输入.释放包()
                self._刷码率()
        except Exception as 错:  # noqa: BLE001
            self._出错(f"解封装线程：{错}")

    def _放队列(self, 队列: queue.Queue, 条目: tuple) -> None:
        """放进队列（条目 = ``(元信息, 包指针)``）；队列满就等（背压），超时丢掉。"""
        截止 = time.time() + 0.5
        while not self._该停():
            try:
                队列.put(条目, timeout=0.05)
                return
            except queue.Full:
                if time.time() > 截止:
                    self.输入.释放新包(条目[1])
                    return

    def _取跳转请求(self) -> Optional[float]:
        with self._跳转锁:
            值 = self._跳转请求
            self._跳转请求 = None
            return 值

    def _执行跳转(self, 秒: float) -> None:
        起点 = max(0.0, 秒 - 0.05)
        好 = self.输入.跳转(起点)
        self._排空队列()
        for 部件 in (self.视频, self.音频):
            if 部件 is not None:
                部件.冲刷()
        self.时钟.重置(秒)
        with self._锁:
            self._最新帧 = None
        self.统计.当前时间秒 = 秒
        self._日志(f"[播放] 跳到 {秒:.1f}s（{'成功' if 好 else '容器不支持精确跳转'}）")

    def _刷码率(self) -> None:
        现在 = time.monotonic()
        上次时刻, 上次字节 = self._码率基点
        if 现在 - 上次时刻 >= 1.0:
            增量 = self.统计.读入字节 - 上次字节
            self.统计.码率bps = 增量 * 8 / max(0.001, 现在 - 上次时刻)
            self._码率基点 = (现在, self.统计.读入字节)

    def _视频循环(self) -> None:
        try:
            while not self._该停():
                if not self._等一等():
                    return
                try:
                    条目 = self._视频队列.get(timeout=0.2)
                except queue.Empty:
                    continue
                if 条目 == "冲刷":
                    self.视频.送空包()
                    self._收视频帧()
                    continue
                包, 新包 = 条目
                try:
                    self.视频.送包(新包)
                finally:
                    self.输入.释放新包(新包)
                self._收视频帧()
        except Exception as 错:  # noqa: BLE001
            self._出错(f"视频线程：{错}")

    def _收视频帧(self) -> None:
        提前帧: Optional[解码.解码帧] = None
        while True:
            代码 = self.视频.收帧()
            if 代码 < 0:
                break
            帧 = self.视频.取帧()
            if 帧 is None:
                continue
            self.统计.已解视频帧 += 1
            self.统计.硬解帧 = int(getattr(self.视频, "硬解帧数", 0))
            if 帧.宽 and not self.统计.输出尺寸:
                self.统计.输出尺寸 = f"{帧.宽}x{帧.高}"
            if 帧.时间秒 < 0:
                帧.时间秒 = self.统计.当前时间秒
            if not self._等到该显示(帧):
                self.统计.已丢视频帧 += 1
                continue
            with self._锁:
                self._最新帧 = 帧
                self._帧序号 += 1
            self.统计.当前时间秒 = 帧.时间秒
        if 提前帧 is not None:
            with self._锁:
                self._最新帧 = 提前帧

    def _等到该显示(self, 帧: 解码.解码帧) -> bool:
        """按音频时钟等（或直接丢）。返回 False = 这帧太晚了，丢掉。

        ⚠️ 里面有**看门狗**：如果时钟 1 秒都没往前走（音频早放完了、或者设备出问题），
        就放行这一帧并告警 —— 宁可时序有点偏，也绝不能死等在这里
        （Windows CI 上实测到过：帧卡在 1，视频线程一直在 sleep）。
        """
        延迟 = self.输出.延迟秒() if self.输出 is not None else 0.0
        上次时钟 = self.时钟.现在秒(延迟)
        上次前进时刻 = time.monotonic()
        等待起点 = 上次前进时刻
        while not self._该停():
            if self._暂停.is_set():
                time.sleep(0.01)
                上次前进时刻 = time.monotonic()
                上次时钟 = self.时钟.现在秒(延迟)
                continue
            现在 = self.时钟.现在秒(延迟)
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

    def _音频循环(self) -> None:
        try:
            while not self._该停():
                if not self._等一等():
                    return
                try:
                    条目 = self._音频队列.get(timeout=0.2)
                except queue.Empty:
                    continue
                if 条目 == "冲刷":
                    self.音频.送空包()
                    self._收音频块()
                    # 音频到此为止：时钟改由真实时间推进（否则视频会死等，见 音频时钟.音频已完）
                    if isinstance(self.时钟, 音频时钟):
                        self.时钟.音频已完 = True
                    continue
                包, 新包 = 条目
                try:
                    self.音频.送包(新包)
                finally:
                    self.输入.释放新包(新包)
                self._收音频块()
        except Exception as 错:  # noqa: BLE001
            self._出错(f"音频线程：{错}")

    def _收音频块(self) -> None:
        while True:
            代码 = self.音频.收帧()
            if 代码 < 0:
                break
            块 = self.音频.取块()
            if 块 is None:
                continue
            self.统计.已解音频块 += 1
            数据 = self._按音量缩放(块.数据)
            try:
                self.输出.写(数据)
            except Exception as 错:  # noqa: BLE001
                self._日志(f"[音频] 写入失败：{错}")
                return
            self.时钟.记写入(块.样本数)
            self.统计.已播音频秒 = self.时钟.现在秒(0.0)

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
        if not self._结束.is_set():
            return False
        if not self._视频队列.empty() or not self._音频队列.empty():
            return False
        if self.输出 is not None and self.时钟.现在秒(0.0) < max(
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
