"""本地弹幕文件源：B 站 XML、我们自己的 JSON、同名自动发现。

两种公开文件格式
================

**B 站 XML**（公开格式，自己解析）::

    <i>
      <chatserver>chat.bilibili.com</chatserver>
      <d p="时间秒,模式,字号,颜色十进制,时间戳,池,发送者,行ID">文本</d>
    </i>

* 模式 ``1=滚动 4=底部 5=顶部``，**其它值（6 逆向、7 高级、8 代码）一律丢弃** ——
  它们要么只在 B 站播放器里有意义，要么是脚本，我们排不了版；
* 颜色就是十进制 ``R*65536+G*256+B``，和我们的 0xRRGGBB 完全一致，不用转；
* ``p`` **字段数不足 8 时尽力解析**：时间必须有（没有时间就没法显示），模式缺了按
  滚动，字号缺了按 25，颜色缺了按白色，发送者/行ID 缺了就是空串。理由：老工具
  导出的 XML 经常只写前 4 项，为了"格式完整"把整份文件丢掉太亏。

**我们自己的 JSON**（导出的格式，也是缓存/分享用的格式）::

    [{"毫秒": 12345, "文本": "…", "模式": "滚动", "颜色": 16777215,
      "发送者": "…", "字号": 25}, …]

为什么不引 ``xml`` 模块
=======================
V2 的依赖白名单（``tests/test_独立性.py``）里没有 ``xml``，而且这个格式简单到不值得
为它开口子：``<d p="…">…</d>`` 是唯一的条目形状。手写正则有代价 —— **属性值里不能有
``>``**、CDATA 不认 —— 这两点在 B 站 XML 里都不会出现（``p`` 只有数字和逗号）。
解析时会做 XML 实体反转义（``&amp;``/``&lt;``/``&#39;``…），而且是**单趟**替换：
``&amp;lt;`` 应该还原成字面量 ``&lt;`` 而不是 ``<``，先换 ``&amp;`` 就会换错。

编码
====
XML 有 UTF-8（带/不带 BOM）、GB18030、UTF-16 都可能（不同下载工具导出）。UTF-16 无 BOM
时 GB18030 几乎"什么都能解"（不报错、只是乱码），所以**先看 BOM**，再按
UTF-8 → GB18030 顺序试 —— 顺序反了不会报错，只会得到满屏乱码，这种 bug 最难发现。
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import replace
from pathlib import Path
from typing import Optional

from ..模型 import 弹幕, 弹幕池, 弹幕模式, 来源标识
from .接口 import 弹幕源, 弹幕源错误, 匹配结果, 素材信息, 取浮点, 取整数

__all__ = [
    "本地源", "解析B站XML", "解析我们的JSON", "导出为JSON", "找同名弹幕文件",
    "读文本文件", "B站模式号", "B站后缀", "我们的后缀", "默认字号", "编码尝试",
]

日志 = logging.getLogger(__name__)

#: B 站的模式号 → 我们的三种模式（**不在表里的全丢**）
B站模式号 = {1: 弹幕模式.滚动, 4: 弹幕模式.底部, 5: 弹幕模式.顶部}
#: B 站 XML 的后缀
B站后缀 = (".xml",)
#: 我们自己的 JSON 后缀（``影片.json`` 与 ``影片.danmaku.json`` 都认）
我们的后缀 = (".json",)
#: 缺字号时的默认（与 :class:`~wangpan.danmaku.模型.弹幕` 的默认值保持一致）
默认字号 = 25
#: 读文本时的编码尝试顺序（详见模块 docstring；顺序错了只会静默乱码）
编码尝试 = ("utf-8-sig", "utf-8", "gb18030", "utf-16")

#: 编码探测用的 BOM → 编码
_BOM表 = ((b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16"),
         (b"\xef\xbb\xbf", "utf-8-sig"))

# ---------------------------------------------------------------------------
# XML：手写解析（见模块 docstring 里的取舍）
# ---------------------------------------------------------------------------

#: 一条弹幕的标签：``<d 属性们>文本</d>`` 或自闭合 ``<d 属性们/>``
_标签模式 = re.compile(r"<d\b([^>]*?)(?:/>|>(.*?)</d>)", re.S | re.I)
#: 标签里的属性（双引号/单引号都认）
_属性模式 = re.compile(r"""([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
#: XML 实体（命名 + 十进制 + 十六进制）
_实体模式 = re.compile(r"&(?:#(\d+)|#[xX]([0-9A-Fa-f]+)|([A-Za-z][A-Za-z0-9]*));")
_命名实体 = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'",
           "nbsp": "\u00a0"}


def 反转义(文本: str) -> str:
    """XML 实体反转义（**单趟**扫描，见模块 docstring 里 ``&amp;lt;`` 的坑）。"""

    def _换(匹配: "re.Match") -> str:
        十进, 十六, 名 = 匹配.groups()
        try:
            if 十进:
                return chr(int(十进))
            if 十六:
                return chr(int(十六, 16))
        except (ValueError, OverflowError):
            return 匹配.group()          # 越界码点：原样留着，别把整份文件搞崩
        return _命名实体.get(str(名).lower(), 匹配.group())

    return _实体模式.sub(_换, str(文本 or ""))


def _从B站条目(属性: dict, 文本: str) -> Optional[弹幕]:
    """解析一条 ``<d p="…">…</d>``；不合规返回 None（丢弃）。"""
    段 = [项.strip() for 项 in str(属性.get("p") or "").split(",")]
    if not 段 or not 段[0]:
        return None
    秒 = 取浮点(段[0])
    if 秒 is None:                       # 时间解析不了 → 整条丢弃
        return None
    模式 = 弹幕模式.滚动                  # 只有时间时按滚动（"尽力解析"）
    if len(段) >= 2:
        模式 = B站模式号.get(取整数(段[1], -1))
        if 模式 is None:                 # 模式不认识的（6/7/8…）→ 丢弃
            return None
    字号 = 默认字号
    if len(段) >= 3:
        字号 = 取整数(段[2], 默认字号) or 默认字号
    颜色 = 0xFFFFFF
    if len(段) >= 4:
        颜色 = 取整数(段[3])
        if 颜色 is None or not (0 <= 颜色 <= 0xFFFFFF):
            return None                  # 颜色不合法 → 丢弃（别拿黑白去蒙）
    return 弹幕(毫秒=int(round(秒 * 1000)), 文本=str(文本 or ""), 模式=模式, 颜色=颜色,
              字号=int(字号), 发送者=段[6] if len(段) >= 7 else "",
              来源=来源标识.本地, 标识=段[7] if len(段) >= 8 else "",
              池=段[5] if len(段) >= 6 else "")


def 解析B站XML(文本: str) -> list[弹幕]:
    """B 站 XML → 弹幕列表（非法条目直接丢掉，不抛异常：一份文件里坏几条很常见）。"""
    条目们: list[弹幕] = []
    for 匹配 in _标签模式.finditer(str(文本 or "")):
        属性 = {}
        for 一个 in _属性模式.finditer(匹配.group(1) or ""):
            属性[一个.group(1).lower()] = 一个.group(2) if 一个.group(2) is not None \
                else 一个.group(3)
        正文 = 反转义(匹配.group(2) or "")
        if not 正文:
            continue                     # 空文本（占位/已删弹幕）没有意义
        条 = _从B站条目(属性, 正文)
        if 条 is not None:
            条目们.append(条)
    return 条目们


# ---------------------------------------------------------------------------
# 我们自己的 JSON
# ---------------------------------------------------------------------------

def _取模式(值) -> 弹幕模式:
    """模式字段容错：``"滚动"``/``"2"``/``2``/``None`` 都能落到三种模式之一。"""
    if isinstance(值, 弹幕模式):
        return 值
    if isinstance(值, str):
        文本 = 值.strip()
        for 模式 in 弹幕模式:
            if 文本 == 模式.value or 文本 == 模式.name:
                return 模式
        数字 = 取整数(文本)
    else:
        数字 = 取整数(值)
    return B站模式号.get(数字 if 数字 is not None else -1, 弹幕模式.滚动)


def _取来源(值, 默认: 来源标识 = 来源标识.本地) -> 来源标识:
    if isinstance(值, 来源标识):
        return 值
    文本 = str(值 or "").strip()
    for 一个 in 来源标识:
        if 文本 in (一个.value, 一个.name):
            return 一个
    return 默认


def _从字典(项: dict) -> Optional[弹幕]:
    """一条 JSON 记录 → 弹幕；**没有时间**就丢掉（时间都没有没法显示）。"""
    毫秒 = 取整数(项.get("毫秒"))
    if 毫秒 is None:
        秒 = 取浮点(项.get("秒"))       # 手写的文件可能只写了秒
        if 秒 is None:
            return None
        毫秒 = int(round(秒 * 1000))
    颜色 = 取整数(项.get("颜色"), 0xFFFFFF)
    if 颜色 is None:
        颜色 = 0xFFFFFF
    return 弹幕(毫秒=int(毫秒), 文本=str(项.get("文本") or ""),
              模式=_取模式(项.get("模式")),
              # ⚠️ 别写成 `取整数(...) or 0xFFFFFF`：黑色是 0，会被 falsy 判成白色
              颜色=int(颜色) & 0xFFFFFF,
              字号=int(取整数(项.get("字号"), 默认字号) or 默认字号),
              发送者=str(项.get("发送者") or ""),
              来源=_取来源(项.get("来源")),
              标识=str(项.get("标识") or ""), 池=str(项.get("池") or ""))


def 解析我们的JSON(文本: str) -> list[弹幕]:
    """我们导出的 JSON → 弹幕列表。坏 JSON **抛错**（调用方给了个文件，得让人知道）。"""
    try:
        数据 = json.loads(str(文本 or ""))
    except ValueError as 错:
        raise 弹幕源错误(f"弹幕 JSON 不合法：{错}") from 错
    if isinstance(数据, dict):
        for 键 in ("弹幕", "弹幕们", "comments"):
            if isinstance(数据.get(键), list):
                数据 = 数据[键]
                break
        else:
            数据 = []
    if not isinstance(数据, list):
        raise 弹幕源错误("弹幕 JSON 的顶层应该是数组")
    条目们: list[弹幕] = []
    for 项 in 数据:
        if not isinstance(项, dict):
            continue
        条 = _从字典(项)
        if 条 is not None:
            条目们.append(条)
    return 条目们


def 导出为JSON(池: 弹幕池, 带元信息: bool = False) -> str:
    """弹幕池 → JSON 文本（导出/分享用）。

    ``带元信息=False``（默认）只写文档约定的 6 个字段：``毫秒/文本/模式/颜色/发送者/字号``
    —— 这是"我们的 JSON 格式"的定义，多写字段就让别人对不上了。
    ``带元信息=True`` 额外写 ``来源/标识/池``，这时导入回来能保住来源与源内 id
    （默认不写：这两样会暴露"从哪一集拿的"，分享出去的备份没必要带）。
    """
    条目们 = []
    for 条 in 池:
        项 = {"毫秒": int(条.毫秒), "文本": 条.文本, "模式": 条.模式.value,
             "颜色": int(条.颜色), "发送者": 条.发送者, "字号": int(条.字号)}
        if 带元信息:
            项.update({"来源": 条.来源.value, "标识": 条.标识, "池": 条.池})
        条目们.append(项)
    return json.dumps(条目们, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 同名自动发现 / 读文件
# ---------------------------------------------------------------------------

def 找同名弹幕文件(视频路径) -> list[Path]:
    """在视频旁边找同名弹幕：``影片.mp4`` → ``影片.xml`` / ``影片.json`` /
    ``影片.danmaku.json``（按这个顺序返回存在的那些）。

    "同名"就是**去掉视频扩展名后的完整路径**再拼后缀 —— 别用 ``glob("影片*")``：
    那会把 ``影片 预告.xml`` 这种不相关的文件也捞进来。
    """
    路径 = Path(视频路径)
    基 = 路径.with_suffix("") if 路径.suffix else 路径
    候选 = [基.with_suffix(".xml"), 基.with_suffix(".json"),
          基.parent / (基.name + ".danmaku.json")]
    找到: list[Path] = []
    for 一个 in 候选:
        if 一个.is_file() and 一个 not in 找到:
            找到.append(一个)
    return 找到


def 读文本文件(路径) -> str:
    """按 BOM/候选编码读文本（顺序的理由见模块 docstring）。"""
    原始 = Path(路径).read_bytes()
    for 头, 编码 in _BOM表:
        if 原始.startswith(头):
            return 原始.decode(编码, errors="replace")
    for 编码 in 编码尝试:
        try:
            return 原始.decode(编码)
        except UnicodeDecodeError:
            continue
    # 全失败（半截文件/混编码）：留个能看个大概的结果，别让播放起不来
    return 原始.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 源
# ---------------------------------------------------------------------------

class 本地源(弹幕源):
    """本地弹幕文件源：**不支持"按文件找节目"**（它的"匹配"就是路径本身）。

    ``取弹幕(标识)`` 的 ``标识`` 既可以是弹幕文件本身，也可以是视频路径 ——
    后者会走 :func:`找同名弹幕文件` 自动发现（多个文件合并去重：同一集同时有
    B 站 XML 和我们导出的 JSON 很常见）。
    """

    标识 = "local"
    名字 = "本地文件"

    def __init__(self, 自动发现: bool = True, 缓存: bool = True) -> None:
        self.自动发现 = bool(自动发现)
        self.启用缓存 = bool(缓存)
        self._锁 = threading.Lock()
        #: (路径, 修改时间, 大小) → 池；文件被改了自然失效，不用手动清
        self._缓存: dict[tuple, 弹幕池] = {}
        #: 诊断用：真的读了几次文件
        self.读取次数 = 0

    def 能匹配(self) -> bool:
        """本地源不能"按文件找节目"：给个文件就取它的弹幕，没有"候选"这回事。"""
        return False

    def 匹配(self, 素材: 素材信息) -> list[匹配结果]:  # noqa: ARG002 - 接口约定
        return []

    # ---- 读 ----

    def _读一个(self, 路径: Path) -> 弹幕池:
        键 = None
        try:
            状态 = 路径.stat()
            键 = (str(路径), 状态.st_mtime_ns, 状态.st_size)
        except OSError:
            键 = None
        if self.启用缓存 and 键 is not None:
            with self._锁:
                已有 = self._缓存.get(键)
            if 已有 is not None:
                return 已有
        文本 = 读文本文件(路径)
        if 路径.suffix.lower() in B站后缀:
            条目们 = 解析B站XML(文本)
            说明 = f"B站XML {路径.name}"
        elif 路径.suffix.lower() in 我们的后缀:
            条目们 = 解析我们的JSON(文本)
            说明 = f"JSON {路径.name}"
        else:
            raise 弹幕源错误(f"不认识的弹幕文件类型：{路径.name}")
        self.读取次数 += 1
        池 = 弹幕池([replace(条, 来源=来源标识.本地) for 条 in 条目们],
                  来源说明=f"{说明} {len(条目们)} 条")
        if self.启用缓存 and 键 is not None:
            with self._锁:
                self._缓存[键] = 池
        return 池

    def 取弹幕(self, 标识: str) -> 弹幕池:
        文本 = str(标识 or "").strip()
        if not 文本:
            raise 弹幕源错误("没有给本地弹幕文件路径")
        路径 = Path(文本)
        if 路径.is_file():
            文件们 = [路径]
        elif self.自动发现:
            文件们 = 找同名弹幕文件(路径)
        else:
            文件们 = []
        if not 文件们:
            raise 弹幕源错误(f"找不到弹幕文件：{路径}"
                           "（同名的 .xml/.json/.danmaku.json 都没有）")
        池们: list[弹幕池] = []
        坏的: list[str] = []
        for 一个 in 文件们:
            try:
                池们.append(self._读一个(一个))
            except (弹幕源错误, OSError) as 错:
                # 一个文件坏了不该让另一份能用的也取不到
                坏的.append(f"{一个.name}：{错}")
                日志.warning("本地弹幕读不了，跳过：%s", 坏的[-1])
        if not 池们:
            raise 弹幕源错误("；".join(坏的) or f"读不出弹幕：{路径}")
        合并 = 弹幕池.合并(池们, 去重=True)
        合并.来源说明 = (f"本地 {len(文件们)} 个文件 {len(合并)} 条"
                     + (f"（跳过 {len(坏的)} 个坏文件）" if 坏的 else ""))
        return 合并

    def 关闭(self) -> None:
        with self._锁:
            self._缓存.clear()
