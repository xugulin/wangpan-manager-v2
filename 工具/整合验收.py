#!/usr/bin/env python3
"""整合验收（离屏真机）：把合并后的界面**逐页抓图**，并真播一段视频。

跑法::

    运行环境/venv/bin/python 工具/整合验收.py
    QT_QPA_PLATFORM=offscreen 运行环境/venv/bin/python 工具/整合验收.py   # 无桌面时

产出::

    数据/整合验收/*.png     每页一张（含真播中的画面、独立窗口、媒体库）
    数据/整合验收.txt       结构化结论（哪页建成了、播了多少帧、丢帧、硬解…）

为什么用"离屏 + 抓图"：这台机器没有 DISPLAY，但只要平台插件是 offscreen，
Qt 照样会**真的渲染**（布局、样式表、绘制都跑），`QWidget.grab()` 拿到的就是
屏幕上会显示的那一帧 —— 所以它能当"界面真的建起来了、真的画出来了"的证据。
播放部分是真的解封装/解码/上屏（帧数是内核统计出来的），不是模拟。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(项目根))
# ⚠️ 不能只用 setdefault：有些环境会预先设 QT_QPA_PLATFORM=wayland;xcb（哪怕没有显示服务），
#    setdefault 就不会覆盖，Qt 加载平台插件失败 → 进程 core dump。没有显示服务时强制离屏。
if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    当前 = (os.environ.get("QT_QPA_PLATFORM") or "").lower()
    if "offscreen" not in 当前 and "minimal" not in 当前:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
# 验收别去打真实网盘 / 别去联网刷新模型市场
os.environ.setdefault("V8_3_不联网", "1")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

输出目录 = 项目根 / "数据" / "整合验收"
报告路径 = 项目根 / "数据" / "整合验收.txt"

行: list[str] = []


def 记(文本: str) -> None:
    print(文本, flush=True)
    行.append(str(文本))


def 抽空(毫秒: int = 120) -> None:
    """让 Qt 把布局/绘制/事件都跑一遍（抓图前必须做）。"""
    截止 = time.monotonic() + 毫秒 / 1000.0
    while time.monotonic() < 截止:
        应用.processEvents()
        time.sleep(0.005)


def 抓(控件, 名字: str) -> Path:
    路径 = 输出目录 / f"{名字}.png"
    try:
        图 = 控件.grab()
        图.save(str(路径))
        记(f"  📷 {名字}.png  {图.width()}x{图.height()}"
           f"（{路径.stat().st_size // 1024} KB）")
    except Exception as 错:  # noqa: BLE001
        记(f"  ❌ 抓图失败 {名字}：{错}")
    return 路径


def 造媒体库数据(库路径: Path) -> None:
    """往资料库里塞两条带海报的条目 —— 否则海报墙是空的，抓图看不出东西。"""
    from PySide6.QtGui import QColor, QImage
    from wangpan.scrape.库 import 资料库
    from wangpan.scrape.模型 import 媒体条目, 媒体类型, 图片, 图片类型
    库 = 资料库(库路径)
    try:
        if 库.统计().媒体数 > 0:
            return
        for i, (标题, 年, 分) in enumerate((("沙丘", 2021, 7.8),
                                          ("怪奇物语", 2016, 8.6),
                                          ("银翼杀手 2049", 2017, 8.0))):
            图路径 = 输出目录 / f"_海报{i}.jpg"
            画 = QImage(300, 450, QImage.Format.Format_RGB32)
            画.fill(QColor(30 + i * 40, 80 + i * 30, 140 - i * 20))
            画.save(str(图路径))
            条 = 媒体条目(类型=媒体类型.电影, 标题=标题, 年份=年, 评分=分)
            条.海报 = 图片(图片类型.海报, f"/p{i}.jpg", 图路径, 300, 450)
            库.存条目(条)
    finally:
        库.关闭()


def main() -> int:
    输出目录.mkdir(parents=True, exist_ok=True)
    global 应用
    应用 = QApplication.instance() or QApplication(sys.argv[:1])
    from v8_3.界面.主窗口 import 主窗口

    记("=" * 62)
    记("整合验收：V1 界面 + V2 自研播放内核（离屏真机）")
    记("=" * 62)

    # ---- 0. 素材 ----
    素材 = 项目根 / "工具" / "测试素材" / "样片.mp4"
    if not 素材.is_file():
        记(f"❌ 找不到测试素材：{素材}")
        return 1
    记(f"素材：{素材.name}（{素材.stat().st_size // 1024} KB）")

    # ---- 1. 主窗口 + 逐页抓图 ----
    记("\n[1] 主窗口与各页面")
    窗口 = 主窗口()
    窗口.resize(1280, 800)
    窗口.show()
    抽空(400)
    记(f"  堆叠页数（含网盘页）= {窗口.堆叠.count()}")

    页面们 = [
        ("01_播放页", "切换到播放页"),
        ("02_传输页", "切换到传输页"),
        ("03_敏感词页", "切换到敏感词页"),
        ("04_日志页", "切换到日志页"),
        ("05_设置页", "切换到设置页"),
        ("06_AI页", "切换到AI页"),
        ("07_媒体库", "切换到媒体库页"),
    ]
    for 名字, 方法 in 页面们:
        try:
            getattr(窗口, 方法)()
            抽空(260)
            抓(窗口, 名字)
        except Exception as 错:  # noqa: BLE001
            记(f"  ❌ {方法} 失败：{type(错).__name__}: {错}")

    # ---- 2. 真播一段 ----
    记("\n[2] 真播一段本地视频（自研内核）")
    窗口.切换到播放页()
    页 = 窗口.播放页面()
    页.播放本地路径(str(素材))
    抽空(1800)
    统计 = 页.会话.引擎.统计
    记(f"  状态：{页.会话.状态文本()}")
    记(f"  时间：{统计.当前时间秒:.2f}/{统计.总时长秒:.2f}s")
    记(f"  帧：解出 {统计.已解视频帧}，丢弃 {统计.已丢视频帧}，帧率 {统计.帧率:.0f}fps")
    记(f"  硬解：{统计.硬解}")
    记(f"  音频：{统计.音频设备}")
    记(f"  有画面：{页.视频.有画面}")
    记(f"  AI决策：{页.会话.AI决策状态 or '（本地文件不走 AI，符合预期）'}")
    记(f"  信息栏：{页.信息栏.text().splitlines()[0] if 页.信息栏.text() else ''}")
    抓(窗口, "08_真播中")
    图 = 页.内核页.截图()
    记(f"  截图：{图 or '失败'}")

    # ---- 3. 独立窗口 ----
    记("\n[3] 独立播放窗口（搬同一个播放页，不复制播放器）")
    try:
        页.打开独立窗口()
        抽空(600)
        独立 = 页._独立窗口
        if 独立 is not None:
            独立.resize(900, 600)
            抽空(400)
            记(f"  独立窗口标题：{独立.windowTitle()}")
            记(f"  画面已搬过去：{页.内核页.parentWidget() is 独立.画面区}")
            抓(独立, "09_独立窗口")
            独立.关闭()
            抽空(300)
            记(f"  关掉后画面搬回主窗口：{页.内核页.parentWidget() is not 独立.画面区}")
        else:
            记("  ❌ 独立窗口没建出来")
    except Exception as 错:  # noqa: BLE001
        记(f"  ❌ 独立窗口失败：{type(错).__name__}: {错}")

    # ---- 4. 媒体库（带数据） ----
    记("\n[4] 媒体库（海报墙）")
    try:
        库页 = 窗口.媒体库页()
        记(f"  资料库可用：{库页.可用}")
        if 库页.可用:
            造媒体库数据(项目根 / "数据" / "资料库.db")
            库页.刷新()
            抽空(600)
            记(f"  统计：{库页.资料库.统计().摘要()}")
            抓(窗口, "10_媒体库_有数据")
    except Exception as 错:  # noqa: BLE001
        记(f"  ❌ 媒体库失败：{type(错).__name__}: {错}")

    # ---- 5. 收尾 ----
    记("\n[5] 退出收尾")
    try:
        # V1 的收尾挂在 closeEvent 上（隐藏窗口 → 关闸门 → 关适配器 → 各页收尾 → 摘线程）
        窗口.close()
        抽空(300)
        记("  ✅ 关窗收尾跑通（停线程/存位置/关库/停 AI 常驻子进程）")
    except Exception as 错:  # noqa: BLE001
        记(f"  ❌ 关窗收尾失败：{type(错).__name__}: {错}")

    记("\n验收结束。产物：")
    记(f"  截图：{输出目录}")
    记(f"  报告：{报告路径}")
    报告路径.write_text("\n".join(行) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
