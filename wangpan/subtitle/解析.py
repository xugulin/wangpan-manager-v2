"""字幕解析：SRT / ASS(SSA) / VTT（当 SRT 处理）→ :class:`~wangpan.subtitle.模型.字幕条目`。

踩过的坑（都写在这里，别再犯）
================================
1. **ASS 的 ``Format:`` 行列顺序不是固定的**：SSA 写 ``Marked, Start, End, ...``，
   ASS 写 ``Layer, Start, End, ...``，不同工具生成的顺序、字段多少都可能不一样
   （有人把 ``Text`` 挪到中间）。**必须**先读 ``Format:`` 行拿到字段名，再按字段名去
   ``Dialogue:`` 行取值。按固定下标取的表现是"大部分文件能跑，个别文件文本全串位"——
   这种问题最难查，因为不报错。
2. **ASS 颜色是 ``&HBBGGRR``（BGR 顺序）**，不是 RGB：``\\c&H0000FF&`` 是**红色**。
   8 位形式 ``&HAABBGGRR`` 的前两位是透明度，而且 ASS 里 ``00`` 表示**不**透明
   （跟直觉相反），所以这里只取后 6 位。
3. **``Text`` 必须是最后一列**（标准如此）：切分用 ``split(",", 字段数-1)``，
   否则文本里的逗号会把后面的字段拱乱。
4. **SRT 的序号行不可信**：乱序、缺号、块之间多/少空行都见过。所以只按"时间行"分块，
   序号行仅在"后面紧跟时间行"时才丢掉 —— 绝不拿序号当解析依据。
5. **编码**：字幕文件 GBK / BIG5 / UTF-16 都常见，而且 UTF-16 要先看 BOM ——
   GB18030 几乎不挑字节，用错编码**不报错**，只是静默变乱码（比报错更难发现）。
6. **``\\p1`` 之后到 ``\\p0`` 之前是矢量绘图命令**（ASS 的招牌/图形字幕），
   当文本画出来就是满屏乱码字母，必须整段丢掉。

样式标签的取舍
==============
* ``{...}`` 块里**有** ``\\标签`` → 当覆盖块整块丢掉（认不出的 ``\\blur``/``\\2c`` 也丢，
  否则屏幕上会出现字面的 "\\blur3"）；
* ``{...}`` 块里**没有**任何 ``\\标签``（例如歌词里的 ``{副歌}``）→ 原样保留当文本；
* ``<i>`` / ``<b>`` / ``<font color=...>`` 也顺手认（SRT 里很常见），
  两个格式共用一套，免得各写一份。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

from .模型 import (位置底部, 位置顶部, 位置中间, 字幕条目, 字幕轨道,
                  补全样式, 空样式)

__all__ = ["解析SRT", "解析ASS", "读字幕文件", "找同名字幕", "字幕后缀们", "默认编码尝试"]

#: 解码候选顺序（按"出错概率从低到高"排）：UTF-8 带 BOM → 无 BOM → 简中 → 繁中 → UTF-16
默认编码尝试: tuple = ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16")

#: 认得的字幕后缀（.vtt 的时间行与 SRT 兼容，只是小数点，所以共用 SRT 解析器）
字幕后缀们: tuple = (".srt", ".ass", ".ssa", ".vtt")

#: ASS 没写 PlayResX/PlayResY 时的惯例默认（libass 用的就是 384x288）
默认脚本宽 = 384.0
默认脚本高 = 288.0

# ---------------------------------------------------------------------------
# 时间与文本的小工具
# ---------------------------------------------------------------------------

#: ``HH:MM:SS,mmm`` / ``H:MM:SS.cc`` / ``MM:SS``；小时可省、毫秒/厘秒都认
_时间格式 = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[,.](\d{1,6}))?$")

#: SRT/VTT 的时间行（箭头两侧）
_时间行 = re.compile(r"^\s*(\d{1,3}:\d{1,2}:\d{1,2}(?:[,.]\d{1,3})?)\s*-->\s*"
                     r"(\d{1,3}:\d{1,2}:\d{1,2}(?:[,.]\d{1,3})?)")

#: SRT 的序号行（只在"后面紧跟时间行"时才丢）
_序号行 = re.compile(r"^\s*\d{1,6}\s*$")

#: ``{...}`` 覆盖块
_花括号块 = re.compile(r"\{([^{}]*)\}")

#: 块里的一条覆盖标签：``\fs20`` / ``\pos(x,y)`` / ``\1c&HFFFFFF&``
#: 注意 ``\1c`` 这类以数字开头的标签，第二个分支专门接它
_覆盖标签 = re.compile(r"\\([A-Za-z]+|[1-4][a-z])([^\\{]*)")

#: ASS 颜色：``&HBBGGRR`` / ``&HBBGGRR&`` / ``&HAABBGGRR``
_ASS颜色模式 = re.compile(r"&?H([0-9A-Fa-f]{1,8})&?", re.IGNORECASE)

#: SRT 里常见的 HTML 味道标签（``<i>`` / ``<font color="#RRGGBB">``）
_尖括号标签 = re.compile(r"</?\s*(?:i|b|u|s|font)\b[^>]*>", re.IGNORECASE)
_尖括号颜色 = re.compile(r"""color\s*=\s*["']?(#[0-9A-Fa-f]{6})""", re.IGNORECASE)

