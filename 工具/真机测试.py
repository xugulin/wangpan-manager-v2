#!/usr/bin/env python3
"""真机测试：把软件**真的跑在这台电脑的桌面上**，用真实输入驱动，抓真实屏幕。

和 `工具/整合验收.py` 的区别
============================
* 整合验收走 **offscreen**（没有窗口管理器、没有合成器、没有真实输入），
  验证的是"代码逻辑接得对不对"；
* 这一个走**真实会话**：真窗口、真焦点、真鼠标键盘事件、真合成器输出截图。
  验证的是"用户双击图标之后到底能不能用、退出干不干净"。

跑法::

    运行环境/venv/bin/python 工具/真机测试.py               # 默认 X11/XWayland（能真点）
    运行环境/venv/bin/python 工具/真机测试.py --平台 wayland  # 原生 Wayland（只能键盘）
    运行环境/venv/bin/python 工具/真机测试.py --保留窗口       # 测完不关窗口，自己看

前置
====
* 一个真实图形会话。脚本会自己把 `DISPLAY`/`WAYLAND_DISPLAY`/`XDG_RUNTIME_DIR`
  凑出来（从 `/tmp/.X11-unix/X*`、`/run/user/<uid>/wayland-*` 推），
  **不需要**你在图形终端里跑它。
* 截图用 `grim`（合成器输出，抓到的是屏幕上真实那一帧）；
  输入用 `xdotool`（X11）或 `wtype`（Wayland）。

三个"只有真机才踩得到"的经验（都写进代码里了）
==============================================
1. **不要用 `xdotool windowclose` 关窗**：它在 XWayland 下根本不投递
   `WM_DELETE_WINDOW`，于是应用不会退出 —— 看上去像"关窗有 bug"，
   其实是测试工具没送到（实测日志里一条"关窗"都没有）。这里自己按
   X11 协议发那条 ClientMessage。
2. **别只靠截图判断"点了有没有用"**：系统对话框（例如 COSMIC 的
   "允许远程控制?"）随时可能抢焦点，键盘事件就进不到应用里。
   所以断言读的是 **窗口标题**（`xdotool getwindowname`）—— 它不依赖焦点。
   （窗口标题跟随当前页 + 正在播的片名，就是这次整合为此加的功能。）
3. **强杀要连进程组一起收**：启动器是 shell 包了一层，只 `terminate()`
   启动器会留下 python 主进程与三家适配器桥进程。

产出::

    数据/真机测试/真机测试.txt      结构化结论（每一步做了什么、成没成）
    数据/真机测试/*.png             每一步的真实屏幕截图
    数据/真机测试/启动输出.txt       程序自己的日志（含 stderr）
"""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
输出目录 = 项目根 / "数据" / "真机测试"
启动输出 = 输出目录 / "启动输出.txt"
报告路径 = 输出目录 / "真机测试.txt"
启动器 = 项目根 / "一键启动_网盘管理_V2.sh"

行: list[str] = []
失败项: list[str] = []
跳过项: list[str] = []


def 记(文本: str = "") -> None:
    print(文本, flush=True)
    行.append(str(文本))


def 断言(条件: bool, 说明: str) -> bool:
    记(f"  {'✔' if 条件 else '❌'} {说明}")
    if not 条件:
        失败项.append(说明)
    return bool(条件)


def 跳过(说明: str) -> None:
    """环境做不到，不是软件的问题 —— 记成"跳过"而不是"失败"。"""
    记(f"  ⏭ 跳过：{说明}")
    跳过项.append(说明)


