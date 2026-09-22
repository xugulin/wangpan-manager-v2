"""音频输出：**自己接系统音频**（不用 Qt Multimedia、不用第三方库）。

后端顺序
========
1. **PulseAudio**（``libpulse-simple``）：Linux 桌面首选，阻塞式写、自带延迟查询
   （``pa_simple_get_latency`` —— 音画同步的"音频主时钟"就靠它）；
2. **空设备**（``空输出``）：没有声卡/没有 PulseAudio 时使用 —— 播放继续、
   只是没声音，并把这件事如实写进日志（绝不假装成功）。

Windows 上第一版先用空设备（WASAPI 后端见 docs/路线图.md），
这样播放内核与平台解耦：**引擎只认"能不能写、延迟多少"**。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import time
from typing import Optional

__all__ = ["音频输出设备", "打开输出", "空输出", "Pulse输出", "可用后端"]

#: pa_sample_format_t 里我们要的那个：S16LE == 3
PA_SAMPLE_S16LE = 3
PA_STREAM_PLAYBACK = 1
PA_STREAM_PLAYBACK_枚举 = 1


class _样例规格(ctypes.Structure):
    _fields_ = [("format", ctypes.c_int), ("rate", ctypes.c_uint32),
                ("channels", ctypes.c_ubyte)]


class _缓冲属性(ctypes.Structure):
    _fields_ = [("maxlength", ctypes.c_uint32),
                ("tlength", ctypes.c_uint32),
                ("prebuf", ctypes.c_uint32),
                ("minreq", ctypes.c_uint32),
                ("fragsize", ctypes.c_uint32)]


class 音频输出设备:
    """音频输出接口：``写(字节)`` / ``延迟秒()`` / ``排空()`` / ``关()``。"""

    名字 = "抽象"

    def 写(self, 数据: bytes) -> bool:
        raise NotImplementedError

    def 延迟秒(self) -> float:
        return 0.0

    def 排空(self) -> None:
        return

    def 恢复(self) -> None:
        """从暂停恢复（PulseAudio 的 simple API 没有 pause，这里是钩子）。"""
        return

    def 关(self) -> None:
        return


class 空输出(音频输出设备):
    """没有可用音频设备时的兜底：按样本数**模拟**时间流逝（保证播放不卡）。"""

    名字 = "空输出（没有可用音频设备）"

    def __init__(self, 采样率: int = 48000, 声道数: int = 2) -> None:
        self.采样率 = 采样率
        self.声道数 = 声道数
        self._已写样本 = 0
        self._开始 = time.monotonic()

    def 写(self, 数据: bytes) -> bool:
        每帧字节 = max(1, self.声道数 * 2)
        self._已写样本 += len(数据) // 每帧字节
        return True

    def 延迟秒(self) -> float:
        已过 = time.monotonic() - self._开始
        应播 = self._已写样本 / max(1, self.采样率)
        return max(0.0, 应播 - 已过)


class Pulse输出(音频输出设备):
    """``pa_simple`` 阻塞式输出（自带延迟查询 → 天然适合当音频主时钟）。"""

    名字 = "PulseAudio（libpulse-simple）"

    def __init__(self, 采样率: int = 48000, 声道数: int = 2,
                 应用名: str = "网盘管理V2", 流名: str = "播放") -> None:
        名字 = ctypes.util.find_library("pulse-simple") or "libpulse-simple.so.0"
        self._库 = ctypes.CDLL(名字)
        L = self._库
        L.pa_simple_new.restype = ctypes.c_void_p
        L.pa_simple_new.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int,
                                    ctypes.c_char_p, ctypes.c_char_p,
                                    ctypes.POINTER(_样例规格), ctypes.c_void_p,
                                    ctypes.POINTER(_缓冲属性),
                                    ctypes.POINTER(ctypes.c_int)]
        L.pa_simple_write.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_size_t, ctypes.POINTER(ctypes.c_int)]
        L.pa_simple_write.restype = ctypes.c_int
        L.pa_simple_drain.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        L.pa_simple_drain.restype = ctypes.c_int
        L.pa_simple_get_latency.argtypes = [ctypes.c_void_p,
                                            ctypes.POINTER(ctypes.c_int)]
        L.pa_simple_get_latency.restype = ctypes.c_uint64
        L.pa_simple_free.argtypes = [ctypes.c_void_p]
        错误 = ctypes.c_int(0)
        规格 = _样例规格(PA_SAMPLE_S16LE, 采样率, 声道数)
        self._流 = L.pa_simple_new(None, 应用名.encode(), PA_STREAM_PLAYBACK,
                                 None, 流名.encode(), ctypes.byref(规格), None,
                                 None, ctypes.byref(错误))
        if not self._流:
            raise RuntimeError(f"连不上 PulseAudio（错误码 {错误.value}）")
        self.采样率 = 采样率
        self.声道数 = 声道数
        self._错误 = ctypes.c_int(0)
        self._已写字节 = 0

    def 写(self, 数据: bytes) -> bool:
        缓冲 = ctypes.create_string_buffer(数据, len(数据))
        代码 = self._库.pa_simple_write(self._流, 缓冲, len(数据),
                                      ctypes.byref(self._错误))
        if 代码 < 0:
            raise RuntimeError(f"写音频失败（错误码 {self._错误.value}）")
        self._已写字节 += len(数据)
        return True

    def 延迟秒(self) -> float:
        微秒 = self._库.pa_simple_get_latency(self._流, ctypes.byref(self._错误))
        if self._错误.value < 0:
            return 0.0
        return float(微秒) / 1_000_000.0

    def 排空(self) -> None:
        self._库.pa_simple_drain(self._流, ctypes.byref(self._错误))

    def 关(self) -> None:
        if getattr(self, "_流", None):
            self._库.pa_simple_free(self._流)
            self._流 = None


def 可用后端() -> list[str]:
    结果 = []
    if os.name != "nt":
        try:
            ctypes.CDLL(ctypes.util.find_library("pulse-simple") or "libpulse-simple.so.0")
            结果.append("pulse")
        except OSError:
            pass
    结果.append("空")
    return 结果


def 打开输出(采样率: int = 48000, 声道数: int = 2, 首选: str = "") -> 音频输出设备:
    """按优先级打开输出；失败就退到空设备（并把原因记在 :attr:`失败原因`）。"""
    想要 = 首选 or ("pulse" if os.name != "nt" else "空")
    if 想要 == "pulse":
        try:
            return Pulse输出(采样率, 声道数)
        except Exception as 错:  # noqa: BLE001
            设备 = 空输出(采样率, 声道数)
            设备.失败原因 = f"PulseAudio 打不开（{错}），已退回空输出"
            return 设备
    设备 = 空输出(采样率, 声道数)
    设备.失败原因 = "按配置使用空输出（Windows 的 WASAPI 后端见路线图）"
    return 设备
