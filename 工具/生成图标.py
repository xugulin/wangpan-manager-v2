#!/usr/bin/env python3
"""生成 V2 的图标（**不依赖任何外部素材/工具**：QPainter 画出来）。

风格：**简约清新**。三款可选：

* ``简约``（默认）：近白薄荷底 + 青绿细圆环 + 实心播放三角 + 两条极浅短横（弹幕意象）；
* ``极简``：只要一个青绿播放三角（透明底，交给桌面/任务栏自己的底）；
* ``深色``：深色底 + 薄荷色环与三角（深色面板/壁纸上更清楚）。

为什么自己画：快捷方式（桌面/菜单/任务栏）需要一个图标文件，而拿别的项目的图标
既不合身、也违背"V2 完全独立"。这里就是几十行绘图代码，没有任何第三方素材。

几个"小尺寸还认得出"的讲究：
* 三角用**圆角**（实心圆角比描边干净得多）；
* 尺寸 ≤ 24 px 时**不画环、不画短横**，只留一个略大的三角 —— 16 px 下环会糊成一团；
* 底用极浅薄荷渐变而不是纯白：白壁纸上也能看出边界（再配一圈极细青绿描边）。

产出（``--出目录`` 默认写到仓库根与 ``资源/图标/``）::

    网盘管理_V2_图标.png          256×256，快捷方式直接指它（与 V1 的做法一致）
    资源/图标/hicolor/<尺寸>/apps/网盘管理_V2.png    Linux 图标主题（16→256）
    资源/图标/网盘管理_V2.ico        Windows 快捷方式用
    资源/图标/预览.png             三款风格对比图（``--对比图`` 生成，选风格看它）

用法::

    工具/生成图标.py                                   # 默认（简约）
    工具/生成图标.py --样式 深色                        # 换风格
    工具/生成图标.py --对比图 资源/图标/预览.png         # 出一张三款对比图
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(项目根))

尺寸们 = (16, 32, 48, 64, 128, 256)
图标名 = "网盘管理_V2"
样式们 = ("简约", "极简", "深色")
#: 小于等于这个尺寸就简化（不画环与短横）：16/24 px 下细节只会糊
简化阈值 = 24


def _圆角三角(中心x: float, 中心y: float, 半径: float, 圆角比: float = 0.18):
    """朝右的圆角播放三角（三个角都倒圆，小尺寸下也不会有脏尖角）。"""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainterPath
    顶点 = [(中心x - 半径 * 0.52, 中心y - 半径), (中心x + 半径 * 0.72, 中心y),
          (中心x - 半径 * 0.52, 中心y + 半径)]
    路径 = QPainterPath()
    圆 = 半径 * 圆角比
    for i, p in enumerate(顶点):
        上, 下 = 顶点[i - 1], 顶点[(i + 1) % 3]
        v1 = (上[0] - p[0], 上[1] - p[1])
        v2 = (下[0] - p[0], 下[1] - p[1])
        l1 = math.hypot(*v1) or 1.0
        l2 = math.hypot(*v2) or 1.0
        a = (p[0] + v1[0] / l1 * 圆, p[1] + v1[1] / l1 * 圆)
        b = (p[0] + v2[0] / l2 * 圆, p[1] + v2[1] / l2 * 圆)
        if i == 0:
            路径.moveTo(*a)
        else:
            路径.lineTo(*a)
        路径.quadTo(QPointF(*p), QPointF(*b))
    路径.closeSubpath()
    return 路径


def 画图标(尺寸: int, 样式: str = "简约"):
    """把图标画成一张 ``QImage``（几何比例随尺寸缩放，小尺寸自动简化）。"""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import (QBrush, QColor, QImage, QLinearGradient, QPainter,
                             QPainterPath, QPen)

    S = int(尺寸)
    if S < 8:
        raise ValueError("尺寸太小了")
    if 样式 not in 样式们:
        raise ValueError(f"不认识的样式：{样式}（可选 {样式们}）")
    简化 = S <= 简化阈值
    主色 = QColor("#12B5A5")              # 青绿：清新，又不像"科技蓝"那么冷
    图 = QImage(S, S, QImage.Format.Format_ARGB32_Premultiplied)
    图.fill(Qt.GlobalColor.transparent)
    画 = QPainter(图)
    try:
        画.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        底 = QRectF(S * 0.05, S * 0.05, S * 0.90, S * 0.90)
        圆角 = S * 0.26
        if 样式 == "简约":
            渐 = QLinearGradient(底.topLeft(), 底.bottomRight())
            渐.setColorAt(0.0, QColor(255, 255, 255))
            渐.setColorAt(1.0, QColor(228, 244, 241))
            路径 = QPainterPath()
            路径.addRoundedRect(底, 圆角, 圆角)
            画.fillPath(路径, QBrush(渐))
            画.setPen(QPen(QColor(18, 181, 165, 56), max(1.0, S * 0.014)))
            画.drawPath(路径)
        elif 样式 == "深色":
            渐 = QLinearGradient(底.topLeft(), 底.bottomRight())
            渐.setColorAt(0.0, QColor(31, 45, 58))
            渐.setColorAt(1.0, QColor(22, 62, 70))
            路径 = QPainterPath()
            路径.addRoundedRect(底, 圆角, 圆角)
            画.fillPath(路径, QBrush(渐))
            主色 = QColor("#5FE3CF")       # 深底上得用薄荷色才亮得起来

        if not 简化 and 样式 != "极简":
            # 细圆环；播放三角落在环心（略偏右，视觉更稳）
            画.setBrush(Qt.BrushStyle.NoBrush)
            画.setPen(QPen(主色, max(1.6, S * 0.058)))
            画.drawEllipse(QPointF(S * 0.52, S * 0.5), S * 0.245, S * 0.245)
        if not 简化 and 样式 == "简约":
            # 两条极浅的"弹幕"短横：给上面留一点身份，但不抢视线
            画.setPen(Qt.PenStyle.NoPen)
            画.setBrush(QColor(18, 181, 165, 64))
            画.drawRoundedRect(QRectF(S * 0.17, S * 0.155, S * 0.22, S * 0.048),
                            S * 0.024, S * 0.024)
            画.drawRoundedRect(QRectF(S * 0.17, S * 0.245, S * 0.13, S * 0.048),
                            S * 0.024, S * 0.024)
        # 实心圆角三角
        画.setPen(Qt.PenStyle.NoPen)
        画.setBrush(主色)
        画.drawPath(_圆角三角(S * (0.52 if 简化 else 0.545), S * 0.5,
                            S * (0.185 if 简化 else 0.145)))
    finally:
        画.end()
    return 图


def 画对比图(路径: Path, 尺寸: int = 256) -> Path:
    """三款风格并排 + 小尺寸缩略（挑风格/看回归都靠它）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    间距 = 20
    宽 = 尺寸 * len(样式们) + 间距 * (len(样式们) + 1)
    图 = QImage(宽, 尺寸 + 130, QImage.Format.Format_ARGB32_Premultiplied)
    图.fill(QColor("#3A8F6E"))            # 拿桌面上那种绿当背景，看贴上去什么效果
    画 = QPainter(图)
    try:
        画.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        字 = QFont()
        字.setPixelSize(20)
        画.setFont(字)
        for i, 样式 in enumerate(样式们):
            x = 间距 + i * (尺寸 + 间距)
            画.drawImage(x, 间距, 画图标(尺寸, 样式))
            for j, 小 in enumerate((64, 32, 16)):
                画.drawImage(x + 间距 + j * 46, 尺寸 + 间距 + 6, 画图标(小, 样式))
            画.setPen(QColor(255, 255, 255))
            画.drawText(x, 尺寸 + 间距 - 4, 样式)
    finally:
        画.end()
    路径.parent.mkdir(parents=True, exist_ok=True)
    图.save(str(路径), "PNG")
    return 路径


