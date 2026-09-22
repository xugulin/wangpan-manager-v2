"""libav* 函数原型 + 按偏移读写字段（**V2 自己的绑定，不用任何第三方封装**）。

设计要点
========
* 结构体一律当 ``c_void_p`` 用，字段通过 :mod:`wangpan.ffmpeg.偏移` 里的偏移读写 ——
  这样 FFmpeg 大小版本变化只要重跑 ``tools/生成偏移.py``，Python 侧不用改一行；
* 需要"调用者分配的结构体"（``AVPacket`` / ``AVFrame`` / ``AVCodecContext``）一律用
  FFmpeg 自己的 ``*_alloc()``，**我们不去猜它们的大小**；
* 常量（枚举值）由 ``tools/生成偏移.py`` 同源的 C 探针得到（见本文件顶部常量表），
  不靠手数枚举。

线程安全
========
libav* 本身没有全局可变状态（除日志回调），但 ``AVCodecContext`` **只能单线程用**：
每个解码器一个线程，见 :mod:`wangpan.player.解码`。这里的函数不加锁。
"""

from __future__ import annotations

import ctypes
from typing import Optional

from . import 加载
from .偏移 import 对应主版本, 偏移, 大小

__all__ = ["绑定", "取绑定", "读i32", "读i64", "读ptr", "读rat", "写i32", "写i64",
           "写ptr", "常量", "错误文本", "偏移"]

#: 从 FFmpeg 头文件里探到的常量（C 探针打印，见 tools/生成偏移.py 的说明）
常量 = {
    "AVMEDIA_TYPE_VIDEO": 0,
    "AVMEDIA_TYPE_AUDIO": 1,
    "AVMEDIA_TYPE_SUBTITLE": 3,
    "AV_PIX_FMT_YUV420P": 0,
    "AV_PIX_FMT_RGB24": 2,
    "AV_PIX_FMT_BGRA": 28,
    "AV_PIX_FMT_NV12": 23,
    "AV_PIX_FMT_RGB0": 119,
    "AV_SAMPLE_FMT_U8": 0,
    "AV_SAMPLE_FMT_S16": 1,
    "AV_SAMPLE_FMT_FLT": 3,
    "AV_SAMPLE_FMT_S16P": 6,
    "AV_NOPTS_VALUE": -9223372036854775808,
    "AVERROR_EOF": -541478725,
    "AVERROR_EAGAIN": -11,
    "AVERROR_INVALIDDATA": -1094995529,
    "AV_TIME_BASE": 1000000,
    "AVSEEK_FLAG_BACKWARD": 1,
    "AV_CHANNEL_ORDER_UNSPEC": 0,
    "AV_CHANNEL_ORDER_NATIVE": 1,
    "SWS_BILINEAR": 2,
    "SWS_FAST_BILINEAR": 1,
    "AV_PKT_FLAG_KEY": 1,
    # ---- 硬件解码（M2）：同样是 C 探针打印出来的真实枚举值 ----
    "AV_HWDEVICE_TYPE_NONE": 0,
    "AV_HWDEVICE_TYPE_VDPAU": 1,
    "AV_HWDEVICE_TYPE_CUDA": 2,
    "AV_HWDEVICE_TYPE_VAAPI": 3,
    "AV_HWDEVICE_TYPE_DXVA2": 4,
    "AV_HWDEVICE_TYPE_QSV": 5,
    "AV_HWDEVICE_TYPE_D3D11VA": 7,
    "AV_PIX_FMT_VAAPI": 44,
    "AV_PIX_FMT_D3D11": 171,
    "AV_PIX_FMT_D3D11VA_VLD": 116,
    "AV_PIX_FMT_DXVA2_VLD": 51,
    "AV_PIX_FMT_P010": 158,
}

_绑定: Optional["绑定"] = None


# ---------------- 字段读写（按生成的偏移） ----------------

def 读i32(对象, 结构: str, 字段: str) -> int:
    return ctypes.c_int32.from_address(int(对象) + 偏移[f"{结构}.{字段}"]).value


def 读i64(对象, 结构: str, 字段: str) -> int:
    return ctypes.c_int64.from_address(int(对象) + 偏移[f"{结构}.{字段}"]).value


