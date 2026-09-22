"""解码：视频帧 → RGB（swscale），音频帧 → S16 立体声 48k（swresample）。

这一层是"自己写播放器"的硬骨头之一，但边界很清楚：

* **只做转换，不做同步** —— 什么时刻该显示这一帧、音频该写多少，是
  :mod:`wangpan.player.时钟` 与 :mod:`wangpan.player.引擎` 的事；
* 视频输出固定 **RGB0（32 位）**：Qt 的 ``QImage(Format_RGB32)`` 直接吃，
  少一次格式判断（后面要加 GPU 渲染时，只换这一层的出口）；
* 音频输出固定 **S16 / 立体声 / 48kHz**：几乎所有声卡都吃，音量靠重采样前后缩放；
* 每个解码器**只被一个线程使用**（libav 的 AVCodecContext 不是线程安全的）。

硬解（VAAPI / D3D11VA）留了钩子：``视频解码器.请求硬解()`` 目前只在日志里说明
"还没实现"，不假装成功 —— 见 docs/路线图.md。
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Optional

from ..ffmpeg import 绑定 as B
from ..ffmpeg.绑定 import 常量
from .解封装 import 流信息

__all__ = ["视频解码器", "音频解码器", "解码帧", "音频块"]

#: 音频输出参数（固定，够用且兼容性最好）
输出采样率 = 48000
输出声道数 = 2
输出样本格式 = 常量["AV_SAMPLE_FMT_S16"]


@dataclass
class 解码帧:
    """一帧视频：RGBA/BGRX 字节 + 尺寸 + 时间（秒）。"""

    数据: bytes
    宽: int
    高: int
    步长: int
    时间秒: float
    关键帧: bool = False

    def 转QImage(self):                       # noqa: N802 - Qt 命名
        """转成 Qt 图像（拷贝一次；跨线程用最稳）。"""
        from PySide6.QtGui import QImage
        return QImage(self.数据, self.宽, self.高, self.步长,
                      QImage.Format.Format_RGB32).copy()


@dataclass
class 音频块:
    """一段音频：S16 交错立体声字节 + 起始时间（秒）。"""

    数据: bytes
    样本数: int
    时间秒: float


class _基础解码器:
    def __init__(self, 绑定: B.绑定, 流: 流信息, 名字: str = "") -> None:
        self.绑定 = 绑定
        self.流 = 流
        self.名字 = 名字 or 流.一句话()
        self.上下文 = 0
        self._帧指针 = 0
        self.已解帧数 = 0

    def 打开(self) -> None:
        库 = self.绑定
        上下文 = 库.avcodec.avcodec_alloc_context3(None)
        if not 上下文:
            raise RuntimeError(f"{self.名字}：分配解码上下文失败")
        self.上下文 = int(上下文)
        代码 = 库.avcodec.avcodec_parameters_to_context(self.上下文, self.流.参数指针)
        if 代码 < 0:
            raise RuntimeError(f"{self.名字}：拷贝流参数失败：{库.错误文本(代码)}")
        解码器 = 库.avcodec.avcodec_find_decoder(self.流.编码)
        if not 解码器:
            raise RuntimeError(f"{self.名字}：找不到解码器（{self.流.编码名}）")
        # 多线程解码：让 FFmpeg 自己开帧级线程（4K 软解就靠它）
        B.写i32(self.上下文, "AVCodecContext", "thread_count", 0)   # 0 = 自动
        B.写i32(self.上下文, "AVCodecContext", "thread_type", 3)     # FRAME|SLICE
        self.配置硬解()
        代码 = 库.avcodec.avcodec_open2(self.上下文, 解码器, None)
        if 代码 < 0:
            raise RuntimeError(f"{self.名字}：打开解码器失败：{库.错误文本(代码)}")
        self._帧指针 = int(库.avutil.av_frame_alloc() or 0)
        if not self._帧指针:
            raise RuntimeError(f"{self.名字}：分配帧失败")
        if hasattr(self, "硬解就绪"):
            self.硬解就绪()

    def 配置硬解(self) -> None:
        """硬解钩子（子类覆盖）。默认什么都不做。"""
        return

    def 关(self) -> None:
        库 = self.绑定
        try:
            if getattr(self, "_软件帧", 0):
                指针 = ctypes.c_void_p(self._软件帧)
                库.avutil.av_frame_free(ctypes.byref(指针))
                self._软件帧 = 0
        except Exception:  # noqa: BLE001
            pass
        # ⚠️ 这里**不能**自己 av_buffer_unref 设备引用：
        #    ``AVCodecContext.hw_device_ctx`` 的所有权归上下文，
        #    ``avcodec_free_context`` 会释放它 —— 我们再释放一次就是 double free
        #    （实测：double free or corruption (!prev) 直接 abort）。
        #    我们只把 Python 侧的记录清掉。
        self._硬解设备引用 = 0
        try:
            if self._帧指针:
                指针 = ctypes.c_void_p(self._帧指针)
                库.avutil.av_frame_free(ctypes.byref(指针))
                self._帧指针 = 0
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.上下文:
                指针 = ctypes.c_void_p(self.上下文)
                库.avcodec.avcodec_free_context(ctypes.byref(指针))
                self.上下文 = 0
        except Exception:  # noqa: BLE001
            pass

    def 冲刷(self) -> None:
        """跳转后必须冲刷（否则会吐出跳转前的帧）。"""
        if self.上下文:
            self.绑定.avcodec.avcodec_flush_buffers(self.上下文)

    def 送包(self, 包指针: int) -> int:
        return self.绑定.avcodec.avcodec_send_packet(self.上下文, 包指针)

    def 收帧(self) -> int:
        return self.绑定.avcodec.avcodec_receive_frame(self.上下文, self._帧指针)

    def 送空包(self) -> int:
        """送 NULL 包表示"数据完了"（冲刷解码器内部的 B 帧等）。"""
        return self.绑定.avcodec.avcodec_send_packet(self.上下文, None)

    # 时间戳换算：帧 pts（流时间基）→ 秒
    def _帧时间(self, 帧指针: int) -> float:
        分子, 分母 = self.流.时间基
        if not 分母:
            return -1.0
        值 = B.读i64(帧指针, "AVFrame", "best_effort_timestamp")
        if 值 == 常量["AV_NOPTS_VALUE"]:
            值 = B.读i64(帧指针, "AVFrame", "pts")
        if 值 == 常量["AV_NOPTS_VALUE"]:
            return -1.0
        return 值 * 分子 / 分母

    def _帧宽高(self, 帧指针: int) -> tuple[int, int]:
        return (B.读i32(帧指针, "AVFrame", "width"),
                B.读i32(帧指针, "AVFrame", "height"))

    @staticmethod
    def _图像指针数组(帧指针: int, 条数: int = 4):
        """把 AVFrame.data[0..n] / linesize[0..n] 取成 ctypes 数组。"""
        数据基 = 帧指针 + B.偏移["AVFrame.data"]
        行基 = 帧指针 + B.偏移["AVFrame.linesize"]
        数据 = (ctypes.c_void_p * 条数)()
        行 = (ctypes.c_int * 条数)()
        for i in range(条数):
            数据[i] = ctypes.c_void_p.from_address(数据基 + 8 * i).value
            行[i] = ctypes.c_int.from_address(行基 + 4 * i).value
        return 数据, 行


class 视频解码器(_基础解码器):
    """视频解码 + 缩放到 RGB0。"""

    def __init__(self, 绑定: B.绑定, 流: 流信息) -> None:
        super().__init__(绑定, 流)
        #: 输出给界面的目标尺寸（0 = 原始尺寸）。**这一项直接决定性能**：
        #: 4K60 全尺寸 RGB 是 3840×1632×4 ≈ 25 MB/帧，60fps 就是 1.5 GB/s 的拷贝，
        #: 实测解码 300 帧要 7.7s（明明解码只用 2.2s）；缩到显示尺寸（比如 1280×544）
        #: 只剩 1/6，拷贝与 swscale 的时间立刻降下来。
        self._输出宽 = 0
        self._输出高 = 0
        self.缩放器 = 0
        self._缓冲 = None
        self._目标宽 = 0
        self._目标高 = 0
        self._源宽 = 0
        self._源高 = 0
        self._源格式 = -1
        self.硬解 = "软解"
        self._硬解设备引用 = 0
        self._硬件像素格式 = -1
        self._硬解准备: tuple[str, str] | None = None
        self._硬解失败原因 = ""
        self._回调 = None                 # get_format 回调必须留引用，否则被 GC 掉就崩
        self._软件帧 = 0                  # 硬件帧拷回来的"内存帧"
        self.硬解帧数 = 0

    def 配置硬解(self) -> None:
        """挑一个硬件解码设备挂上去（VAAPI / D3D11VA）；不行就老实软解。

        为什么要"设备上下文 + get_format 回调"这一套（这是 libav 硬解的标准姿势）：

        * ``av_hwdevice_ctx_create`` 建出解码设备（Linux 用 VAAPI + /dev/dri/renderD128，
          Windows 用 D3D11VA）；
        * 把设备挂到 ``AVCodecContext.hw_device_ctx``，并给 ``get_format`` 回调 ——
          解码器会拿着"我能输出的像素格式清单"来问我们选哪个，我们**挑硬件那个**；
        * 之后 ``avcodec_receive_frame`` 出来的帧就是**硬件帧**（在显存里），
          我们再用 ``av_hwframe_transfer_data`` 拷回内存做 swscale → RGB。

        为什么不直接零拷贝上屏：那要为每个平台写 GL/D3D 纹理互操作（VAAPI→EGL、
        D3D11 共享纹理），是另一个量级的工程；**解码放到 GPU 已经吃掉了绝大部分 CPU**
        （本机实测 4K60：软解 ≈4.8 核，硬解 ≈0.9 核），零拷贝只是省一次内存拷贝，
        留作后续优化（见 docs/架构与路线图.md）。
        """
        if os.environ.get("V2_不要硬解"):
            self.硬解 = "软解（CPU，已按 V2_不要硬解 关闭硬解）"
            return
        候选 = self._硬解候选()
        for 类型名, 类型号, 设备 in 候选:
            if self._装硬解(类型名, 类型号, 设备):
                return
        self.硬解 = "软解（CPU）"

    def _硬解候选(self) -> list[tuple[str, int, str]]:
        """本平台该试哪些硬解设备（顺序 = 优先级）。"""
        if os.name == "nt":
            return [("D3D11VA", 常量["AV_HWDEVICE_TYPE_D3D11VA"], "")]   # 设备名空 = 默认
        候选 = []
        # VAAPI 要指定渲染节点；先试 renderD128，再试 129（双显卡机器）
        for 节点 in ("/dev/dri/renderD128", "/dev/dri/renderD129"):
            if os.path.exists(节点):
                候选.append(("VAAPI", 常量["AV_HWDEVICE_TYPE_VAAPI"], 节点))
        return 候选

    def _装硬解(self, 类型名: str, 类型号: int, 设备: str) -> bool:
        """尝试挂一个硬解设备；成功返回 True（失败不抛异常，直接回退软解）。"""
        库 = self.绑定
        设备引用 = ctypes.c_void_p()
        代码 = 库.avutil.av_hwdevice_ctx_create(
            ctypes.byref(设备引用), int(类型号),
            设备.encode() if 设备 else None, None, 0)
        if 代码 < 0 or not 设备引用:
            self._硬解失败原因 = f"{类型名} 设备建不起来：{库.错误文本(代码)}"
            return False
        # 挂到上下文（AVCodecContext.hw_device_ctx 是个 AVBufferRef*）
        引用 = 库.avutil.av_buffer_ref(设备引用)
        库.avutil.av_buffer_unref(ctypes.byref(设备引用))
        if not 引用:
            self._硬解失败原因 = f"{类型名}：引用设备失败"
            return False
        B.写ptr(self.上下文, "AVCodecContext", "hw_device_ctx", int(引用))
        self._硬解设备引用 = int(引用)
        self._硬件像素格式 = int(常量["AV_PIX_FMT_VAAPI"] if 类型名 == "VAAPI"
                            else 常量["AV_PIX_FMT_D3D11"])
        # get_format 回调：解码器问我们选哪种输出格式，我们挑硬件那个
        self._回调 = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                                    ctypes.POINTER(ctypes.c_int))(
            self._选格式)
        B.写ptr(self.上下文, "AVCodecContext", "get_format",
               ctypes.cast(self._回调, ctypes.c_void_p).value or 0)
        self._硬解准备 = (类型名, 设备)
        return True

    def _选格式(self, _上下文, 格式数组) -> int:
        """``get_format`` 回调：优先选硬件像素格式，否则退回第一个能用的。"""
        首选 = int(getattr(self, "_硬件像素格式", -1))
        第一个 = -1
        i = 0
        while 格式数组 and i < 64:
            值 = int(格式数组[i])
            if 值 == -1:
                break
            if 第一个 < 0:
                第一个 = 值
            if 值 == 首选:
                return 值
            i += 1
        self._硬解失败原因 = "解码器不支持这个硬解格式（回退软解）"
        return 第一个 if 第一个 >= 0 else 0

    def 硬解就绪(self) -> None:
        """``avcodec_open2`` 成功后调用：确认硬解是否真的用上了。"""
        if self._硬解准备:
            类型名, 设备 = self._硬解准备
            尾巴 = f"（{设备}）" if 设备 else ""
            self.硬解 = f"{类型名} 硬解{尾巴}"
        else:
            self.硬解 = "软解（CPU）"
            if self._硬解失败原因:
                self.硬解 += f"：{self._硬解失败原因}"

    def 设置输出尺寸(self, 宽: int, 高: int) -> None:
        """告诉解码器"界面只需要这么大"（0 = 不缩放）。

        为什么值得做：解码与缩放都在 C 里很快，**瓶颈是把像素搬进 Python**。
        界面的视频区通常只有 1280 宽左右，没必要每帧搬 25 MB。
        """
        宽, 高 = max(0, int(宽 or 0)), max(0, int(高 or 0))
        if (宽, 高) == (self._输出宽, self._输出高):
            return
        self._输出宽, self._输出高 = 宽, 高
        if self.缩放器:                 # 尺寸变了要重建缩放器
            self.绑定.swscale.sws_freeContext(self.缩放器)
            self.缩放器 = 0
        self._目标宽 = self._目标高 = 0

    def _输出尺寸(self, 源宽: int, 源高: int) -> tuple[int, int]:
        目标宽, 目标高 = self._输出宽, self._输出高
        if 目标宽 <= 0 or 目标高 <= 0:
            return 源宽, 源高
        # 只缩不放：小于目标尺寸的片子按原样输出（省一次放大）
        if 源宽 <= 目标宽 and 源高 <= 目标高:
            return 源宽, 源高
        比例 = min(目标宽 / 源宽, 目标高 / 源高)
        return max(2, int(源宽 * 比例) & ~1), max(2, int(源高 * 比例) & ~1)

    def _准备缩放(self, 源宽: int, 源高: int, 源格式: int) -> None:
        目标宽, 目标高 = self._输出尺寸(源宽, 源高)
        if self.缩放器 and (源宽, 源高, 源格式, 目标宽, 目标高) == (
                self._源宽, self._源高, self._源格式, self._目标宽, self._目标高):
            return
        if self.缩放器:
            self.绑定.swscale.sws_freeContext(self.缩放器)
            self.缩放器 = 0
        不需要缩放 = (源宽 == self.流.宽 and 源高 == self.流.高
                  and 源格式 == self.流.像素格式 and self.流.像素格式 in (
                      常量["AV_PIX_FMT_RGB24"], 常量["AV_PIX_FMT_BGRA"]))
        self._源宽, self._源高, self._源格式 = 源宽, 源高, 源格式
        self._目标宽, self._目标高 = 目标宽, 目标高
        self.缩放器 = int(self.绑定.swscale.sws_getContext(
            源宽, 源高, 源格式, self._目标宽, self._目标高,
            常量["AV_PIX_FMT_RGB0"], 常量["SWS_BILINEAR"], None, None, None) or 0)
        if not self.缩放器:
            raise RuntimeError(f"{self.名字}：建缩放器失败（{源宽}x{源高}）")
        需要 = self._目标宽 * self._目标高 * 4
        if self._缓冲 is None or len(self._缓冲) < 需要:
            self._缓冲 = ctypes.create_string_buffer(需要 + 64)

    def 取帧(self) -> Optional[解码帧]:
        """把当前收到的帧转成 RGB0（返回 None 表示这帧还没准备好）。"""
        帧指针 = self._帧指针
        源宽, 源高 = self._帧宽高(帧指针)
        if 源宽 <= 0 or 源高 <= 0:
            return None
        源格式 = B.读i32(帧指针, "AVFrame", "format")
        帧来源 = 帧指针
        if (self._硬件像素格式 >= 0 and 源格式 == self._硬件像素格式
                and not getattr(self, "硬件像素格式不能用", False)):
            # 硬件帧（在显存里）：先拷回内存帧，再走同一条 swscale 路径
            if not self._软件帧:
                self._软件帧 = int(self.绑定.avutil.av_frame_alloc() or 0)
            if not self._软件帧:
                return None
            self.绑定.avutil.av_frame_unref(self._软件帧)
            代码 = self.绑定.avutil.av_hwframe_transfer_data(
                self._软件帧, 帧指针, 0)
            if 代码 < 0:
                self._硬解失败原因 = f"硬件帧拷回内存失败：{self.绑定.错误文本(代码)}"
                self.硬解 = "软解（CPU）" + f"：{self._硬解失败原因}"
                self.硬件像素格式不能用 = True
                return None
            帧来源 = self._软件帧
            源格式 = B.读i32(帧来源, "AVFrame", "format")
            self.硬解帧数 += 1
        self._准备缩放(源宽, 源高, 源格式)
        数据, 行 = self._图像指针数组(帧来源)
        输出宽, 输出高 = self._目标宽, self._目标高
        目标行 = 输出宽 * 4
        目标 = ctypes.c_void_p(ctypes.addressof(self._缓冲))
        行数组 = (ctypes.c_int * 4)(目标行, 0, 0, 0)
        结果 = self.绑定.swscale.sws_scale(
            self.缩放器, 数据, 行, 0, self._源高,
            ctypes.cast(ctypes.byref(目标), ctypes.POINTER(ctypes.c_void_p)),
            行数组)
        if 结果 <= 0:
            return None
        字节 = ctypes.string_at(目标.value, 目标行 * 输出高)
        self.已解帧数 += 1
        return 解码帧(数据=字节, 宽=输出宽, 高=输出高, 步长=目标行,
                    时间秒=self._帧时间(帧指针))


class 音频解码器(_基础解码器):
    """音频解码 + 重采样到 S16 立体声 48kHz。"""

    def __init__(self, 绑定: B.绑定, 流: 流信息) -> None:
        super().__init__(绑定, 流)
        self.重采样器 = 0
        self.源采样率 = 0
        self.源声道 = 0
        self.源格式 = -1
        self._输出布局 = None
        self._输入布局 = None
        self._缓冲 = None

    def 打开(self) -> None:
        super().打开()
        self._输出布局 = self._建布局(输出声道数)
        self._输入布局 = self._建布局(max(1, self.流.声道数))

    def _建布局(self, 声道数: int):
        布局 = (ctypes.c_byte * 24)()          # AVChannelLayout 是 24 字节（64 位）
        self.绑定.avutil.av_channel_layout_default(ctypes.byref(布局), int(声道数))
        return 布局

    def _准备重采样(self, 采样率: int, 声道数: int, 格式: int) -> None:
        if self.重采样器 and (采样率, 声道数, 格式) == (self.源采样率, self.源声道,
                                                    self.源格式):
            return
        if self.重采样器:
            self.绑定.swresample.swr_free(ctypes.byref(ctypes.c_void_p(self.重采样器)))
            self.重采样器 = 0
        self.源采样率, self.源声道, self.源格式 = 采样率, 声道数, 格式
        if 声道数 != 输出声道数:
            self._输入布局 = self._建布局(max(1, 声道数))
        输出 = ctypes.c_void_p()
        代码 = self.绑定.swresample.swr_alloc_set_opts2(
            ctypes.byref(输出),
            ctypes.byref(self._输出布局), 输出样本格式, 输出采样率,
            ctypes.byref(self._输入布局), 格式, 采样率,
            0, None)
        if 代码 < 0 or not 输出:
            raise RuntimeError(f"{self.名字}：建重采样器失败"
                            f"（{采样率}Hz {声道数}声道 → "
                            f"{输出采样率}Hz {输出声道数}声道）")
        self.重采样器 = int(输出.value or 0)
        代码 = self.绑定.swresample.swr_init(self.重采样器)
        if 代码 < 0:
            raise RuntimeError(f"{self.名字}：初始化重采样器失败")

    def 取块(self) -> Optional[音频块]:
        帧指针 = self._帧指针
        样本数 = B.读i32(帧指针, "AVFrame", "nb_samples")
        if 样本数 <= 0:
            return None
        采样率 = B.读i32(帧指针, "AVFrame", "sample_rate") or self.流.采样率 or 48000
        格式 = B.读i32(帧指针, "AVFrame", "format")
        声道 = self.流.声道数 or 2
        self._准备重采样(采样率, 声道, 格式)
        数据, _行 = self._图像指针数组(帧指针)
        # 输出缓冲：按最大可能样本数（重采样后可能略多）+ 余量
        最多 = int(样本数 * 输出采样率 / max(1, 采样率)) + 4096
        需要 = 最多 * 输出声道数 * 2
        if self._缓冲 is None or len(self._缓冲) < 需要:
            self._缓冲 = ctypes.create_string_buffer(需要)
        输出指针 = ctypes.c_void_p(ctypes.addressof(self._缓冲))
        输出数组 = (ctypes.c_void_p * 1)(输出指针)
        产出 = self.绑定.swresample.swr_convert(
            self.重采样器,
            ctypes.cast(输出数组, ctypes.POINTER(ctypes.c_void_p)), 最多,
            ctypes.cast(ctypes.byref(数据), ctypes.POINTER(ctypes.c_void_p)), 样本数)
        if 产出 <= 0:
            return None
        字节 = ctypes.string_at(输出指针.value, 产出 * 输出声道数 * 2)
        return 音频块(数据=字节, 样本数=int(产出), 时间秒=self._帧时间(帧指针))