def 跑(命令: list[str], *, 超时: float = 30.0,
      环境: dict | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(命令, capture_output=True, text=True,
                              timeout=超时, env=环境)
    except subprocess.TimeoutExpired as 错:
        return subprocess.CompletedProcess(命令, 124, 错.stdout or "", "超时")


# ---------------------------------------------------------------------------
# 会话环境
# ---------------------------------------------------------------------------

def 找会话环境() -> dict:
    """把真实图形会话的环境变量凑出来（脚本通常不是从桌面终端启动的）。"""
    环境 = os.environ.copy()
    uid = os.getuid()
    环境.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    if not 环境.get("WAYLAND_DISPLAY"):
        for 候选 in sorted(Path(环境["XDG_RUNTIME_DIR"]).glob("wayland-*")):
            if 候选.suffix != ".lock":
                环境["WAYLAND_DISPLAY"] = 候选.name
                break
    if not 环境.get("DISPLAY"):
        插座 = Path("/tmp/.X11-unix")
        if 插座.is_dir():
            们 = sorted(插座.glob("X*"))
            if 们:
                环境["DISPLAY"] = ":" + 们[0].name[1:]
    return 环境


def 工具齐吗(平台: str, 环境: dict) -> tuple[bool, str]:
    if shutil.which("grim") is None:
        return False, "缺 grim（截图工具：apt install grim）"
    if 平台 == "wayland":
        if not 环境.get("WAYLAND_DISPLAY"):
            return False, "这台机器没有 Wayland 会话"
        if shutil.which("wtype") is None:
            return False, "缺 wtype（键盘注入：apt install wtype）"
    else:
        if not 环境.get("DISPLAY"):
            return False, "这台机器没有 X 会话"
        if shutil.which("xdotool") is None:
            return False, "缺 xdotool（输入注入：apt install xdotool）"
    return True, ""


# ---------------------------------------------------------------------------
# 真实关窗：按 X11 协议发 WM_DELETE_WINDOW
# ---------------------------------------------------------------------------

def 发关窗消息(窗口号: int, display: str = ":0") -> bool:
    """给 X11 窗口发一条 `WM_DELETE_WINDOW` —— 合成器上点 ✖ 发的就是这条。

    为什么要自己发：`xdotool windowclose` 在 XWayland 下不投递这条消息
    （实测应用完全没反应、日志里一条"关窗"都没有），会让人误判成软件有 bug。

    ⚠️ ctypes 调 libX11 **必须声明 argtypes**：不声明时指针会被按 int 截断，
    调用直接段错误 —— 本项目文档里"手写 ctypes 最容易踩的坑"正是这个，
    结果我写这个助手时自己先踩了一次。
    """
    class 客户消息(ctypes.Structure):
        _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                    ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                    ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                    ("format", ctypes.c_int), ("data", ctypes.c_long * 5)]

    class 事件(ctypes.Union):
        _fields_ = [("xclient", 客户消息), ("pad", ctypes.c_long * 24)]

    try:
        x11 = ctypes.CDLL("libX11.so.6")
    except OSError:
        return False
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XSendEvent.restype = ctypes.c_int
    x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                              ctypes.c_long, ctypes.c_void_p]
    x11.XFlush.restype = ctypes.c_int
    x11.XFlush.argtypes = [ctypes.c_void_p]

    显示 = x11.XOpenDisplay(str(display).encode())
    if not 显示:
        return False
    消息 = 事件()
    消息.xclient.type = 33                     # ClientMessage
    消息.xclient.send_event = 1
    消息.xclient.display = 显示
    消息.xclient.window = 窗口号
    消息.xclient.message_type = x11.XInternAtom(显示, b"WM_PROTOCOLS", 0)
    消息.xclient.format = 32
    消息.xclient.data[0] = x11.XInternAtom(显示, b"WM_DELETE_WINDOW", 0)
    结果 = x11.XSendEvent(显示, 窗口号, 0, 0, ctypes.byref(消息))
    x11.XFlush(显示)
    return bool(结果)


# ---------------------------------------------------------------------------
# 桌面：真实输入 + 真实截图
# ---------------------------------------------------------------------------

