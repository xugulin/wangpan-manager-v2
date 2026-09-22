"""解封装：打开输入、找流、跳转、读包（**我们自己的播放器循环**的第一层）。

这一层只做"把容器拆成包"，不做解码；所有时间都归一成 **秒（float）** 给上层，
避免把 AVRational 的换算散到各处（V1 那种到处 rescale 的写法最容易出错）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..ffmpeg import 绑定 as B
from ..ffmpeg.绑定 import 常量

__all__ = ["流信息", "输入"]


@dataclass
class 流信息:
    """一条流（视频/音频/字幕）的可读信息。"""

    序号: int
    类型: int
    编码: int
    编码名: str
    时间基: tuple[int, int]
    时长秒: float = 0.0
    宽: int = 0
    高: int = 0
    像素格式: int = -1
    采样率: int = 0
    声道数: int = 0
    帧率: float = 0.0
    参数指针: int = 0
    流指针: int = 0

    @property
    def 是视频(self) -> bool:
        return self.类型 == 常量["AVMEDIA_TYPE_VIDEO"]

    @property
    def 是音频(self) -> bool:
        return self.类型 == 常量["AVMEDIA_TYPE_AUDIO"]

    def 一句话(self) -> str:
        if self.是视频:
            return (f"视频#{self.序号} {self.编码名} {self.宽}x{self.高} "
                    f"{self.帧率:.3g}fps")
        if self.是音频:
            return (f"音频#{self.序号} {self.编码名} {self.采样率}Hz "
                    f"{self.声道数}声道")
        return f"其它#{self.序号} {self.编码名}"


@dataclass
class 输入:
    """一个打开的输入（文件 / HTTP 直链）。"""

    绑定: B.绑定
    格式指针: int
    流们: list[流信息] = field(default_factory=list)
    时长秒: float = 0.0
    视频流: Optional[流信息] = None
    音频流: Optional[流信息] = None
    地址: str = ""
    _包指针: int = 0

    # ---------------- 打开 / 关闭 ----------------

    @classmethod
    def 打开(cls, 地址: str, 选项: Optional[dict] = None) -> "输入":
        绑定 = B.取绑定()
        格式, 原始流们 = 绑定.打开(地址, 选项)
        自己 = cls(绑定=绑定, 格式指针=int(格式), 地址=地址)
        自己._包指针 = int(绑定.avcodec.av_packet_alloc() or 0)
        for 项 in 原始流们:
            流 = 流信息(序号=项["序号"], 类型=项["类型"], 编码=项["编码"],
                     编码名=项["编码名"], 时间基=(项["时间基分子"], 项["时间基分母"]),
                     宽=项["宽"], 高=项["高"], 像素格式=项["像素格式"],
                     采样率=项["采样率"], 声道数=项["声道数"],
                     参数指针=项["参数"], 流指针=项["指针"])
            分子, 分母 = 流.时间基
            if 分母:
                流.时长秒 = 项["时长"] / 分母 if 项["时长"] > 0 else 0.0
            自己.流们.append(流)
            if 流.是视频 and 自己.视频流 is None:
                自己.视频流 = 流
            elif 流.是音频 and 自己.音频流 is None:
                自己.音频流 = 流
        # 时长：优先用容器 duration（微秒），否则用视频流时长
        容器时长 = B.读i64(自己.格式指针, "AVFormatContext", "duration")
        自己.时长秒 = (容器时长 / 常量["AV_TIME_BASE"]) if 容器时长 > 0 else 0.0
        if 自己.时长秒 <= 0 and 自己.视频流 is not None:
            自己.时长秒 = 自己.视频流.时长秒
        if 自己.视频流 is not None:
            自己.视频流.帧率 = 自己._算帧率(自己.视频流)
        return 自己

    def _算帧率(self, 流: 流信息) -> float:
        """优先 avg_frame_rate，退化到 r_frame_rate（0/0 要挡住）。"""
        for 字段 in ("avg_frame_rate", "r_frame_rate"):
            分子, 分母 = B.读rat(流.流指针, "AVStream", 字段)
            if 分子 > 0 and 分母 > 0:
                return 分子 / 分母
        return 0.0

    def 关闭(self) -> None:
        try:
            if self._包指针:
                指针 = __import__("ctypes").c_void_p(self._包指针)
                self.绑定.avcodec.av_packet_free(__import__("ctypes").byref(指针))
                self._包指针 = 0
        except Exception:  # noqa: BLE001
            pass
        try:
            self.绑定.关闭(self.格式指针)
        except Exception:  # noqa: BLE001
            pass
        self.格式指针 = 0

    # ---------------- 读包 / 跳转 ----------------

    def 复制包(self) -> int:
        """把当前这个包**引用共享**成一个新包（返回新包指针，调用方负责释放）。

        为什么要复制：解封装线程马上会用同一个 AVPacket 读下一个包，
        直接把指针交给解码线程 = 数据被覆盖 + 悬垂指针（真机踩过：
        ``Invalid NAL unit size`` 之后段错误）。``av_packet_ref`` 是零拷贝的，
        只是把底层缓冲的引用计数 +1。
        """
        新包 = int(self.绑定.avcodec.av_packet_alloc() or 0)
        if not 新包:
            raise MemoryError("av_packet_alloc 失败")
        代码 = self.绑定.avcodec.av_packet_ref(新包, self._包指针)
        if 代码 < 0:
            指针 = __import__("ctypes").c_void_p(新包)
            self.绑定.avcodec.av_packet_free(__import__("ctypes").byref(指针))
            raise OSError(f"复制包失败：{self.绑定.错误文本(代码)}")
        return 新包

    def 释放新包(self, 包指针: int) -> None:
        """释放 :meth:`复制包` 出来的包。"""
        if not 包指针:
            return
        指针 = __import__("ctypes").c_void_p(int(包指针))
        self.绑定.avcodec.av_packet_free(__import__("ctypes").byref(指针))

    def 读包(self) -> Optional[dict]:
        """读一个包（数据在内部包缓冲里，**不要**把指针交给别的线程）。

        返回 ``{流序号, pts秒, dts秒, 关键帧, 大小}``；读完返回 None。
        """
        import ctypes
        代码 = self.绑定.avformat.av_read_frame(self.格式指针, self._包指针)
        if 代码 < 0:
            if 代码 != 常量["AVERROR_EOF"]:
                # 不是 EOF 的错误（网络抖动等）由上层决定是否重试，这里如实抛出
                raise OSError(f"读包失败：{self.绑定.错误文本(代码)}")
            return None
        序号 = B.读i32(self._包指针, "AVPacket", "stream_index")
        pts = B.读i64(self._包指针, "AVPacket", "pts")
        dts = B.读i64(self._包指针, "AVPacket", "dts")
        大小 = B.读i32(self._包指针, "AVPacket", "size")
        标志 = B.读i32(self._包指针, "AVPacket", "flags")
        时间基 = self.流们[序号].时间基 if 0 <= 序号 < len(self.流们) else (1, 1)
        分子, 分母 = 时间基
        def 换算(值: int) -> float:
            if 值 == 常量["AV_NOPTS_VALUE"] or not 分母:
                return -1.0
            return 值 * 分子 / 分母
        return {"流序号": 序号, "pts秒": 换算(pts), "dts秒": 换算(dts),
                "关键帧": bool(标志 & 常量["AV_PKT_FLAG_KEY"]), "大小": 大小}

    def 释放包(self) -> None:
        self.绑定.avcodec.av_packet_unref(self._包指针)

    def 跳转(self, 秒: float, 流序号: Optional[int] = None) -> bool:
        """跳到某个时间点（秒）。用容器级 seek + 关键帧回退。"""
        目标流 = 流序号 if 流序号 is not None else -1
        if 目标流 is not None and 目标流 >= 0:
            分子, 分母 = self.流们[目标流].时间基
            时间戳 = int(秒 * 分母 / 分子) if 分子 else 0
        else:
            时间戳 = int(秒 * 常量["AV_TIME_BASE"])
        代码 = self.绑定.avformat.avformat_seek_file(
            self.格式指针, int(目标流), -(1 << 62), 时间戳, 时间戳,
            常量["AVSEEK_FLAG_BACKWARD"])
        return 代码 >= 0