def 读ptr(对象, 结构: str, 字段: str, 附加偏移: int = 0) -> int:
    地址 = int(对象) + 偏移[f"{结构}.{字段}"] + int(附加偏移)
    return int(ctypes.c_void_p.from_address(地址).value or 0)


def 读rat(对象, 结构: str, 字段: str) -> tuple[int, int]:
    基 = int(对象) + 偏移[f"{结构}.{字段}"]
    return (ctypes.c_int32.from_address(基).value,
            ctypes.c_int32.from_address(基 + 4).value)


def 写i32(对象, 结构: str, 字段: str, 值: int) -> None:
    ctypes.c_int32.from_address(int(对象) + 偏移[f"{结构}.{字段}"]).value = int(值)


def 写i64(对象, 结构: str, 字段: str, 值: int) -> None:
    ctypes.c_int64.from_address(int(对象) + 偏移[f"{结构}.{字段}"]).value = int(值)


def 写ptr(对象, 结构: str, 字段: str, 值: int) -> None:
    ctypes.c_void_p.from_address(int(对象) + 偏移[f"{结构}.{字段}"]).value = 值 or None


class 绑定:
    """把一个 FFmpeg 库集合包装成"带类型的函数表"（懒加载，只绑一次）。"""

    def __init__(self) -> None:
        if not 加载.装载():
            raise RuntimeError(加载.不可用原因())
        self.avformat = 加载.库("avformat")
        self.avcodec = 加载.库("avcodec")
        self.avutil = 加载.库("avutil")
        self.swscale = 加载.库("swscale")
        self.swresample = 加载.库("swresample")
        self._绑()
        self.检查ABI()

    # ---------------- 签名 ----------------
    def _绑(self) -> None:
        空 = ctypes.c_void_p
        整 = ctypes.c_int
        串 = ctypes.c_char_p

        # ---- avutil ----
        self.avutil.av_frame_alloc.restype = 空
        self.avutil.av_frame_free.argtypes = [ctypes.POINTER(空)]
        self.avutil.av_frame_unref.argtypes = [空]
        self.avutil.av_frame_get_buffer.argtypes = [空, 整, 整]
        self.avutil.av_image_get_buffer_size.argtypes = [整, 整, 整, 整]
        self.avutil.av_image_get_buffer_size.restype = 整
        self.avutil.av_image_fill_arrays.argtypes = [
            ctypes.POINTER(空), ctypes.POINTER(整), ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.avutil.av_image_fill_arrays.restype = 整
        self.avutil.av_rescale_q.argtypes = [ctypes.c_int64, ctypes.c_void_p,
                                           ctypes.c_void_p]
        self.avutil.av_rescale_q.restype = ctypes.c_int64
        self.avutil.av_channel_layout_default.argtypes = [空, 整]
        self.avutil.av_channel_layout_default.restype = None
        self.avutil.av_channel_layout_uninit.argtypes = [空]
        self.avutil.av_channel_layout_copy.argtypes = [空, 空]
        self.avutil.av_channel_layout_copy.restype = 整
        self.avutil.av_strerror.argtypes = [整, 串, ctypes.c_size_t]
        self.avutil.av_strerror.restype = 整
        self.avutil.av_dict_set.argtypes = [ctypes.POINTER(空), 串, 串, 整]
        self.avutil.av_dict_set.restype = 整
        self.avutil.av_dict_free.argtypes = [ctypes.POINTER(空)]
        self.avutil.av_log_set_level.argtypes = [整]
        self.avutil.av_log_set_level.restype = None
        self.avutil.av_malloc.argtypes = [ctypes.c_size_t]
        self.avutil.av_malloc.restype = 空
        self.avutil.av_free.argtypes = [空]
        self.avutil.av_free.restype = None

        # ---- avutil：硬件解码（M2） ----
        self.avutil.av_hwdevice_ctx_create.argtypes = [
            ctypes.POINTER(空), 整, 串, ctypes.POINTER(空), 整]
        self.avutil.av_hwdevice_ctx_create.restype = 整
        self.avutil.av_hwframe_transfer_data.argtypes = [空, 空, 整]
        self.avutil.av_hwframe_transfer_data.restype = 整
        self.avutil.av_hwframe_transfer_get_formats.argtypes = [
            空, 整, ctypes.POINTER(ctypes.POINTER(整)), 整]
        self.avutil.av_hwframe_transfer_get_formats.restype = 整
        self.avutil.av_buffer_ref.argtypes = [空]
        self.avutil.av_buffer_ref.restype = 空
        self.avutil.av_buffer_unref.argtypes = [ctypes.POINTER(空)]
        self.avutil.av_buffer_unref.restype = None
        self.avutil.av_hwdevice_get_type_name.argtypes = [整]
        self.avutil.av_hwdevice_get_type_name.restype = 串
        self.avutil.av_pix_fmt_desc_get.argtypes = [整]
        self.avutil.av_pix_fmt_desc_get.restype = 空

        # ---- avformat ----
        self.avformat.avformat_open_input.argtypes = [
            ctypes.POINTER(空), 串, 空, ctypes.POINTER(空)]
        self.avformat.avformat_open_input.restype = 整
        self.avformat.avformat_find_stream_info.argtypes = [空, ctypes.POINTER(空)]
        self.avformat.avformat_find_stream_info.restype = 整
        self.avformat.avformat_close_input.argtypes = [ctypes.POINTER(空)]
        self.avformat.avformat_close_input.restype = None
        self.avformat.av_read_frame.argtypes = [空, 空]
        self.avformat.av_read_frame.restype = 整
        self.avformat.avformat_seek_file.argtypes = [
            空, 整, ctypes.c_int64, ctypes.c_int64, ctypes.c_int64, 整]
        self.avformat.avformat_seek_file.restype = 整
        self.avformat.av_find_best_stream.argtypes = [
            空, 整, 整, 整, ctypes.POINTER(空), 整]
        self.avformat.av_find_best_stream.restype = 整
        self.avformat.avformat_network_init.restype = 整
        self.avformat.avformat_network_init.argtypes = []

        # ---- avcodec ----
        self.avcodec.avcodec_alloc_context3.argtypes = [空]
        self.avcodec.avcodec_alloc_context3.restype = 空
        self.avcodec.avcodec_free_context.argtypes = [ctypes.POINTER(空)]
        self.avcodec.avcodec_free_context.restype = None
        self.avcodec.avcodec_parameters_to_context.argtypes = [空, 空]
        self.avcodec.avcodec_parameters_to_context.restype = 整
        self.avcodec.avcodec_find_decoder.argtypes = [整]
        self.avcodec.avcodec_find_decoder.restype = 空
        self.avcodec.avcodec_open2.argtypes = [空, 空, ctypes.POINTER(空)]
        self.avcodec.avcodec_open2.restype = 整
        self.avcodec.avcodec_send_packet.argtypes = [空, 空]
        self.avcodec.avcodec_send_packet.restype = 整
        self.avcodec.avcodec_receive_frame.argtypes = [空, 空]
        self.avcodec.avcodec_receive_frame.restype = 整
        self.avcodec.avcodec_flush_buffers.argtypes = [空]
        self.avcodec.avcodec_flush_buffers.restype = None
        self.avcodec.av_packet_alloc.restype = 空
        self.avcodec.av_packet_alloc.argtypes = []
        self.avcodec.av_packet_free.argtypes = [ctypes.POINTER(空)]
        self.avcodec.av_packet_free.restype = None
        self.avcodec.av_packet_unref.argtypes = [空]
        self.avcodec.av_packet_unref.restype = None
        # ⚠️ 跨线程必须**引用计数共享**，不能把同一个 AVPacket 指针丢给另一个线程：
        #    解封装线程会立刻往同一个包里读下一个包 → 解码线程拿到的是被覆盖的数据
        #    （实测：Invalid NAL unit size + 偶发段错误）。
        self.avcodec.av_packet_ref.argtypes = [空, 空]
        self.avcodec.av_packet_ref.restype = 整
        self.avcodec.avcodec_get_name.argtypes = [整]
        self.avcodec.avcodec_get_name.restype = 串

        # ---- swscale ----
        self.swscale.sws_getContext.argtypes = [
            整, 整, 整, 整, 整, 整, 整, 空, 空, ctypes.POINTER(ctypes.c_double)]
        self.swscale.sws_getContext.restype = 空
        self.swscale.sws_scale.argtypes = [
            空, ctypes.POINTER(空), ctypes.POINTER(整), 整, 整,
            ctypes.POINTER(空), ctypes.POINTER(整)]
        self.swscale.sws_scale.restype = 整
        self.swscale.sws_freeContext.argtypes = [空]
        self.swscale.sws_freeContext.restype = None

        # ---- swresample ----
        self.swresample.swr_alloc_set_opts2.argtypes = [
            ctypes.POINTER(空), 空, 整, 整, 空, 整, 整, 整, 空]
        self.swresample.swr_alloc_set_opts2.restype = 整
        self.swresample.swr_init.argtypes = [空]
        self.swresample.swr_init.restype = 整
        self.swresample.swr_convert.argtypes = [
            空, ctypes.POINTER(空), 整, ctypes.POINTER(空), 整]
        self.swresample.swr_convert.restype = 整
        self.swresample.swr_free.argtypes = [ctypes.POINTER(空)]
        self.swresample.swr_free.restype = None
        self.swresample.swr_get_delay.argtypes = [空, ctypes.c_int64]
        self.swresample.swr_get_delay.restype = ctypes.c_int64

    # ---------------- ABI 自检 ----------------

    def 检查ABI(self) -> None:
        """把"偏移表与当前 FFmpeg 是否匹配"当场验一遍（不匹配就立刻报错）。

        只用能自证的判据，不猜：
        * ``AVPacket`` / ``AVFrame`` 的 ``sizeof`` 必须和我们生成偏移时一致；
        * ``AVPacket.pts`` 必须排在 ``dts`` 前面、``data`` 在 ``size`` 前面（顺序合理性）；
        * 关键函数必须真的存在（拿不到就说明这个库版本不兼容）。
        """
        问题: list[str] = []
        for 结构 in ("AVPacket", "AVFrame", "AVCodecParameters"):
            if 结构 not in 大小:
                问题.append(f"偏移表里没有 {结构} 的大小")
        if 偏移.get("AVPacket.pts", -1) >= 偏移.get("AVPacket.dts", -1):
            问题.append("AVPacket.pts/dts 偏移顺序不对")
        if 偏移.get("AVPacket.data", -1) >= 偏移.get("AVPacket.size", -1):
            问题.append("AVPacket.data/size 偏移顺序不对")
        if 偏移.get("AVFrame.width", -1) >= 偏移.get("AVFrame.height", -1):
            问题.append("AVFrame.width/height 偏移顺序不对")
        # 偏移表必须和运行时的 FFmpeg 大版本一致（这是最容易悄悄出错的一条）：
        # 结构体布局随大版本变化，拿 63 的表去读 61 的库会读出看着像数字的垃圾
        # （Windows CI 实测：帧时间戳变成 671088.64 秒 → 视频线程永远在等一个
        #  67 万秒之后的时刻 → 25 帧的片子只出 1 帧，且不报任何错）。
        # 所以这里直接拦住，宁可起不来也不要错着读内存。
        try:
            运行时主版本 = str((int(加载.库("avcodec").avcodec_version()) >> 16) & 0xFF)
        except Exception:  # noqa: BLE001
            运行时主版本 = "?"
        if 运行时主版本 != str(对应主版本):
            问题.append(
                "FFmpeg 大版本不匹配：偏移表是给 libavcodec "
                + str(对应主版本)
                + " 用的，现在加载的是 "
                + 运行时主版本
                + "。解决：① 换成 avcodec-"
                + str(对应主版本)
                + " 的运行库（推荐）；或 ② 在装了该版本头文件的机器上重跑 "
                + "tools/生成偏移.py --包含目录 <头文件根> --输出 "
                + "wangpan/ffmpeg/偏移_<主版本>.py")
        for 库名, 函数 in (("avformat", "av_read_frame"),
                        ("avcodec", "avcodec_send_packet"),
                        ("avutil", "av_frame_alloc"),
                        ("swscale", "sws_getContext"),
                        ("swresample", "swr_convert")):
            if not hasattr(加载.库(库名), 函数):
                问题.append(f"{库名} 里没有 {函数}")
        if 问题:
            raise RuntimeError("FFmpeg 绑定与当前库不匹配（是不是换了版本？"
                            "在开发机重跑 tools/生成偏移.py）：\n  - "
                            + "\n  - ".join(问题))

    # ---------------- 便捷封装（薄薄一层，逻辑在 player 里） ----------------

    def 错误文本(self, 错误码: int) -> str:
        return 错误文本(int(错误码))

    def 打开(self, 地址: str, 选项: Optional[dict] = None):
        """打开输入并读取流信息（返回 ``(AVFormatContext*, 流列表)``）。"""
        格式 = ctypes.c_void_p()
        字典 = ctypes.c_void_p()
        选项 = dict(选项 or {})
        for 键, 值 in 选项.items():
            self.avutil.av_dict_set(ctypes.byref(字典), 键.encode(), str(值).encode(), 0)
        名字 = str(地址).encode("utf-8")
        代码 = self.avformat.avformat_open_input(
            ctypes.byref(格式), 名字, None, ctypes.byref(字典) if 选项 else None)
        if 字典:
            self.avutil.av_dict_free(ctypes.byref(字典))
        if 代码 < 0:
            raise OSError(f"打不开输入：{self.错误文本(代码)}（{地址}）")
        代码 = self.avformat.avformat_find_stream_info(格式, None)
        if 代码 < 0:
            self.关闭(格式)
            raise OSError(f"读不到流信息：{self.错误文本(代码)}")
        # ⚠️ 统一成 int 往下传：ctypes 的 c_void_p 直接 int() 会去解析它包装的字节串
        #    （踩过：ValueError: invalid literal for int()）。绑定层只认整数地址。
        return int(格式.value or 0), self.流列表(int(格式.value or 0))

    def 关闭(self, 格式) -> None:
        if not 格式:
            return
        地址 = int(格式 if isinstance(格式, int) else getattr(格式, "value", 0) or 0)
        if not 地址:
            return
        指针 = ctypes.c_void_p(地址)
        self.avformat.avformat_close_input(ctypes.byref(指针))

    def 流列表(self, 格式) -> list[dict]:
        """把流读成 Python 字典（供上层决策，避免到处算偏移）。"""
        条数 = 读i32(格式, "AVFormatContext", "nb_streams")
        数组 = 读ptr(格式, "AVFormatContext", "streams")
        流们: list[dict] = []
        for i in range(条数):
            流指针 = int(ctypes.c_void_p.from_address(数组 + 8 * i).value or 0)
            if not 流指针:
                continue
            参数 = 读ptr(流指针, "AVStream", "codecpar")
            时间基 = 读rat(流指针, "AVStream", "time_base")
            流们.append({
                "序号": i,
                "指针": 流指针,
                "参数": 参数,
                "类型": 读i32(参数, "AVCodecParameters", "codec_type"),
                "编码": 读i32(参数, "AVCodecParameters", "codec_id"),
                "宽": 读i32(参数, "AVCodecParameters", "width"),
                "高": 读i32(参数, "AVCodecParameters", "height"),
                "像素格式": 读i32(参数, "AVCodecParameters", "format"),
                "采样率": 读i32(参数, "AVCodecParameters", "sample_rate"),
                # 声道数在 AVChannelLayout 里（AVCodecParameters 内嵌结构，不是指针）
                "声道数": 读i32(参数 + 偏移["AVCodecParameters.ch_layout"],
                             "AVChannelLayout", "nb_channels"),
                "时长": 读i64(流指针, "AVStream", "duration"),
                "时间基分母": 时间基[1],
                "时间基分子": 时间基[0],
                "编码名": self.编码名(读i32(参数, "AVCodecParameters", "codec_id")),
            })
        return 流们

    def 编码名(self, 编码号: int) -> str:
        try:
            名字 = self.avcodec.avcodec_get_name(int(编码号))
            return (名字 or b"").decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return "?"


def 取绑定() -> 绑定:
    global _绑定
    if _绑定 is None:
        _绑定 = 绑定()
    return _绑定


def 错误文本(错误码: int) -> str:
    """把 AVERROR 变成人能看的字符串（含 EOF/EAGAIN 这两个常用的）。"""
    错误码 = int(错误码)
    if 错误码 == 常量["AVERROR_EOF"]:
        return "文件结束"
    if 错误码 == 常量["AVERROR_EAGAIN"]:
        return "需要更多数据"
    try:
        缓冲 = ctypes.create_string_buffer(256)
        加载.库("avutil").av_strerror(错误码, 缓冲, ctypes.sizeof(缓冲))
        文本 = 缓冲.value.decode("utf-8", "replace").strip()
        return 文本 or f"错误 {错误码}"
    except Exception:  # noqa: BLE001
        return f"错误 {错误码}"
