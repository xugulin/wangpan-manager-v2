"""测试公用：造素材、跑子进程（**只用系统 ffmpeg 造测试素材**，播放本身不依赖它）。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
if str(项目根) not in sys.path:
    sys.path.insert(0, str(项目根))

#: 离屏跑 Qt（没有 DISPLAY 时）
if not os.environ.get("DISPLAY") and os.name != "nt":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


#: 随仓库带的小素材（几十 KB）：没有 ffmpeg 命令时用它，测试就不再"跳过"
自带素材目录 = 项目根 / "工具" / "测试素材"


def 有ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg"))


def 自带素材(带声音: bool = True) -> Path:
    """仓库里带的测试素材（不依赖 ffmpeg 命令的存在）。"""
    名字 = "样片.mp4" if 带声音 else "样片_无音轨.mp4"
    路径 = 自带素材目录 / 名字
    if not 路径.is_file():
        raise RuntimeError(f"仓库里缺少测试素材：{路径}")
    return 路径


def 自带带章节素材() -> Path:
    """带 3 个章节的小素材（章节测试用，不需要 ffmpeg 现场生成）。"""
    路径 = 自带素材目录 / "带章节.mkv"
    if not 路径.is_file():
        raise RuntimeError(f"仓库里缺少章节素材：{路径}")
    return 路径


def 造素材(目录: Path, 秒: float = 3.0, 带声音: bool = True,
         尺寸: str = "320x180") -> Path:
    """拿一个小视频当测试素材。

    优先用系统 ffmpeg **现场生成**（尺寸/时长可控）；没有 ffmpeg（比如 Windows CI
    runner）就用仓库里带的素材 —— 这样"解码/硬解/引擎/网络"这些测试在 Windows 上
    也能真跑，而不是被 skip 掉。
    """
    目标 = Path(目录) / ("样片带声音.mp4" if 带声音 else "样片.mp4")
    if 目标.is_file():
        return 目标
    if not 有ffmpeg():
        import shutil as _shutil
        _shutil.copyfile(自带素材(带声音), 目标)
        return 目标
    命令 = ["ffmpeg", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc2=size={尺寸}:rate=25:duration={秒}"]
    if 带声音:
        命令 += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={秒}",
               "-c:a", "aac", "-shortest"]
    命令 += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-y", str(目标)]
    子 = subprocess.run(命令, capture_output=True, text=True, timeout=180)
    if 子.returncode != 0 or not 目标.is_file():
        raise RuntimeError(f"造素材失败：{子.stderr[-500:]}")
    return 目标


def 临时目录():
    return tempfile.TemporaryDirectory(prefix="v2测试_")
