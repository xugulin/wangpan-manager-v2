#!/usr/bin/env python3
"""绑定自检（CI 与排查用）：能不能加载 libav*、版本、路径、ABI 是否匹配。

用它而不是 ``python -c "…中文…"``：Windows 控制台默认 cp936，
命令行里带中文的 ``-c`` 脚本会直接 UnicodeEncodeError（真机踩过）。
"""

from __future__ import annotations

import sys
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(项目根))
from wangpan.控制台 import 修控制台          # noqa: E402

修控制台()

from wangpan.ffmpeg import 加载, 绑定        # noqa: E402


def main() -> int:
    print("平台：", sys.platform, "｜Python", sys.version.split()[0])
    print("可用：", 加载.可用())
    if not 加载.可用():
        print("不可用原因：", 加载.不可用原因())
        return 2
    print("版本：", {k: 加载.版本文本(v) for k, v in 加载.库版本().items()})
    print("路径：", {k: 加载.路径(k) for k in
                  ("avformat", "avcodec", "avutil", "swscale", "swresample")})
    from wangpan.ffmpeg.偏移 import 对应主版本
    运行时 = (int(加载.库("avcodec").avcodec_version()) >> 16) & 0xFF
    print(f"偏移表主版本：{对应主版本}｜运行时 avcodec 主版本：{运行时}")
    绑定.取绑定().检查ABI()
    print("ABI 自检：通过（偏移表与当前库匹配）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
