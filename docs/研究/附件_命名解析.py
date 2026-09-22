# -*- coding: utf-8 -*-
"""按 Jellyfin 官方命名规范解析影片/剧集文件名（研究用参考实现，已用官方示例对拍）。

规则来源（逐条对应官方文档，均已核对）：
- 电影：https://jellyfin.org/docs/general/server/media/movies/
- 剧集：https://jellyfin.org/docs/general/server/media/shows/
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

#: 数据源 ID：[imdbid-tt1234567] / [tmdbid-12345] / [tvdbid-12345]
数据源ID = re.compile(r"\[(?P<源>imdbid|tmdbid|tvdbid)-(?P<id>[^\]]+)\]", re.I)
#: 年份：(2019)
年份 = re.compile(r"\((?P<年>(19|20)\d{2})\)")
#: 季集：S01E01 / S01E01-E02 / s1e1 / 1x01 / 第01集 / 第01话
季集们 = [
    ("标准 SxxExx", re.compile(r"[Ss](?P<季>\d{1,2})[Ee](?P<集>\d{1,3})(?:-[Ee](?P<集到>\d{1,3}))?")),
    ("1x01", re.compile(r"(?P<季>\d{1,2})[xX](?P<集>\d{1,3})")),
    ("第N集/话", re.compile(r"第(?P<集>\d{1,3})[集话]")),
    ("日期式", re.compile(r"(?P<年>19\d{2}|20\d{2})[.\-_ ](?P<月>\d{1,2})[.\-_ ](?P<日>\d{1,2})")),
    # ⚠️ 坑 2：绝对集号（"剧名 07.mkv"）只能**保守**匹配：必须是"名字末尾/括号里的独立数字"，
    #    否则 "3D.FTAB"、"cd1"、"1080p"、"x264" 里的数字都会被误当集号（实测踩过）。
    ("绝对集号（末尾独立数字）", re.compile(r"(?:^|[\s._\-\[])(?P<集>\d{1,3})(?=[\s._\-\]]*$)")),
]
#: 季目录：Season 01 / Season 1 / S01（官方明确不推荐缩写，但要能认）
季目录 = re.compile(r"^(?:season|s)[\s._-]*(?P<季>\d{1,3}|specials|extras|ova|oad)$", re.I)
#: 特别篇目录（官方：Season 00 / Specials / Extras）
特别目录 = {"specials", "extras", "ova", "oad", "特别篇", "sp"}
#: 多段：cd1/dvd2/part1/pt1/disc1/disk1（官方给的类型与分隔符）
多段 = re.compile(r"(?:^|[\s._-])(?:cd|dvd|part|pt|disc|disk)[\s._-]*(?P<号>\d+|[a-dA-D])(?![a-zA-Z0-9])", re.I)
#: 3D 标记（官方：3D 需与 hsbs/fsbs/htab/ftab/mvc 组合）
三维 = re.compile(r"(?:^|[\s._-])3d(?:[\s._-]*(?P<格式>hsbs|fsbs|htab|ftab|mvc))?(?![a-zA-Z0-9])", re.I)
#: 番外目录（官方清单，小写匹配）
番外目录 = {"behind the scenes", "deleted scenes", "interviews", "scenes", "samples",
        "shorts", "featurettes", "clips", "other", "extras", "trailers",
        "theme-music", "backdrops"}
#: 番外后缀（官方清单）
番外后缀 = ("-trailer", ".trailer", "_trailer", "-sample", ".sample", "_sample",
        "-scene", "-clip", "-interview", "-behindthescenes", "-deleted",
        "-deletedscene", "-featurette", "-short", "-other", "-extra")
#: 字幕/音轨标记（官方：default / forced|foreign / sdh|cc|hi）
语言标记 = re.compile(r"\.(?P<标记>(?:default|forced|foreign|sdh|cc|hi|[a-z]{2,3})"
                 r"(?:\.(?:default|forced|foreign|sdh|cc|hi|[a-z]{2,3}))*)\.(?:srt|ass|ssa|sub|idx|vtt|sup|aac|ac3|dts|flac|mp3)$", re.I)
#: 清晰度/版本标签（用于多版本识别）
清晰度 = re.compile(r"(?:^|[\s._-])(?P<标签>\d{3,4}[pi])(?![a-zA-Z0-9])", re.I)


@dataclass
class 解析结果:
    类型: str                      # 电影 / 剧集 / 集 / 未知
    标题: str = ""
    年份: int | None = None
    数据源: str = ""               # imdbid / tmdbid / tvdbid
    数据源ID: str = ""
    季: int | None = None
    集: int | None = None
    集到: int | None = None        # S01E01-E02 的 E02
    特别篇: bool = False
    版本: str = ""                 # 1080p / Directors Cut …
    段: int | None = None          # 多段的段号
    三维: str = ""
    是番外: bool = False
    标记: list[str] = field(default_factory=list)   # default/forced/sdh/语言

    def 一句话(self) -> str:
        尾巴 = []
        if self.年份: 尾巴.append(f"{self.年份}")
        if self.数据源: 尾巴.append(f"[{self.数据源}-{self.数据源ID}]")
        if self.季 is not None: 尾巴.append(f"S{self.季:02d}")
        if self.集 is not None:
            尾巴.append(f"E{self.集:02d}" + (f"-E{self.集到:02d}" if self.集到 else ""))
        if self.特别篇: 尾巴.append("特别篇")
        if self.段: 尾巴.append(f"第{self.段}段")
        if self.版本: 尾巴.append(self.版本)
        if self.三维: 尾巴.append(f"3D-{self.三维}")
        if self.是番外: 尾巴.append("番外")
        if self.标记: 尾巴.append("标记:" + "+".join(self.标记))
        return f"{self.类型}｜{self.标题}｜{'｜'.join(尾巴)}"


#: 视频扩展名（只有这些才当"影片/剧集"解析；图片/字幕/音轨另走标记提取）
视频后缀 = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".flv", ".wmv",
        ".m4v", ".mpg", ".mpeg", ".iso", ".rmvb", ".rm", ".vob", ".strm"}


def 解析(文件名: str, 父目录名: str = "", 季目录名: str = "") -> 解析结果:
    """解析一个视频文件名（可用父目录/季目录补充信息，模拟扫描时的上下文）。"""
    名 = 文件名.rsplit("/", 1)[-1]
    # ⚠️ 坑 1：poster.jpg / xxx-thumb.jpg / 字幕文件都不是"影片"，先按扩展名挡掉，
    #    否则 "S01E01 Some Episode-thumb.jpg" 会被当成一集
    if "." in 名 and ("." + 名.rsplit(".", 1)[-1].lower()) not in 视频后缀:
        结果 = 解析结果(类型="非视频")
        if (m := 语言标记.search(名)):
            结果.标记 = m.group("标记").lower().split(".")
        return 结果
    主名, _, 后缀 = 名.rpartition(".")
    结果 = 解析结果(类型="未知")
    # 1) 番外判定（目录名或文件名后缀）
    if 父目录名.strip().lower() in 番外目录 or 季目录名.strip().lower() in 番外目录:
        结果.是番外 = True
    for 尾巴 in 番外后缀:
        if 主名.lower().endswith(尾巴):
            结果.是番外 = True
            主名 = 主名[: -len(尾巴)]
            break
    # 2) 字幕/音轨标记（先摘掉，免得干扰）
    标记命中 = 语言标记.search(名)
    if 标记命中:
        结果.标记 = 标记命中.group("标记").lower().split(".")
    # 3) 数据源 ID / 年份
    if (m := 数据源ID.search(主名)):
        结果.数据源, 结果.数据源ID = m.group("源").lower(), m.group("id")
        主名 = 主名.replace(m.group(0), " ")
    if (m := 年份.search(主名)):
        结果.年份 = int(m.group("年"))
    # 4) 季集
    for 名字, 式 in 季集们:
        m = 式.search(主名)
        if not m: continue
        组 = m.groupdict()
        if "月" in 组 and 组.get("月"):                     # 日期式：季=年，集=月日
            结果.季, 结果.集 = None, int(组["月"]) * 100 + int(组["日"])
            结果.类型 = "集"
            break
        结果.季 = int(组["季"]) if 组.get("季") else None
        结果.集 = int(组["集"]) if 组.get("集") else None
        if 组.get("集到"): 结果.集到 = int(组["集到"])
        结果.类型 = "集"
        break
    # 5) 多段 / 3D / 版本
    if (m := 多段.search(主名)):
        号 = m.group("号")
        结果.段 = int(号) if 号.isdigit() else (ord(号.lower()) - ord("a") + 1)
        主名 = 主名.replace(m.group(0), " ")
    # ⚠️ 坑 3：片名里本来就可能带 "3D"（如 "Awesome 3D Movie (2022).3D.FTAB"）。
    #    必须**优先匹配带格式的 3D**（3D.FTAB / 3D_FTAB / 3d-hsbs…），找不到才认孤立的 3D。
    带格式 = re.compile(r"(?:^|[\s._-])3d[\s._-]*(?P<格式>hsbs|fsbs|htab|ftab|mvc)(?![a-zA-Z0-9])", re.I)
    裸三D = re.compile(r"(?:^|[\s._-])3d(?![a-zA-Z0-9])", re.I)
    if (m := 带格式.search(主名)):
        结果.三维 = m.group("格式").lower()
        主名 = 主名.replace(m.group(0), " ")
    elif (m := 裸三D.search(主名)):
        结果.三维 = "未标注格式"
        主名 = 主名.replace(m.group(0), " ")
    # 版本标签：优先清晰度，其次 "- XXX" 形式
    if (m := 清晰度.search(主名)):
        结果.版本 = m.group("标签").lower()
    # 6) 标题：去掉年份/ID/季集/括号后的剩余部分
    标题 = 主名
    for 式 in (数据源ID, 年份, 清晰度, 多段, 三维):
        标题 = 式.sub(" ", 标题)
    for _名, 式 in 季集们:                       # 把季集片段也摘掉
        标题 = 式.sub(" ", 标题)
        标题 = 式.sub(" ", 标题)
    标题 = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", 标题)        # 去掉所有方括号/圆括号组
    标题 = re.sub(r"[\s._\-]+", " ", 标题).strip(" -_.")
    if not 标题 and 父目录名:
        标题 = 年份.sub(" ", 数据源ID.sub(" ", 父目录名)).strip()
    # ⚠️ 坑 5：标题清理干净后**不要**退回原始文件名。原始名里带着版本/清晰度标签，
    #    拿它当标题会让"同一集的两个版本"被判成两部不同的片子（实测踩过）。
    #    没有父目录就让它空着（调用方显示"未知"或让用户确认）更安全。
    结果.标题 = 标题 or (年份.sub(" ", 数据源ID.sub(" ", 父目录名)).strip() if 父目录名 else "")
    if 结果.类型 == "未知":
        结果.类型 = "电影" if (结果.年份 or not 季目录名) else "剧集"
    return 结果


def 解析季目录(名字: str) -> tuple[int | None, bool]:
    """Season 01 → (1, False)；Specials/Season 00 → (0, True)；认不出 → (None, False)。"""
    文本 = 名字.strip()
    if 文本.lower() in 特别目录:
        return 0, True
    if (m := 季目录.match(文本)):
        值 = m.group("季")
        if 值.isdigit():
            数字 = int(值)
            return 数字, 数字 == 0
        return 0, True
    if 文本.isdigit():
        return int(文本), int(文本) == 0
    return None, False


def 是同一集的不同版本(甲: str, 乙: str) -> bool:
    """多版本判定（官方原文：同季同集 = 同一集的不同版本）。

    ⚠️ 坑 4：**不要**把标题也算进判定 —— 多版本文件名里标题部分会被版本标签挤掉
    （`S01E01 - 1080p.mkv` 去掉标签后标题是空的），拿标题比较会得出"不是同一集"的错结论。
    """
    A, B = 解析(甲), 解析(乙)
    if A.集 is None or B.集 is None:
        return False
    if (A.季, A.集) != (B.季, B.集):
        return False
    return (not A.标题 or not B.标题 or A.标题 == B.标题)


if __name__ == "__main__":
    print("=== 官方文档里的例子对拍 ===")
    for 名 in ["Best_Movie_Ever (2019).mp4", "Movie (2021) [imdbid-tt12801262] - 2160p.mp4",
             "Movie (2021) [imdbid-tt12801262] - 1080p.mp4",
             "Movie (2021) [imdbid-tt12801262] - Directors Cut.mp4",
             "Awesome 3D Movie (2022).3D.FTAB.mp4",
             "Movie Name-cd1.mkv", "Movie Name-cd2.mkv",
             "Series Name A S01E01-E02.mkv", "Series Name A S02E03 Part 1.mkv",
             "S01E01 - 720p - Part 1.mkv", "S01E01 Some Episode-thumb.jpg",
             "Making of The Best Movie Ever-behindthescenes.mp4",
             "Series Name A (2021) S01E01 Title.ja.ass"]:
        结果 = 解析(名, 父目录名="Movies")
        print(f"  {名:58s} → {结果.一句话()}")
    print("=== 季目录 ===")
    for 名 in ["Season 01", "Season 1", "Season 00", "Specials", "S01", "5"]:
        数字, 特别 = 解析季目录(名)
        print(f"  {名:12s} → 季={数字} 特别篇={特别}")
    print("=== 多版本判定 ===")
    print("  1080p vs 2160p：", 是同一集的不同版本("S01E01 - 1080p.mkv", "S01E01 - 2160p.mkv"))
    print("  不同集：      ", 是同一集的不同版本("S01E01.mkv", "S01E02.mkv"))
