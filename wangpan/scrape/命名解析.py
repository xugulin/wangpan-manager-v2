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
#: 第2季 / Season 2 / S02（只在文件名里当"季标识"，S02E01 走 _季集）
_季标识 = re.compile(r"(?:第\s*(\d{1,2})\s*季|season[\s._-]*(\d{1,2})(?![\s._-]*e\d)|(?<![A-Za-z0-9])s(\d{1,2})(?![\s._-]*e\d))", re.I)
#: 日期式命名（日更剧）：2021-05-01
_日期 = re.compile(r"(?<![0-9])((?:19|20)\d{2})[-._ ](\d{1,2})[-._ ](\d{1,2})(?![0-9])")
#: 括号里的年份（括号里的一定可信）
_括号年份 = re.compile(r"[\[(（【]\s*((?:19|20)\d{2})\s*[\])）】]")
#: 裸年份（1920x1080 这种分辨率要排除；未来年份不认，见 _摘年份）
_裸年份 = re.compile(r"(?<![0-9xX×])((?:19|20)\d{2})(?![0-9pPiI])(?![xX×]\d)")

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
    """
    结果 = 解析结果(类型="未知")
    名 = (文件名 or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not 名:
        return 结果

    主名, 扩展 = _切扩展名(名)
    是视频 = 扩展 in 视频扩展名
    if 扩展 and not 是视频:
        结果.类型 = "非视频"
    elif not 扩展:
        结果.类型 = "未知"          # 没有扩展名：可能是文件夹（剧集目录交给 猜剧集路径）

    结果.标记, 名_去标记 = _摘标记(主名, 只看尾部=是视频 and 扩展 not in 字幕扩展名)
    结果.是番外 = _是番外目录(父目录名) or _是番外目录(季目录名)

    # 季目录上下文（给季号，也决定"视频文件算不算一集"）
    目录季, 目录特别 = 解析季目录(季目录名) if 季目录名 else (None, False)
    if 目录季 is None and not 季目录名 and 父目录名:
        目录季, 目录特别 = 解析季目录(父目录名)
        父目录名_是季目录 = 目录季 is not None
    else:
        父目录名_是季目录 = False

    工作 = 名_去标记
    # 番外后缀（"-behindthescenes"）：摘掉它，免得污染标题（"Making of X-behindthescenes"）
    if (m := _番外后缀.search(工作)):
        结果.是番外 = True
        工作 = 工作[:m.start()].rstrip(" -_.")

    # 数据源 ID → 3D → 多段（顺序重要：坑 2 要求先把 3D/多段摘掉再找数字）
    if (m := _数据源ID.search(工作)):
        结果.数据源 = _规范源名(m.group(1))
        结果.数据源ID = m.group(2).strip()
        工作 = (工作[:m.start()] + " " + 工作[m.end():]).strip()
    工作, 结果.三维 = _摘三维(工作)
    工作, 结果.段 = _摘段(工作)

    # 日期式命名：先于年份处理（日期里含年份，不能被年份正则吃掉）
    日期 = _日期.search(工作)
    if 日期:
        结果.标记.append("日期:" + 日期.group(0))

    # 季/集。注意：非视频（字幕/图片）也要把季集解析出来 —— 外挂字幕要靠它找对应的集；
    # 但 **类型 不许被改**（坑 1：`S01E01 Some Episode-thumb.jpg` 不是一集）。
    可判集 = 结果.类型 != "非视频"
    季m = _季标识.search(工作)
    集m = _季集.search(工作) or _叉集.search(工作) or _中文集.search(工作)
    切点: int | None = None
    if 集m:
        if 集m.re is _中文集:
            结果.集 = int(集m.group(1))
        else:
            结果.季 = int(集m.group(1))
            结果.集 = int(集m.group(2))
            结果.集到 = int(集m.group(3)) if 集m.group(3) else None
        if 可判集:
            结果.类型 = "集"
        切点 = 集m.start()
    elif 日期:
        # 日更剧（Plex 惯例）：季 = 年份，集 = 月日（0501）；日期原文进标记，方便回溯
        if 可判集:
            结果.类型 = "集"
        结果.季 = int(日期.group(1))
        结果.集 = int(日期.group(2)) * 100 + int(日期.group(3))
        切点 = 日期.start()

    if 季m and (切点 is None or 季m.start() < 切点):
        切点 = 季m.start()
    if 季m and 结果.季 is None:
        结果.季 = int(next(x for x in 季m.groups() if x))
    if 结果.季 is None and 父目录名_是季目录:
        结果.季 = 目录季

    # 目录里的季号最权威（官方：路径决定季，文件名只是辅助）
    if 季目录名 and 目录季 is not None:
        结果.季 = 目录季
    elif 目录季 is not None and 结果.季 is None:
        结果.季 = 目录季
    if 目录特别 or (结果.季 == 0):
        结果.特别篇 = True
        if 结果.季 is None:
            结果.季 = 0

    # 标题（先按切点砍掉季集之后的"单集标题/标签"，再清理）
    标题段 = 工作[:切点] if 切点 is not None else 工作
    剩余段 = 工作[切点:] if 切点 is not None else ""

    # 年份：日期已经被摘掉的话不会重复命中（日期段在 剩余段 里，不参与）
    标题段, 结果.年份 = _摘年份(标题段)
    if 结果.年份 is None and 剩余段:
        _, 年 = _摘年份(剩余段)
        结果.年份 = 年

    # 绝对集号兜底（坑 2）：**保守**三条门槛
    if 结果.集 is None and 结果.类型 in ("电影", "集", "未知") and 是视频:
        剩余, 集号 = _摘绝对集号(标题段, 有季上下文=(目录季 is not None) or 结果.特别篇)
        if 集号 is not None:
            标题段 = 剩余
            结果.集 = 集号
            可判集 = True
            if 结果.类型 != "非视频":
                结果.类型 = "集"

    # 类型收口
    if 结果.类型 == "未知" and 是视频:
        结果.类型 = "集" if (目录季 is not None or 结果.特别篇) else "电影"
    elif 结果.类型 == "电影" and (目录季 is not None):
        结果.类型 = "集"

    标题 = _净标题(标题段)
    if not 标题 and 父目录名 and not 父目录名_是季目录:
        标题 = _从目录名(父目录名)[0]
    # 坑 5：清理不干净就让它空着，**不要**退回原始文件名
    结果.标题 = 标题

    标签 = 提取发布标签(主名)
    结果.版本 = _版本名(标签)
    return 结果


def _规范源名(文本: str) -> str:
    小写 = (文本 or "").strip().lower()
    return {"imdbid": "imdb", "tmdbid": "tmdb", "tvdbid": "tvdb"}.get(小写, 小写)


def _摘年份(文本: str) -> tuple[str, int | None]:
    """摘年份。括号里的一律可信；裸年份要过三道关（防分辨率/防未来年份/防日更剧日期）。"""
    if (m := _括号年份.search(文本)):
        return (文本[:m.start()] + " " + 文本[m.end():]).strip(), int(m.group(1))
    for m in _裸年份.finditer(文本):
        年 = int(m.group(1))
        # 未来年份多半是片名的一部分（"Blade Runner 2049"）；今年+1 之内的才是发行年
        if not (1900 <= 年 <= _现在年份() + 1):
            continue
        return (文本[:m.start()] + " " + 文本[m.end():]).strip(), 年
    return 文本, None


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
    """把"剩下的那段"清理成标题；清不干净就返回空串（坑 5）。"""
    文本 = _去开头括号组(文本)
    文本 = _去发布标签(文本)
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