class 桌面:
    def __init__(self, 平台: str, 环境: dict) -> None:
        self.平台 = 平台
        self.环境 = 环境
        self.窗口号 = ""
        self.窗口位置 = (0, 0)
        self.窗口尺寸 = (0, 0)

    def 截图(self, 名字: str) -> Path:
        路径 = 输出目录 / f"{名字}.png"
        结果 = 跑(["grim", str(路径)], 超时=40, 环境=self.环境)
        if 结果.returncode != 0 or not 路径.is_file():
            记(f"  ⚠️ grim 失败：{结果.stderr.strip()[:80]}")
        return 路径

    def 找窗口(self, 标题片段: str = "网盘管理") -> str:
        if self.平台 != "xcb":
            return ""
        结果 = 跑(["xdotool", "search", "--name", 标题片段], 环境=self.环境)
        号们 = [x for x in 结果.stdout.split() if x.strip()]
        if not 号们:
            return ""
        self.窗口号 = 号们[-1]
        几何 = 跑(["xdotool", "getwindowgeometry", "--shell", self.窗口号],
                 环境=self.环境).stdout
        值 = dict(行_.split("=", 1) for 行_ in 几何.splitlines() if "=" in 行_)
        self.窗口位置 = (int(值.get("X", 0)), int(值.get("Y", 0)))
        self.窗口尺寸 = (int(值.get("WIDTH", 0)), int(值.get("HEIGHT", 0)))
        return self.窗口号

    def 改窗口尺寸(self, 宽: int, 高: int) -> bool:
        """把窗口改成指定尺寸（X11 直接改，不依赖焦点）。

        为什么用它验"播放中改窗口不崩"：用户真机反馈的崩溃是**窗口尺寸变化**触发的
        （点「面板」→ 按视频比例 resize 主窗口 → 界面线程去调解码器输出尺寸）。
        鼠标/键盘都受焦点影响，``xdotool windowsize`` 不受 —— 拿它当"改窗口"的稳定手段。
        """
        if self.平台 != "xcb" or not self.窗口号:
            return False
        结果 = 跑(["xdotool", "windowsize", self.窗口号, str(int(宽)), str(int(高))],
                 环境=self.环境)
        time.sleep(0.5)
        return 结果.returncode == 0

    def 标题(self) -> str:
        """当前窗口标题 —— 不依赖键盘焦点的可靠状态信号。"""
        if self.平台 != "xcb" or not self.窗口号:
            return ""
        return 跑(["xdotool", "getwindowname", self.窗口号],
                 环境=self.环境).stdout.strip()

    def 激活(self) -> None:
        if self.平台 == "xcb" and self.窗口号:
            跑(["xdotool", "windowactivate", "--sync", self.窗口号], 环境=self.环境)
            time.sleep(0.4)

    def 点(self, dx: int, dy: int, 说明: str = "") -> bool:
        """点窗口内相对坐标。

        ⚠️ 每次都**重新读一次窗口位置**，不能用启动时缓存的那份：全屏往返之后
        合成器会把窗口重新摆放（实测从 (580,236) 变成 (196,284)），
        拿旧几何算出来的绝对坐标就点到别处去了 —— 表现是"鼠标点击全没反应"。
        优先用 `mousemove --window`（相对窗口，天然不怕移动）。
        """
        if self.平台 != "xcb" or not self.窗口号:
            记(f"  ⏭ 跳过点击「{说明}」（{self.平台} 下外部工具点不了窗口）")
            return False
        self.找窗口()                     # 刷新位置/尺寸
        结果 = 跑(["xdotool", "mousemove", "--window", self.窗口号, str(dx), str(dy)],
                 环境=self.环境)
        if 结果.returncode != 0:
            x, y = self.窗口位置[0] + dx, self.窗口位置[1] + dy
            跑(["xdotool", "mousemove", str(x), str(y)], 环境=self.环境)
        跑(["xdotool", "click", "1"], 环境=self.环境)
        time.sleep(0.6)
        return True

    def 键(self, 键名: str, 说明: str = "") -> bool:
        if self.平台 == "wayland":
            结果 = 跑(["wtype", "-k", 键名], 环境=self.环境)
        else:
            self.激活()
            结果 = 跑(["xdotool", "key", 键名], 环境=self.环境)
        好 = 结果.returncode == 0
        记(f"  ⌨ {键名}（{说明}）{'✔' if 好 else '✘ ' + 结果.stderr.strip()[:60]}")
        return 好

    def 组合键(self, 修饰: str, 键名: str, 说明: str = "") -> bool:
        if self.平台 == "wayland":
            结果 = 跑(["wtype", "-M", 修饰, "-k", 键名, "-m", 修饰], 环境=self.环境)
        else:
            self.激活()
            结果 = 跑(["xdotool", "key", f"{修饰}+{键名}"], 环境=self.环境)
        好 = 结果.returncode == 0
        记(f"  ⌨ {修饰}+{键名}（{说明}）{'✔' if 好 else '✘ ' + 结果.stderr.strip()[:60]}")
        return 好


