#!/usr/bin/env python3
"""P2 真网探测：拿**真 TMDB** 把"③ 候选生成 + ④ 检索"跑一遍，把真实候选打给人看。

为什么要有这个脚本（不是"又一个 demo"）
======================================
P2 的验收指标是"候选召回 ≥ 99%"，而 ③④ 全是**跟真实数据打交道**的规则：
"仙逆"这两个字 TMDB 到底认不认、中英混排要不要拆、改名文件靠目录名能不能救回来 ——
这些都是**单测的假服务回答不了**的问题（假服务对任何查询词都回同一批候选）。
所以每次调完候选/检索规则，都该拿真网样本看一眼真实候选表，而不是只看单测绿了。

默认样本是这台电脑上真实遇到过的四个（见 ``数据/识别标注.jsonl`` 同源的前几条）：
被网盘改名规避限制的剧（``146.SDR.8bit…``）、同一部剧的规范命名（``Renegade.Immortal.S01E138…``）、
一部电影（``沙丘.2021…``）、以及"文件名只有集号、作品名全在目录里"的（``葬送的芙莉莲/Season 01/05.mkv``）。

跑法
====
::

    运行环境/venv/bin/python 工具/识别真网探测.py              # 跑默认 4 个真机样本
    运行环境/venv/bin/python 工具/识别真网探测.py --不落盘      # 不用/不写 数据/识别检索缓存.json
    运行环境/venv/bin/python 工具/识别真网探测.py --目录 电影 --文件 沙丘.2021.2160p.mkv

退出码
======
* ``0``：至少一个样本拿到了候选（有错误也照样 0，错误会打出来）；
* ``1``：命令行用法错了；
* ``2``：TMDB 凭据没配（**不猜、不硬编**，直接说清楚怎么办）；
* ``3``：所有样本都零候选（多半是网络/代理不通，脚本会把每个查询词的错误原样打出来）。

结构从哪来
==========
② 结构抽取是 **P1** 的活（``wangpan/identify/结构.py``）。这个脚本**优先调 P1 的**
``抽取()``；万一 P1 的文件还没落地/导不进来，就退回到脚本内的"脚手架抽取"
（只认最基本的几种写法，够跑通这条链）。用的是哪一路会打在输出里 ——
免得有人把脚手架的错当成 P1 的错。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
# 直接跑 `python 工具/识别真网探测.py` 时 sys.path[0] 是 `工具/`，不是项目根。
if str(项目根) not in sys.path:
    sys.path.insert(0, str(项目根))

from wangpan.控制台 import 修控制台                                        # noqa: E402
from wangpan.identify.候选 import 生成查询, 去标签, 是标签, 清洗              # noqa: E402
from wangpan.identify.检索 import 检索, 检索缓存, 默认落盘缓存路径           # noqa: E402

修控制台()

#: 默认要落的检索缓存（"同一目录几十个文件不重复打接口"是 ④ 的核心收益，探测也要吃上）
默认缓存路径 = 默认落盘缓存路径()


# ============================ 默认样本 ============================

class 样本:
    """一个真机样本：目录链（近→远）+ 文件名（+ 期望，只用来打印命中/未命中）。"""

    def __init__(self, 目录链, 文件名, 期望标识="", 期望标题="", 说明=""):
        self.目录链 = tuple(目录链)
        self.文件名 = 文件名
        self.期望标识 = 期望标识
        self.期望标题 = 期望标题
        self.说明 = 说明


默认样本 = (
    样本(("仙逆.4K高码.SDR.60fps",), "146.SDR.8bit.2160p.60fps.DDP5.1.WEB-DL.H265.mp4",
        期望标识="223911", 期望标题="仙逆",
        说明="真机：网盘改名规避限制，作品名只剩在目录里，文件名只有集号"),
    样本(("仙逆.4K高码.SDR.60fps",),
        "Renegade.Immortal.S01E138.2023.2160p.WEB-DL.H265.10bit.DDP2.0-PanWEB.mkv",
        期望标识="223911", 期望标题="仙逆",
        说明="同一部剧的规范命名（英文名 + 年份 + 季集）"),
    样本(("电影",), "沙丘.2021.2160p.WEB-DL.H265.mkv",
        期望标识="438631", 期望标题="沙丘", 说明="电影：年份是硬信息，剧集接口会给出同名干扰"),
    样本(("葬送的芙莉莲", "Season 01"), "05.mkv",
        期望标识="209867", 期望标题="葬送的芙莉莲",
        说明="文件名只有集号，作品名与季都在目录里"),
)


# ============================ 脚手架结构抽取（P1 缺席时的兜底） ============================

class 简易结构:
    """脚手架用的结构对象（鸭子类型，字段名与 P1 的 ``结构结果`` 对齐）。"""

    def __init__(self, 标题="", 年份=None, 类型="unknown", 季=None, 集=None, 目录链=()):
        self.标题 = 标题
        self.年份 = 年份
        self.类型 = 类型
        self.季 = 季
        self.集 = 集
        self.标题候选们: list[str] = []
        self.目录链 = tuple(目录链)

    def 一句话(self) -> str:
        return (f"标题「{self.标题 or '空'}」｜年份 {self.年份}｜{self.类型}"
                f"｜季 {self.季}｜集 {self.集}")


def 脚手架抽取(文件名: str, 目录链) -> 简易结构:
    """**不是 P1 的替代品**：只认最基本写法，让 P2 能在 P1 落地前真网自验。

    规则尽量少（多写一条就多一处将来要删的代码）：季集看 ``S01E138`` / ``1x03`` /
    ``第03集`` / 前导数字；年份看 19xx/20xx；类型看"有没有集号"。标题交给
    :func:`~wangpan.identify.候选.去标签` 把标记与结构数字摘掉。
    """
    import re
    基名 = Path(文件名).stem
    文本 = 清洗(基名)
    季 = 集 = None
    匹配 = re.search(r"(?i)\bs(\d{1,2})[\s.\-]*e(\d{1,4})\b", 文本)
    if 匹配:
        季, 集 = int(匹配.group(1)), int(匹配.group(2))
    else:
        匹配 = re.search(r"\b(\d{1,2})x(\d{1,4})\b", 文本)
        if 匹配:
            季, 集 = int(匹配.group(1)), int(匹配.group(2))
        else:
            匹配 = re.search(r"第\s*0*(\d{1,4})\s*[集话話]", 文本)
            if 匹配:
                集 = int(匹配.group(1))
            else:
                匹配 = re.match(r"^0*(\d{1,4})(?=[\s.]|$)", 文本)
                if 匹配 and int(匹配.group(1)) <= 9999:
                    集 = int(匹配.group(1))
    if 集 is not None:
        季 = 季 if 季 is not None else 1
    年份 = None
    for 段 in re.findall(r"\b(19\d{2}|20\d{2})\b", 文本):
        年份 = int(段)
        break
    干净, _ = 去标签(文本, 年份=年份, 季=季, 集=集)
    标题 = 干净 if 干净 and not (是标签(干净)) else ""
    类型 = "tv" if 集 is not None else ("movie" if 年份 else "unknown")
    return 简易结构(标题=标题, 年份=年份, 类型=类型, 季=季, 集=集, 目录链=目录链)


def 取结构(文件名: str, 目录链, 模式: str = "自动"):
    """按 ``模式`` 取结构（P1 / 脚手架 / 自动）。返回 ``(结构对象, 来源说明)``。

    自动 = 先试 P1，导不进来再用脚手架；把"用的是哪一路"显式返回，
    是为了不让人把脚手架的错当成 P1 的错。
    """
    if 模式 == "脚手架":
        return 脚手架抽取(文件名, 目录链), "脚手架（--结构 脚手架）"
    try:
        from wangpan.identify.结构 import 抽取 as P1抽取          # noqa: PLC0415 - 故意延迟导入
        结构 = P1抽取(文件名, 目录链=目录链)
        return 结构, "P1 wangpan/identify/结构.py"
    except Exception as 错:                                       # noqa: BLE001
        if 模式 == "P1":
            raise
        return (脚手架抽取(文件名, 目录链),
                f"脚手架（P1 结构层没能用上：{type(错).__name__}: {错}）")


# ============================ 别名表 ============================


def 读别名表(路径: Path) -> dict:
    """读 P4 的学习库（可能还没有）。**坏了当空**：探测脚本不该因为别名库打不开就跑不动。

    认两种形状：``{"别名": {...}}`` 与"整个文件就是一个别名表"。
    """
    try:
        数据 = json.loads(路径.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(数据, dict):
        return {}
    节点 = 数据.get("别名") if isinstance(数据.get("别名"), dict) else 数据
    出: dict = {}
    for 键, 值 in 节点.items():
        if isinstance(键, str) and isinstance(值, (str, list, tuple, dict, int)):
            出[键] = 值
    return 出


# ============================ 打印 ============================


def 打标题(文本: str) -> None:
    print(f"\n{'=' * 78}\n{文本}\n{'=' * 78}", flush=True)


def 打样本(编号: int, 总数: int, 样本: 样本) -> None:
    打标题(f"样本 {编号}/{总数}：{样本.目录链 or '（没有目录）'} / {样本.文件名}")
    if 样本.说明:
        print(f"说明：{样本.说明}")


def 打结构(结构, 来源: str) -> None:
    一句话 = getattr(结构, "一句话", None)
    摘要 = 一句话() if callable(一句话) else (f"标题「{getattr(结构, '标题', '')}」"
                                        f"｜年份 {getattr(结构, '年份', None)}"
                                        f"｜{getattr(结构, '类型', '')}")
    print(f"② 结构（{来源}）：{摘要}")


def 打查询(查询集) -> None:
    if 查询集.空的():
        print("③ 候选：**没有查询词**（这条样本没有可搜的东西）")
    else:
        print(f"③ 候选：{len(查询集.查询词们)} 个查询词（按权重从高到低）")
        for 序号, 词 in enumerate(查询集.查询词们, start=1):
            备注 = f"　← {词.备注}" if 词.备注 else ""
            其他 = ("（也被 " + "、".join(词.其它来源) + " 命中）") if 词.其它来源 else ""
            print(f"   {序号}. {词.文本}　[{词.来源}｜权重 {词.权重:.0f}｜{词.语言}]{其他}{备注}")
    if 查询集.丢弃们:
        前几条 = "；".join(f"{文本}←{原因}" for 文本, 原因 in 查询集.丢弃们[:8])
        print(f"   丢掉的：{前几条}")
    print(f"   过滤条件：年份={查询集.年份}　类型偏好={查询集.类型}　季={查询集.季}　集={查询集.集}")


def 打命中(结果, 每查询条数: int = 5) -> None:
    print(f"\n④ 检索：{结果.汇总()}")
    for 命中 in 结果.命中们:
        缓存 = "（缓存）" if 命中.来自缓存 else ""
        回退 = "（年份对不上，已退回不带年份重搜）" if 命中.年份回退 else ""
        print(f"   [{'剧集' if 命中.类型 == 'tv' else '电影'}] {命中.查询词} {缓存}{回退}")
        print(f"        {命中.前几条(每查询条数)}")
    if 结果.错误们:
        print("   错误（失败要软：单个查询词报错不影响其它）：")
        for 错 in 结果.错误们:
            print(f"       × {错.可读()}")


def 打合并(结果, 样本: 样本) -> None:
    print(f"\n合并去重后的候选表（{len(结果.候选们)} 条）")
    if not 结果.候选们:
        print("   （空）")
        return
    期望位置 = 0
    for 序号, 候选 in enumerate(结果.候选们, start=1):
        if 样本.期望标识 and 候选.标识 == 样本.期望标识 and not 期望位置:
            期望位置 = 序号
    for 序号, 候选 in enumerate(结果.候选们[:15], start=1):
        年 = f"（{候选.年份}）" if 候选.年份 else ""
        命中标 = "✓" if 序号 == 期望位置 else " "
        print(f"   {命中标}{序号:2}. [{候选.类型}#{候选.标识}] {候选.标题}{年}"
              f"　原名：{候选.原名 or '—'}　热度 {候选.热度:.1f}　票数 {候选.票数}")
        print(f"        来源证据（{len(候选.来源)} 条）："
              + "；".join(f"{文本}(权重{w:.0f})" for 文本, w in 候选.来源.items()))
    if len(结果.候选们) > 15:
        print(f"   …还有 {len(结果.候选们) - 15} 条")
    if 样本.期望标识:
        命中 = next((x for x in 结果.候选们 if x.标识 == 样本.期望标识), None)
        if 命中:
            print(f"   ✅ 期望的《{样本.期望标题}》(#{样本.期望标识}) 在候选里，"
                  f"排第 {期望位置} 位，"
                  f"来源：{'、'.join(命中.来源查询们())}")
        else:
            print(f"   ❌ 期望的《{样本.期望标题}》(#{样本.期望标识}) **没被搜到**")
    elif 样本.期望标题:
        命中 = next((x for x in 结果.候选们 if 样本.期望标题 in x.标题), None)
        print(("   ✅ 候选里有标题含"
               f"《{样本.期望标题}》的条目") if 命中 else
              f"   ❌ 候选里没有标题含《{样本.期望标题}》的条目")


# ============================ 主流程 ============================


def _取参数() -> argparse.Namespace:
    解析 = argparse.ArgumentParser(
        description="P2 真网探测：真 TMDB 跑一遍 ③ 候选生成 + ④ 检索",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    解析.add_argument("--目录", action="append", default=[], metavar="名字",
                    help="父目录名（可重复；给多层时**从近到远**，最近的一层放第一条）")
    解析.add_argument("--文件", action="append", default=[], metavar="文件名",
                    help="文件名（与 --目录 一一对应；只给 --文件 就用默认样本的目录链规则）")
    解析.add_argument("--不落盘", action="store_true",
                    help=f"不用也不写落盘缓存（默认 {默认缓存路径.name}）")
    解析.add_argument("--别名库", default="数据/识别学习.json",
                    help="P4 学习库（可能还不存在；坏了当空）")
    解析.add_argument("--每查询条数", type=int, default=5, help="每个查询词打印前几条候选")
    解析.add_argument("--json", default="", metavar="路径", help="把原始结果也写一份 JSON")
    解析.add_argument("--结构", choices=("自动", "P1", "脚手架"), default="自动",
                    help="② 结构从哪来（默认自动：优先 P1）")
    解析.add_argument("--允许离线", action="store_true",
                    help="连不上 TMDB 也继续（此时只能吃落盘缓存里的旧结果）")
    return 解析.parse_args()


def 组装样本(参数) -> tuple:
    """``--目录/--文件`` 成对给；没给就用默认样本。返回 ``(样本们, 用法错误信息)``。"""
    if not 参数.文件:
        return list(默认样本), ""
    样本们 = []
    for 序号, 文件名 in enumerate(参数.文件):
        目录 = 参数.目录[序号] if 序号 < len(参数.目录) else ""
        链 = tuple(x for x in 目录.replace("\\", "/").split("/") if x)
        # 目录链约定是**从近到远**：`葬送的芙莉莲/Season 01` 要写成
        # `--目录 "Season 01/葬送的芙莉莲"`（最近的一层在前）。
        样本们.append(样本(链, 文件名))
    return 样本们, ""


def main() -> int:
    参数 = _取参数()
    样本们, 错误 = 组装样本(参数)
    if 错误:
        print(f"❌ {错误}")
        return 1
    打标题(f"P2 真网探测：{len(样本们)} 个样本 × （③ 候选生成 → ④ 检索）")

    # ---- 凭据与网络先探一次：不通就**说清楚原因**，别让 30 行报错糊一脸 ----
    try:
        from wangpan.scrape.tmdb import TMDB客户端, TMDB配置, 请求失败
    except Exception as 异常:                                     # noqa: BLE001
        print(f"❌ 导不进 TMDB 客户端：{type(异常).__name__}: {异常}")
        return 2
    配置 = TMDB配置.从环境与配置()
    客户端 = TMDB客户端(配置)
    print("TMDB 配置：" + json.dumps(客户端.配置摘要(), ensure_ascii=False))
    if not 配置.可用():
        print("❌ 没配 TMDB 凭据（token / apiKey）：写进 数据/刮削.json 的 tmdb 节点，"
              "或设环境变量 V2_TMDB_TOKEN / V2_TMDB_API_KEY。")
        print("   （这一步只是**读配置**，不会打印密钥本身）")
        return 2
    # 连通性自检必须**绕过 TMDB 客户端自己的磁盘缓存**：它默认有 7 天缓存，
    # 网络断了也会"秒返回成功"，于是这个自检就成了摆设（第一次写这个脚本时真踩到了）。
    # 所以拿一个 缓存TTL秒=0 的副本去真发一次请求。
    探针客户端 = TMDB客户端(dataclasses.replace(配置, 缓存TTL秒=0))
    try:
        探针客户端.搜电影("沙丘", 2021)
    except 请求失败 as 错:
        print("❌ 连不上 TMDB（或凭据不对），真网探测无法进行。原样原因如下：\n"
              f"   {错}")
        print("   提示：本机实测**直连到不了 api.themoviedb.org**，要走本机代理 —— "
              "数据/刮削.json 的 tmdb.proxy（例如 socks5://127.0.0.1:10808）或 V2_TMDB_PROXY。")
        print("   自检：运行环境/venv/bin/python 工具/检查刮削凭据.py")
        if not 参数.允许离线:
            return 3
        print("   （--允许离线：继续跑，但只能吃落盘缓存里的旧结果）")
    finally:
        探针客户端.关闭()

    别名表 = 读别名表(项目根 / 参数.别名库)
    if 别名表:
        print(f"别名库：{参数.别名库}（{len(别名表)} 条，命中会给最高权重）")
    else:
        print(f"别名库：{参数.别名库}（还没有 / 是空的 —— P4 学习层落地后这里会有东西）")
    缓存路径 = None if 参数.不落盘 else 默认缓存路径
    if 缓存路径:
        print(f"落盘缓存：{缓存路径}（键里带日期，跨天自动失效）")
    缓存 = 检索缓存(落盘=缓存路径)

    全部结果 = []
    拿到候选的样本数 = 0
    for 序号, 样本 in enumerate(样本们, start=1):
        打样本(序号, len(样本们), 样本)
        try:
            结构, 来源 = 取结构(样本.文件名, 样本.目录链, 模式=参数.结构)
        except Exception as 异常:                               # noqa: BLE001
            print(f"❌ 结构抽取失败（--结构 P1）：{type(异常).__name__}: {异常}")
            return 1
        打结构(结构, 来源)

        查询集 = 生成查询(结构, 目录链=样本.目录链, 别名表=别名表)
        打查询(查询集)

        结果 = 检索(客户端, 查询集, 查询集.类型, 过滤年份=查询集.年份, 缓存=缓存)
        打命中(结果, 参数.每查询条数)
        打合并(结果, 样本)
        全部结果.append({"样本": {"目录链": list(样本.目录链), "文件名": 样本.文件名},
                      "结构": {"标题": getattr(结构, "标题", ""),
                             "年份": getattr(结构, "年份", None),
                             "类型": getattr(结构, "类型", ""),
                             "季": getattr(结构, "季", None),
                             "集": getattr(结构, "集", None),
                             "来源": 来源},
                      "查询词们": [{"文本": x.文本, "来源": x.来源, "权重": x.权重,
                                 "语言": x.语言, "备注": x.备注}
                                for x in 查询集.查询词们],
                      "候选们": [x.到字典() for x in 结果.候选们],
                      "命中们": [{"查询词": h.查询词, "类型": h.类型, "错误": h.错误,
                                "来自缓存": h.来自缓存, "年份回退": h.年份回退,
                                "条数": len(h.候选们)} for h in 结果.命中们],
                      "错误们": [e.可读() for e in 结果.错误们],
                      "请求次数": 结果.请求次数, "缓存命中": 结果.缓存命中})
        if 结果.候选们:
            拿到候选的样本数 += 1

    客户端.关闭()
    打标题("汇总")
    print(f"样本 {len(样本们)} 个：拿到候选的 {拿到候选的样本数} 个")
    print(f"缓存统计：{缓存.统计()}")
    for 项, 结果 in zip(样本们, 全部结果):
        最佳 = 结果["候选们"][0] if 结果["候选们"] else None
        尾巴 = (f"[{最佳['类型']}#{最佳['标识']}] {最佳['标题']}"
                f"（{最佳['年份']}）｜来源：{'、'.join(最佳['来源'])}") if 最佳 else "（零候选）"
        print(f"   {项.文件名[:46]:48} → {尾巴}")
    if 参数.json:
        落点 = Path(参数.json)
        落点.parent.mkdir(parents=True, exist_ok=True)
        落点.write_text(json.dumps({"样本们": 全部结果}, ensure_ascii=False, indent=1),
                      encoding="utf-8")
        print(f"（原始结果已写入 {落点}）")
    if 拿到候选的样本数 == 0:
        print("\n❌ 所有样本都零候选：看上面每个查询词后面的错误原因（多半是网络/代理）。")
        return 3
    print("\n✅ 真网探测完成（有错误的样本请看上面逐条打印的原因）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