#: ASS 没写 ``Format:`` 行时的默认字段顺序（按标准 ASS 来，SSA 的 Marked 只是第一列的名字）
_默认事件字段 = ("layer", "start", "end", "style", "name",
                "marginl", "marginr", "marginv", "effect", "text")


def _时间秒(文本: str) -> Optional[float]:
    """``HH:MM:SS,mmm`` / ``H:MM:SS.cc`` / ``MM:SS`` → 秒。

    小数位不分支判断：``float("0." + 小数)`` 对 3 位（SRT 毫秒）和 2 位（ASS 厘秒）
    都天然正确，写成分支反而容易把厘秒当毫秒（差 10 倍，肉眼一慢一快很难查）。
    """
    匹配 = _时间格式.match((文本 or "").strip())
    if not 匹配:
        return None
    时, 分, 秒, 小数 = 匹配.groups()
    值 = int(分) * 60 + int(秒)
    if 时:
        值 += int(时) * 3600
    if 小数:
        值 += float("0." + 小数)
    return float(值)


def _整数(文本: str) -> Optional[int]:
    """从标签参数里抠出第一个整数（``"20"`` / ``"(320,240)"`` / ``"700"`` 都能用）。"""
    匹配 = re.search(r"-?\d+", 文本 or "")
    return int(匹配.group(0)) if 匹配 else None


def _开关(文本: str) -> bool:
    """ASS 的布尔值：``0/1``、``-1``（样式表里的"真"）、字重 ``100..900`` 都算。"""
    数值 = _整数(文本)
    if 数值 is None:
        return False
    if 数值 < 0:                    # 样式表里 Bold: -1 表示打开
        return True
    if 数值 <= 1:
        return bool(数值)
    return 数值 >= 600              # 字重写法：\b700


def _位置(对齐: int) -> str:
    """ASS 的 ``\\an``/Alignment 是 1..9（小键盘布局）→ 我们的三档位置。"""
    if 对齐 >= 7:
        return 位置顶部
    if 对齐 >= 4:
        return 位置中间
    return 位置底部


def _ASS颜色(文本: str) -> Optional[str]:
    """``&HBBGGRR`` / ``&HAABBGGRR`` → ``#RRGGBB``（注意是 **BGR** 顺序）。"""
    匹配 = _ASS颜色模式.search(文本 or "")
    if not 匹配:
        return None
    数字 = 匹配.group(1)[-6:].rjust(6, "0")     # 只取后 6 位：前两位是透明度（忽略）
    b, g, r = int(数字[0:2], 16), int(数字[2:4], 16), int(数字[4:6], 16)
    return f"#{r:02X}{g:02X}{b:02X}"