# ---------------------------------------------------------------------------
# 过程清理
# ---------------------------------------------------------------------------

def 收干净(进程, 环境: dict, 超时秒: float = 60.0) -> int | None:
    """等主进程退出；不退就**连进程组**一起收（启动器是 shell 包了一层）。"""
    截止 = time.time() + 超时秒
    while time.time() < 截止:
        if 进程.poll() is not None:
            return 进程.returncode
        time.sleep(0.5)
    try:
        os.killpg(os.getpgid(进程.pid), signal.SIGTERM)
    except Exception:  # noqa: BLE001
        进程.terminate()
    time.sleep(4)
    if 进程.poll() is None:
        try:
            os.killpg(os.getpgid(进程.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            进程.kill()
        time.sleep(2)
    for 项 in 跑(["pgrep", "-f", "工作进程.py --adapter"], 环境=环境).stdout.split():
        if 项.strip().isdigit():
            try:
                os.kill(int(项), signal.SIGTERM)
            except Exception:  # noqa: BLE001
                pass
    return 进程.poll()


def 存活(模式: str, 环境: dict) -> list[str]:
    出 = 跑(["pgrep", "-af", 模式], 环境=环境).stdout.splitlines()
    return [x for x in 出 if x.strip() and "pgrep" not in x and "bash -c" not in x]


# ---------------------------------------------------------------------------
# 测试素材
# ---------------------------------------------------------------------------

def 造素材() -> tuple[Path, Path]:
    视频 = 输出目录 / "真机样片.mp4"
    弹幕 = 输出目录 / "真机样片.xml"
    if not 视频.is_file():
        记("  用系统 ffmpeg 造一段 30 秒测试片（1280x720，带时间码与声音）…")
        跑(["ffmpeg", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
            "-vf", "drawtext=text='%{pts\\:hms}':fontsize=64:fontcolor=white:"
                   "box=1:boxcolor=black@0.6:x=40:y=40",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(视频)], 超时=300)
    if not 弹幕.is_file():
        弹幕.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><i>'
            + "".join(f'<d p="{i * 0.8:.1f},1,25,16777215,0,0,0,0">真机弹幕 第{i}条</d>'
                      for i in range(1, 26)) + "</i>", encoding="utf-8")
    return 视频, 弹幕


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    解析 = argparse.ArgumentParser(description="网盘管理 真机测试")
    解析.add_argument("--平台", choices=("xcb", "wayland"), default="xcb",
                   help="xcb（X11/XWayland，能真点）/ wayland（原生，只能键盘）")
    解析.add_argument("--保留窗口", action="store_true", help="测完不关窗口")
    选项 = 解析.parse_args()
    平台, 保留 = 选项.平台, 选项.保留窗口

    输出目录.mkdir(parents=True, exist_ok=True)
    环境 = 找会话环境()
    好, 原因 = 工具齐吗(平台, 环境)
    记("=" * 62)
    记("真机测试：在真实桌面上跑整合版（真实窗口 + 真实输入 + 真实截图）")
    记("=" * 62)
    记(f"平台    : {平台}")
    记(f"DISPLAY : {环境.get('DISPLAY') or '（无）'}   "
       f"WAYLAND: {环境.get('WAYLAND_DISPLAY') or '（无）'}")
    记(f"工具    : {'齐全' if 好 else 原因}")
    if not 好:
        记("→ 这台机器现在做不了真机测试")
        报告路径.write_text("\n".join(行) + "\n", encoding="utf-8")
        return 2

    视频, 弹幕 = 造素材()
    记(f"素材    : {视频.name}（{视频.stat().st_size // 1024} KB）+ {弹幕.name}")
    记()

    桌 = 桌面(平台, 环境)
    启动环境 = dict(环境, QT_QPA_PLATFORM=平台, PYTHONUNBUFFERED="1")
    启动输出.unlink(missing_ok=True)

    # ---------------- 1. 真实启动 ----------------
    记("[1] 用**真实入口**启动并直接开播")
    句柄 = 启动输出.open("w", encoding="utf-8")
    进程 = subprocess.Popen(
        [str(启动器), "--跳过自检", str(视频)], cwd=str(项目根), env=启动环境,
        stdout=句柄, stderr=subprocess.STDOUT, start_new_session=True)
    记(f"  启动器 PID = {进程.pid}")
    起播 = False
    截止 = time.time() + 90
    while time.time() < 截止:
        if 进程.poll() is not None:
            记(f"  ❌ 启动阶段就退了（退出码 {进程.returncode}）")
            break
        文本 = 启动输出.read_text(encoding="utf-8", errors="replace") \
            if 启动输出.exists() else ""
        if "[播放] 已打开" in 文本:
            起播 = True
        if not 桌.窗口号:
            桌.找窗口()
        if 起播 and 桌.窗口号:
            break
        time.sleep(1)
    if 平台 == "xcb":
        断言(bool(桌.窗口号),
            f"真实窗口已出现（id={桌.窗口号} 位置 {桌.窗口位置} 尺寸 {桌.窗口尺寸}）")
    else:
        文本0 = 启动输出.read_text(encoding="utf-8", errors="replace") \
            if 启动输出.exists() else ""
        断言("已预建各页" in 文本0 or "切到" in 文本0,
            "界面已起来（Wayland 下读不到窗口列表，用应用日志断言）")
    断言(起播, "真实解码已开始（日志出现「[播放] 已打开」）")
    time.sleep(4)
    桌.截图("01_启动并开播")
    标题 = 桌.标题()
    if 标题:
        断言("网盘管理" in 标题, f"窗口标题 = {标题!r}")
        断言(视频.name in 标题, f"标题带上了正在播的片名：{标题!r}")
    else:
        文本0 = 启动输出.read_text(encoding="utf-8", errors="replace")
        断言("切到" in 文本0, "应用日志里有切页记录（Wayland 下用它代替窗口标题）")

    # ---------------- 2. 逐页切换（真键盘） ----------------
    记()
    记("[2] 真实键盘翻页（Ctrl+数字），用**窗口标题**断言（不受焦点影响）")
    页们 = (("2", "跨网盘传输"), ("3", "视频播放"), ("4", "媒体库"),
           ("5", "敏感词"), ("6", "AI"), ("7", "日志"), ("8", "设置"),
           ("1", "📁"))

    if 平台 == "wayland":
        # 先探一下"键到不到得了应用"：Wayland 的键盘事件发给**当前焦点窗口**，
        # 而外部工具在 Wayland 下**没有**"把某个窗口激活"的协议（X11 有
        # `xdotool windowactivate`）。焦点在别处时键会全打进别的程序里，
        # 这时报"失败"是冤枉软件 —— 记成跳过并说清怎么让它有焦点。
        行数前0 = len(启动输出.read_text(encoding="utf-8", errors="replace").splitlines()) \
            if 启动输出.exists() else 0
        桌.组合键("ctrl", "2", "探测键盘可达性")
        time.sleep(1.5)
        文本探 = 启动输出.read_text(encoding="utf-8", errors="replace").splitlines()[行数前0:] \
            if 启动输出.exists() else []
        if not any("切到" in x for x in 文本探):
            跳过("原生 Wayland 下键盘没到应用：外部工具无法在 Wayland 里激活窗口，"
               "键只会发给当前焦点窗口。想跑完整交互：先用鼠标点一下程序窗口再重跑，"
               "或用 --平台 xcb（那条路是全自动且已全覆盖）")
            记("  （本轮剩下的键盘/退出断言一并跳过）")
            桌.截图("00_wayland无焦点")
            收干净(进程, 环境, 超时秒=10)
            记(f"\n产物：截图 {输出目录}")
            报告路径.write_text("\n".join(行) + "\n", encoding="utf-8")
            return 0

    for 序号, 期望 in 页们:
        行数前 = len(启动输出.read_text(encoding="utf-8", errors="replace").splitlines()) \
            if 启动输出.exists() else 0
        桌.组合键("ctrl", 序号, f"切到「{期望}」")
        time.sleep(1.5)
        标题 = 桌.标题()
        新行 = 启动输出.read_text(encoding="utf-8", errors="replace").splitlines()[行数前:] \
            if 启动输出.exists() else []
        日志说切了 = any(期望 in x and "切到" in x for x in 新行)
        # ⚠️ 两个信号**取或**：标题走的是窗口管理器（XWayland 下实测会读到过期值：
        #    应用日志已经"切到 媒体库"了，xdotool 读到的还是上一页的标题），
        #    而应用自己写的「[界面] 切到 X」是它的真实状态、且必然打到 stdout。
        #    只认标题会把"环境读不到"误判成"软件坏了"（实测一次报了 8 项没通过）。
        if 标题:
            断言(期望 in 标题 or 日志说切了,
                f"Ctrl+{序号} → 切到「{期望}」（标题 {标题!r}｜日志{'有' if 日志说切了 else '没有'}记录）")
        else:
            断言(日志说切了, f"Ctrl+{序号} → 日志出现「切到 {期望}」：{新行[-1:]!r}")
        桌.截图(f"02_页_{期望}")

    # ---------------- 2.5 播放中反复改窗口尺寸（用户崩溃场景） ----------------
    #  用户原话："播放页播放时点击面板会GUI窗宽度高度变化到这不是我想要的，
    #  多点两次面板还导致窗口崩溃了，GUI窗口变化时视屏的左上角没有对齐。"
    #  三件事对应三处修复，这里盯住"改窗口尺寸不许崩、不许软件自己乱改尺寸"：
    #   ① 点面板会 resize 主窗口 → 切换侧栏 不再调「重排」（单测钉住）；
    #   ② resize → 界面线程去改解码输出尺寸，原实现当场释放缩放器 → use-after-free。
    #      本机复现：播放中每 2ms 改一次尺寸，3/3 必崩；改后 3/3 存活。
    #      这里用 xdotool windowsize 反复改真窗口，等价于用户拖窗口/点面板。
    #   ③ 画面贴左上角（单测钉住：目标矩形 恒为 (0,0,w,h)）。
    记()
    记("[2.5] 播放中反复改窗口尺寸：进程必须活着、软件不许自己乱改尺寸")
    if 桌.窗口号:
        # 切回播放页再改尺寸 —— 用户报的场景是"播放页播放时"，别在网盘页上改
        桌.组合键("ctrl", "3", "回播放页再改尺寸")
        time.sleep(1.2)
        前尺寸 = 桌.窗口尺寸
        行数前25 = len(启动输出.read_text(encoding="utf-8", errors="replace").splitlines()) \
            if 启动输出.exists() else 0
        尺寸们 = [(900, 620), (1400, 860), (1100, 700), (1280, 820), (820, 560), (1400, 860)]
        活着 = True
        for 第, (宽, 高) in enumerate(尺寸们, 1):
            桌.改窗口尺寸(宽, 高)
            time.sleep(0.9)
            if 进程.poll() is not None:
                活着 = False
                断言(False, f"第 {第} 次改成 {宽}×{高} 后进程死了（用户报的就是这个）")
                break
            桌.找窗口()
        if 活着:
            断言(True, f"连着改了 {len(尺寸们)} 次窗口尺寸，进程一直活着")
            桌.找窗口()
            断言(桌.窗口尺寸 == (1400, 860),
                f"窗口尺寸就是我们最后设的那个（软件不许自己改尺寸）：{桌.窗口尺寸}")
            桌.截图("03_改尺寸6次后")
            断言("视频播放" in 桌.标题() or "视频播放" in (
                启动输出.read_text(encoding="utf-8", errors="replace")[-2000:]),
                f"改尺寸时停在播放页（用户报的场景）：{桌.标题()!r}")
        # 这一段的日志里不该出现新的异常
        新行25 = 启动输出.read_text(encoding="utf-8", errors="replace").splitlines()[行数前25:] \
            if 启动输出.exists() else []
        坏 = [x for x in 新行25 if "Traceback" in x or "Segmentation" in x or "Aborted" in x]
        断言(not 坏, f"改尺寸期间没有异常/段错误（{len(坏)} 行）")

    # ---------------- 3. 播放控制（真按键） ----------------
    记()
    记("[3] 播放控制（真按键）：暂停 / 继续 / 快进 / 全屏")
    桌.组合键("ctrl", "3", "回播放页")
    time.sleep(1.0)
    桌.键("space", "暂停")
    time.sleep(1.0)
    桌.截图("03_暂停")
    桌.键("space", "继续")
    time.sleep(1.0)
    桌.键("Right", "快进 5 秒")
    time.sleep(1.2)
    桌.截图("04_快进后")
    桌.键("f", "全屏")
    time.sleep(2.5)
    桌.截图("05_全屏")
    桌.键("Escape", "退出全屏")
    time.sleep(1.5)
    桌.截图("06_退出全屏")
    if 桌.标题():
        断言(视频.name in 桌.标题(), f"播放中标题仍是：{桌.标题()!r}")

    # ---------------- 4. 真鼠标点击 ----------------
    记()
    记("[4] 真鼠标点击左侧功能栏（X11 才有；Wayland 下跳过）")
    if 平台 == "xcb":
        桌.找窗口()                          # 全屏往返后位置会变，重新读一次
        # ⚠️ 不硬编码按钮 y 坐标：字体/主题/分辨率一变就会算错，而且误差会**累积**
        #    （实测间距不是估的 66px，于是从第 3 个按钮起就点空了）。
        #    这里改成"边点边看标题"的**自标定**：在每个按钮的可能范围内扫，
        #    点中了就把实测坐标记下来，下一个从它往下找。
        首页 = 10 + 64 + 8 + 8 + 20 + 6      # 顶栏 + 边距 + 标题累加（起点估计）
        上次 = 首页 + 30 - 66
        标定: dict[str, int] = {}
        for 名字, 期望 in (("传输", "跨网盘传输"), ("播放", "视频播放"),
                        ("媒体库", "媒体库"), ("敏感词", "敏感词"),
                        ("AI", "AI"), ("日志", "日志"), ("设置", "设置")):
            找到 = None
            for dy in range(max(60, 上次 + 24), 上次 + 140, 4):
                桌.点(56, dy)
                time.sleep(0.7)
                if 期望 in 桌.标题():
                    找到 = dy
                    break
            if 找到 is None:
                断言(False, f"点不到「{名字}」（扫过 y {max(60, 上次 + 24)}…{上次 + 140}）")
                上次 = 上次 + 66
                continue
            标定[名字] = 找到
            上次 = 找到
            断言(True, f"点「{名字}」→ 标题含「{期望}」（实测 y={找到}）")
        记("  📐 本机左侧栏实测坐标：" + "、".join(f"{k}={v}" for k, v in 标定.items()))
        桌.截图("07_鼠标点完")
    else:
        记("  ⏭ Wayland 下外部工具点不了窗口（键盘那部分已覆盖同样的功能）")

    # ---------------- 5. 关窗 ----------------
    记()
    记("[5] 真实关窗（发 X11 的 WM_DELETE_WINDOW）并检查退出是否干净")
    记(f"  关窗前：{len(存活('启动.py --跳过自检', 环境))} 个主进程，"
       f"{len(存活('工作进程.py --adapter', 环境))} 个适配器桥")
    if 保留:
        记("  ⏭ --保留窗口：不关")
    elif 平台 == "xcb" and 桌.窗口号:
        断言(发关窗消息(int(桌.窗口号), 环境.get("DISPLAY", ":0")),
             "WM_DELETE_WINDOW 已投递")
        退出码 = 收干净(进程, 环境, 超时秒=45)
        断言(退出码 == 0, f"进程正常退出（退出码 {退出码}）")
    else:
        # 原生 Wayland 发不了 WM_DELETE_WINDOW（xdotool 管不到），
        # 改用应用自己的 Ctrl+Q —— 一样会过 closeEvent 那套收尾。
        记("  用 Ctrl+Q 走应用自己的关窗收尾…")
        桌.组合键("ctrl", "q", "退出")
        退出码 = 收干净(进程, 环境, 超时秒=45)
        断言(退出码 == 0, f"进程正常退出（退出码 {退出码}）")

    # ---------------- 6. 事后检查 ----------------
    记()
    记("[6] 事后检查")
    文本 = 启动输出.read_text(encoding="utf-8", errors="replace") \
        if 启动输出.exists() else ""
    for 关键词, 说明 in (("Traceback", "Python 异常栈"),
                     ("Fatal Python error", "解释器致命错误"),
                     ("Aborted", "进程被 abort"),
                     ("Segmentation fault", "段错误"),
                     ("QThread: Destroyed", "线程还在跑就被销毁（会 abort）")):
        断言(关键词 not in 文本, f"日志里没有「{关键词}」（{说明}）")
    断言("已关闭" in 文本 or "已卸载" in 文本,
        "退出时真的走了收尾（关库 / 卸 AI 子进程）")
    # core 文件只看**项目自己的目录**（venv 里一堆叫 core.py 的，别误判）
    核心: list[Path] = []
    for 目录名 in ("v8_3", "wangpan", "工具", "tests", "适配器"):
        基 = 项目根 / 目录名
        if 基.is_dir():
            核心 += [p for p in 基.rglob("core") if p.is_file()]
            核心 += [p for p in 基.rglob("core.[0-9]*") if p.is_file()]
    断言(not 核心, f"项目源码目录里没有 core dump（{len(核心)} 个）")
    桥 = 存活("工作进程.py --adapter", 环境)
    断言(not 桥, f"没有遗留的适配器桥进程（{len(桥)} 个）")
    断言(not 存活("启动.py --跳过自检", 环境), "没有遗留的主进程")
    assert 弹幕.is_file()

    记()
    if 失败项:
        记(f"结论：**{len(失败项)} 项没通过**"
           + (f"（另有 {len(跳过项)} 项因环境限制跳过）" if 跳过项 else ""))
        for 项 in 失败项:
            记(f"  · {项}")
    else:
        记("结论：全部通过"
           + (f"（{len(跳过项)} 项因环境限制跳过）" if 跳过项 else ""))
    for 项 in 跳过项:
        记(f"  ⏭ 跳过原因：{项}")
    记(f"\n产物：截图 {输出目录}\n      报告 {报告路径}")
    报告路径.write_text("\n".join(行) + "\n", encoding="utf-8")
    return 1 if 失败项 else 0


if __name__ == "__main__":
    raise SystemExit(main())
