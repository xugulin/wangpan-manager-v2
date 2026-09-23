#!/usr/bin/env python3
"""网盘管理 V2 启动入口（自研播放内核）。

用法::

    ./启动.sh                      # 打开空窗口，点「📂 打开文件」
    运行环境/venv/bin/python 启动.py 某个视频.mp4      # 直接播

自举：如果当前解释器不是项目自带的 venv，就自动换成它（跟 V1 一样的做法，
但 V2 **不依赖 V1**）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

项目根 = Path(__file__).resolve().parent
自带解释器 = 项目根 / "运行环境" / "venv" / "bin" / ("python.exe" if os.name == "nt"
                                                 else "python")


def _自举() -> None:
    """不是自带解释器就换过去（避免用系统 python 跑出"没装 PySide6"）。"""
    if os.environ.get("V2_已自举"):
        return
    if not 自带解释器.is_file():
        return
    try:
        if Path(sys.executable).resolve() == 自带解释器.resolve():
            return
    except Exception:  # noqa: BLE001
        pass
    os.environ["V2_已自举"] = "1"
    os.execv(str(自带解释器), [str(自带解释器), str(Path(__file__).resolve()),
                             *sys.argv[1:]])


def main() -> int:
    _自举()
    sys.path.insert(0, str(项目根))
    from wangpan.控制台 import 修控制台
    修控制台()          # Windows 控制台是 cp936，不修的话第一句中文日志就抛异常
    if not os.environ.get("DISPLAY") and os.name != "nt":
        # 没有显示环境时给个明确说法（比 Qt 自己 abort 友好）
        print("没有 DISPLAY：V2 是桌面程序，请在图形会话里运行", file=sys.stderr)
        return 2
    from PySide6.QtWidgets import QApplication
    from wangpan import __version__
    from wangpan.ffmpeg import 加载
    from wangpan.ui.主窗口 import 主窗口

    if not 加载.可用():
        print("❌ " + 加载.不可用原因(), file=sys.stderr)
        return 3
    版本 = {名: 加载.版本文本(号) for 名, 号 in 加载.库版本().items()}
    print(f"网盘管理 V2 {__version__}｜FFmpeg：" +
          "、".join(f"{k} {v}" for k, v in 版本.items()), flush=True)

    应用 = QApplication(sys.argv[:1])
    _设应用标识(应用, 项目根)
    窗口 = 主窗口()
    窗口.show()
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
        窗口.打开(sys.argv[1])
    return 应用.exec()


def _设应用标识(应用, 根: Path) -> None:
    """给窗口一个**稳定的名字与图标**（快捷方式/任务栏靠它认人）。

    为什么必须显式设置：不设的话 Qt 用脚本名（``启动``）当 WM_CLASS，
    桌面环境会把窗口和快捷方式对不上号 —— 任务栏显示通用 Python 图标、
    ``StartupWMClass`` 也没法写死。设了之后：
    * ``WM_CLASS`` = ``网盘管理V2``（与 .desktop 的 StartupWMClass 一致）；
    * Wayland/GNOME 系还能靠 ``setDesktopFileName`` 与 .desktop 关联；
    * 窗口/任务栏图标用我们自己生成的 PNG（没有就用系统默认，绝不因此启动失败）。
    """
    try:
        应用.setApplicationName("网盘管理V2")
        应用.setApplicationDisplayName("网盘管理 V2")
        应用.setOrganizationName("WangpanV2")
        应用.setDesktopFileName("网盘管理_V2")
        from PySide6.QtGui import QIcon
        图 = 根 / "网盘管理_V2_图标.png"
        if 图.is_file():
            图标 = QIcon(str(图))
            if not 图标.isNull():
                应用.setWindowIcon(图标)
    except Exception:  # noqa: BLE001 - 图标/名字出问题不该让程序起不来
        pass



if __name__ == "__main__":
    raise SystemExit(main())