# ---------------------------------------------------------------------------
# 样式标签（SRT 的 {\an8} 与 ASS 的 {\fs20\c&H...&} 共用同一套）
# ---------------------------------------------------------------------------

def _解析样式标签(文本: str, 脚本宽: float = 0.0, 脚本高: float = 0.0,
                 处理转义: bool = True) -> tuple:
    """剥掉样式标签，返回 ``(纯文本, 样式)``；样式里只放**真的设了**的键。

    ``脚本宽/高`` 是 ASS 的 ``PlayResX/PlayResY``：``\\pos`` 给的是脚本像素绝对坐标，
    而 :func:`~wangpan.subtitle.绘制.画字幕` 手里只有一张图、拿不到脚本分辨率，
    所以这里就把坐标**归一化成比例**，画的时候乘画面尺寸即可。
    """
    样式: dict = {}
    if 处理转义:
        # ASS 的换行/空格转义。注意 \\N（硬换行）和 \\n（软换行）只差大小写，别写反
        文本 = (文本 or "").replace("\\N", "\n").replace("\\n", " ").replace("\\h", " ")

    def 应用(名字: str, 参数: str) -> None:
        nonlocal 绘图
        if 名字 == "p":                             # \p1 进入绘图模式，\p0 退出
            值 = _整数(参数)
            绘图 = bool(值 and 值 > 0)
        elif 名字 == "an":
            值 = _整数(参数)
            if 值 and 1 <= 值 <= 9:
                样式["对齐"] = 值
                样式["位置"] = _位置(值)
        elif 名字 == "fs":
            if 参数[:1] in "+-":
                return          # \fs+2 是"相对默认字号"，这里算不出基准，宁可不改（别当成 2）
            值 = _整数(参数)
            if 值 and 值 > 0:
                样式["字号"] = 值
                if 脚本高 > 0:
                    样式["字号比例"] = 值 / 脚本高
        elif 名字 in ("c", "1c"):                   # \c / \1c = 主色（\2c\3c\4c 是次色/描边/阴影，忽略）
            颜色 = _ASS颜色(参数)
            if 颜色:
                样式["颜色"] = 颜色
        elif 名字 == "b":
            样式["粗体"] = _开关(参数)
        elif 名字 == "i":
            样式["斜体"] = _开关(参数)
        elif 名字 == "pos":
            数字 = re.findall(r"-?\d+(?:\.\d+)?", 参数 or "")
            if len(数字) >= 2 and 脚本宽 > 0 and 脚本高 > 0:
                样式["坐标比例"] = (float(数字[0]) / 脚本宽, float(数字[1]) / 脚本高)

    段们: list = []
    位置指针 = 0
    绘图 = False                    # \p1 之后、\p0 之前是矢量绘图命令，不能当文本
    for 匹配 in _花括号块.finditer(文本):
        片段 = 文本[位置指针:匹配.start()]
        if not 绘图:
            段们.append(片段)
        位置指针 = 匹配.end()
        有标签 = False
        for 标签 in _覆盖标签.finditer(匹配.group(1)):
            有标签 = True
            应用(标签.group(1).lower(), 标签.group(2))
        if not 有标签 and not 绘图:
            段们.append(匹配.group(0))      # 不是覆盖块（如 {副歌}）→ 当纯文本留着
    末尾 = 文本[位置指针:]
    if not 绘图:
        段们.append(末尾)

    纯文本 = _去尖括号标签("".join(段们), 样式)
    return 纯文本.strip(), 样式


def _去尖括号标签(文本: str, 样式: dict) -> str:
    """把 ``<i>`` / ``<b>`` / ``<font color=...>`` 收进样式并从文本里删掉。

    只看**开**标签（``</b>`` 不改样式）：我们不做逐字样式，"整条"是最小单位 ——
    ``<b>重点</b>其余`` 里只有"重点"该粗，但整条变粗总比整条丢掉加粗效果强。
    """
    for 匹配 in _尖括号标签.finditer(文本):
        小写 = 匹配.group(0).lower()
        if 小写.startswith("<i"):
            样式["斜体"] = True
        elif 小写.startswith("<b"):
            样式["粗体"] = True
        颜色 = _尖括号颜色.search(匹配.group(0))
        if 颜色:
            样式["颜色"] = 颜色.group(1).upper()
    return _尖括号标签.sub("", 文本)


