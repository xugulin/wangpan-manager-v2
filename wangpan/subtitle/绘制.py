"""字幕绘制：把一条 :class:`~wangpan.subtitle.模型.字幕条目` 画到画布上（QPainter 自绘）。

为什么画在"画布"上而不是做一个字幕控件
======================================
V2 的画面本来就是我们自己画的（:meth:`wangpan.ui.视频控件.paintEvent` 里
``drawImage``），字幕跟着同一张画布走 —— 于是"字幕窗口和视频窗口对不齐"、
"全屏时字幕不见了"、"播放器自己又开一个窗口放字幕"这类问题**结构上不存在**。
这正是 V2 相对"把窗口交给第三方播放器"的价值所在。

为什么用 QPainterPath 描边（而不是把字画 8 遍）
=============================================
把同一条文本按 8 个方向偏移画黑、再画一次白的做法：要画 9 遍，而且偏移之间会漏出
锯齿缝（字号小的时候特别明显）。``QPainterPath`` + 粗笔描边 + 填充是**一次成型**，
描边的转角还是圆的。

为什么字号按"显示高度"算
========================
同一个 :func:`画字幕` 要能画在两种画布上：原始帧图（会被控件缩放）和控件本身。
都按"画布高度/22"算就会出现"窗口越大字越小"的怪现象，所以统一以控件的显示高度为视觉
基准，再乘"画布高/基准高"换算回画布像素 —— 屏幕上看起来才是同样大小。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

from .模型 import 位置中间, 位置顶部, 位置底部, 字幕条目

__all__ = ["画字幕", "默认字号比例", "脚本参考高"]

#: 没给字号时的默认：画面高度的 1/22 左右（常见播放器的默认字幕大小）
默认字号比例 = 1.0 / 22.0
#: 只有 ASS 原始字号、没有 PlayResY 时用的参考高（ASS 的惯例默认）
脚本参考高 = 288.0
#: 字号上下限：太小看不清，太大一屏只能放几个字（都别越界）
最小字号 = 8.0
最大字号 = 200.0

#: 候选字体族：Linux/Windows/macOS 的中文字体各不相同，**别写死一个** ——
#: 写死一个没装的族名，中文就是一排方块（看不清还查不出原因）。都没有就用 Qt 默认。
候选字体族 = ("Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei",
              "PingFang SC", "WenQuanYi Micro Hei", "DejaVu Sans")


def 画字幕(图像, 条目, 控件尺寸=None, 底部留白比例: float = 0.08) -> bool:
    """把 ``条目`` 画到 ``图像`` 上（**原地修改**），返回是否真画了东西。

    ``图像`` 可以是 ``QImage``（截图/离屏测试），也可以是 ``QWidget``（控件自绘）——
    两者都有 ``width()/height()``，QPainter 也都能接。注意：传控件时，**必须**在
    ``paintEvent`` 里那个 QPainter ``end()`` 之后再调（同一个设备不能同时有两个
    QPainter，嵌套 begin 会直接失败并整屏不画）。

    ``控件尺寸``：画面在界面上最终显示的尺寸。字号以它为**视觉基准**算，再乘
    "画布高 / 基准高"换算回画布像素 —— 同一份字幕画在原始帧上和画在控件上，
    屏幕上看起来才是同样大（不给就按画布自己的高度算）。
    ``底部留白比例`` 是字幕离画面底边的距离占高度的比例——0 会贴着边，太大又像浮在中间。
    """
    if 条目 is None or not (条目.文本 or "").strip():
        return False                # 没字幕就一个像素都别碰（截图/比对像素的测试靠这个）
    宽, 高 = int(图像.width()), int(图像.height())      # QImage 和 QWidget 都有这两个方法
    if 宽 <= 0 or 高 <= 0:
        return False

    样式 = 条目.样式 or {}
    留白 = max(0.0, min(0.45, float(底部留白比例)))
    基准高 = float(控件尺寸.height()) if 控件尺寸 is not None else float(高)
    if 基准高 <= 0:
        基准高 = float(高)
    换算 = 高 / 基准高
    设备字号 = _夹字号(_算字号(样式, 基准高) * 换算)

    字体 = QFont()
    字体.setFamilies(list(候选字体族))
    字体.setPixelSize(int(round(设备字号)))
    字体.setBold(bool(样式.get("粗体")))
    字体.setItalic(bool(样式.get("斜体")))
    尺 = QFontMetricsF(字体)

    行们 = (条目.文本 or "").split("\n")
    行高 = 尺.height()
    块高 = 行高 * len(行们)
    块宽 = max((尺.horizontalAdvance(行) for 行 in 行们), default=0.0)
    水平, 垂直 = _对齐方式(样式)
    左, 上 = _定位(水平, 垂直, 块宽, 块高, 宽, 高, 留白, 样式)

    # 逐行拼路径：每行在"块宽"里按对齐方式放，再把各行的基线按行高依次排下去
    路径 = QPainterPath()
    for 序号, 行 in enumerate(行们):
        if not 行:
            continue
        行宽 = 尺.horizontalAdvance(行)
        if 水平 == "左":
            起x = 左
        elif 水平 == "右":
            起x = 左 + 块宽 - 行宽
        else:
            起x = 左 + (块宽 - 行宽) / 2
        路径.addText(起x, 上 + 行高 * 序号 + 尺.ascent(), 字体, 行)

    颜色 = QColor(str(样式.get("颜色") or "#FFFFFF"))
    if not 颜色.isValid():
        颜色 = QColor("#FFFFFF")        # 解析出来的颜色可能不合法，界面不能被一条烂字幕搞崩

    画 = QPainter(图像)
    try:
        # 中文小字号必须开抗锯齿，不然笔画糊成一团
        画.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        画.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        # 先粗黑描边、再填白字：亮背景（雪地/白墙）上也看得清 —— 这是字幕可读性的关键
        画.setPen(QPen(QColor(0, 0, 0), max(1.0, 设备字号 * 0.16),
                       Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        画.setBrush(Qt.BrushStyle.NoBrush)
        画.drawPath(路径)
        画.setPen(Qt.PenStyle.NoPen)
        画.setBrush(颜色)
        画.drawPath(路径)
    finally:
        画.end()
    return True


def _夹字号(字号: float) -> float:
    return max(最小字号, min(最大字号, float(字号)))


def _算字号(样式: dict, 基准高: float) -> float:
    """样式 → 视觉字号（像素，尚未换算回画布）。

    优先用"字号比例"（解析 ASS 时就按 PlayResY 算好了，跨分辨率不会错）；
    只有原始 ASS 字号（手工构造的条目）时按惯例的 288 参考高折算。
    """
    比例 = 样式.get("字号比例")
    if 比例:
        return max(1.0, 基准高 * float(比例))
    字号 = 样式.get("字号")
    if 字号:
        return max(1.0, 基准高 * float(字号) / 脚本参考高)
    return max(1.0, 基准高 * 默认字号比例)


def _对齐方式(样式: dict) -> tuple:
    """→ ``(水平, 垂直)``，水平取 左/中/右，垂直取 顶部/中间/底部。

    ASS 的 ``\\an``/Alignment 是 1..9 的小键盘布局：**个位定水平**（1/4/7 左，3/6/9 右，
    2/5/8 中）、**十位档定垂直**（1-3 底、4-6 中、7-9 顶）。
    显式的"位置"优先于从"对齐"推出来的 —— 前面的里程碑/手工调用只给"位置"时也得对。
    """
    水平, 垂直 = "中", 位置底部
    对齐 = 样式.get("对齐")
    if isinstance(对齐, int) and 1 <= 对齐 <= 9:
        余 = 对齐 % 3
        水平 = "左" if 余 == 1 else ("右" if 余 == 0 else "中")
        垂直 = 位置顶部 if 对齐 >= 7 else (位置中间 if 对齐 >= 4 else 位置底部)
    位置 = 样式.get("位置")
    if 位置 in (位置顶部, 位置中间, 位置底部):
        垂直 = 位置
    return 水平, 垂直


def _定位(水平: str, 垂直: str, 块宽: float, 块高: float,
          宽: int, 高: int, 留白: float, 样式: dict) -> tuple:
    """算出文本块的左上角（画布像素）。"""
    边距 = max(2.0, 高 * 留白)
    坐标 = 样式.get("坐标比例")
    if isinstance(坐标, (tuple, list)) and len(坐标) == 2:
        # ASS 的 \pos 给的是"对齐锚点"：按对齐方式把整块贴到锚点上
        锚x, 锚y = float(坐标[0]) * 宽, float(坐标[1]) * 高
        左 = 锚x if 水平 == "左" else (锚x - 块宽 if 水平 == "右" else 锚x - 块宽 / 2)
        上 = 锚y if 垂直 == 位置顶部 else (锚y - 块高 if 垂直 == 位置底部 else 锚y - 块高 / 2)
    else:
        if 垂直 == 位置顶部:
            上 = 边距
        elif 垂直 == 位置中间:
            上 = (高 - 块高) / 2
        else:
            上 = 高 * (1.0 - 留白) - 块高      # 底部：留白按整幅高度算，窗口大小变了比例不变
        if 水平 == "左":
            左 = 边距
        elif 水平 == "右":
            左 = 宽 - 块宽 - 边距
        else:
            左 = (宽 - 块宽) / 2
    # 长句 + 小窗口时宁可贴边，也别把字画到画布外面（画出去就永远看不到了）
    return max(0.0, min(左, 宽 - 块宽)), max(0.0, min(上, 高 - 块高))
