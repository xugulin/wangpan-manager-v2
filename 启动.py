#!/usr/bin/env python3
"""网盘管理 · 整合版启动入口。

一个程序，两套血脉：

* **界面 / 网盘管理 / AI** —— 来自 V1（``v8_3/``：顶栏网盘导航 + 左侧功能栏 +
  10 套主题的 GUI、百度/夸克/光鸭三家适配器与子进程桥、传输引擎与敏感词上传守卫、
  AI 播放顾问/字幕助手/本地模型/模型市场/语音识别 …）；
* **播放** —— 来自 V2（``wangpan/player``：ctypes 直连 libav* 的自研播放内核，
  画面由 Qt 控件自绘；另有弹幕引擎、TMDB 刮削与海报墙）。

用法::

    ./启动.sh                        # 图形界面（点「播放」页挑片子）
    ./启动.sh 某个视频.mp4            # 直接开播这个文件
    ./启动.sh --纯播放                # 只起 V2 的"纯内核"窗口（排查内核问题用）
    ./启动.sh --cli 网盘列表          # 命令行模式（同 V1 的 --cli）
    ./启动.sh --调试 / --静默 / --日志级别 警告 / --跳过自检

自举：当前解释器不是项目自带的 ``运行环境/venv`` 就换成它再跑一遍
（跟两边原来各自的做法一致；整合后**只有一个环境**，桥子进程也用它）。
"""

from __future__ import annotations

import gc
import os
import sys
from pathlib import Path

项目根 = Path(__file__).resolve().parent
自带解释器 = 项目根 / "运行环境" / "venv" / "bin" / ("python.exe" if os.name == "nt"
                                                 else "python")

# ---------------------------------------------------------------------------
# Python 3.14 + PySide6 的 GC 兼容策略（沿用 V1 的结论）
# ---------------------------------------------------------------------------
# 不手工 gc.collect()；只把自动整堆回收的阈值调高 —— 高阈值下"整堆扫描"次数骤降，
# 拖动进度条/切页时不再被一次几百毫秒的 GC 卡住。
if sys.version_info >= (3, 14):
    gc.disable()
    gc.set_threshold(1_000_000, 1_000_000, 1_000_000)

if str(项目根) not in sys.path:
    sys.path.insert(0, str(项目根))

# Windows 绿色版双击 启动.exe 时用 pythonw.exe（GUI 子系统，不弹控制台），
# 这种情况下 sys.stdout / sys.stderr 是 None：任何 print / logging 都可能炸。
if sys.stdout is None or sys.stderr is None:
    _空设备 = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = _空设备
    if sys.stderr is None:
        sys.stderr = _空设备


def _自举() -> None:
    """不是自带解释器就换过去（避免"ModuleNotFoundError: PySide6"）。"""
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
    sys.stderr.write(
        f"[启动] 当前解释器（{sys.executable}）不是项目自带的，"
        f"改用 {自带解释器} 重新执行…\n")
    sys.stderr.flush()
    os.execv(str(自带解释器), [str(自带解释器), str(Path(__file__).resolve()),
                             *sys.argv[1:]])


def _取出日志参数(argv: list[str]) -> tuple[list[str], str, bool, bool]:
    """取出 --调试 / --静默 / --日志级别 X / --跳过自检（其余原样返回）。"""
    级别 = "信息"
    静默 = False
    自检 = True
    其余: list[str] = []
    i = 0
    while i < len(argv):
        项 = argv[i]
        if 项 in ("--调试", "-d", "--debug"):
            级别 = "调试"
        elif 项 in ("--静默", "-q", "--quiet"):
            静默 = True
        elif 项 in ("--跳过自检", "--no-check"):
            自检 = False
        elif 项 in ("--日志级别", "--log-level") and i + 1 < len(argv):
            i += 1
            级别 = argv[i]
        elif 项.startswith("--日志级别="):
            级别 = 项.split("=", 1)[1]
        else:
            其余.append(项)
        i += 1
    return 其余, 级别, 静默, 自检


def _装卡死自诊断() -> None:
    """卡死时按需打印所有线程的调用栈（``kill -USR1 <pid>``）。"""
    try:
        import faulthandler
        import signal
        faulthandler.enable()
        if hasattr(signal, "SIGUSR1"):
            faulthandler.register(signal.SIGUSR1, all_threads=True)
    except Exception:  # noqa: BLE001
        pass


def 装卡顿诊断() -> bool:
    """命令行给了 --性能诊断/--诊断（或环境变量 V8_3_卡顿诊断=1）就开诊断。"""
    try:
        from v8_3.卡顿诊断 import 开着吗, 开始
        if not 开着吗():
            return False
        开了 = 开始()
        if 开了:
            print("[诊断] 卡顿诊断已开启 → 数据/卡顿诊断.log")
        return 开了
    except Exception:  # noqa: BLE001
        return False


