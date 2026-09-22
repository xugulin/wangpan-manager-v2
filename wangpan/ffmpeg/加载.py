"""libav* 的加载：**自己 dlopen，不依赖 PyAV / ffmpeg-python / 系统 ffmpeg 命令**。

V2 的定位（用户定的路线）
=========================
"自己用 libav* 写播放器" —— 拆包、解码、缩放、重采样全部由我们自己驱动；
FFmpeg 只提供库（libavformat / libavcodec / libavutil / libswscale / libswresample）。
**不调用 ffmpeg / ffprobe 命令行**，也不依赖任何第三方 Python 绑定。

这个模块只负责"把库找出来并加载"，结构体定义在 :mod:`wangpan.ffmpeg.结构`，
函数原型在 :mod:`wangpan.ffmpeg.绑定`。

跨平台（Linux / Windows）
=========================
* Linux：按 soname 顺序试 ``libavformat.so.63`` → ``.62`` → ``.61`` …；
* Windows：官方/常见构建是 ``avformat-63.dll`` / ``avformat-61.dll`` 这种名字，
  也试 ``avformat.dll``；找不到时给出"把 DLL 放在哪"的提示；
* 可用环境变量覆盖（``V2_LIBAVFORMAT`` / ``V2_LIBAVCODEC`` / …），排查问题用。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
from pathlib import Path
from typing import Optional

__all__ = ["库", "可用", "不可用原因", "装载", "库版本", "搜索目录"]

#: 每个库的候选文件名（按优先级）
候选名 = {
    "avformat": ("libavformat.so.63", "libavformat.so.62", "libavformat.so.61",
                 "libavformat.so.60", "libavformat.so.59", "libavformat.so",
                 "avformat-63.dll", "avformat-62.dll", "avformat-61.dll",
                 "avformat.dll", "libavformat.dll"),
    "avcodec": ("libavcodec.so.63", "libavcodec.so.62", "libavcodec.so.61",
                "libavcodec.so.60", "libavcodec.so.59", "libavcodec.so",
                "avcodec-63.dll", "avcodec-62.dll", "avcodec-61.dll",
                "avcodec.dll", "libavcodec.dll"),
    "avutil": ("libavutil.so.61", "libavutil.so.60", "libavutil.so.59",
               "libavutil.so.58", "libavutil.so", "avutil-61.dll",
               "avutil-59.dll", "avutil.dll", "libavutil.dll"),
    "swscale": ("libswscale.so.10", "libswscale.so.9", "libswscale.so.8",
                "libswscale.so.7", "libswscale.so", "swscale-10.dll",
                "swscale-9.dll", "swscale-8.dll", "swscale.dll"),
    "swresample": ("libswresample.so.7", "libswresample.so.6",
                   "libswresample.so.5", "libswresample.so.4",
                   "libswresample.so", "swresample-7.dll", "swresample-6.dll",
                   "swresample-5.dll", "swresample.dll"),
}

#: 环境变量名（V2_LIBAVFORMAT 等）
环境名 = {名: f"V2_LIBAV{名.upper()}" for 名 in 候选名}

#: 额外搜索目录（自带库放这儿就能被找到：项目内 运行环境/libav，或程序同目录）
搜索目录 = (
    Path(__file__).resolve().parents[2] / "运行环境" / "libav",
    Path(__file__).resolve().parents[2] / "运行环境" / "ffmpeg",
    Path(sys.executable).resolve().parent,
    Path.cwd(),
)

_库: dict[str, ctypes.CDLL] = {}
_路径: dict[str, str] = {}
_错误 = ""


def _试加载(名: str) -> Optional[ctypes.CDLL]:
    """按候选名/环境变量/搜索目录依次尝试加载一个库。"""
    候选: list[str] = []
    指定 = os.environ.get(环境名[名], "").strip()
    if 指定:
        候选.append(指定)
    for 目录 in 搜索目录:
        try:
            if 目录.is_dir():
                for 文件 in 候选名[名]:
                    候选.append(str(目录 / 文件))
        except Exception:  # noqa: BLE001
            pass
    候选.extend(候选名[名])
    # ctypes.util.find_library 兜底（发行版装了 dev 包时有用）
    try:
        找到 = ctypes.util.find_library(名)
        if 找到:
            候选.append(找到)
    except Exception:  # noqa: BLE001
        pass
    for 文件名 in 候选:
        try:
            if os.sep in 文件名 and not Path(文件名).exists():
                continue
            句柄 = ctypes.CDLL(文件名)
            _路径[名] = 文件名
            return 句柄
        except OSError:
            continue
    return None


def 装载() -> bool:
    """加载全部需要的库；成功返回 True（失败原因见 :func:`不可用原因`）。"""
    global _错误
    if _库:
        return True
    for 名 in 候选名:
        句柄 = _试加载(名)
        if 句柄 is None:
            _错误 = (f"找不到 lib{名}（FFmpeg 的运行库）。\n"
                   f"  Linux：装 ffmpeg 或 libav{名[2:] if 名.startswith('lib') else 名} "
                   f"（Arch: sudo pacman -S ffmpeg；Debian: sudo apt install ffmpeg）\n"
                   f"  Windows：把 avformat-*.dll / avcodec-*.dll / avutil-*.dll / "
                   f"swscale-*.dll / swresample-*.dll 放到 运行环境/libav/ 下\n"
                   f"  也可以用环境变量指定，例如 {环境名[名]}=/绝对路径")
            _库.clear()
            return False
        _库[名] = 句柄
    _错误 = ""
    return True


def 可用() -> bool:
    return 装载()


def 不可用原因() -> str:
    if not _库:
        装载()
    return _错误


def 库(名: str) -> ctypes.CDLL:
    """取一个已加载的库（没装载就抛错，避免拿到 None 再炸在别处）。"""
    if not _库 and not 装载():
        raise RuntimeError(_错误)
    try:
        return _库[名]
    except KeyError as 错:  # pragma: no cover - 调用方写错库名
        raise RuntimeError(f"没有这个库：{名}") from 错


def 路径(名: str) -> str:
    return _路径.get(名, "")


def 库版本() -> dict[str, int]:
    """各库的版本号（avformat/avcodec/avutil 都有 *_version() 函数）。"""
    结果: dict[str, int] = {}
    for 名, 函数 in (("avformat", "avformat_version"),
                   ("avcodec", "avcodec_version"),
                   ("avutil", "avutil_version"),
                   ("swscale", "swscale_version"),
                   ("swresample", "swresample_version")):
        try:
            句柄 = 库(名)
            函数对象 = getattr(句柄, 函数)
            函数对象.restype = ctypes.c_uint
            结果[名] = int(函数对象())
        except Exception:  # noqa: BLE001
            continue
    return 结果


def 版本文本(编号: int) -> str:
    """``avcodec_version()`` 那种整数拆成 61.19.101 这种可读版本。"""
    主 = (编号 >> 16) & 0xFF
    次 = (编号 >> 8) & 0xFF
    微 = 编号 & 0xFF
    return f"{主}.{次}.{微}"
