#!/usr/bin/env python3
"""造测试素材（用系统 ffmpeg 命令；**播放本身不依赖它**）。

用法::

    python3 tools/生成素材.py                 # 造 2 秒 320x180 带声音
    python3 tools/生成素材.py 4K60            # 造 4K60 短片（压测用）
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
输出 = 项目根 / "数据" / "测试素材"


def 造(尺寸: str, 帧率: int, 秒: float, 名字: str) -> Path:
    输出.mkdir(parents=True, exist_ok=True)
    目标 = 输出 / 名字
    命令 = ["ffmpeg", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc2=size={尺寸}:rate={帧率}:duration={秒}",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={秒}",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", "-y", str(目标)]
    子 = subprocess.run(命令, capture_output=True, text=True)
    if 子.returncode:
        raise SystemExit(f"造素材失败：{子.stderr[-400:]}")
    print(f"✓ {目标}（{目标.stat().st_size / 1048576:.1f} MB）")
    return 目标


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("4k60", "4k"):
        造("3840x2160", 60, 5.0, "4K60.mp4")
    else:
        造("320x180", 25, 2.0, "样片.mp4")
        print("提示：加 4K60 参数可以造压测素材")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
