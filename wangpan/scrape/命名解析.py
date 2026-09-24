"""命名解析：把"文件名 + 目录名"翻译成"这是什么"（V2 从零实现，不抄任何项目）。

规则照 **Jellyfin 官方命名规范**写（Emby/Kodi/Plex 也认，取它们的最大公约数）：

* 电影：``影片名 (年份)/影片名 (年份).mkv``；可选 ID 标签 ``[imdbid-tt1234567]`` /
  ``[tmdbid-123]`` / ``[tvdbid-123]``（``[tmdbid=123]`` 这种等号写法也认）；
* 剧集：``剧名 (年份)/Season 01/剧名 S01E01.mkv``；``Season 00``/``Specials``/``Extras``
  是特别篇；``S01E01-E02`` 是一文件多集；``cd1/dvd2/part1/pt1/disc1/disk1`` 是多段；
* 番外目录：behind the scenes / deleted scenes / interviews / featurettes / trailers …
  番外后缀：``-trailer`` / ``.sample`` / ``_behindthescenes`` …
* 外挂字幕/音轨标记：``.default.`` / ``.forced.`` / ``.sdh.`` / 语言码（``zh``/``en``/``ja``…）；
* 3D：``3D`` 与 ``hsbs/fsbs/htab/ftab/mvc`` 组合，大小写不敏感。

为什么解析要单独一个模块：扫描器只认"事实"（路径 → 结构），刮削器才去猜"这是哪部片"。
两者混在一起时，命名规则一改就要同时动扫描与匹配两处代码。

**五个已知的坑（都写了回归测试钉住）**

1. 图片/字幕文件（``S01E01 Some Episode-thumb.jpg``）**不是一集** —— 只有视频扩展名才走
   "影片/剧集"判定，其余一律 ``类型="非视频"``；
2. 绝对集号兜底（``剧名 07.mkv``）必须**保守** —— 否则 ``3D.FTAB``、``cd1``、``1080p``、
   ``x264`` 里的数字都会被当成集号。这里要求"名字末尾/括号里的独立数字"，且预先摘掉
   多段与 3D 片段，且还要求"有季目录上下文 / 在括号里 / 带前导零"三者之一；
3. 片名里本来就可能带 ``3D``（``Awesome 3D Movie (2022).3D.FTAB.mp4``）—— **优先匹配带
   格式的 3D**，找不到才认孤立的 ``3D``；
4. "多版本"判定**不能把标题算进去**：``S01E01 - 1080p.mkv`` 去掉标签后标题是空的，
   拿标题比较会得出"不是同一集"的错结论 —— 只看 ``(季, 集)``；
5. 标题清理不干净时**不要退回原始文件名**：原始名带着版本标签，会让同一集的两个版本
   被判成两部片子。宁可为空（目录名兜底或交给人工确认）。

**"改名文件"（网盘的常态）另有两条兜底** —— ``146.SDR.8bit.2160p.60fps.DDP5.1.WEB-DL.H265.mp4``
这种名字里标题已经没了，只剩"数字 + 画质标签"：

1. **集号在最前面**：开头 1~4 位数字当集号（季默认 1，父目录写了季就用父目录的）。
   两条防线保证电影不被误判：年份（``2012.mp4``）与"数字 + 英文单词"的片名
   （``12 Angry Men``）不算集号，裸的四位数也不认（见 :func:`_摘开头集号`）；
2. **标题回退到清洗过的父目录名**：``仙逆.4K高码.SDR.60fps`` → ``仙逆``。
   除了标准发布标签，还要摘"画质补充词/语言字幕/更新状态"（SDR/高码/国语/更新至146集…），
   词表见 ``_标题噪声词``，只作用于标题、不污染"版本名"。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["解析结果", "解析", "解析季目录", "是同一集的不同版本", "提取发布标签",
           "猜剧集路径", "是视频文件", "转半角", "视频扩展名", "字幕扩展名",
           "音频扩展名", "图片扩展名"]


# ============================ 常量表 ============================

#: 视频扩展名（**只有这些**才走"影片/剧集"解析；坑 1）
视频扩展名 = frozenset({
    "mkv", "mp4", "avi", "mov", "wmv", "flv", "ts", "m2ts", "mts", "webm", "mpg",
    "mpeg", "mpe", "m4v", "3gp", "ogm", "ogv", "asf", "f4v", "m2v", "vob", "iso",
    "rmvb", "rm", "divx", "tp", "trp", "strm", "mxf",
})
#: 外挂字幕扩展名（解析结果里是"非视频"，但 季/集 仍然解析出来给字幕配对用）
字幕扩展名 = frozenset({"srt", "ass", "ssa", "sub", "idx", "sup", "vtt", "smi",
                   "sami", "ttml", "dfxp", "pgs", "mks"})
#: 音轨扩展名
音频扩展名 = frozenset({"mka", "flac", "mp3", "aac", "ac3", "eac3", "dts", "m4a",
                   "wav", "ogg", "opus", "thd", "ape", "wv", "truehd", "dtshd"})
#: 图片扩展名（thumb/poster/fanart 之类，绝不能被当成一集）
图片扩展名 = frozenset({"jpg", "jpeg", "png", "webp", "bmp", "gif", "tif", "tiff",
                   "avif", "heic", "jfif", "ico"})
#: 其余"认得出来的"扩展名（用来切主名；切不出来就整串当主名）
其它扩展名 = frozenset({"nfo", "txt", "md5", "sfv", "url", "torrent", "xml", "json",
                   "yml", "yaml", "log", "cue", "m3u", "m3u8", "playlist", "db"})

#: 多段（官方给的类型；分隔符可省，如 ``Movie-cd1`` / ``Movie.part1``）
_段 = re.compile(r"(?<![A-Za-z0-9])(?:cd|dvd|part|pt|disc|disk)[\s._-]?(\d{1,2})(?![0-9])", re.I)
#: 带格式的 3D（``3D.FTAB`` / ``FTAB`` / ``3d-hsbs``）；坑 3：先认它
_三维格式 = re.compile(r"(?<![A-Za-z0-9])(?:3d[\s._-]*)?(hsbs|fsbs|htab|ftab|mvc)(?![A-Za-z0-9])", re.I)
#: 孤立的 3D（被空格/``-``/``.``/``_`` 包住才算）
_三维裸 = re.compile(r"(?<![A-Za-z0-9])3d(?![A-Za-z0-9])", re.I)
#: 数据源 ID：[imdbid-tt123] / [tmdbid-123] / [tvdbid-123]，等号写法也认
_数据源ID = re.compile(r"[\[{]\s*(imdbid|tmdbid|tvdbid)\s*[-=:]\s*([^\]}\s]+)\s*[\]}]", re.I)
#: 季集：S01E01 / S01E01-E02 / S01E01E02 / S01E01-02（大小写不敏感，分隔符可省）
_季集 = re.compile(r"(?<![A-Za-z0-9])s(\d{1,2})[\s._-]*e(\d{1,3})"
                r"(?:[\s._-]*(?:-?e|-)(\d{1,3}))?(?![0-9])", re.I)
#: 1x01 / 01x01（``1920x1080`` 这类分辨率不会命中，见单测）
_叉集 = re.compile(r"(?<![A-Za-z0-9])(\d{1,2})[xX](\d{1,3})"
                r"(?:[\s._-]*(?:-?[xX]|-)(\d{1,3}))?(?![0-9])")
#: 第01集 / 第01话 / 第1回
_中文集 = re.compile(r"第\s*(\d{1,4})\s*[集话話回]")
#: 第2季 / 第二季（中文数字：网盘目录里"第二季"比"第2季"还常见）——只看**目录名**时用
_中文季标识 = re.compile(r"第\s*([一二两三四五六七八九十]{1,3})\s*季")
#: 中文数字 → 阿拉伯数字（季号用；只到"九十九"够用了）
_季中文数字 = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}
#: 第2季 / Season 2 / S02（只在文件名里当"季标识"，S02E01 走 _季集）
_季标识 = re.compile(r"(?:第\s*(\d{1,2})\s*季|season[\s._-]*(\d{1,2})(?![\s._-]*e\d)|(?<![A-Za-z0-9])s(\d{1,2})(?![\s._-]*e\d))", re.I)
#: 日期式命名（日更剧）：2021-05-01
_日期 = re.compile(r"(?<![0-9])((?:19|20)\d{2})[-._ ](\d{1,2})[-._ ](\d{1,2})(?![0-9])")
#: 括号里的年份（括号里的一定可信）
_括号年份 = re.compile(r"[\[(（【]\s*((?:19|20)\d{2})\s*[\])）】]")
#: 裸年份（1920x1080 这种分辨率要排除；未来年份不认，见 _摘年份）
_裸年份 = re.compile(r"(?<![0-9xX×])((?:19|20)\d{2})(?![0-9pPiI])(?![xX×]\d)")

#: "集号在最前面"的写法：``146.SDR.2160p…`` / ``05.mkv`` / ``12 - 标题.mp4``
#: （数字后面**紧跟**分隔符或直接结束 —— ``1080p``/``8bit``/``3D`` 里的数字后面跟的是字母，
#: 靠这一条一步挡掉，比"先摘标签再找数字"稳）
_开头集号 = re.compile(r"^(\d{1,4})(?=$|[\s._\-])")
#: ``12 Angry Men`` 这类"数字 + 英文单词"的片名：不带前导零时不当集号（见 _摘开头集号）
_开头数字_像片名 = re.compile(r"^\s+[A-Z][a-z]{2,}")

#: 外挂字幕/音轨的语言标记（ISO 639-1 常用码 + ISO 639-2/B 三字母码）
_语言码 = frozenset("""
aa ab af am ar as az ba be bg bn bo br bs ca co cs cy da de dz el en eo es et eu
fa fi fo fr fy ga gd gl gu ha he hi hr hu hy id is it iu ja ka kk kl km kn ko ks
ku ky la lb lo lt lv mg mi mk ml mn mr ms mt my nb ne nl nn no oc or pa pl ps pt
qu rm ro ru rw sa sd se si sk sl so sq sr su sv sw ta te tg th ti tk tl tn tr tt
uk ur uz vi wo xh yi yo zh zu
""".split())
_三字母语言码 = frozenset("""
eng chi zho jpn kor fre fra deu ger spa ita rus por tha vie ind msa may hin tur
pol nld dut swe nor dan fin cze ces hun gre ell heb ukr ron rum bul hrv srp slk
slo slv lit lav est per fas urd cat glg eus baq gle cym wel isl ice mkd sqi alb
bel aze kaz uzb tgl fil mal tam tel kan mar ben guj pan nep sin khm lao mya amh
swa zul afr lat epo
""".split())
#: 外挂字幕常用简称（简繁/双语），也当标记
_字幕简称 = frozenset({"chs", "cht", "sc", "tc", "gb", "big5", "简", "繁", "简繁",
                   "双语", "中字", "英字"})
#: 标记词（官方：default / forced|foreign / sdh|cc|hi）
_标记词 = frozenset({"default", "forced", "foreign", "sdh", "cc", "hi", "dub", "sub"})

#: 发布标签：清晰度（值 = 归一化写法）
_清晰度表 = {
    "2160p": "2160p", "1080p": "1080p", "1080i": "1080i", "720p": "720p",
    "576p": "576p", "540p": "540p", "480p": "480p", "480i": "480i", "360p": "360p",
    "4k": "2160p", "8k": "4320p", "uhd": "2160p", "fhd": "1080p", "hd": "HD", "sd": "SD",
}
#: 发布标签：来源（值 = 归一化写法）
_来源表 = {
    "bluray": "BluRay", "blu-ray": "BluRay", "bdrip": "BDRip", "brrip": "BDRip",
    "bdremux": "Remux", "remux": "Remux", "web-dl": "WEB-DL", "webdl": "WEB-DL",
    "web": "WEB-DL", "webrip": "WEBRip", "hdtvrip": "HDTV", "hdtv": "HDTV",
    "dvdrip": "DVDRip", "dvd": "DVD", "hdrip": "HDRip", "tvrip": "TVRip",
    "vhsrip": "VHS", "vhs": "VHS", "cam": "CAM", "ts": "TS", "tc": "TC", "r5": "R5",
}
#: 发布标签：编码（同族归一，便于"同片不同版本"归并：AVC ≡ H.264 ≡ x264）
_编码表 = {
    "x265": "x265", "h265": "x265", "h.265": "x265", "hevc": "x265",
    "x264": "x264", "h264": "x264", "h.264": "x264", "avc": "x264",
    "av1": "AV1", "xvid": "XviD", "divx": "DivX", "mpeg2": "MPEG-2",
    "mpeg-2": "MPEG-2", "vp9": "VP9", "vvc": "VVC", "h266": "VVC",
}
#: 发布标签：音轨
_音轨表 = {
    "dts-hd.ma": "DTS-HD MA", "dts-hd": "DTS-HD", "dtsx": "DTS:X", "dts-x": "DTS:X",
    "dts": "DTS", "truehd": "TrueHD", "atmos": "Atmos", "eac3": "EAC3", "ddp": "DD+",
    "dd+": "DD+", "ac3": "AC3", "aac": "AAC", "flac": "FLAC", "mp3": "MP3",
    "opus": "Opus", "lpcm": "PCM", "pcm": "PCM",
}
#: 发布标签：其它（进 提取发布标签()["其它"]，也会拼进 版本）
_其它标签表 = {
    "hdr10+": "HDR10+", "hdr10": "HDR10", "hdr": "HDR", "dolby.vision": "杜比视界",
    "dovi": "杜比视界", "dv": "杜比视界", "10bit": "10bit", "8bit": "8bit",
    "12bit": "12bit", "repack": "REPACK", "proper": "PROPER", "imax": "IMAX",
    "directors.cut": "导演剪辑版", "extended.cut": "加长版", "extended": "加长版",
    "uncut": "未删减", "unrated": "未分级", "remastered": "重制版",
    "theatrical": "剧场版", "criterion": "CC版", "导演剪辑版": "导演剪辑版",
    "加长版": "加长版", "重制版": "重制版", "未删减": "未删减",
    "amzn": "AMZN", "nf": "NF", "dsnp": "DSNP", "atvp": "ATVP", "hmax": "HMAX",
    "itunes": "iTunes", "双语": "双语", "中字": "中字", "简繁": "简繁",
}

#: 番外**目录**名（归一化后比对）。
#: 注意 ``specials``/``extras`` 不在这里 —— 官方把它们当"第 0 季（特别篇）"，
#: 所以归到 特别篇目录，扫描时会建出 Season 00 而不是一堆散装番外。
番外目录 = frozenset({"behind the scenes", "deleted scenes", "interviews", "scenes",
                  "samples", "shorts", "featurettes", "clips", "other", "trailers",
                  "theme-music", "backdrops", "making of", "featurette", "花絮",
                  "幕后", "预告片", "特典影像"})
#: 特别篇目录名（官方：Season 00 / Specials / Extras）
特别篇目录 = frozenset({"specials", "special", "extras", "extra", "season0", "s00",
                  "特别篇", "番外篇", "特典", "sp", "ova", "oad"})
#: 番外**后缀**词（必须带 ``-``/``.``/``_`` 分隔符，否则 "The Other Guys" 会被误判）
_番外后缀词 = ("trailer", "sample", "scene", "scenes", "clip", "clips", "interview",
           "interviews", "behindthescenes", "deleted", "deletedscene",
           "deletedscenes", "featurette", "featurettes", "short", "shorts",
           "other", "others", "extra", "extras", "blooper", "bloopers",
           "outtake", "outtakes", "gagreel", "promo", "teaser")
_番外后缀 = re.compile(r"[-._](?:" + "|".join(sorted(_番外后缀词, key=len, reverse=True)) + r")$", re.I)
#: 字幕组/压制组的前缀标记（番剧常见 "[字幕组] 剧名 [1080p]"）
_组标记 = ("字幕", "压制", "发布", "汉化", "字幕社", "fansub", "subs", "raw", "rip")

#: 全角 → 半角（不引 unicodedata：V2 的依赖白名单里没有它）
_全角表 = {码: 码 - 0xFEE0 for 码 in range(0xFF01, 0xFF5F)}
_全角表[0x3000] = 0x20


def 转半角(文本: str) -> str:
    """全角字符转半角（含全角空格）。匹配打分/搜索都用它，保证两边归一方式一致。"""
    return (文本 or "").translate(_全角表)


def 是视频文件(名字: str | Path) -> bool:
    """按扩展名判断是不是视频（扫描器筛文件用同一个判据，避免两处规则不一致）。"""
    return _切扩展名(str(名字))[1] in 视频扩展名


# ============================ 小工具 ============================


def _切扩展名(名: str) -> tuple[str, str]:
    """切出 (主名, 扩展名)。**只认已知扩展名**，认不出就整串当主名。

    为什么不用 ``Path(name).suffix``：``Show.S01E01`` 这种没有扩展名的名字会被切成
    扩展名 ``.S01E01``，"Show S01E01" 这类无后缀文件就再也解析不出来了。
    """
    名 = (名 or "").strip()
    if "." not in 名:
        return 名, ""
    主, _, 尾 = 名.rpartition(".")
    小写 = 尾.lower()
    if 小写 in 视频扩展名 or 小写 in 字幕扩展名 or 小写 in 音频扩展名 \
            or 小写 in 图片扩展名 or 小写 in 其它扩展名:
        return 主, 小写
    return 名, ""


def _归一(文本: str) -> str:
    """目录名归一：小写 + 去掉空格/点/下划线/连字符/括号，便于整表比对。"""
    return re.sub(r"[\s._\-\[\]()（）【】]+", "", (文本 or "").strip().lower())


def _是番外目录(名字: str) -> bool:
    return _归一(名字) in {_归一(x) for x in 番外目录}


def _是特别篇目录(名字: str) -> bool:
    return _归一(名字) in {_归一(x) for x in 特别篇目录}


def _现在年份() -> int:
    return time.localtime().tm_year


def _是年份(数字: str) -> bool:
    """四位数字落在 1900~2099 内就是年份（**电影年份绝不当集号**）。"""
    return len(数字) == 4 and 1900 <= int(数字) <= 2099


def _目录季号(名字: str) -> int | None:
    """从**目录名**里搜季标识（``仙逆 第2季`` / ``仙逆 第二季`` / ``Show S02``）。

    为什么不能直接用 :func:`解析季目录`：那个要求整串就是季目录名（``Season 01``），
    而网盘里的剧目录常写成"剧名 + 第N季"，季号得从里面搜出来。
    """
    if not 名字:
        return None
    if (m := _中文季标识.search(名字)):
        return _中文数字(m.group(1))
    m = _季标识.search(名字)
    return int(next(x for x in m.groups() if x)) if m else None


def _中文数字(文本: str) -> int | None:
    """``二``→2 / ``十二``→12（季号；网盘目录里"第二季"比"第2季"还常见）。"""
    文本 = (文本 or "").strip()
    if 文本 in _季中文数字:
        return _季中文数字[文本]
    if "十" in 文本:                       # 十二 / 二十 / 二十二
        左, _, 右 = 文本.partition("十")
        return (_季中文数字.get(左, 1) if 左 else 1) * 10 + (_季中文数字.get(右, 0) if 右 else 0)
    return None


# ============================ 发布标签 ============================


def _建词表正则(表: dict, 多词分隔: bool = True) -> re.Pattern:
    r"""把词表编成"被分隔符/边界包住"的正则；长词优先，``.`` 允许写成 ``[\s._-]``。"""
    词们 = sorted(表, key=len, reverse=True)
    片段 = []
    for 词 in 词们:
        段 = re.escape(词)
        if 多词分隔:
            # ``dts-hd.ma`` 里的 ``-`` 与 ``.`` 在真实文件名里经常互换
            段 = 段.replace(r"\-", r"[\s._-]?").replace(r"\.", r"[\s._-]?")
        片段.append(段)
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(片段) + r")(?![A-Za-z0-9])", re.I)


_清晰度正则 = _建词表正则(_清晰度表)
_来源正则 = _建词表正则(_来源表)
_编码正则 = _建词表正则(_编码表)
_音轨正则 = _建词表正则(_音轨表)
_其它正则 = _建词表正则(_其它标签表)
#: 发布标签里所有不该当"语言码/标题词"的 token
_发布词集合 = frozenset(list(_清晰度表) + list(_来源表) + list(_编码表)
                   + list(_音轨表) + list(_其它标签表)
                   + ["dd5", "dd2", "5", "1", "2", "0", "7", "dtshd"])

#: 标题清洗词（**只用于标题**，不进 :func:`提取发布标签`）。
#: 为什么单独一张表、不并进上面的发布标签表：网盘里的目录名常被改成
#: ``仙逆.4K高码.SDR.60fps``，而"高码/SDR/国语/内嵌"这些词既不是标准发布标签，
#: 拿来当"版本名"也没有意义（会拼出"2160p WEB-DL 国语 内嵌"这种四不像）；
#: 不摘干净的话，标题兜底就成了"仙逆 高码 SDR 60fps"（真机日志实证）。
_标题噪声词 = (
    "高码率", "低码率", "高码", "低码",            # 码率俗称（"4K高码"）
    "国语", "粤语",                              # 配音版本
    "蓝光", "原盘",                              # 介质俗称
    "sdr", "hdr10+", "hdr10", "hdr", "dovi", "dv",   # 动态范围（发布标签表里只有部分）
)
#: 字幕/语言/内嵌这类"附件标记"常连写（"外挂字幕"/"简体中字"）——
#: 光删单个词会把"字幕"剩在标题里，所以整串一起删。
_字幕标记 = re.compile(r"(?:简体|繁体|简中|繁中|中文|英文|中英|双语|国语|粤语|内嵌|外挂|内封)*"
                  r"\s*(?:字幕|中字|简繁|内嵌|外挂|内封)")
#: 帧率（60fps / 23.976fps）
_帧率 = re.compile(r"\d{1,3}(?:\.\d{1,3})?\s*fps(?![A-Za-z0-9])", re.I)
#: 更新状态（"更新至146集"/"全12话"/"共24集"/"已完结"）——番剧/日更剧的目录名里到处都是
_更新状态 = re.compile(r"(?:更新至|更新到|更新|全|共)\s*\d{1,4}\s*[集话話回]|已完结|完结|合集|全集")
#: 标题噪声正则（_建词表正则 只要"可遍历的词"，这里包成 dict 与上面的表同形）
_标题噪声正则 = _建词表正则({词: 词 for 词 in _标题噪声词})


def 提取发布标签(文件名: str) -> dict:
    """从文件名里抠出发布标签（界面展示 + "同片不同版本"归并都要用）。

    返回 ``{"清晰度","来源","编码","音轨","发布组","其它"}``（取不到就是空串 / 空列表）。
    自己写规则，不引别人的库：这些词表是"发行圈的习惯写法"，不是官方规范，随时可扩。

    为什么"编码"要同族归一（AVC ≡ H.264 ≡ x264）：同一部片子的两个版本只差编码写法时，
    界面上应该显示成"两种版本"，而不是两个牛头不对马嘴的标签。
    """
    主名, _ = _切扩展名(str(文件名 or "").strip())
    结果 = {"清晰度": "", "来源": "", "编码": "", "音轨": "", "发布组": "", "其它": []}
    for 键, 正则, 表 in (("清晰度", _清晰度正则, _清晰度表), ("来源", _来源正则, _来源表),
                       ("编码", _编码正则, _编码表), ("音轨", _音轨正则, _音轨表)):
        if (m := 正则.search(主名)):
            结果[键] = _表查(表, m.group(0))
    其它: list[str] = []
    for m in _其它正则.finditer(主名):
        值 = _表查(_其它标签表, m.group(0))
        if 值 and 值 not in 其它:
            其它.append(值)
    结果["其它"] = 其它
    结果["发布组"] = _发布组(主名)
    return 结果


def _表查(表: dict, 命中文本: str) -> str:
    """把命中文本按"分隔符可互换"的规则查回词表（``DTS.HD.MA`` → ``DTS-HD MA``）。"""
    规范 = re.sub(r"[\s._-]+", ".", 命中文本.strip().lower())
    for 词, 值 in 表.items():
        if re.sub(r"[\s._-]+", ".", 词.lower()) == 规范:
            return 值
    return 命中文本.strip()


def _发布组(主名: str) -> str:
    """发布组 = 结尾 ``-XXX`` 那一段（发行圈惯例）。

    三个坑：
    * 只有**同时出现别的发布标签**时才算发布组 —— 否则 ``Spider-Man.mkv`` 会被
      当成"组名 Man"、标题被砍成 ``Spider``（实测踩过）；
    * 要排掉已知标签（``WEB-DL`` 里也有 ``-``）与分段/3D 片段（``Movie-cd1`` 的 ``cd1``）；
    * 中文名没有这个惯例，取不到就空着。
    """
    主名 = 主名.strip()
    if not any(正则.search(主名) for 正则 in (_清晰度正则, _来源正则, _编码正则, _音轨正则)):
        return ""
    m = re.search(r"[-–]([A-Za-z0-9][A-Za-z0-9@._]{1,19})$", 主名)
    if not m:
        return ""
    候选 = m.group(1)
    小写 = 候选.lower()
    if 小写 in _发布词集合 or re.fullmatch(r"(?:cd|dvd|part|pt|disc|disk)\d{1,2}", 小写) \
            or 小写 in {x.lower() for x in _三维格式.findall(主名)}:
        return ""
    # 只由数字/清晰度组成的尾巴不算组名（"Movie-2021" 之类）
    if re.fullmatch(r"\d{3,4}[pi]?", 小写):
        return ""
    return 候选


def _去发布标签(文本: str) -> str:
    """把发布标签从标题里摘掉（标题清理用）；结尾的发布组一并摘掉。

    注意顺序：**先认发布组，再去标签**。反过来的话，标签被摘掉之后就不剩什么"证据"，
    发布组会认不出来（``Movie.1080p.BluRay.x264-GROUP`` → 标题里留下 "-GROUP"）。
    """
    组 = _发布组(文本)
    for 正则 in (_清晰度正则, _来源正则, _编码正则, _音轨正则, _其它正则):
        文本 = 正则.sub(" ", 文本)
    if 组:
        # 组名紧跟在 "-" 后面，去掉它连同那个连字符
        文本 = re.sub(r"[-–]" + re.escape(组) + r"\s*$", " ", 文本, flags=re.I)
    return 文本


def _去标题噪声(文本: str) -> str:
    """摘掉"画质补充词 + 语言/字幕标记 + 更新状态"（**只给标题用**，见 _标题噪声词）。

    为什么不把这些词并进发布标签表：``提取发布标签()`` 的结果要拼成"版本名"给界面看，
    而"国语/内嵌/更新至146集"当版本名毫无意义；这里只是别让它们污染标题。
    """
    文本 = _标题噪声正则.sub(" ", 文本)
    文本 = _字幕标记.sub(" ", 文本)
    文本 = _帧率.sub(" ", 文本)
    return _更新状态.sub(" ", 文本)


def _版本名(标签: dict) -> str:
    """拼出"版本名"（Jellyfin 里 ``影片名 (年份) [id] - 版本名.mp4`` 的那一段）。"""
    部分 = [标签.get("清晰度", ""), 标签.get("来源", ""), 标签.get("编码", "")]
    部分 += list(标签.get("其它") or [])
    return " ".join(x for x in 部分 if x)


# ============================ 标记 / 3D / 段 ============================


def _摘标记(主名: str, 只看尾部: bool) -> tuple[list[str], str]:
    """抠字幕/音轨标记（语言码 + default/forced/sdh…），返回 ``(标记, 新主名)``。

    为什么视频**只看尾部 3 段**：``It.1990.mkv`` 里的 ``It``、``No.2020.mkv`` 里的 ``No``
    都是合法语言码，全局扫会把片名吃掉（实测踩过）。而且第 0 段永远不当标记
    —— 那一段就是片名。
    视频的标记**不从文本里删**（它们在尾部，留着比误删安全）；字幕/音轨文件才删，
    因为那些文件的标题本来就是干净片名，``Movie.zh.forced`` 留着只会污染标题。
    """
    段们 = [x for x in re.split(r"[\s._\-\[\]()【】]+", 主名) if x]
    if not 段们:
        return [], 主名
    起点 = max(0, len(段们) - 3) if 只看尾部 else 0
    标记: list[str] = []
    命中: set[str] = set()
    for i, 段 in enumerate(段们):
        if i < 起点 or i == 0:
            continue
        小写 = 段.lower()
        if 小写 in _语言码 or 小写 in _三字母语言码 or 小写 in _字幕简称:
            值 = 小写
        elif 小写 in _标记词:
            值 = 小写
        else:
            continue
        if 值 not in 标记:
            标记.append(值)
            命中.add(段)
    新名 = 主名
    if not 只看尾部 and 命中:
        for 段 in 命中:
            新名 = re.sub(r"(?<![0-9A-Za-z])" + re.escape(段) + r"(?![0-9A-Za-z])", " ", 新名)
    return 标记, 新名


def _摘三维(文本: str) -> tuple[str, str]:
    """摘 3D 标记：**先带格式，后孤立**（坑 3）。返回 (剩余文本, 三维值)。"""
    if (m := _三维格式.search(文本)):
        return (文本[:m.start()] + " " + 文本[m.end():]).strip(), m.group(1).upper()
    if (m := _三维裸.search(文本)):
        return (文本[:m.start()] + " " + 文本[m.end():]).strip(), "3D"
    return 文本, ""


def _摘段(文本: str) -> tuple[str, int | None]:
    """摘多段标记 cd1/dvd2/part1/pt1/disc1/disk1（坑 2：先摘掉它，数字才不会被当集号）。"""
    if (m := _段.search(文本)):
        return (文本[:m.start()] + " " + 文本[m.end():]).strip(), int(m.group(1))
    return 文本, None


# ============================ 结果模型 ============================


@dataclass
class 解析结果:
    类型: str = "未知"            # 电影 / 剧集 / 集 / 非视频 / 未知
    标题: str = ""
    年份: int | None = None
    数据源: str = ""             # imdb / tmdb / tvdb
    数据源ID: str = ""
    季: int | None = None
    集: int | None = None
    集到: int | None = None      # S01E01-E02 的 E02
    特别篇: bool = False
    版本: str = ""               # 1080p / 加长版 …（由发布标签拼出来）
    段: int | None = None        # cd1/dvd2/part1… 的段号
    三维: str = ""               # FTAB / HSBS / 3D
    是番外: bool = False
    标记: list[str] = field(default_factory=list)   # default/forced/sdh/语言码

    def 一句话(self) -> str:
        """给日志与界面用的一行摘要（不追求好看，追求信息全）。"""
        尾巴: list[str] = []
        if self.年份:
            尾巴.append(str(self.年份))
        if self.数据源:
            尾巴.append(f"[{self.数据源}-{self.数据源ID}]")
        if self.季 is not None:
            尾巴.append(f"S{self.季:02d}")
        if self.集 is not None:
            尾巴.append(f"E{self.集:02d}" + (f"-E{self.集到:02d}" if self.集到 else ""))
        if self.特别篇:
            尾巴.append("特别篇")
        if self.段:
            尾巴.append(f"第{self.段}段")
        if self.三维:
            尾巴.append(f"3D-{self.三维}")
        if self.版本:
            尾巴.append(self.版本)
        if self.是番外:
            尾巴.append("番外")
        if self.标记:
            尾巴.append("标记:" + "+".join(self.标记))
        return f"{self.类型}｜{self.标题}｜" + "｜".join(尾巴)


# ============================ 季目录 ============================


def 解析季目录(名字: str) -> tuple[int | None, bool]:
    """季目录名 → ``(季号, 是否特别篇)``；认不出就是 ``(None, False)``。

    认：``Season 01`` / ``Season 1`` / ``Season.01`` / ``S01``（官方不推荐缩写，但必须能认）/
    纯数字 ``5`` / ``第2季``；``Season 00``、``Specials``、``Extras``、``特别篇`` → ``(0, True)``。

    为什么纯数字只认 1~2 位：4 位数字是年份目录（``2019``），不是季号。
    """
    文本 = (名字 or "").strip()
    if not 文本:
        return None, False
    if _是特别篇目录(文本):
        return 0, True
    if (m := re.fullmatch(r"(?:season|s|season[\s._-]*)\s*[._-]?\s*(\d{1,2})", 文本, re.I)):
        n = int(m.group(1))
        return n, n == 0
    if (m := re.fullmatch(r"第\s*(\d{1,2})\s*[季部]", 文本)):
        n = int(m.group(1))
        return n, n == 0
    if (m := re.fullmatch(r"(\d{1,2})", 文本)):
        n = int(m.group(1))
        return n, n == 0
    return None, False


# ============================ 主解析 ============================


def 解析(文件名: str, 父目录名: str = "", 季目录名: str = "") -> 解析结果:
    """解析一个文件名（可用父目录名/季目录名补上下文）。

    ``父目录名``：文件名所在的目录。它**本身是季目录**（``Season 01``/``Specials``/``5``）时，
    剧名兜底就没了着落 —— 这种情况请直接调 :func:`猜剧集路径`，它会自己往上找剧名目录。
    ``季目录名``：显式给出季目录；给了它就认为 ``父目录名`` 是"剧名目录"（可用来兜底标题/年份）。

    **P5 起本函数是薄壳（兼容层）**
    ==============================
    结构部分（标题/年份/类型/季/集/集到/特别篇）转调 ``wangpan.identify.结构.抽取``
    —— P1 那一层的结构与标题清洗是 91/91（``数据/识别评测.md``），全项目只该有一处
    结构规则；这里再留一份"老规则"就是两套真相，迟早对不上。

    **老字段继续由本模块补齐**（新结构层不产出这些，去掉就是既有功能悄悄退化）：

    * ``数据源``/``数据源ID``（``[imdbid-tt123]``）—— 扫描器据此跳过搜索；
    * ``版本``（1080p/WEB-DL/x265…）—— 界面与"同片多版本"归并要用；
    * ``段``（cd1/part1）—— 多段合并不是多版本；
    * ``三维``（FTAB/HSBS）—— 与段同理；
    * ``标记``（default/forced/语言码）—— 外挂字幕配对要用；
    * ``是番外``（-trailer/花絮目录）—— 扫描时要剔出去；
    * **日期式命名**（``Show Name 2021-05-01 Title.mkv``，Plex 惯例：季=年份、集=MMDD）
      —— 架构文档第二节列的写法清单里没有它，新结构层也没实现；它就是"某些写法新层
      不产出"的那一类，所以在这里按老规则补（`下面那段 if 日期` 就是它的全部实现`）。

    为什么 import 放在函数里：``命名解析`` 是扫描/服务/界面都引的底层模块，
    把 identify 那一串（候选/检索/打分）在 import 期拖进来没必要；也免得
    "谁依赖谁"从文件头看不出来。
    """
    结果 = 解析结果(类型="未知")
    名 = (文件名 or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not 名:
        return 结果

    主名, 扩展 = _切扩展名(名)
    是视频 = 扩展 in 视频扩展名

    # ---- 结构与标题：转调 P1 的 identify.结构 ----
    # 只传文件名（不传整条路径）：老契约里 "文件名" 就是最后一段，路径目录由 父目录名/
    # 季目录名 两个参数显式给（`猜剧集路径` 自己往上找）—— 把路径也喂进去会让
    # "同一段目录"从两个来源各出现一次，标题兜底的顺序就变了。
    from ..identify.结构 import 抽取 as _新抽取
    链 = (季目录名,) if 季目录名 else ()
    新 = _新抽取(名, 父目录名=父目录名, 目录链=链)
    结果.标题 = 新.标题
    结果.年份 = 新.年份
    结果.季 = 新.季
    结果.集 = 新.集
    结果.集到 = 新.集到
    结果.特别篇 = bool(新.特别篇)
    if 扩展 and not 是视频:
        结果.类型 = "非视频"          # 坑 1：图片/字幕不是一集（但季集照抽，字幕配对要用）
    elif 新.类型 == "tv":
        结果.类型 = "集"
    elif 新.类型 == "movie":
        结果.类型 = "电影"
    else:
        结果.类型 = "未知"            # 没有扩展名：可能是文件夹（剧集目录交给 猜剧集路径）

    # ---- 老字段：标记 / 番外 / 数据源ID / 三维 / 段 / 版本 ----
    结果.标记 = _摘标记(主名, 只看尾部=是视频 and 扩展 not in 字幕扩展名)[0]
    结果.是番外 = _是番外目录(父目录名) or _是番外目录(季目录名)
    工作 = 主名
    if (m := _番外后缀.search(工作)):
        结果.是番外 = True
        去尾 = 工作[:m.start()].rstrip(" -_.")
        # 番外语（-behindthescenes）新结构层不认识，标题里可能还挂着它：这里补一刀。
        # 判据要求"标题确实以它结尾"：不然后面还会被别的字段覆盖，白清一遍。
        if 去尾 and 结果.标题.endswith(m.group(0)):
            结果.标题 = _净标题(去尾) or 结果.标题
        工作 = 去尾
    if (m := _数据源ID.search(工作)):
        结果.数据源 = _规范源名(m.group(1))
        结果.数据源ID = m.group(2).strip()
        工作 = (工作[:m.start()] + " " + 工作[m.end():]).strip()
    工作, 结果.三维 = _摘三维(工作)
    工作, 结果.段 = _摘段(工作)
    if 结果.三维 and "3d" in 主名.lower() and "3d" not in (结果.标题 or "").lower():
        # 片名里本来就有「3D」（``Awesome 3D Movie (2022).3D.FTAB.mp4``）时，新结构层会
        # 把它当发布标签一并摘掉（identify.归一化 的标签表里有 ``3d``）；老实现是
        # **先把 3D 格式标记摘掉、再算标题**，所以片名里的 3D 留得住。这里是那条老规则的
        # 补丁（只对"确实带 3D 标记"的文件生效，别的文件一个字节都不动）。
        去三维, _ = _摘年份(工作)
        结果.标题 = _净标题(去三维) or 结果.标题
    if 结果.年份 is None:
        # 年份口径差异（有意的，两边都有测试钉着）：
        # * 新结构层：``2012.mp4`` 的年份是 **null** —— "摘掉年份后标题不能为空"，
        #   而 2012 就是片名（``数据/识别评测.md`` 的口径段、P1 评测按它打分）；
        # * 老契约：``年份防误判测试`` 要 ``年份 == 2012``（片名与年份并存，老扫描器
        #   与界面都按"有年份就显示年份"来用）。
        # 薄壳服务的是老调用方，所以按老口径补；**服务层的新管线走 结构.抽取，不受这里影响**。
        _, 结果.年份 = _摘年份(工作)

    # ---- 日期式命名（新结构层没实现，见函数开头那条）----
    # ⚠️ 优先级跟老实现一样：**真的季集标记（S01E01 / 1x03 / 第01集）优先**，
    # 日期只在"一个季集标记都没有"时才当季集用 —— 否则
    # ``Show.S01E01.2021-05-01.mkv`` 会被改写成第 501 集（老实现是 elif 分支）。
    if (日期 := _日期.search(工作)):
        结果.标记.append("日期:" + 日期.group(0))
        有真集标记 = bool(_季集.search(工作) or _叉集.search(工作) or _中文集.search(工作))
        if not 有真集标记:
            if 结果.类型 != "非视频":
                结果.类型 = "集"
            结果.季 = int(日期.group(1))
            结果.集 = int(日期.group(2)) * 100 + int(日期.group(3))
            结果.集到 = None

    结果.版本 = _版本名(提取发布标签(主名))
    return 结果


def _规范源名(文本: str) -> str:
    小写 = (文本 or "").strip().lower()
    return {"imdbid": "imdb", "tmdbid": "tmdb", "tvdbid": "tvdb"}.get(小写, 小写)


def _摘年份(文本: str) -> tuple[str, int | None]:
    """摘年份。括号里的一律可信；裸年份要过三道关（防分辨率/防未来年份/防日更剧日期）。

    裸年份取**最后一个**（不是第一个）：改名后的电影常写成 ``1917.2019.1080p.mkv``
    —— 片名就是数字 1917，后面那个才是"片名.年份.画质"里的年份。
    """
    if (m := _括号年份.search(文本)):
        return (文本[:m.start()] + " " + 文本[m.end():]).strip(), int(m.group(1))
    命中: re.Match | None = None
    for m in _裸年份.finditer(文本):
        年 = int(m.group(1))
        # 未来年份多半是片名的一部分（"Blade Runner 2049"）；今年+1 之内的才是发行年
        if not (1900 <= 年 <= _现在年份() + 1):
            continue
        命中 = m
    if 命中 is not None:
        return (文本[:命中.start()] + " " + 文本[命中.end():]).strip(), int(命中.group(1))
    return 文本, None


def _摘开头集号(文本: str, 有季上下文: bool) -> int | None:
    """支持"集号在最前面"的改名文件：``146.SDR.8bit.2160p.60fps.DDP5.1…mp4`` → 第 146 集。

    为什么要这条规则：网盘里的番剧/剧集经常被改成"数字 + 画质标签"（标题整个丢掉），
    开头的数字是**唯一**还在的季集线索；不认它，这一集就永远挂不到剧上（真机日志实证）。

    三条防误判，一条都不能少（否则会拿电影去"补集"）：
    1. **年份不当集号**：``2012.mp4``、``1917.2019.1080p.mkv`` 是电影，不是第 2012/1917 集；
    2. **裸的 4 位数不当集号**（除非有前导零或有季目录上下文）：``1408.mkv`` 是电影，
       ``0146.mkv`` / ``Season 02/1408.mkv`` 才当集 —— 长番的集号上四位数时才需要它；
    3. **"数字 + 英文单词"像片名就不当集号**：``12 Angry Men.mkv`` / ``13 Going on 30.mkv``
       是电影；只有前面带零（``01 Pilot.mkv``）才另说 —— 前导零本来就是"这是集号"的强信号。

    为什么用"数字后面紧跟分隔符或直接结束"当门槛：``1080p``/``2160p``/``8bit``/``3D``/
    ``x264`` 里的数字后面跟的都是字母，一步就挡掉了，不必先摘发布标签再找数字。
    """
    m = _开头集号.match((文本 or "").strip())
    if not m:
        return None
    数字 = m.group(1)
    if _是年份(数字):
        return None
    前导零 = 数字.startswith("0")
    if len(数字) == 4 and not 前导零 and not 有季上下文:
        return None
    # 数字后面那一段（用来判断"这到底是不是片名"）
    if not 前导零 and _开头数字_像片名.match(m.string[m.end():]):
        return None
    return int(数字)


def _摘绝对集号(文本: str, 有季上下文: bool) -> tuple[str, int | None]:
    """绝对集号兜底（``剧名 07.mkv``）—— **保守**是这里的全部要点（坑 2）。

    三个门槛满足其一才认：
    1. 有季目录上下文（``Season 01/剧名 07.mkv`` —— 路径已经说明它是剧集）；
    2. 数字在括号里（``剧名 (07).mkv``）；
    3. 数字带前导零（``剧名 07.mkv``）—— 这是"这是集号不是续集编号"的最强信号：
       ``Movie 3.mkv`` 是第三部电影，``Movie 03.mkv`` 才是第 3 集。

    另外：4 位数只在括号里认（``(1000)`` 这种长番集号），裸的 4 位数留给年份。
    调用前 3D/多段/发布标签已经摘掉，所以 ``3D.FTAB``/``cd1``/``1080p``/``x264`` 里的
    数字根本到不了这里。
    """
    if (m := re.search(r"[(（\[]\s*(\d{1,4})\s*[)）\]]\s*$", 文本.strip())):
        return 文本[:m.start()].strip(), int(m.group(1))
    if (m := re.search(r"(?<![A-Za-z0-9])(\d{1,3})\s*$", 文本.strip())):
        号 = m.group(1)
        前导零 = 号.startswith("0") and len(号) > 1
        if 有季上下文 or 前导零:
            return 文本[:m.start()].strip(), int(号)
    return 文本, None


def _净标题(文本: str) -> str:
    """把"剩下的那段"清理成标题；清不干净就返回空串（坑 5）。

    "剩下那段"里通常还有两大类垃圾：发布标签（1080p/BluRay…）和
    "画质补充词/语言字幕/更新状态"（SDR/高码/国语/更新至146集…）。两类都要摘，
    否则网盘目录名兜底出来的标题会变成"仙逆 高码 SDR 60fps"。
    """
    文本 = _去开头括号组(文本)
    文本 = _去发布标签(文本)
    文本 = _去标题噪声(文本)
    文本 = re.sub(r"[\[\]{}（）()【】]", " ", 文本)
    文本 = 转半角(文本)
    文本 = 文本.replace("_", " ").replace(".", " ")
    文本 = re.sub(r"\s+", " ", 文本).strip(" -–—_·|~")
    # 只剩间隔符/单个符号的，等于没清出标题
    if not re.search(r"[0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", 文本):
        return ""
    return re.sub(r"\s+", " ", 文本)


def _去开头括号组(文本: str) -> str:
    """去掉**开头的发布组/字幕组方括号**（番剧命名 ``[字幕组][剧名][1080p][BDRip]``）。

    两条门槛，都是防误伤：
    * 组名里带"字幕/压制/fansub…"字样的，一定去；
    * 光看"开头连续两组"就去会把 ``[REC] 2 [2009].mkv`` 砍成 ``2`` —— 所以还要求
      **整名里出现过发布标签**（1080p/BDRip…）才认第一组是发布组。
    """
    组们 = list(re.finditer(r"\s*[\[【]([^\]】]{1,40})[\]】]", 文本))
    if not 组们 or 组们[0].start() != 0:
        return 文本
    有标签 = any(正则.search(文本) for 正则 in (_清晰度正则, _来源正则, _编码正则))
    剩余 = 文本
    第一组 = True
    while (m := re.match(r"\s*[\[【]([^\]】]{1,40})[\]】]\s*", 剩余)):
        内容 = m.group(1).lower()
        是标签 = bool(_清晰度正则.search(m.group(1)) or _来源正则.search(m.group(1))
                  or _编码正则.search(m.group(1)))
        是组名 = any(词 in 内容 for 词 in _组标记)
        if not (是标签 or 是组名 or (第一组 and 有标签 and len(组们) >= 2)):
            break
        剩余 = 剩余[m.end():]
        第一组 = False
    return 剩余.lstrip(" -_.") or 文本


def _从目录名(名字: str) -> tuple[str, int | None]:
    """目录名 → ``(标题, 年份)``（``剧名 (2021)`` / ``影片名 (2019)``）。"""
    文本 = (名字 or "").strip()
    if not 文本 or 解析季目录(文本)[0] is not None or _是番外目录(文本):
        return "", None
    文本 = _数据源ID.sub(" ", 文本)
    文本, 年 = _摘年份(文本)
    return _净标题(文本), 年


# ============================ 多版本判定 ============================


def 是同一集的不同版本(甲: str, 乙: str) -> bool:
    """两个文件名是不是"同一条内容的不同版本"（合并/去重时用）。

    坑 4：**不看标题**。``S01E01 - 1080p.mkv`` 与 ``S01E01 - 2160p.mkv`` 去掉标签后标题
    都是空的，拿标题比较会把同一集判成两集。剧集只看 ``(季, 集, 特别篇)``；电影按
    "归一化标题相同 + 年份不冲突"判（同一个片子的 1080p / 2160p 版本要能合到一起）。

    多段（``cd1`` vs ``cd2``）**不算不同版本**：它们是同一个文件的两半，要拼起来而不是二选一。
    两个名字完全一样也返回 True（合并逻辑把重名当重复，调用方不需要再判一次）。
    """
    A, B = 解析(甲), 解析(乙)
    if A.类型 == "非视频" or B.类型 == "非视频":
        return False
    if A.段 and B.段 and A.段 != B.段:
        return False
    if A.集 is not None and B.集 is not None:
        if A.类型 != "集" or B.类型 != "集":
            return False
        return (A.季, A.集, A.集到, A.特别篇) == (B.季, B.集, B.集到, B.特别篇)
    if A.类型 == "电影" and B.类型 == "电影":
        if not A.标题 or not B.标题:
            return False
        if A.标题 != B.标题:
            return False
        return not (A.年份 and B.年份 and A.年份 != B.年份)
    return False


# ============================ 路径级猜测 ============================


def 猜剧集路径(路径: Path) -> 解析结果:
    """结合路径给出最终结果：自己往上找"季目录 / 番外目录 / 剧名目录"。

    为什么要走路径而不是只看文件名：官方命名规范把"季"放在**目录**上
    （``剧名/Season 01/剧名 S01E01.mkv``），文件名里可能什么季集信息都没有
    （``S01E01.mkv``）；反过来，文件名里的季集信息又可能比目录更细。
    这里的规则是：季号以目录为准，集号以文件名为准，标题两边兜底。
    """
    路径 = Path(路径)
    if 路径.is_dir():
        return _解析剧集目录(路径)

    父 = 路径.parent
    季目录名 = ""
    剧名目录 = 父
    是番外 = False
    上 = 父
    for _ in range(4):
        季号, _特别 = 解析季目录(上.name)
        if 季号 is not None:
            if not 季目录名:
                季目录名 = 上.name
            上 = 上.parent
            continue
        if _是番外目录(上.name):
            是番外 = True
            上 = 上.parent
            continue
        break
    剧名目录 = 上

    结果 = 解析(路径.name, 父目录名=剧名目录.name, 季目录名=季目录名)
    结果.是番外 = 结果.是番外 or 是番外

    剧名, 目录年 = _从目录名(剧名目录.name)
    if not 结果.标题:
        结果.标题 = 剧名
    if 结果.年份 is None:
        结果.年份 = 目录年
    if 结果.类型 == "未知" and 结果.标题:
        # 没扩展名又说得出标题：多半是"剧集目录"被当成文件传进来了
        结果.类型 = "剧集" if 结果.季 is not None else "未知"
    return 结果


def _解析剧集目录(目录: Path) -> 解析结果:
    """整个目录（``剧名 (2021)/``）→ 类型"剧集"的结果（扫描器建剧集条目时用）。

    ``季`` 只作提示：给最小的**非零**季号；只有特别篇目录时才是 0（并置 特别篇）。
    为什么不把有 ``Specials`` 子目录的整部剧都标成"特别篇"：那是"这部剧有一个特别篇目录"，
    不是"这部剧是特别篇"（实测踩过：整季的季号都被写成 S00）。
    """
    标题, 年 = _从目录名(目录.name)
    结果 = 解析结果(类型="剧集", 标题=标题, 年份=年, 是番外=_是番外目录(目录.name))
    普通季: list[int] = []
    有特别篇 = False
    try:
        子们 = list(目录.iterdir())
    except OSError:
        子们 = []
    for 子 in 子们:
        if not 子.is_dir():
            continue
        季号, 特别 = 解析季目录(子.name)
        if 季号 is None:
            continue
        if 特别:
            有特别篇 = True
            continue
        普通季.append(季号)
    if 普通季:
        结果.季 = min(普通季)
    elif 有特别篇:
        结果.季 = 0
        结果.特别篇 = True
    return 结果


if __name__ == "__main__":       # pragma: no cover - 手工对拍官方示例用
    样例 = ["Best_Movie_Ever (2019).mp4", "Movie (2021) [imdbid-tt12801262] - 2160p.mp4",
          "Movie (2021) [tmdbid=123] - 导演剪辑版.mp4", "Series Name A S01E01-E02.mkv",
          "Movie Name-cd1.mkv", "Awesome 3D Movie (2022).3D.FTAB.mp4",
          "S01E01 Some Episode-thumb.jpg", "Making of The Best Movie Ever-behindthescenes.mp4",
          "Series Name A (2021) S01E01 Title.ja.ass", "剧名 07.mkv"]
    for 名 in 样例:
        print(f"{名:52s} → {解析(名).一句话()}")
