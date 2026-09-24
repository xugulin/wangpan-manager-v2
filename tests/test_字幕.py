"""字幕模块（M4）：SRT/ASS 解析、按时间取条目、同名查找、离屏绘制叠加。

测什么、为什么这么测
====================
* 解析部分全部用**字符串样例**，不依赖任何外部素材（不调 ffmpeg、不读别的项目）；
  样例专门挑"真实文件里踩过的畸形写法"：逗号/点号时间、乱序缺号、BOM、
  GBK/UTF-16 编码、ASS 的 ``Format:`` 字段顺序被打乱、``\\p1`` 绘图。
* 轨道部分钉住**边界语义**（正好在起始/结束时刻）与"取() 必须是二分"——
  后者用统计 ``__getitem__`` 次数来测：线性扫 5000 条会访问几千次，二分只有十几次。
* 绘制部分在**离屏 QImage** 上画完直接数亮像素：不比较"看起来对不对"，
  而是钉"像素真的变了 / 该不变时一个像素都没动"。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.公用 import 临时目录

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from wangpan.subtitle import (位置顶部, 字幕条目, 字幕轨道, 找同名字幕, 画字幕,
                             空样式, 补全样式, 解析ASS, 解析SRT, 读字幕文件)

# ---------------------------------------------------------------------------
# 离屏工具
# ---------------------------------------------------------------------------

_应用 = None


def _取应用():
    """离屏 QApplication。

    画文字要用字体数据库，没有 QGuiApplication 时 Qt 会直接 abort
    （不是抛异常，是整个进程挂掉），所以绘制相关的用例必须先把它建起来。
    """
    global _应用
    _应用 = QApplication.instance() or QApplication([])
    return _应用


def _黑图(宽: int = 320, 高: int = 180) -> QImage:
    图 = QImage(宽, 高, QImage.Format.Format_RGB32)
    图.fill(Qt.GlobalColor.black)
    return 图


def _亮像素(图: QImage, 区域=None) -> int:
    """数"亮"像素（阈值 180，白字/描边附近才算）。

    为什么先转 8 位灰度：``Format_RGB32`` 的黑底在内存里是 ``0xFF000000``
    —— A 通道不是 0，所以"数非零字节"这种写法完全判断不出画没画上东西。
    转灰度后按阈值数，还顺便绕开了字节序与行填充（``bytesPerLine``）两个坑。
    """
    灰 = 图.convertToFormat(QImage.Format.Format_Grayscale8)
    数据 = bytes(灰.constBits())
    步 = 灰.bytesPerLine()
    宽, 高 = 灰.width(), 灰.height()
    x0, y0, x1, y1 = 区域 or (0, 0, 宽, 高)
    数 = 0
    for y in range(max(0, y0), min(高, y1)):
        行 = 数据[y * 步: y * 步 + 宽]
        数 += sum(1 for 值 in 行[x0:x1] if 值 > 180)
    return 数


def _亮范围(图: QImage):
    """亮像素的横向包围盒 ``(最小x, 最大x)``；一个亮的都没有时返回 None。"""
    灰 = 图.convertToFormat(QImage.Format.Format_Grayscale8)
    数据 = bytes(灰.constBits())
    步 = 灰.bytesPerLine()
    宽, 高 = 灰.width(), 灰.height()
    最小, 最大 = None, None
    for y in range(高):
        行 = 数据[y * 步: y * 步 + 宽]
        for x, 值 in enumerate(行):
            if 值 > 180:
                最小 = x if 最小 is None else min(最小, x)
                最大 = x if 最大 is None else max(最大, x)
    return None if 最小 is None else (最小, 最大)


class 计数列表(list):
    """数一下 ``__getitem__`` 被调了几次（用来证明 取() 是二分而不是线性扫）。"""

    def __init__(self, *参数) -> None:
        super().__init__(*参数)
        self.次数 = 0

    def __getitem__(self, 索引):
        if isinstance(索引, int):
            self.次数 += 1
        return super().__getitem__(索引)


# ---------------------------------------------------------------------------
# 样例
# ---------------------------------------------------------------------------

SRT正常 = """1
00:00:01,000 --> 00:00:03,000
第一行
第二行

