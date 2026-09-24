#!/usr/bin/env python3
r"""在 **wine**（真实 Windows 运行时）里跑界面验收 —— 只是 `工具/界面验收.py` 的薄包装。

为什么单独留一个入口
====================
wine 上有三件事必须在**导入 Qt 和项目之前**设好，否则会莫名其妙地失败：

1. `V2_已自举=1` —— 否则入口自举会想把解释器换成 Linux 的 `运行环境/venv/bin/python`；
2. `QT_QPA_PLATFORM=offscreen` —— wine 的 GL 在渲染复杂界面时会段错误，
   而 offscreen 下 `widget.grab()` 拿到的**仍然是真实 Windows 渲染结果**；
3. 输出写到 wine 的 `Z:\tmp\...`（wine 里 Linux 的 `/` 就是 `Z:\`），
   免得和 Linux 侧的产物混在一起。

跑法::

    WINEDEBUG=-all 运行环境/venv/bin/python 工具/wine验证.py
    # 或者直接给 wine 里的 Windows 解释器：
    WINEDEBUG=-all wine 构建/windows/python主/python.exe \
        'Z:\\home\\xgl\\python\\网盘管理_V2\\工具\\wine验证.py'

Windows 运行库怎么备（本机一次性）::

    # 1) Windows 版 CPython + PySide6：用 构建/windows/python主（V1 留下的那套）
    # 2) Windows 版 libav DLL（必须 **avcodec 63**，偏移表是按 63 生成的）：
    mkdir -p 构建/windows/libav
    curl -L -o /tmp/ff.zip \
      https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip
    7z x -y -o/tmp/ffx /tmp/ff.zip && cp /tmp/ffx/*/bin/av*.dll /tmp/ffx/*/bin/sw*.dll 构建/windows/libav/
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

os.environ["V2_已自举"] = "1"                 # 见模块开头第 1 条
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # 第 2 条
os.environ.setdefault("V8_3_不联网", "1")

项目根 = Path(__file__).resolve().parents[1]
if str(项目根) not in sys.path:
    sys.path.insert(0, str(项目根))

# 第 3 条：产物写 Z:\tmp\wine验收
sys.argv = [str(项目根 / "工具" / "界面验收.py"), "--输出目录",
            r"Z:\tmp\wine验收" if os.name == "nt" else "/tmp/wine验收",
            *[a for a in sys.argv[1:]]]
raise SystemExit(runpy.run_path(str(项目根 / "工具" / "界面验收.py"),
                               run_name="__main__").get("__exitcode__", 0))