# ---------------------------------------------------------------------------
# 编码与拆行
# ---------------------------------------------------------------------------

def _解码字节(字节: bytes, 编码尝试: tuple = 默认编码尝试) -> str:
    """按候选编码逐个试。

    UTF-16 有 BOM 就先认它：GB18030 几乎不挑字节，UTF-16 文件被它"解成功"的结果是
    满屏乱码而不报错。全都失败时用 ``errors="replace"`` 兜底 —— 字幕读不出来只该
    少一条字幕，不该把整个播放按死。
    """
    顺序 = 编码尝试
    if 字节[:2] in (b"\xff\xfe", b"\xfe\xff"):
        顺序 = ("utf-16",) + tuple(编码 for 编码 in 编码尝试 if 编码 != "utf-16")
    for 编码 in 顺序:
        try:
            return 字节.decode(编码)
        except (UnicodeDecodeError, LookupError):
            continue
    return 字节.decode("utf-8", errors="replace")


def _拆行(文本: Union[str, bytes], 编码尝试: tuple = 默认编码尝试) -> list:
    """统一换行、去 BOM，切成行列表。

    接受 bytes 是为了让 :func:`读字幕文件` 直接把文件内容丢进来、由这里统一试编码，
    不用在两处各写一遍解码逻辑。
    """
    if isinstance(文本, (bytes, bytearray)):
        文本 = _解码字节(bytes(文本), 编码尝试)
    if 文本.startswith("\ufeff"):       # 调用方自己用 utf-8（非 sig）解出来的 BOM
        文本 = 文本[1:]
    return 文本.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _键值化(内容: str, 字段们: list) -> dict:
    """``a,b,c`` + 字段名 → 字典；字段比实际列多时补空串，少时截断。"""
    部分 = 内容.split(",", max(0, len(字段们) - 1))     # Text 里的逗号留在最后一个字段里
    return {名: (部分[i].strip() if i < len(部分) else "") for i, 名 in enumerate(字段们)}


# ---------------------------------------------------------------------------
# SRT / VTT
# ---------------------------------------------------------------------------

def 解析SRT(文本: Union[str, bytes], 编码尝试: tuple = 默认编码尝试) -> list:
    """SRT（以及语法兼容的 VTT）→ 条目列表，**按文件里的顺序**返回。

    不按序号行分块，而是"见到时间行就开一条，往下收文本直到空行/下一个时间行/下一条的
    序号行"。这样乱序序号、缺序号、块间少了空行的畸形文件都能吃下（见模块 docstring 第 4 条）。
    """
    行们 = _拆行(文本, 编码尝试)
    结果: list = []
    索引 = 0
    while 索引 < len(行们):
        匹配 = _时间行.match(行们[索引])
        if not 匹配:
            索引 += 1
            continue
        起始, 结束 = _时间秒(匹配.group(1)), _时间秒(匹配.group(2))
        索引 += 1
        文本行们: list = []
        while 索引 < len(行们):
            行 = 行们[索引]
            if not 行.strip():
                break                                   # 空行 = 本条结束
            if _时间行.match(行):
                break                                   # 有的文件块之间不空行
            if _序号行.match(行) and 索引 + 1 < len(行们) and _时间行.match(行们[索引 + 1]):
                break                                   # 这是下一条的序号行，别算进正文
            文本行们.append(行.strip())
            索引 += 1
        原始 = "\n".join(文本行们)
        纯文本, 样式 = _解析样式标签(原始, 处理转义=False)
        if 起始 is not None and 结束 is not None:
            结果.append(字幕条目(起始, 结束, 纯文本, 补全样式(样式), 原始))
        索引 += 1                                       # 跳过那个空行
    return 结果


# ---------------------------------------------------------------------------
# ASS / SSA
# ---------------------------------------------------------------------------