2
00:00:04,500 --> 00:00:06,000
第三条字幕
"""

SRT乱序缺号 = """3
00:00:05,000 --> 00:00:06,000
排在文件最前面的第三条

1
00:00:01,000 --> 00:00:02,500
第一条

00:00:03,000 --> 00:00:04,000
没有序号的一条
"""

ASS标准 = """[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, \
BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, \
BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: 默认,思源黑体,24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,\
100,100,0,0,1,2,1,2,10,10,10,2

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.50,默认,讲解,0,0,0,,{\\fs20\\c&H0000FF&}第一行\\N第二行
Dialogue: 0,0:00:04.00,0:00:06.00,默认,,0,0,0,,{\\an8\\i1}顶部斜体
Comment: 0,0:00:07.00,0:00:08.00,默认,,0,0,0,,这是注释行，不该出现在轨道里
"""

#: ``Format:`` 的字段顺序被打乱（Start 打头、Text 挪到中间），Value 按同样顺序排
ASS乱序字段 = """[Events]
Format: Start, End, Layer, Style, Text, Name, MarginL, MarginR, MarginV, Effect
Dialogue: 0:00:02.00,0:00:04.00,0,默认,打乱顺序也不该串位,讲解,0,0,0,
"""

#: Text 在最后一列（标准如此）：**文本里的逗号全靠这一点**才不会被切断，
#: 所以 ``\pos(x,y)`` 这种自带逗号的标签要用这张样例测（塞进上面那张就会被切坏）
ASS文本最后 = """[Script Info]
PlayResX: 640
PlayResY: 360

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:02.00,0:00:04.00,默认,,0,0,0,,{\\pos(320,180)}中间的字
"""


# ---------------------------------------------------------------------------
# SRT
# ---------------------------------------------------------------------------

class SRT解析测试(unittest.TestCase):
    def test_正常解析(self):
        条目们 = 解析SRT(SRT正常)
        self.assertEqual(len(条目们), 2)
        第一条, 第二条 = 条目们
        self.assertAlmostEqual(第一条.起始秒, 1.0)
        self.assertAlmostEqual(第一条.结束秒, 3.0)
        self.assertEqual(第一条.文本, "第一行\n第二行")
        self.assertAlmostEqual(第二条.起始秒, 4.5)
        self.assertEqual(第二条.文本, "第三条字幕")
        # 没有样式标签时样式就是"什么都没设"（键齐全，上层不用判断键在不在）
        self.assertEqual(第一条.样式, 空样式())
        # 原始文本留着排查用（纯文本看不出标签有没有被吃掉）
        self.assertIn("第一行", 第一条.原始)

    def test_时间行的逗号与点号都认(self):
        逗号 = 解析SRT("1\n00:00:01,250 --> 00:00:02,500\n逗号\n")
        点号 = 解析SRT("1\n00:00:01.250 --> 00:00:02.500\n点号\n")
        for 条目 in (逗号[0], 点号[0]):
            self.assertAlmostEqual(条目.起始秒, 1.25, places=3)
            self.assertAlmostEqual(条目.结束秒, 2.5, places=3)

    def test_序号乱序与缺失都不影响(self):
        条目们 = 解析SRT(SRT乱序缺号)
        self.assertEqual([条.文本 for 条 in 条目们],
                         ["排在文件最前面的第三条", "第一条", "没有序号的一条"])
        # 解析按文件顺序返回，轨道负责排序
        轨道 = 字幕轨道("乱序", 条目们, "srt")
        self.assertEqual([条.文本 for 条 in 轨道.条目们],
                         ["第一条", "没有序号的一条", "排在文件最前面的第三条"])
        self.assertEqual(轨道.取(3.5).文本, "没有序号的一条")

    def test_带BOM与各种编码(self):
        正文 = "1\n00:00:01,000 --> 00:00:02,000\n带BOM的字幕\n"
        字符串版 = 解析SRT("\ufeff" + 正文)
        字节版 = 解析SRT(b"\xef\xbb\xbf" + 正文.encode("utf-8"))
        GBK版 = 解析SRT(正文.encode("gb18030"))
        UTF16版 = 解析SRT(正文.encode("utf-16"))
        for 条目们, 说明 in ((字符串版, "字符串 BOM"), (字节版, "字节 BOM"),
                            (GBK版, "GB18030"), (UTF16版, "UTF-16")):
            self.assertEqual(len(条目们), 1, 说明)
            self.assertEqual(条目们[0].文本, "带BOM的字幕", 说明)

    def test_多行文本与样式标签(self):
        样例 = ('1\n00:00:01,000 --> 00:00:03,000\n'
               '{\\an8}上面的字\n'
               '<b>加粗</b>与<font color="#00FF00">绿字</font>\n')
        条目 = 解析SRT(样例)[0]
        self.assertEqual(条目.样式["位置"], 位置顶部)
        self.assertEqual(条目.样式["对齐"], 8)
        self.assertIn("上面的字", 条目.文本)
        self.assertNotIn("{", 条目.文本, "{\\an8} 要当样式剥掉，不能留在字幕里")
        self.assertNotIn("<", 条目.文本, "HTML 味道的标签也要剥掉")
        self.assertEqual(条目.样式["颜色"], "#00FF00")
        self.assertTrue(条目.样式["粗体"])
        self.assertEqual(条目.文本.count("\n"), 1, "SRT 的真换行要保留成多行")

    def test_花括号里不是标签就原样留着(self):
        # 解析不了就当纯文本：{副歌} 这种是歌词内容，不能当覆盖块删掉
        条目 = 解析SRT("1\n00:00:01,000 --> 00:00:02,000\n{副歌}一起唱\n")[0]
        self.assertEqual(条目.文本, "{副歌}一起唱")


# ---------------------------------------------------------------------------
# ASS
# ---------------------------------------------------------------------------

class ASS解析测试(unittest.TestCase):
    def test_按Format字段顺序解析(self):
        条目们 = 解析ASS(ASS标准)
        self.assertEqual(len(条目们), 2, "Comment: 行不是字幕")
        第一条, 第二条 = 条目们
        self.assertAlmostEqual(第一条.起始秒, 1.0)
        self.assertAlmostEqual(第一条.结束秒, 3.5)
        self.assertEqual(第一条.文本, "第一行\n第二行", "\\N 要变成换行")
        self.assertAlmostEqual(第二条.起始秒, 4.0)
        self.assertEqual(第二条.文本, "顶部斜体")

    def test_Format字段顺序被打乱也对(self):
        # 这一条是 ASS 解析最容易踩的坑：字段顺序不固定，硬编码下标会"文本串位"
        条目们 = 解析ASS(ASS乱序字段)
        self.assertEqual(len(条目们), 1)
        条目 = 条目们[0]
        self.assertAlmostEqual(条目.起始秒, 2.0)
        self.assertAlmostEqual(条目.结束秒, 4.0)
        self.assertEqual(条目.文本, "打乱顺序也不该串位")
        self.assertNotIn("讲解", 条目.文本, "Name 字段不能混进文本")

    def test_颜色是BGR顺序_0000FF是红色(self):
        # ASS 写 &HBBGGRR&，所以 &H0000FF& 是**红**的（不是蓝的）—— 按 RGB 理解会正好搞反
        条目 = 解析ASS(ASS标准)[0]
        self.assertEqual(条目.样式["颜色"], "#FF0000")
        # 8 位形式 &HAABBGGRR 的前两位是透明度，要忽略（而且 ASS 里 00 表示**不**透明）
        样例 = ASS乱序字段.replace(
            "Dialogue: 0:00:02.00,0:00:04.00,0,默认,打乱顺序也不该串位,讲解,0,0,0,",
            "Dialogue: 0:00:02.00,0:00:04.00,0,默认,{\\1c&H00FF0000&}蓝色,讲解,0,0,0,")
        条目2 = 解析ASS(样例)[0]
        self.assertEqual(条目2.样式["颜色"], "#0000FF", "&H00FF0000& → AA=00 BB=FF GG=00 RR=00")
        self.assertEqual(条目2.文本, "蓝色")

    def test_样式表继承与行内覆盖(self):
        第一条, 第二条 = 解析ASS(ASS标准)
        # 样式表里 Fontsize=24（PlayResY=360 → 比例 24/360），行内 \fs20 覆盖成 20
        self.assertEqual(第一条.样式["字号"], 20)
        self.assertAlmostEqual(第一条.样式["字号比例"], 20 / 360, places=6)
        self.assertEqual(第二条.样式["字号"], 24, "没有 \\fs 时继承样式表")
        self.assertEqual(第二条.样式["颜色"], "#FFFFFF", "颜色也继承样式表")
        self.assertAlmostEqual(第二条.样式["字号比例"], 24 / 360, places=6)
        # style 行里 Bold=-1（ASS 用 -1 表示真）
        self.assertTrue(第二条.样式["粗体"])
        self.assertTrue(第二条.样式["斜体"], "\\i1")

    def test_an8是顶部与an2是底部(self):
        第一条, 第二条 = 解析ASS(ASS标准)
        self.assertEqual(第二条.样式["对齐"], 8)
        self.assertEqual(第二条.样式["位置"], 位置顶部)
        self.assertEqual(第一条.样式["位置"], "底部", "样式表 Alignment=2 → 底部")
        self.assertEqual(第一条.样式["对齐"], 2)

    def test_pos坐标按脚本分辨率归一化(self):
        条目 = 解析ASS(ASS文本最后)[0]
        self.assertEqual(条目.文本, "中间的字")
        # 画字幕只拿到一张图、拿不到 PlayRes，所以解析时就把绝对坐标化成比例：
        # \pos(320,180) 在 640x360 的脚本里 = 画面正中间
        比例 = 条目.样式["坐标比例"]
        self.assertAlmostEqual(比例[0], 0.5, places=6)
        self.assertAlmostEqual(比例[1], 0.5, places=6)

    def test_绘图指令当文本丢掉(self):
        样例 = ASS乱序字段.replace(
            "Dialogue: 0:00:02.00,0:00:04.00,0,默认,打乱顺序也不该串位,讲解,0,0,0,",
            "Dialogue: 0:00:02.00,0:00:04.00,0,默认,{\\p1}m 0 0 l 100 0 100 100{\\p0}画完后面的字,"
            "讲解,0,0,0,")
        条目 = 解析ASS(样例)[0]
        self.assertEqual(条目.文本, "画完后面的字", "\\p1..\\p0 之间是矢量绘图命令，不能当字幕文本")


# ---------------------------------------------------------------------------
# 轨道：取 / 在区间
# ---------------------------------------------------------------------------

class 字幕轨道测试(unittest.TestCase):
    def _轨(self):
        return 字幕轨道("测试", [字幕条目(0.0, 2.0, "一"),
                               字幕条目(2.0, 4.0, "二"),
                               字幕条目(10.0, 11.0, "三")], "srt")

    def test_取_边界与区间外(self):
        轨 = self._轨()
        self.assertEqual(轨.取(0.0).文本, "一", "起始时刻算命中")
        self.assertEqual(轨.取(1.999).文本, "一")
        self.assertEqual(轨.取(2.0).文本, "二", "两条首尾相接时，边界归下一条")
        self.assertEqual(轨.取(4.0).文本, "二", "结束时刻也算命中")
        self.assertIsNone(轨.取(4.001), "区间外要返回 None")
        self.assertIsNone(轨.取(-5.0))
        self.assertIsNone(轨.取(9.9))
        self.assertEqual(轨.取(10.5).文本, "三")

    def test_取_空轨道(self):
        轨 = 字幕轨道("空", [], "srt")
        self.assertIsNone(轨.取(0.0))
        self.assertIsNone(轨.取(123.0))
        self.assertEqual(轨.在区间(0.0, 10.0), [])
        self.assertTrue(轨.空)
        self.assertEqual(len(轨), 0)

    def test_取_重叠字幕也能找回来(self):
        # 正常字幕不重叠，但卡拉OK/双语文件里会出现嵌套；取() 要能往回找
        轨 = 字幕轨道("重叠", [字幕条目(0.0, 10.0, "长的一条"),
                             字幕条目(1.0, 2.0, "短的一条"),
                             字幕条目(3.0, 4.0, "更晚的一条")], "ass")
        self.assertEqual(轨.取(3.5).文本, "更晚的一条")
        self.assertEqual(轨.取(9.5).文本, "长的一条", "晚的条目已经结束，要回看还在盖着的那条")

    def test_取_必须是二分查找(self):
        # 界面每 16ms 问一次，长片几千条：线性扫会把主线程拖出可见卡顿
        条目们 = 计数列表(字幕条目(i * 3.0, i * 3.0 + 2.0, f"第{i}条") for i in range(5000))
        轨 = 字幕轨道("大量", 条目们, "srt")
        条目们.次数 = 0
        self.assertEqual(轨.取(3000 * 3.0).文本, "第3000条")
        self.assertIsNone(轨.取(3000 * 3.0 + 2.5))
        次数 = 条目们.次数
        self.assertLess(次数, 80, f"5000 条只该访问几十次（实际 {次数} 次）——取() 退化成线性了")

    def test_在区间(self):
        轨 = self._轨()
        self.assertEqual([条.文本 for 条 in 轨.在区间(1.0, 3.0)], ["一", "二"])
        self.assertEqual([条.文本 for 条 in 轨.在区间(3.0, 10.5)], ["二", "三"],
                         "跨过区间起点的条目也算（交集语义）")
        self.assertEqual([条.文本 for 条 in 轨.在区间(5.0, 9.0)], [])
        self.assertEqual([条.文本 for 条 in 轨.在区间(3.0, 1.0)], ["一", "二"], "传反了也认")

    def test_建轨道时自动排序(self):
        轨 = 字幕轨道("倒着给的", [字幕条目(9.0, 10.0, "后"), 字幕条目(1.0, 2.0, "前")], "srt")
        self.assertEqual([条.文本 for 条 in 轨.条目们], ["前", "后"])
        self.assertEqual(轨.取(1.5).文本, "前")


# ---------------------------------------------------------------------------
# 同名找字幕 / 读文件
# ---------------------------------------------------------------------------

class 找同名字幕测试(unittest.TestCase):
    def _造目录(self):
        临时 = 临时目录()
        self.addCleanup(临时.cleanup)
        根 = Path(临时.name)
        (根 / "影片.mp4").write_bytes(b"\x00")            # 媒体文件（内容不重要）
        (根 / "影片.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\n无语言后缀\n",
                                    encoding="utf-8")
        (根 / "影片.zh.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\n中文字幕\n",
                                       encoding="utf-8")
        (根 / "影片.chs.ass").write_text(ASS乱序字段, encoding="utf-8")
        # 下面这些都不该被算进来
        (根 / "影片2.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\n另一部\n", encoding="utf-8")
        (根 / "另一部.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\n无关\n", encoding="utf-8")
        (根 / "影片.zh.txt").write_text("不是字幕格式\n", encoding="utf-8")
        return 根

    def test_能找到同名字幕且不认无关文件(self):
        根 = self._造目录()
        轨道们 = 找同名字幕(根 / "影片.mp4")
        名字们 = [轨.名字 for 轨 in 轨道们]
        self.assertEqual(名字们, ["影片.srt", "影片.chs.ass", "影片.zh.srt"],
                         "精确同名的排最前，其余按文件名（顺序必须稳定）")
        self.assertEqual([轨.来源 for 轨 in 轨道们], ["srt", "ass", "srt"])
        self.assertEqual(轨道们[1].取(2.5).文本, "打乱顺序也不该串位", "找到的轨道要能直接用")

    def test_不相干文件一个都不算(self):
        根 = self._造目录()
        名字们 = [轨.名字 for 轨 in 找同名字幕(根 / "影片.mp4")]
        for 坏的 in ("影片2.srt", "另一部.srt", "影片.zh.txt"):
            self.assertNotIn(坏的, 名字们, f"{坏的} 不该算作 影片.mp4 的字幕")

    def test_没有字幕时返回空(self):
        临时 = 临时目录()
        self.addCleanup(临时.cleanup)          # 不留着引用，临时目录会立刻被回收掉
        根 = Path(临时.name)
        (根 / "孤单.mp4").write_bytes(b"\x00")
        self.assertEqual(找同名字幕(根 / "孤单.mp4"), [])
        self.assertEqual(找同名字幕(根 / "不存在的目录" / "孤单.mp4"), [])


class 读字幕文件测试(unittest.TestCase):
    def _临时文件(self, 名字: str, 内容: bytes):
        临时 = 临时目录()
        self.addCleanup(临时.cleanup)
        路径 = Path(临时.name) / 名字
        路径.write_bytes(内容)
        return 路径

    def test_按扩展名选解析器(self):
        srt = self._临时文件("a.srt", SRT正常.encode("utf-8"))
        ass = self._临时文件("b.ass", ASS标准.encode("utf-8"))
        vtt = self._临时文件(
            "c.vtt",
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nVTT 也按 SRT 处理\n".encode("utf-8"))
        self.assertEqual(读字幕文件(srt).名字, "a.srt")
        self.assertEqual(len(读字幕文件(srt).条目们), 2)
        self.assertEqual(读字幕文件(ass).来源, "ass")
        self.assertEqual(读字幕文件(ass).取(1.5).文本, "第一行\n第二行")
        self.assertEqual(读字幕文件(vtt).取(1.5).文本, "VTT 也按 SRT 处理")

    def test_GBK与UTF16文件都能读(self):
        gbk = self._临时文件("gbk.srt", SRT正常.encode("gb18030"))
        utf16 = self._临时文件("utf16.srt", SRT正常.encode("utf-16"))
        for 路径 in (gbk, utf16):
            self.assertEqual(读字幕文件(路径).取(1.5).文本, "第一行\n第二行", 路径.name)


# ---------------------------------------------------------------------------
# 画字幕（离屏像素断言）
# ---------------------------------------------------------------------------

class 画字幕测试(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _取应用()

    def test_像素真的变了(self):
        图 = _黑图()
        self.assertEqual(_亮像素(图), 0, "黑底应该一个亮像素都没有")
        条目 = 字幕条目(0.0, 2.0, "字幕 ABC", 补全样式())
        self.assertTrue(画字幕(图, 条目), "画上了要返回 True")
        self.assertGreater(_亮像素(图), 0, "画完必须真的有白字（黑底 + 白字最强对比）")

    def test_条目为None时一个像素都不改(self):
        图 = _黑图()
        原样 = bytes(图.constBits())
        self.assertFalse(画字幕(图, None))
        self.assertEqual(bytes(图.constBits()), 原样, "没字幕时不该碰画布（截图/像素比对靠这个）")
        空文本 = 字幕条目(0.0, 2.0, "   ", 补全样式())
        self.assertFalse(画字幕(图, 空文本), "空白文本也不画")
        self.assertEqual(bytes(图.constBits()), 原样)

    def test_位置顶部与底部(self):
        图 = _黑图()
        画字幕(图, 字幕条目(0.0, 2.0, "位置测试", 补全样式()))
        self.assertGreater(_亮像素(图, (0, 90, 320, 180)), 0, "不给位置时默认画在底部")
        顶图 = _黑图()
        画字幕(顶图, 字幕条目(0.0, 2.0, "位置测试", 补全样式({"位置": 位置顶部})))
        self.assertGreater(_亮像素(顶图, (0, 0, 320, 90)), 0, "位置=顶部 要画在上半部分")
        self.assertEqual(_亮像素(顶图, (0, 90, 320, 180)), 0, "顶部字幕不该跑到下半部分")

    def test_字号按图像高度换算(self):
        _取应用()
        小图, 大图 = _黑图(), _黑图()
        画字幕(小图, 字幕条目(0.0, 2.0, "字号", 补全样式({"字号比例": 0.03})))
        画字幕(大图, 字幕条目(0.0, 2.0, "字号", 补全样式({"字号比例": 0.16})))
        self.assertGreater(_亮像素(大图), _亮像素(小图), "字号比例大的，亮的像素必须更多")
        # 只有 ASS 原始字号（没有比例）时按惯例的 288 参考高折算，不能报错也不能画不出字
        原始图 = _黑图()
        self.assertTrue(画字幕(原始图, 字幕条目(0.0, 2.0, "字号", 补全样式({"字号": 20}))))
        self.assertGreater(_亮像素(原始图), 0)

    def test_对齐的左右(self):
        _取应用()
        左图, 右图 = _黑图(), _黑图()
        画字幕(左图, 字幕条目(0.0, 2.0, "左对齐", 补全样式({"对齐": 1})))
        画字幕(右图, 字幕条目(0.0, 2.0, "右对齐", 补全样式({"对齐": 3})))
        左范围, 右范围 = _亮范围(左图), _亮范围(右图)
        self.assertIsNotNone(左范围)
        self.assertIsNotNone(右范围)
        self.assertLess(左范围[0], 60, "对齐=1（ASS 左）要贴左边")
        self.assertGreater(右范围[1], 260, "对齐=3（ASS 右）要贴右边")

    def test_pos坐标画到指定位置(self):
        _取应用()
        图 = _黑图()
        条目 = 字幕条目(0.0, 2.0, "中间的锚点", 补全样式({"坐标比例": (0.5, 0.5), "对齐": 5}))
        self.assertTrue(画字幕(图, 条目))
        范围 = _亮范围(图)
        self.assertIsNotNone(范围)
        中点 = (范围[0] + 范围[1]) / 2
        self.assertAlmostEqual(中点, 160, delta=25, msg="\\pos 的锚点应该落在画面中间附近")

    def test_帧图与控件尺寸的关系(self):
        # 帧图很小、界面会把它放大显示：字号以"显示高度"为基准再换算回画布像素，
        # 屏幕上看起来才是同样大小（不是"窗口越大字越小"）。
        条目 = 字幕条目(0.0, 2.0, "缩放", 补全样式())
        帧图 = _黑图(160, 90)
        直接画 = _黑图(160, 90)
        画字幕(帧图, 条目)
        画字幕(直接画, 条目, QSize(160, 90))
        self.assertEqual(bytes(帧图.constBits()), bytes(直接画.constBits()),
                         "控件尺寸 == 画布尺寸时，两种调用必须画出完全一样的像素")
        # 控件比画布大得多（帧图会被放大）时，字也不能被算到画布外面去
        放大画 = _黑图(160, 90)
        self.assertTrue(画字幕(放大画, 条目, QSize(1920, 1080)))
        范围 = _亮范围(放大画)
        self.assertIsNotNone(范围, "放大显示时字也得画得出来（字号有下限，不能缩成 0）")
        self.assertGreaterEqual(范围[0], 0)
        self.assertLess(范围[1], 160)


# ---------------------------------------------------------------------------
# 界面钩子
# ---------------------------------------------------------------------------

class 视频控件字幕测试(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _取应用()

    def test_控件上真的叠加出字幕(self):
        from wangpan.ui.视频控件 import 视频控件
        控件 = 视频控件()
        控件.resize(320, 180)
        控件.设置帧(_黑图())
        控件.设置当前秒(1.0)
        self.assertEqual(_亮像素(控件.grab().toImage()), 0, "没有字幕轨时不该多画东西")
        轨道 = 字幕轨道("临时", [字幕条目(0.0, 5.0, "字幕 ABC", 补全样式())], "srt")
        控件.设置字幕(轨道)
        self.assertGreater(_亮像素(控件.grab().toImage()), 0, "挂了轨道以后控件上要真的出现字")
        # 播放头走到字幕区间外 → 这一帧没有字幕
        控件.设置当前秒(99.0)
        self.assertEqual(_亮像素(控件.grab().toImage()), 0, "区间外不该显示字幕")
        # 暂停字幕（启用=False）→ 也不画
        控件.设置当前秒(1.0)
        控件.设置字幕(轨道, False)
        self.assertEqual(_亮像素(控件.grab().toImage()), 0, "启用=False 时不该画字幕")
        控件.设置字幕(轨道, True)
        self.assertGreater(_亮像素(控件.grab().toImage()), 0)
        控件.设置字幕(None)
        self.assertEqual(_亮像素(控件.grab().toImage()), 0, "摘掉轨道后不该再画字幕")


class 画面摆放测试(unittest.TestCase):
    """画面怎么摆：默认**铺满**（不留黑边）、贴左上角；另有适应/拉伸两种模式。

    这一组是用户两次真机反馈的结果：
    * 先说"窗口一变，画面没跟播放器左上角对齐"（当时居中）；
    * 改成贴左上角后又发现"右侧有大黑边"（适应式会把余量全堆在右边）。
    ⇒ 默认铺满（按 max 缩放，多余裁掉）+ 贴左上角：**没有黑边、也不跳**。
    """

    @classmethod
    def setUpClass(cls):
        _取应用()

    def _控件(self, 控件宽, 控件高, 图宽=320, 图高=180):
        from wangpan.ui.视频控件 import 视频控件
        控件 = 视频控件()
        控件.resize(控件宽, 控件高)
        控件.设置帧(QImage(图宽, 图高, QImage.Format.Format_RGB32))
        return 控件

    def test_默认铺满且没有黑边(self):
        控件 = self._控件(1360, 600, 1920, 1080)      # 播放区比视频更宽
        区 = 控件.目标矩形()
        self.assertEqual(控件.缩放模式, "铺满", "默认必须是铺满（用户要求不留黑边）")
        self.assertEqual((区.x(), 区.y()), (0, 0), "仍然贴左上角")
        self.assertGreaterEqual(区.width(), 控件.width() - 1, "横向要铺满，不许留黑边")
        self.assertGreaterEqual(区.height(), 控件.height() - 1, "纵向也要铺满")

    def test_适应模式居中留黑边(self):
        控件 = self._控件(1360, 600, 1920, 1080)
        self.assertTrue(控件.设置缩放模式("适应"))
        区 = 控件.目标矩形()
        self.assertLessEqual(区.width(), 控件.width())
        self.assertLessEqual(区.height(), 控件.height() + 1)
        self.assertAlmostEqual(区.x(), (控件.width() - 区.width()) / 2, delta=1,
                            msg="适应模式要居中，黑边两侧平分")
        self.assertTrue(控件.设置缩放模式("铺满"), "切回去要生效")

    def test_拉伸模式正好填满(self):
        控件 = self._控件(1000, 400, 1920, 1080)
        控件.设置缩放模式("拉伸")
        区 = 控件.目标矩形()
        self.assertEqual((区.width(), 区.height()), (1000, 400))

    def test_不认识的模式被拒绝(self):
        from wangpan.ui.视频控件 import 视频控件
        控件 = 视频控件()
        self.assertFalse(控件.设置缩放模式("乱写"))
        self.assertEqual(控件.缩放模式, "铺满")

    def test_窗口变宽画面不动(self):
        """铺满模式下画面恒从 (0,0) 开始 —— 窗口怎么变都不会位移。"""
        窄 = self._控件(640, 360).目标矩形()
        宽 = self._控件(1280, 360).目标矩形()
        self.assertEqual((窄.x(), 窄.y()), (宽.x(), 宽.y()))

    def test_尺寸变化会上报(self):
        """控件尺寸变了要发信号 —— 页面据此同步引擎输出尺寸（不再去 resize 窗口）。"""
        控件 = self._控件(640, 360)
        收到 = []
        控件.尺寸变了.connect(lambda w, h: 收到.append((w, h)))
        控件.show()
        _取应用().processEvents()
        控件.resize(900, 500)
        _取应用().processEvents()
        self.assertTrue(收到, "resize 之后必须发出 尺寸变了")
        self.assertEqual(收到[-1], (900, 500))


if __name__ == "__main__":
    unittest.main()
