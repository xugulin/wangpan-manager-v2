#!/usr/bin/env python3
"""四项新功能的**真机验收**（进程内跑，不依赖键盘/鼠标注入）。

   运行环境/venv/bin/python 工具/新功能验收.py            # 用真实桌面（xcb/wayland）
   运行环境/venv/bin/python 工具/新功能验收.py --离屏      # 没显示器时用离屏

为什么写成"进程内"
==================
真机自动化有两条路：
* **外部工具注入键鼠**（``工具/真机测试.py`` 那条）—— 真实，但怕两件事：
  焦点被抢、以及系统偶尔弹出的模态框（本机实测被 COSMIC 的「允许远程控制?」
  整个挡住过，键鼠全进不去，看着像软件坏了）；
* **进程内调用**（本文件）—— 由程序自己把界面搭出来并调用那些入口，
  再截图存证。验的是同一套代码路径（``切换侧栏``/``要预览``/``打开独立窗口``/
  ``管理文件夹``），但**不需要任何输入注入**，因此不会被模态框挡。

两者互补：交互手感看 `工具/真机测试.py`，功能摆放看这里。

验的四件事
==========
1. 面板 → 独立悬浮窗口，**高度与主窗口一致**，且不超出屏幕；
2. 进度条预览 → 紧贴进度条上方弹出的小窗（控制条里不再有那条 90px 长条）；
3. 独立窗口 → 各种屏幕下都**不超出屏幕**（含"被撑到比屏幕还大"的收边）；
4. 媒体库文件夹 → 能打开管理框（可加多个文件夹，含网盘内的）。

产物：``数据/新功能验收/``（截图）+ ``数据/新功能验收.txt``（结构化结果）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

#: 有没有真实桌面（决定用 xcb 还是离屏）
项目根 = Path(__file__).resolve().parents[1]
if str(项目根) not in sys.path:
    sys.path.insert(0, str(项目根))

输出目录 = 项目根 / "数据" / "新功能验收"
报告路径 = 项目根 / "数据" / "新功能验收.txt"

结果: dict = {}
行: list[str] = []
失败项: list[str] = []


def 记(文本: str = "") -> None:
    print(文本, flush=True)
    行.append(str(文本))


def 断言(条件: bool, 说明: str, 数据=None) -> bool:
    记(f"  {'✔' if 条件 else '❌'} {说明}")
    if 数据 is not None:
        结果[说明] = 数据
    if not 条件:
        失败项.append(说明)
    return bool(条件)


def 截图(名字: str, 窗口=None) -> None:
    """抓一张屏（有 grim 就抓桌面；窗口对象还能再单独 grab 一份）。"""
    输出目录.mkdir(parents=True, exist_ok=True)
    if 窗口 is not None:
        try:
            图 = 窗口.grab()
            if not 图.isNull():
                图.save(str(输出目录 / f"{名字}_窗口.png"))
        except Exception:  # noqa: BLE001
            pass
    try:
        subprocess.run(["grim", str(输出目录 / f"{名字}.png")],
                       capture_output=True, timeout=40,
                       env={**os.environ, "XDG_RUNTIME_DIR":
                            os.environ.get("XDG_RUNTIME_DIR", "/run/user/1001"),
                            "WAYLAND_DISPLAY":
                            os.environ.get("WAYLAND_DISPLAY", "wayland-1")})
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    解析 = argparse.ArgumentParser(description="四项新功能的真机验收")
    解析.add_argument("--离屏", action="store_true", help="强制离屏（没有显示器时用）")
    解析.add_argument("--秒", type=float, default=2.0, help="每步等多久（默认 2 秒）")
    选项 = 解析.parse_args()

    有显示器 = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if 选项.离屏 or not 有显示器:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ.pop("DISPLAY", None)
        os.environ.pop("WAYLAND_DISPLAY", None)
    else:
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    os.environ["V8_3_不联网"] = "1"

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QGuiApplication, QImage
    from PySide6.QtWidgets import QApplication

    记("=" * 62)
    记("四项新功能真机验收")
    记("=" * 62)
    应用 = QApplication.instance() or QApplication([])
    from v8_3.界面.主窗口 import 主窗口
    窗口 = 主窗口()
    窗口.resize(1360, 840)
    窗口.show()
    窗口.切换到播放页()
    for _ in range(20):
        应用.processEvents()
    点 = lambda 毫秒=0: (time.sleep(毫秒 / 1000.0), 应用.processEvents())

    素材 = 项目根 / "工具" / "测试素材" / "样片.mp4"
    长素材 = 输出目录 / "长样片.mp4"
    页 = 窗口.播放页面()
    用的素材 = str(长素材 if 长素材.is_file() else 素材)
    页.播放本地路径(用的素材)
    for _ in range(40):
        应用.processEvents()
        time.sleep(0.05)

    # ---------------- 1) 面板：独立悬浮窗口 + 高度一致 ----------------
    记("\n[1] 面板 = 独立悬浮窗口，高度与主窗口一致")
    页.切换侧栏(True)
    点(800)
    面板窗 = 页._面板窗口
    断言(面板窗 is not None and 页.面板是独立窗口, "面板是独立悬浮窗口")
    if 面板窗 is not None:
        主框 = 窗口.frameGeometry()
        屏幕 = (窗口.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        期望高 = min(主框.height(), 屏幕.height())
        记(f"  主窗口 {主框.width()}x{主框.height()}｜面板 {面板窗.width()}x"
          f"{面板窗.height()}｜屏幕 {屏幕.width()}x{屏幕.height()}")
        断言(abs(面板窗.height() - 期望高) <= 40,
            f"面板高度与主窗口一致（{面板窗.height()} vs {期望高}）",
            {"面板高": 面板窗.height(), "主窗口高": 主框.height(),
             "屏幕高": 屏幕.height()})
        断言(面板窗.geometry().bottom() <= 屏幕.bottom() + 2
            and 面板窗.geometry().x() >= 屏幕.x(),
            "面板不超出屏幕",
            {"面板几何": list(面板窗.geometry().getRect())})
        断言(面板窗.isActiveWindow() is False, "面板不该抢走主窗口的键盘焦点")
    截图("1_面板", 页.左面板)
    页.切换侧栏(False)
    点(400)
    断言(not 页.面板可见(), "再切一次面板能收起来")

    # ---------------- 2) 进度条预览：悬浮小窗 ----------------
    记("\n[2] 进度条预览 = 紧贴进度条的小窗（没有了长条区域）")
    # 画面与控制条在**内核页**上（V1 播放页只是外壳 + 菜单/清单）
    内核 = 页.内核页
    条 = 内核.按钮滚动区.parentWidget()
    布局 = 条.layout()
    子们 = [布局.itemAt(i).widget() for i in range(布局.count())]
    断言(内核.预览窗 not in 子们 and 内核.预览 not in 子们,
        "控制条里不再有常驻的预览区（用户嫌它长又粗）")
    断言(内核.预览窗.isWindow(), "预览是独立小窗")
    # 走**真实路径**：把进度拖到中间 → 触发预览定时器 → 等它弹出
    内核.进度.setValue(500)
    内核._要预览(500)
    for _ in range(40):
        应用.processEvents()
        time.sleep(0.05)
    可见 = bool(内核.预览显示中)
    断言(可见, "拖进度条后预览小窗弹出来了", {"预览显示中": 可见})
    if 可见:
        条顶 = 内核.进度.mapToGlobal(内核.进度.rect().topLeft()).y()
        面板下边 = 内核.预览窗.geometry().bottom()
        屏幕 = QGuiApplication.primaryScreen().availableGeometry()
        记(f"  进度条顶 y={条顶}｜预览窗 {内核.预览窗.geometry().getRect()}"
          f"｜时间码 {内核.预览窗.时间.text()!r}")
        断言(面板下边 <= 条顶 + 2, "小窗贴在进度条**上方**（不遮进度条）")
        断言(内核.预览窗.geometry().x() >= 屏幕.x()
            and 内核.预览窗.geometry().right() <= 屏幕.right() + 2,
            "小窗不超出屏幕左右")
    截图("2_悬浮预览", 内核.预览窗)
    内核._收起预览()
    点(300)
    断言(not 内核.预览显示中, "鼠标离开/收起后小窗消失")

    # ---------------- 3) 独立窗口：不超出屏幕 ----------------
    记("\n[3] 独立窗口：适应屏幕、不超出屏幕")
    页.打开独立窗口()
    点(1200)
    独窗 = 页._独立窗口
    断言(独窗 is not None, "独立窗口开得出来")
    if 独窗 is not None:
        屏幕 = (独窗.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        角 = 独窗.frameGeometry()
        记(f"  独立窗 {角.width()}x{角.height()} @({角.x()},{角.y()})"
          f"｜屏幕 {屏幕.width()}x{屏幕.height()}")
        断言(角.x() >= 屏幕.x() and 角.y() >= 屏幕.y()
            and 角.right() <= 屏幕.right() + 2
            and 角.bottom() <= 屏幕.bottom() + 2,
            "开出来就在屏幕内", {"独立窗": list(角.getRect()),
                           "屏幕": list(屏幕.getRect())})
        # 故意撑到比屏幕还大：必须自己收回来
        独窗.resize(屏幕.width() + 700, 屏幕.height() + 500)
        点(600)
        角2 = 独窗.frameGeometry()
        断言(角2.width() <= 屏幕.width() and 角2.height() <= 屏幕.height(),
            f"被撑到比屏幕还大时会收回屏幕内（{角2.width()}x{角2.height()}）",
            {"收边后": list(角2.getRect())})
        截图("3_独立窗口", 独窗)
    页._收回独立窗口()
    点(800)
    断言(页._独立窗口 is None, "能收回主窗口")

    # ---------------- 4) 媒体库：多个文件夹（含网盘） ----------------
    记("\n[4] 媒体库文件夹：可加多个（本地 + 网盘）")
    窗口.切换到媒体库页()
    点(800)
    库页 = 窗口.媒体库页()
    断言(hasattr(库页, "管理文件夹") and hasattr(库页, "扫描全部来源"),
        "媒体库有'管理文件夹 + 扫描全部来源'")
    断言(callable(getattr(库页, "_列网盘目录", None)),
        "网盘列目录能力已经注入（能扫网盘里的文件夹）")
    断言(callable(getattr(库页, "_选网盘文件夹", None)),
        "网盘选文件夹能力已经注入")
    from wangpan.ui.媒体库文件夹对话框 import 媒体库文件夹对话框
    框 = 媒体库文件夹对话框(库页.来源清单, 窗口,
                       选网盘文件夹=库页._选网盘文件夹)
    框.show()
    点(500)
    断言(框.列表 is not None and 框.加网盘按钮.isEnabled(),
        "管理框能列出清单、且'＋网盘文件夹'可用")
    记(f"  清单现有 {len(库页.来源清单)} 个文件夹")
    截图("4_媒体库文件夹框", 框)
    框.close()
    点(200)

    记("")
    if 失败项:
        记(f"结论：**{len(失败项)} 项没通过**")
        for 项 in 失败项:
            记(f"  · {项}")
    else:
        记("结论：全部通过 ✔")
    报告路径.parent.mkdir(parents=True, exist_ok=True)
    报告路径.write_text(json.dumps(结果, ensure_ascii=False, indent=1)
                    + "\n\n" + "\n".join(行) + "\n", encoding="utf-8")
    记(f"\n报告：{报告路径}\n截图：{输出目录}")
    窗口.close()
    应用.processEvents()
    return 1 if 失败项 else 0


if __name__ == "__main__":
    raise SystemExit(main())