def 解析ASS(文本: Union[str, bytes], 编码尝试: tuple = 默认编码尝试) -> list:
    """ASS / SSA → 条目列表。

    两件事必须做对（其余都是装饰）：
    * 按 ``[Events]`` 段 ``Format:`` 行的字段顺序取值（见模块 docstring 第 1 条）；
    * 继承 ``[V4+ Styles]`` 里的样式 —— 字号/颜色/粗斜体/对齐普遍写在 Style 行上，
      不继承的话整条轨道只能吃"默认字号"，看起来就像"字幕没生效"。
    行内 ``{\\...}`` 覆盖样式表（标准行为：行内优先）。
    """
    行们 = _拆行(文本, 编码尝试)
    脚本宽, 脚本高 = _读脚本分辨率(行们)
    样式表 = _读样式表(行们, 脚本高)
    事件字段 = list(_默认事件字段)
    在事件段 = False
    结果: list = []
    for 行 in 行们:
        去空 = 行.strip()
        if not 去空:
            continue
        if 去空.startswith("[") and 去空.endswith("]"):
            在事件段 = 去空.lower() == "[events]"
            continue
        if not 在事件段:
            continue
        小写 = 去空.lower()
        if 小写.startswith("format:"):
            # ★ 字段顺序以这一行为准：不同工具写出来的顺序不一样，硬编码下标必错
            事件字段 = [名.strip().lower() for 名 in 去空.split(":", 1)[1].split(",")]
            continue
        if not 小写.startswith("dialogue:"):
            continue            # Comment: 行、Picture: 行都不是字幕，直接跳过
        值 = _键值化(去空.split(":", 1)[1], 事件字段)
        起始, 结束 = _时间秒(值.get("start", "")), _时间秒(值.get("end", ""))
        原文 = 值.get("text", "")
        if 起始 is None or 结束 is None or not 原文.strip():
            continue
        样式 = dict(样式表.get(值.get("style", "").strip(), {}))     # 先吃样式表的默认
        纯文本, 覆盖 = _解析样式标签(原文, 脚本宽, 脚本高)
        样式.update(覆盖)                                            # 行内覆盖优先
        结果.append(字幕条目(起始, 结束, 纯文本, 补全样式(样式), 去空))
    return 结果


def _读脚本分辨率(行们: list) -> tuple:
    """从 ``[Script Info]`` 读 ``PlayResX/PlayResY``（``\\pos`` 与字号换算都要它）。

    缺了就按惯例的 384x288 补：不补的话字号比例/坐标比例都算不出来，
    表现就是"有的字幕文件字号全错"，而这类文件恰好还不少（手写的 ASS 常省略这两项）。
    """
    宽, 高 = 0.0, 0.0
    for 行 in 行们:
        去空 = 行.strip()
        if 去空.startswith("[") and 去空.endswith("]") and 去空.lower() != "[script info]":
            break               # 这两项只在 [Script Info] 段里，出了这段就不用翻了
        小写 = 去空.lower()
        if 小写.startswith("playresx:"):
            宽 = float(_整数(去空.split(":", 1)[1]) or 0)
        elif 小写.startswith("playresy:"):
            高 = float(_整数(去空.split(":", 1)[1]) or 0)
    if 高 <= 0:
        高 = 默认脚本高
    if 宽 <= 0:
        宽 = 默认脚本宽 if 高 == 默认脚本高 else 高 * 默认脚本宽 / 默认脚本高
    return 宽, 高