def main() -> int:
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])

    解析 = argparse.ArgumentParser()
    解析.add_argument("--出目录", default=str(项目根))
    解析.add_argument("--样式", default="简约", choices=list(样式们))
    解析.add_argument("--对比图", default="")
    参数 = 解析.parse_args()
    出 = Path(参数.出目录)
    出.mkdir(parents=True, exist_ok=True)

    主路径 = 出 / f"{图标名}_图标.png"
    画图标(256, 参数.样式).save(str(主路径), "PNG")
    print(f"✓ {主路径}（256×256，样式：{参数.样式}）")

    图标根 = 出 / "资源" / "图标" if 出 == 项目根 else 出 / "图标"
    for 尺寸 in 尺寸们:
        目录 = 图标根 / "hicolor" / f"{尺寸}x{尺寸}" / "apps"
        目录.mkdir(parents=True, exist_ok=True)
        画图标(尺寸, 参数.样式).save(str(目录 / f"{图标名}.png"), "PNG")
    print(f"✓ {图标根 / 'hicolor'}（{len(尺寸们)} 个尺寸：{尺寸们}）")

    ico = 图标根 / f"{图标名}.ico"
    if not 画图标(256, 参数.样式).save(str(ico), "ICO"):
        # Qt 的 ICO 写入插件不是每个平台都有；没有就退回 PNG（Windows 快捷方式也能用 PNG）
        ico = 图标根 / f"{图标名}_256.png"
        画图标(256, 参数.样式).save(str(ico), "PNG")
        print(f"⚠ 这个 Qt 不带 ICO 写入插件，改成 PNG：{ico}")
    else:
        print(f"✓ {ico}（Windows 快捷方式用）")

    if 参数.对比图:
        预览 = 画对比图(Path(参数.对比图))
        print(f"✓ {预览}（三款风格对比，选风格时看它）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
