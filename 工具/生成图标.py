#!/usr/bin/env python3
"""生成 V2 的图标（**不依赖任何外部素材/工具**：QPainter 画出来）。

为什么要自己画：
* 快捷方式（桌面 / 开始菜单 / 任务栏）需要一个图标文件，仓库里原来一个都没有；
* 图标是"一眼认出这是哪个程序"的东西，拿别的项目的图标既不合适、也不符合
  "V2 完全独立"的原则（这里就是几行绘图代码，没有任何第三方素材）。

产出（``--出目录`` 默认写到仓库根与 ``资源/图标/``）::

    网盘管理_V2_图标.png          256×256，快捷方式的 Icon 直接指它（跟 V1 的风格一致）
    资源/图标/hicolor/<尺寸>/apps/网盘管理_V2.png    Linux 图标主题用（16→256）
    资源/图标/网盘管理_V2.ico        Windows 快捷方式用（多尺寸）

图形含义：深色圆角底 + 白色播放三角（自研播放内核）+ 顶部三条"弹幕"短杠
（弹幕引擎）+ 右下角一个小方块（媒体库/海报墙）。16 px 下也认得出。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(项目根))

尺寸们 = (16, 32, 48, 64, 128, 256)
图标名 = "网盘管理_V2"
ICO尺寸 = (16, 32, 48, 64, 128, 256)


def 画图标(尺寸: int):
    """把图标画成一张 ``QImage``（同一个几何比例缩放到任意尺寸）。"""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import (QBrush, QColor, QImage, QLinearGradient, QPainter,
                             QPainterPath, QPen)

    S = int(尺寸)
    if S < 8:
        raise ValueError("尺寸太小了")
    图 = QImage(S, S, QImage.Format.Format_ARGB32_Premultiplied)
    图.fill(Qt.GlobalColor.transparent)
    画 = QPainter(图)
    try:
        画.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # ---- 圆角底 + 斜向渐变（深蓝 → 青）----
        底 = QRectF(S * 0.04, S * 0.04, S * 0.92, S * 0.92)
        渐变 = QLinearGradient(底.topLeft(), 底.bottomRight())
        渐变.setColorAt(0.0, QColor(28, 46, 92))
        渐变.setColorAt(0.55, QColor(24, 84, 132))
        渐变.setColorAt(1.0, QColor(18, 122, 128))
        路径 = QPainterPath()
        路径.addRoundedRect(底, S * 0.22, S * 0.22)
        画.fillPath(路径, QBrush(渐变))
        画.setPen(QPen(QColor(255, 255, 255, 60), max(1.0, S * 0.02)))
        画.drawPath(路径)
        # ---- 顶部三条"弹幕"短杠（右侧留白不同，看着像在飘）----
        画.setPen(Qt.PenStyle.NoPen)
        画.setBrush(QColor(255, 255, 255, 210))
        for 序号, (左边, 宽比) in enumerate(((0.16, 0.34), (0.16, 0.46), (0.16, 0.26))):
            y = S * (0.20 + 序号 * 0.10)
            画.drawRoundedRect(QRectF(S * 左边, y, S * 宽比, S * 0.055),
                            S * 0.03, S * 0.03)
        # ---- 播放三角（重心略偏右，视觉上更稳）----
        三角 = QPainterPath()
        三角.moveTo(QPointF(S * 0.38, S * 0.44))
        三角.lineTo(QPointF(S * 0.74, S * 0.665))
        三角.lineTo(QPointF(S * 0.38, S * 0.89))
        三角.closeSubpath()
        画.setBrush(QColor(255, 255, 255, 245))
        画.drawPath(三角)
        # ---- 右下角小方块：海报墙/媒体库 ----
        画.setBrush(QColor(255, 214, 102, 235))
        画.drawRoundedRect(QRectF(S * 0.16, S * 0.76, S * 0.14, S * 0.14),
                        S * 0.03, S * 0.03)
    finally:
        画.end()
    return 图


def main() -> int:
    from PySide6.QtWidgets import QApplication
    应用 = QApplication.instance() or QApplication([])

    解析 = argparse.ArgumentParser()
    解析.add_argument("--出目录", default=str(项目根))
    参数 = 解析.parse_args()
    出 = Path(参数.出目录)
    出.mkdir(parents=True, exist_ok=True)

    主图 = 画图标(256)
    主路径 = 出 / f"{图标名}_图标.png"
    主图.save(str(主路径), "PNG")
    print(f"✓ {主路径}（256×256）")

    图标根 = 出 / "资源" / "图标" if 出 == 项目根 else 出 / "图标"
    for 尺寸 in 尺寸们:
        目录 = 图标根 / "hicolor" / f"{尺寸}x{尺寸}" / "apps"
        目录.mkdir(parents=True, exist_ok=True)
        路径 = 目录 / f"{图标名}.png"
        画图标(尺寸).save(str(路径), "PNG")
    print(f"✓ {图标根 / 'hicolor'}（{len(尺寸们)} 个尺寸：{尺寸们}）")

    ico = 图标根 / f"{图标名}.ico"
    if not 画图标(256).save(str(ico), "ICO"):
        # Qt 的 ICO 写入插件不是每个平台都有；没有就退回 PNG（Windows 快捷方式也能用 PNG，
        # 只是不那么多尺寸自适应）
        ico = 图标根 / f"{图标名}_256.png"
        画图标(256).save(str(ico), "PNG")
        print(f"⚠ 这个 Qt 不带 ICO 写入插件，改成 PNG：{ico}")
    else:
        print(f"✓ {ico}（Windows 快捷方式用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