def _设应用标识(应用) -> None:
    """给窗口一个**稳定的名字与图标**（快捷方式/任务栏靠它认人）。

    不设的话 Qt 用脚本名（``启动``）当 WM_CLASS，桌面环境会把窗口和快捷方式
    对不上号 —— 任务栏显示通用 Python 图标、``StartupWMClass`` 也没法写死。
    """
    try:
        # ⚠️ setApplicationName 必须与 .desktop 的 StartupWMClass 一致
        #    （工具/安装快捷方式.sh 里的 WM_CLASS），否则任务栏认不出这个程序。
        应用.setApplicationName("网盘管理V2")
        应用.setApplicationDisplayName("网盘管理")
        应用.setOrganizationName("WangpanManager")
        应用.setDesktopFileName("网盘管理_V2")
        from PySide6.QtGui import QIcon
        for 名字 in ("网盘管理_V2_图标.png", "网盘管理_图标.png"):
            图 = 项目根 / 名字
            if 图.is_file():
                图标 = QIcon(str(图))
                if not 图标.isNull():
                    应用.setWindowIcon(图标)
                    break
    except Exception:  # noqa: BLE001 - 图标/名字出问题不该让程序起不来
        pass


def _跑纯播放() -> int:
    """`--纯播放`：只起 V2 的"纯内核"窗口（不加载 V1 的 GUI/网盘/AI）。"""
    from PySide6.QtWidgets import QApplication
    from wangpan.ffmpeg import 加载
    if not 加载.可用():
        print("❌ " + 加载.不可用原因(), file=sys.stderr)
        return 3
    from wangpan import __version__
    版本 = {名: 加载.版本文本(号) for 名, 号 in 加载.库版本().items()}
    print(f"网盘管理 {__version__}｜纯播放模式｜FFmpeg：" +
          "、".join(f"{k} {v}" for k, v in 版本.items()), flush=True)
    from wangpan.控制台 import 修控制台
    修控制台()
    应用 = QApplication(sys.argv[:1])
    _设应用标识(应用)
    from wangpan.ui.主窗口 import 主窗口
    窗口 = 主窗口()
    窗口.show()
    for 参数 in sys.argv[1:]:
        if not 参数.startswith("-") and Path(参数).is_file():
            窗口.打开(参数)
            break
    return int(应用.exec())


def main() -> int:
    argv, 级别, 静默, 要自检 = _取出日志参数(list(sys.argv[1:]))

    if os.name != "nt" and "--cli" not in argv:
        无显示 = not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")
        平台 = (os.environ.get("QT_QPA_PLATFORM") or "").lower()
        离屏 = "offscreen" in 平台 or "minimal" in 平台
        if 无显示 and not 离屏:
            # 没有显示环境时给个明确说法（比 Qt 自己 abort 友好）。
            # 显式设了 offscreen/minimal（无人值守验证）就照常跑。
            print("没有 DISPLAY/WAYLAND_DISPLAY：这是桌面程序，请在图形会话里运行"
                  "（只想验证内核可以设 QT_QPA_PLATFORM=offscreen 并加 --纯播放）",
                  file=sys.stderr)
            return 2

    if "--纯播放" in argv:
        argv.remove("--纯播放")
        return _跑纯播放()

    from wangpan.控制台 import 修控制台
    修控制台()

    from v8_3.日志 import (设置终端日志, 关闭 as 关闭终端,
                        强制关闭 as 强制关闭终端, 打印启动横幅)  # noqa: F401
    设置终端日志(级别=级别)
    if 静默:
        强制关闭终端()      # 比配置里的 界面.终端日志 优先级更高

    if "--cli" in argv:
        argv.remove("--cli")
        from v8_3.命令行 import main as 命令行入口
        return 命令行入口(argv)
    if argv and argv[0] == "cli":
        from v8_3.命令行 import main as 命令行入口
        return 命令行入口(argv[1:])

    # 启动自检（主题/价格/时段/敏感词库/AI/网盘/连通性）
    运行时 = None
    主题名 = ""
    启动日志 = None
    if 要自检:
        from v8_3.配置 import 加载配置
        from v8_3.启动自检 import 运行启动自检
        自检 = 运行启动自检(加载配置(), None)
        运行时 = 自检.运行时
        主题名 = 自检.结果.get("主题", "")
        启动日志 = 自检.行
    else:
        打印启动横幅()

    装卡顿诊断()

    from v8_3.界面.主窗口 import 运行界面
    return int(运行界面(AI运行时=运行时, 主题=主题名,
                      启动日志=启动日志) or 0)


if __name__ == "__main__":
    _装卡死自诊断()
    _自举()
    raise SystemExit(main())
