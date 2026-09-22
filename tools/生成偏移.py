#!/usr/bin/env python3
"""生成 FFmpeg 结构体**偏移表**（``wangpan/ffmpeg/偏移.py``）。

为什么用"生成偏移"而不是"手写 ctypes 结构体"
=============================================
FFmpeg 的公开结构体（``AVFormatContext`` / ``AVCodecParameters`` / ``AVStream`` …）
里夹着大量私有字段，**字段顺序随大版本变化**；手抄一份结构体，换个 FFmpeg 版本
（63 → 64）就可能整体错位，而错位读出来的东西"看起来像数字"，非常难查。

所以这里让 **C 编译器**去回答"字段在哪儿"：用 ``offsetof`` 打印每个我们关心的字段的
字节偏移，生成 ``偏移.py``。运行期只按偏移读，不留任何编译依赖（生成的 .py 进版本库）。

用法（项目根下）::

    python3 tools/生成偏移.py            # 生成/更新 wangpan/ffmpeg/偏移.py
    python3 tools/生成偏移.py --查看      # 只打印，不写文件

* 需要 ``gcc`` 与 FFmpeg 头文件（``libavformat-dev`` / ``libavcodec-dev`` /
  ``libavutil-dev``，Arch 上是 ``ffmpeg`` 包自带）。**只在开发机跑**，用户机器不需要。
* 32 位平台偏移会不同 —— 我们只支持 64 位（Windows x64 / Linux x86_64）。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
输出 = 项目根 / "wangpan" / "ffmpeg" / "偏移.py"

#: 要取偏移的字段：结构体 → 字段列表
要的字段 = {
    "AVFormatContext": ("nb_streams", "streams", "duration", "start_time",
                       "iformat", "bit_rate", "flags", "pb",
                       # 中断回调：网络源读卡住时要能立刻叫停（否则停止/关窗会悬垂）
                       "interrupt_callback", "max_delay"),
    "AVStream": ("index", "id", "codecpar", "time_base", "start_time",
                 "duration", "avg_frame_rate", "r_frame_rate", "nb_frames",
                 "disposition", "metadata"),
    "AVCodecParameters": ("codec_type", "codec_id", "codec_tag", "extradata",
                          "extradata_size", "width", "height", "format",
                          "bit_rate", "bits_per_coded_sample",
                          "bits_per_raw_sample", "sample_rate", "ch_layout",
                          "frame_size", "video_delay", "profile", "level"),
    "AVPacket": ("buf", "pts", "dts", "data", "size", "stream_index", "flags",
                 "side_data", "side_data_elems", "duration", "pos", "opaque",
                 "opaque_ref", "time_base"),
    "AVFrame": ("data", "linesize", "extended_data", "width", "height",
                "nb_samples", "format", "pict_type", "sample_aspect_ratio",
                "pts", "pkt_dts", "time_base", "quality", "opaque",
                "repeat_pict", "sample_rate", "buf", "extended_buf",
                "nb_extended_buf", "side_data", "nb_side_data", "flags",
                "color_range", "color_primaries", "color_trc", "colorspace",
                "chroma_location", "best_effort_timestamp", "metadata",
                "decode_error_flags", "hw_frames_ctx", "opaque_ref",
                "crop_top", "crop_bottom", "crop_left", "crop_right",
                "duration"),
    "AVCodecContext": ("codec_type", "codec_id", "width", "height", "pix_fmt",
                       "sample_rate", "ch_layout", "frame_size", "time_base",
                       "pkt_timebase", "hw_device_ctx", "get_format",
                       "thread_count", "thread_type", "lowres", "flags",
                       "flags2", "err_recognition", "skip_frame",
                       "skip_idct", "skip_loop_filter", "bit_rate",
                       "extradata", "extradata_size", "profile", "level"),
    "AVChannelLayout": ("order", "nb_channels", "u", "opaque"),
    "AVRational": ("num", "den"),
    "AVCodec": ("name", "long_name", "type", "id", "capabilities"),
    "AVInputFormat": ("name", "long_name", "flags", "extensions"),
    "AVDictionaryEntry": ("key", "value"),
}

C程序 = r'''
#include <stddef.h>
#include <stdio.h>
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavutil/frame.h>
#include <libavutil/channel_layout.h>
#include <libavutil/dict.h>
#include <libavutil/rational.h>

#define P(结构, 字段) printf("%s.%s=%zu\n", #结构, #字段, offsetof(结构, 字段))

int main(void) {
%(打印)s
    printf("sizeof.AVPacket=%zu\n", sizeof(AVPacket));
    printf("sizeof.AVFrame=%zu\n", sizeof(AVFrame));
    printf("sizeof.AVCodecParameters=%zu\n", sizeof(AVCodecParameters));
    printf("sizeof.AVStream=%zu\n", sizeof(AVStream));
    printf("sizeof.AVFormatContext=%zu\n", sizeof(AVFormatContext));
    return 0;
}
'''


def 生成() -> dict[str, int]:
    打印行 = []
    for 结构, 字段们 in 要的字段.items():
        for 字段 in 字段们:
            打印行.append(f'    P({结构}, {字段});')
    源码 = C程序.replace("%(打印)s", "\n".join(打印行))
    with tempfile.TemporaryDirectory() as 临时:
        目录 = Path(临时)
        (目录 / "偏移.c").write_text(源码, encoding="utf-8")
        编译器 = shutil.which("gcc") or shutil.which("cc")
        if not 编译器:
            raise SystemExit("找不到 gcc（生成偏移需要 C 编译器；用户机器不需要）")
        编译 = subprocess.run(
            [编译器, "-o", str(目录 / "偏移"), str(目录 / "偏移.c"),
             "-lavformat", "-lavcodec", "-lavutil"],
            capture_output=True, text=True)
        if 编译.returncode != 0:
            raise SystemExit("编译失败（是不是没装 FFmpeg 开发头文件？）：\n"
                          + 编译.stderr[-2000:])
        运行 = subprocess.run([str(目录 / "偏移")], capture_output=True, text=True)
        if 运行.returncode != 0:
            raise SystemExit(f"运行失败：{运行.stderr[-500:]}")
    结果: dict[str, int] = {}
    for 行 in 运行.stdout.splitlines():
        行 = 行.strip()
        if "=" not in 行:
            continue
        键, 值 = 行.split("=", 1)
        try:
            结果[键] = int(值)
        except ValueError:
            continue
    return 结果


def 写文件(表: dict[str, int]) -> None:
    行 = ['"""FFmpeg 结构体字段偏移（**自动生成，别手改**）。',
         "",
         "生成方式：``python3 tools/生成偏移.py``（用 C 编译器的 offsetof 打印，",
         "见该脚本里的说明：FFmpeg 结构体里夹着私有字段，手写会随版本错位）。",
         "",
         f"本次生成：{len(表)} 个字段。运行期只按偏移读，不需要编译器。",
         '"""',
         "",
         "from __future__ import annotations",
         "",
         "__all__ = [\"偏移\", \"大小\", \"偏移表\"]",
         "",
         "#: 结构体.字段 → 字节偏移",
         "偏移: dict[str, int] = {"]
    for 键 in sorted(表):
        if 键.startswith("sizeof."):
            continue
        行.append(f'    {键!r}: {表[键]},')
    行.append("}")
    行.append("")
    行.append("#: 各结构体的 sizeof（校验用：和 C 侧对不上就说明 ABI 变了）")
    行.append("大小: dict[str, int] = {")
    for 键 in sorted(表):
        if 键.startswith("sizeof."):
            行.append(f'    {键.split(".", 1)[1]!r}: {表[键]},')
    行.append("}")
    行.append("")
    行.append("")
    行.append("def 偏移表(结构: str, 字段: str) -> int:")
    行.append('    """取偏移；没有这个字段时抛 KeyError（比悄悄读错内存好）。"""')
    行.append('    return 偏移[f"{结构}.{字段}"]')
    行.append("")
    输出.write_text("\n".join(行) + "\n", encoding="utf-8")


def main() -> int:
    解析 = argparse.ArgumentParser(description="生成 FFmpeg 字段偏移表")
    解析.add_argument("--查看", action="store_true", help="只打印不写文件")
    参数 = 解析.parse_args()
    表 = 生成()
    if 参数.查看:
        for 键 in sorted(表):
            print(f"{键} = {表[键]}")
        return 0
    写文件(表)
    结构数 = len({键.split(".", 1)[0] for 键 in 表 if not 键.startswith("sizeof.")})
    print(f"已写入 {输出}：{结构数} 个结构体、{len(表)} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