def _读样式表(行们: list, 脚本高: float) -> dict:
    """``[V4 Styles]`` / ``[V4+ Styles]`` → ``{样式名: 样式}``。

    同样按 ``Format:`` 行的字段顺序取（跟 Events 段一个道理：顺序不固定）。
    只挑我们用得上的字段（字号/主色/粗体/斜体/对齐），其余（边距、缩放、字体名）
    留给以后需要时再加。
    """
    表: dict = {}
    字段们: list = []
    在样式段 = False
    for 行 in 行们:
        去空 = 行.strip()
        if not 去空:
            continue
        if 去空.startswith("[") and 去空.endswith("]"):
            在样式段 = "style" in 去空.lower()
            字段们 = []
            continue
        if not 在样式段:
            continue
        小写 = 去空.lower()
        if 小写.startswith("format:"):
            字段们 = [名.strip().lower() for 名 in 去空.split(":", 1)[1].split(",")]
            continue
        if not 小写.startswith("style:") or not 字段们:
            continue
        值 = _键值化(去空.split(":", 1)[1], 字段们)
        名字 = 值.get("name", "")
        if not 名字:
            continue
        样式: dict = {}
        字号 = _整数(值.get("fontsize", ""))
        if 字号 and 字号 > 0:
            样式["字号"] = 字号
            if 脚本高 > 0:
                样式["字号比例"] = 字号 / 脚本高
        颜色 = _ASS颜色(值.get("primarycolour", ""))
        if 颜色:
            样式["颜色"] = 颜色
        for 键, 字段名 in (("粗体", "bold"), ("斜体", "italic")):
            if 值.get(字段名, "") != "":
                样式[键] = _开关(值[字段名])
        对齐 = _整数(值.get("alignment", ""))
        if 对齐 and 1 <= 对齐 <= 9:
            样式["对齐"] = 对齐
            样式["位置"] = _位置(对齐)
        表[名字] = 样式
    return 表


# ---------------------------------------------------------------------------
# 文件与"同名找字幕"
# ---------------------------------------------------------------------------

def 读字幕文件(路径) -> 字幕轨道:
    """按扩展名选解析器读一个字幕文件。

    ``.vtt`` 走 SRT 解析器（时间行语法兼容，只是小数点）；
    ``.ssa`` 走 ASS 解析器（Events 段是同一套）；
    认不出的扩展名也按 SRT 试一把 —— 总比直接报"不支持"让用户没字幕看好。
    """
    路径 = Path(路径)
    字节 = 路径.read_bytes()
    后缀 = 路径.suffix.lower()
    if 后缀 in (".ass", ".ssa"):
        条目们 = 解析ASS(字节)
    else:
        条目们 = 解析SRT(字节)
    return 字幕轨道(名字=路径.name, 条目们=条目们, 来源=后缀.lstrip(".") or "srt")


def 找同名字幕(媒体路径) -> list:
    """媒体文件旁边"同名"的字幕：``影片.mp4`` → ``影片.srt`` / ``影片.zh.srt`` / ``影片.chs.ass``。

    判定规则（宁可少认，也别把别的片子的字幕配上来 —— 时间轴对不上的字幕比没字幕更烦）：
    * 后缀必须是字幕后缀；
    * 文件名主干 == 媒体主干，或以 ``媒体主干 + "."`` 开头（``影片.zh``）；
      ``影片2.srt`` / ``预告片.srt`` 都不算：少一个点、多一个字都不是同一部片子；
    * 跳过媒体自己（有人会把 .srt 当"媒体"传进来）。
    顺序：精确同名的在前，其余按文件名排 —— 调用方拿 ``[0]`` 当默认轨时结果必须稳定，
    否则同一部片子每次打开选中的字幕轨都可能不一样。
    """
    媒体路径 = Path(媒体路径)
    目录 = 媒体路径.parent
    if not 目录.is_dir():
        return []
    主干 = 媒体路径.stem
    候选: list = []
    for 文件 in 目录.iterdir():
        if not 文件.is_file() or 文件.name == 媒体路径.name:
            continue
        if 文件.suffix.lower() not in 字幕后缀们:
            continue
        if 文件.stem == 主干 or 文件.stem.startswith(主干 + "."):
            候选.append(文件)
    候选.sort(key=lambda 路径: (路径.stem != 主干, 路径.name))
    轨道们: list = []
    for 文件 in 候选:
        try:
            轨道们.append(读字幕文件(文件))
        except OSError:
            continue        # 读不出来的当没有：一个字幕文件不该把播放按死
    return 轨道们
