#!/usr/bin/env python3
"""②真机验收：弹幕按**资料库身份**取（拿光鸭那部被改名的剧真跑一遍）。

用户原话
========
「获取弹幕应该通过刮削来获取准确的信息来获取对应的弹幕（因为用户会乱放文件夹乱改名，
所以文件名不可信）」。

真机素材（光鸭目录 ``/来自：分享/仙逆.4K高码.SDR.60fps``）：那一集的文件名是
``146.SDR.8bit.2160p.60fps.AAC2.0.WEB-DL.H265.mp4``（另一集更离谱：
``Renegade.Immortal.S01E138…mkv``）。目录名不带集号、文件名里既没有剧名、还有个
**不一定等于真实集号的数字**。

这个脚本怎么验
==============
1. **真刮削**：真光鸭列目录 → 真 TMDB 识别 → 落进**临时库**（拿 tv#223911 仙逆）；
2. **用库里的身份取弹幕**：``danmaku.库身份.造素材``（资料库优先）→ 真 Animeko 源
   （+ 真弹弹play 源，本机没配 AppId 会 403，脚本如实打出来）→ 打印
   **搜到的作品名 / 集号 / 弹幕条数**；
3. **对比"用文件名搜"**：照**改造前**播放页的做法造素材（只给文件名，不查库）→ 同样跑一遍
   → 打印它搜到的是哪部、多少分、多少条 —— 应当明显不是同一部片子（或压根搜不到）。

跑完**恢复现场**：临时库/临时弹幕缓存/临时学习库全删；``数据/识别检索缓存.json``
跑前备份跑完还原；**用户的资料库与弹幕记忆一个字节都不动**（脚本用的是临时库）。

凭据自检（这一步不能省）
========================
"取到 0 条"有**两种**完全不同的原因：①这部片子源上真没有弹幕；②凭据无效/被轮换。
分不清就没法验收。所以脚本开头会**单独打一次** ``GET /api/v2/search/episodes``，
拿 ``数据/弹幕源.json`` 里配的那把密钥 + 用户令牌文件里的另一把**各签一次**，
把服务端的 ``X-Error-Message`` 原文、HTTP 状态码、以及"哪一把有效"直接打出来
（**只打密钥长度和首尾各 2 个字符，绝不打密钥本身**）。发现"另一把才有效"时，
这次运行会临时用有效的那把跑通，**但不会替你改配置文件**。

跑法::

    运行环境/venv/bin/python 工具/弹幕真机验证.py
    运行环境/venv/bin/python 工具/弹幕真机验证.py --目录 /来自：分享/仙逆.4K高码.SDR.60fps

退出码：``0`` 弹弹play 真的返回了弹幕（全过）；
``2`` 凭据/登录问题；``3`` 连不上；``5`` 断言失败；
``4`` **其余都对、但弹弹play 一条都没取到**（凭据在 DevCenter 侧无效）——
这个码专门用来防止"弹弹play 拿不到弹幕却报绿"。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

项目根 = Path(__file__).resolve().parents[1]
if str(项目根) not in sys.path:
    sys.path.insert(0, str(项目根))

from wangpan.控制台 import 修控制台                                        # noqa: E402

修控制台()

默认目录 = "/来自：分享/仙逆.4K高码.SDR.60fps"
默认网盘 = "guangya"
#: 真机上被改名的那个文件（脚本按它找"库里那一集"）
目标文件名 = "146.SDR.8bit.2160p.60fps.DDP5.1.WEB-DL.H265.mp4"
#: 同目录里另一个"乱改名"的文件。为什么还要它：Animeko 公益源上**部分集压根没有弹幕
#: 数据**（实测：仙逆 146 集 0 条、131 集 1 条），拿 146 一集证明不了"取到了弹幕"。
备选文件名 = "131.SDR.8bit.2160p.60fps.AAC2.0.WEB-DL.H265.mp4"
#: 用户的令牌文件（DevCenter 导出）。**只用来取"备选密钥"做配对试验**；
#: 它的内容（含密钥）绝不打印、绝不写进项目里的任何文件。
#: 路径**不写死本机家目录**（别人机器上没有 /home/<用户名>；也有测试盯着这件事）：
#: 默认取"项目根的同级目录"下的 ``令牌/``（本机就是那样摆的），可用
#: 环境变量 ``V2_弹弹令牌`` 或命令行 ``--令牌`` 覆盖。
默认令牌文件名 = "弹弹密钥.txt"


def 默认令牌路径() -> Path:
    """令牌文件的默认位置：``<项目根的上一级>/令牌/弹弹密钥.txt``（环境变量可覆盖）。"""
    改的 = str(os.environ.get("V2_弹弹令牌") or "").strip()
    if 改的:
        return Path(改的).expanduser()
    return 项目根.parent / "令牌" / 默认令牌文件名


def 打印(文本: str = "") -> None:
    print(文本, flush=True)


class _断言失败(Exception):
    pass


def _断言(条件, 说明: str) -> None:
    if not 条件:
        raise _断言失败(说明)


class 现场保护:
    """跑完恢复：检索缓存文件 + ``V2_识别学习库`` 环境变量。"""

    def __init__(self, 临时根: Path) -> None:
        self.临时根 = Path(临时根)
        self.缓存文件 = 项目根 / "数据" / "识别检索缓存.json"
        self._缓存原样: bytes | None = None
        self._学习变量原样: str | None = None

    def 备份(self) -> None:
        self.临时根.mkdir(parents=True, exist_ok=True)
        if self.缓存文件.is_file():
            self._缓存原样 = self.缓存文件.read_bytes()
        self._学习变量原样 = os.environ.get("V2_识别学习库")
        os.environ["V2_识别学习库"] = str(self.临时根 / "识别学习.json")

    def 恢复(self) -> None:
        if self._学习变量原样 is None:
            os.environ.pop("V2_识别学习库", None)
        else:
            os.environ["V2_识别学习库"] = self._学习变量原样
        try:
            if self._缓存原样 is None:
                self.缓存文件.unlink(missing_ok=True)
            else:
                self.缓存文件.write_bytes(self._缓存原样)
        except OSError as 错:                                                # noqa: BLE001
            打印(f"⚠️ 检索缓存恢复失败（请手工检查 {self.缓存文件}）：{错}")


def _备选密钥们(令牌路径: Path) -> list[str]:
    """从用户的令牌文件里读"应用密钥 1 / 应用密钥 2"（**只返回，不打印**）。

    为什么需要两把：DevCenter 给每个应用发两把密钥，轮换过就只剩一把能用。
    官方错误表里 ``Invalid AppId``（签名模式下）= "AppId **或** AppSecret 无效"，
    服务端**不告诉你是哪一把**，所以只能拿两把各试一次来定位。
    """
    import re
    if not 令牌路径.is_file():
        return []
    try:
        文本 = 令牌路径.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return re.findall(r"^([0-9A-Za-z]{16,64})$", 文本, re.M)


def dandanplay鉴权自检(app_id: str, 配置里的密钥: str,
                   令牌路径: Optional[Path] = None) -> dict:
    """**真发一次请求**，把"凭据到底有没有生效"这件事钉死（不掺进后面的弹幕流程）。

    为什么要单独做：签名/时间戳/凭据三类错误都回 403，只看"取到 0 条弹幕"分不清
    "凭据无效"和"这部片子真没弹幕"。这里用固定路径 ``/api/v2/search/episodes``
    只打一次，把服务端的 ``X-Error-Message`` 原文和 HTTP 状态码都打出来。

    返回 ``{"好": bool, "说明": str}``；**任何情况下都不打印密钥本身**，
    只用 ``len()`` 和首尾各 2 个字符标识是哪一把。
    """
    from wangpan.danmaku.源.弹弹play import (官方基地址, 接口_搜索, 鉴权错误说明,
                                            算签名)
    from wangpan.danmaku.源.接口 import 请求, urlopen传输
    import urllib.parse

    打印("\n—— 弹弹play 鉴权自检（只打一次搜索接口，专门看凭据生不生效）——")
    if not app_id:
        打印("    ❌ 没读到 AppId（数据/弹幕源.json 的 dandanplay.appId 是空的）")
        return {"好": False, "说明": "没配 AppId"}
    # AppId 也算半份凭据（单靠它签不了名，但没必要往日志里抄）：只显示首尾各 2 字符。
    打印(f"    AppId：{app_id[:2]}…{app_id[-2:]}（共 {len(app_id)} 字符，来自 数据/弹幕源.json）")

    候选: list[tuple[str, str]] = []
    令牌里的 = _备选密钥们(Path(令牌路径) if 令牌路径 else 默认令牌路径())
    if 配置里的密钥:
        哪一把 = ""
        if 配置里的密钥 in 令牌里的:
            哪一把 = f"（和令牌文件第 {令牌里的.index(配置里的密钥) + 1} 把是同一把）"
        候选.append((配置里的密钥, f"数据/弹幕源.json 里配的那把{哪一把}"))
    已有 = {配置里的密钥}
    for 序, 密钥 in enumerate(令牌里的, 1):
        if 密钥 not in 已有:
            候选.append((密钥, f"令牌文件第 {序} 把（配置里没配的那把）"))
            已有.add(密钥)
    if not 候选:
        打印("    ❌ 一把 AppSecret 都没有（数据/弹幕源.json 和令牌文件都没读到）")
        return {"好": False, "说明": "没配 AppSecret"}

    时刻 = int(time.time())
    路径 = 接口_搜索
    好密钥 = ""
    最后说明 = ""
    for 密钥, 来历 in 候选:
        头 = {"User-Agent": "wangpan-v2/2.0 (danmaku)", "Accept": "application/json",
              "X-AppId": app_id, "X-Timestamp": str(时刻),
              "X-Signature": 算签名(app_id, 时刻, 路径, 密钥)}
        地址 = 官方基地址 + 路径 + "?anime=" + urllib.parse.quote("仙逆")
        try:
            应答 = urlopen传输(请求(方法="GET", 地址=地址, 头=头, 超时=20.0))
        except Exception as 错:                                                # noqa: BLE001
            最后说明 = f"{type(错).__name__}: {错}"
            打印(f"    ⚠️ {来历}（{len(密钥)} 字符，{密钥[:2]}…{密钥[-2:]}）：连不上 —— {最后说明}")
            continue
        服务端 = str((应答.头 or {}).get("X-Error-Message")
                  or (应答.头 or {}).get("x-error-message") or "")
        标识 = f"{来历}（{len(密钥)} 字符，{密钥[:2]}…{密钥[-2:]}）"
        if 应答.状态码 == 200:
            好密钥 = 密钥
            最后说明 = ""
            打印(f"    ✅ {标识}：HTTP 200 —— 这把密钥**有效**（服务端接受了签名）")
            break
        最后说明 = 鉴权错误说明(服务端, 有凭证=True) or f"HTTP {应答.状态码}"
        打印(f"    ❌ {标识}：HTTP {应答.状态码}｜{最后说明}")

    # 对照实验：拿一把**故意写错**的密钥签名。
    # 目的：证明"Invalid AppId 是认证先过、再查库"——错密钥真参与校验，
    # 所以 AppId/密钥配对失败和 AppId 本身不存在，服务端回的是同一个分类。
    对照头 = {"User-Agent": "wangpan-v2/2.0 (danmaku)", "X-AppId": app_id,
            "X-Timestamp": str(时刻),
            "X-Signature": 算签名(app_id, 时刻, 路径, "对照组-故意写错的密钥-0" * 2)}
    try:
        对照 = urlopen传输(请求(方法="GET", 地址=地址, 头=对照头, 超时=20.0))
        打印(f"    对照（故意写错的密钥）：HTTP {对照.状态码}｜"
            + str((对照.头 or {}).get("X-Error-Message") or ""))
    except Exception as 错:                                                    # noqa: BLE001
        打印(f"    对照（故意写错的密钥）：连不上 —— {type(错).__name__}")

    if 好密钥:
        return {"好": True, "说明": "凭据有效", "密钥": 好密钥}
    打印("    ⚠️ 结论：**本机上没有任何一把密钥能让这个 AppId 通过鉴权** —— "
        "不是签名算法的问题（算法已被服务端认可），是这套凭据在 DevCenter 侧的状态问题。")
    打印("       请在 https://dev.dandanplay.com 【管理中心 → 应用管理】核对："
        "①这个 AppId 还在不在 ②应用是不是「已上线」（审核中/已下线都用不了）"
        "③把**当前生效**的那把密钥重新抄进 数据/弹幕源.json。")
    return {"好": False, "说明": 最后说明}


def _取参数() -> argparse.Namespace:
    解析 = argparse.ArgumentParser(description="②真机验收：弹幕按资料库身份取")
    解析.add_argument("--目录", default=默认目录, help=f"光鸭目录（默认 {默认目录}）")
    解析.add_argument("--网盘", default=默认网盘, help="网盘标识（默认 guangya）")
    解析.add_argument("--文件名", default=目标文件名, help="被改名的那个文件")
    解析.add_argument("--文件名2", default=备选文件名,
                    help="同一目录里另一个被改名的文件（源那边这一集有弹幕数据，"
                         "用来证明「真的取到了 N 条」）")
    解析.add_argument("--令牌", default="",
                    help="DevCenter 导出的令牌文件（用来取「另一把」密钥做配对试验）；"
                         f"默认 {默认令牌路径()}，也可用环境变量 V2_弹弹令牌")
    解析.add_argument("--保留现场", action="store_true", help="跑完不删临时目录")
    return 解析.parse_args()


def main() -> int:
    参数 = _取参数()
    临时根 = Path(tempfile.mkdtemp(prefix="v2弹幕真机_"))
    保护 = 现场保护(临时根)
    保护.备份()
    打印(f"临时目录：{临时根}（临时库/弹幕缓存/学习库都在这里，跑完删）")
    try:
        return _跑(参数, 临时根)
    except _断言失败 as 错:
        打印(f"\n❌ 断言失败：{错}")
        return 5
    finally:
        保护.恢复()
        if not 参数.保留现场:
            shutil.rmtree(临时根, ignore_errors=True)
            打印(f"（已清理 {临时根}；检索缓存/学习库已还原）")
        else:
            打印(f"（--保留现场：{临时根}）")


def _造源(临时根: Path, 日志行: list[str], 密钥: str = ""):
    """真弹幕源：Animeko（公开、可用）+ 弹弹play（凭据有效才签得上，否则 403 如实打出来）。

    ``密钥`` 非空时用它顶掉数据/弹幕源.json 里配的那把（鉴权自检发现"另一把才有效"时，
    这次运行就先用有效的那把跑通，**但不会自动改配置文件** —— 改配置是用户的决定）。

    缓存全部落在临时目录（默认缓存目录在 ``数据/缓存/弹幕`` 下，不该让验收脚本往里写东西）。
    """
    from wangpan.danmaku.源.animeko import Animeko源
    from wangpan.danmaku.源.弹弹play import 弹弹play源
    改动 = {"app_secret": 密钥} if 密钥 else {}
    源们 = [
        Animeko源(缓存目录=临时根 / "缓存" / "animeko", 缓存TTL秒=0,
                 最小请求间隔秒=0.25, 日志回调=日志行.append),
        弹弹play源(读环境=True, 缓存目录=临时根 / "缓存" / "dandanplay",
                缓存TTL秒=0, 最小请求间隔秒=0.5, **改动),
    ]
    return 源们


def _取弹幕(素材, 源们, 标签: str) -> dict:
    """跑一遍"匹配 → 决策 → 取弹幕"，把过程与结论都打出来（失败不抛，返回结果供断言）。"""
    from wangpan.danmaku.匹配 import 找弹幕, 取采纳的弹幕
    from wangpan.danmaku.源.接口 import 弹幕源错误

    打印(f"\n—— {标签} ——")
    打印(f"    素材：{素材.文件名}｜标题「{素材.标题 or '(空)'}」｜"
        f"季 {素材.季号}｜集 {素材.集号}｜来自资料库={素材.来自资料库}")
    决策 = 找弹幕(素材, 源们)
    打印(f"    说明：{决策.说明}")
    for 候 in (决策.候选们 or [])[:3]:
        打印(f"    候选：{候.可读()}")
    出 = {"决策": 决策, "候选数": len(决策.候选们 or []), "采纳": 决策.采纳,
         "条数": 0, "池": None, "错": ""}
    if 决策.采纳 is None:
        打印("    → 没有自动采纳（要人工确认）→ 拿不到确定的弹幕")
        return 出
    打印(f"    → 采纳：[{决策.采纳.标识}] 《{决策.采纳.标题}》"
        f" 集号 {决策.采纳.集号 or '-'} 集标题「{决策.采纳.集标题 or ''}」"
        f"（{决策.采纳.分数:.1f} 分）")
    try:
        池 = 取采纳的弹幕(决策, 源们)
    except 弹幕源错误 as 错:
        出["错"] = str(错)
        打印(f"    → 取弹幕失败：{错}")
        return 出
    出["池"], 出["条数"] = 池, len(池)
    打印(f"    → **取到 {len(池)} 条弹幕**（{池.来源说明}）")
    # **按源拆开各取一次**：合并后的总数分不清"是弹弹play给的还是 Animeko 给的"。
    # 用户要的正是"弹弹play 到底出没出弹幕"，所以每个源单独打一次（缓存已关，
    # 这一遍会真发请求；弹弹play 的 0 条/报错都原样打出来，不粉饰）。
    出["分源"] = _分源取弹幕(决策, 源们)
    return 出


def _分源取弹幕(决策, 源们) -> list[tuple[str, str, str, int]]:
    """逐个源取**被采纳的那一集**，返回 ``[(源名, 依据, 作品名, 条数)]``。

    为什么值得多打几次接口：合并池的 ``来源说明`` 只写"哪个源答的"，一旦某个源失败
    就彻底看不出来；验收要的结论是"弹弹play 真的返回了 N 条"，必须单独摆出来。
    单集缓存已关（``缓存TTL秒=0``），所以这里取的就是源上**此刻**的真实条数。
    """
    from wangpan.danmaku.源.接口 import 弹幕源错误
    行: list[tuple[str, str, str, int]] = []
    采纳 = 决策.采纳
    if 采纳 is None:
        return 行
    for 源 in 源们:
        依据 = f"tmdb:{采纳.标识}"
        try:
            池 = 源.取弹幕(采纳.标识)
            行.append((源.名字, 依据, 采纳.标题, len(池)))
            打印(f"    · 按源拆开｜{源.名字}：episodeId={采纳.标识} → "
                f"《{采纳.标题}》 第 {采纳.集号 or '-'} 集 → **{len(池)} 条**"
                + (f"（{池.来源说明}）" if len(池) else "（这个源没有这一集的弹幕）"))
        except 弹幕源错误 as 错:
            行.append((源.名字, 依据, 采纳.标题, -1))
            打印(f"    · 按源拆开｜{源.名字}：取弹幕失败 —— {错}")
        except Exception as 错:                                                # noqa: BLE001
            行.append((源.名字, 依据, 采纳.标题, -1))
            打印(f"    · 按源拆开｜{源.名字}：异常 —— {type(错).__name__}: {错}")
    return 行


def _跑(参数, 临时根: Path) -> int:
    from wangpan.pan.光鸭 import 光鸭适配器
    from wangpan.scrape.库 import 资料库
    from wangpan.scrape.服务 import 刮削服务, 刮削设置
    from wangpan.scrape.扫描 import 造远端单元
    from wangpan.scrape.tmdb import TMDB客户端, TMDB配置
    from wangpan.scrape.图片 import 图片缓存
    from wangpan.danmaku.库身份 import 造素材
    from wangpan.danmaku.源.接口 import 素材信息
    from wangpan.danmaku.源.弹弹play import 读凭证

    # ---- 0) 先自检凭据：分不清"凭据坏了"和"这集真没弹幕"就没法验收 ----
    app_id, 配置密钥, 来源 = 读凭证()
    自检 = dandanplay鉴权自检(app_id, 配置密钥,
                        令牌路径=Path(参数.令牌) if 参数.令牌 else None)
    # 配置里那把不行、令牌文件里另一把行 → 这次运行先用行的那把（顺手把结论打出来）
    有效密钥 = str(自检.get("密钥") or "")
    if 有效密钥 and 配置密钥 and 有效密钥 != 配置密钥:
        打印("    ℹ️ 数据/弹幕源.json 里配的那把**不是**当前生效的；这次运行临时用有效的"
            "那把跑。要长期生效请把 数据/弹幕源.json 的 dandanplay.appSecret 换成它"
            "（脚本不替你改凭据文件）。")
    if 自检["好"] and 配置密钥 and 有效密钥 == 配置密钥:
        打印(f"    （凭据来源：{来源}）")

    # ---- 0) 真 TMDB + 真光鸭 ----
    配置 = TMDB配置.从环境与配置()
    if not 配置.可用():
        打印("❌ 没配 TMDB 凭据")
        return 2
    客户端 = TMDB客户端(配置)
    适配器 = 光鸭适配器()
    状态 = 适配器.登录状态()
    if not 状态.get("已登录"):
        打印("❌ 光鸭没登录")
        return 2
    打印(f"光鸭：已登录（令牌剩余 {float(状态.get('过期剩余秒') or 0) / 60:.0f} 分钟）")

    try:
        条目们 = 适配器.列出(参数.目录)
    except Exception as 错:                                                  # noqa: BLE001
        打印(f"❌ 列目录失败：{type(错).__name__}: {错}")
        return 3
    文件们 = [str(x.路径) for x in 条目们 if not x.是目录]
    打印(f"\n① 列目录：{参数.目录} → {len(文件们)} 个文件")
    if not 文件们:
        打印("❌ 目录里没有文件")
        return 3
    目标 = next((x for x in 文件们 if Path(x).name == 参数.文件名), None)
    _断言(目标 is not None,
         f"目录里没有 {参数.文件名}（用 --文件名 指定；现有："
         f"{[Path(x).name for x in 文件们[:3]]}…）")
    打印(f"    目标（被改名的那个）：{Path(目标).name}")

    # ---- 1) 真刮削进**临时库** ----
    单元们 = 造远端单元(参数.网盘, 参数.目录, 文件们)
    分组: dict = {}
    for 单元 in 单元们:
        分组.setdefault(str(单元.路径), []).append(单元)
    库 = 资料库(临时根 / "资料库.db")
    日志行: list[str] = []
    服务 = 刮削服务(库, 客户端, 图片缓存(临时根 / "图缓存"),
                刮削设置(最小文件字节=0, 本批上限=50, 下载图片=False),
                日志回调=日志行.append, 远端单元=分组)
    打印("\n② 真刮削（写进临时库）：识别 → 取详情 → 挂文件 → 落库")
    try:
        服务.记远端单元(单元们)
        结果 = 服务.续跑()
    finally:
        服务.关闭()
    打印(f"    {结果.摘要()}")
    _断言(结果.成功 >= 1 and not 结果.失败, f"刮削没成功：{结果.摘要()}｜{结果.错误们}")

    # ---- 2) 库里那条记录 ----
    行们 = 库.列表()
    _断言(len(行们) == 1, f"库里应当只有 1 部作品，实际 {len(行们)}")
    条目 = 库.取媒体(int(行们[0]["id"]))
    打印("\n③ 库里那条记录：")
    打印(f"    标题《{条目.标题}》｜原名《{条目.原名}》｜类型 {条目.类型.value}｜"
        f"年份 {条目.年份}｜TMDB {条目.外部ID.get('tmdb')}")
    库里标题 = str(条目.标题 or "")
    _断言(bool(库里标题), "库里没有标题（刮削没成功？）")

    def 库里的集(文件名: str):
        行 = 库.按路径找集(f"{参数.网盘}:{参数.目录.rstrip('/')}/{文件名}")
        if 行 is None:
            行 = 库.按路径找集(f"{参数.网盘}:{参数.目录}/{文件名}")
        return 行

    目标们 = [参数.文件名, 参数.文件名2]
    源们 = _造源(临时根, 日志行, 密钥=有效密钥)

    # ---- 3) 用**库里的身份**取弹幕（每个被改名的文件各来一遍）----
    打印("\n④ 用资料库身份取弹幕")
    库结果们: list[dict] = []
    for 名字 in 目标们:
        全路径 = next((x for x in 文件们 if Path(x).name == 名字), None)
        _断言(全路径 is not None, f"目录里没有 {名字}（用 --文件名/--文件名2 指定）")
        集行 = 库里的集(名字)
        _断言(集行 is not None, f"库里没找到这个文件的集行：{名字}")
        素材库, 说明 = 造素材(None, 文件名=名字, 远端路径=全路径,
                            网盘标识=参数.网盘, 资料库=库, 日志回调=日志行.append)
        _断言(素材库.来自资料库, f"没有用上资料库身份：{说明}")
        _断言(素材库.标题 == 库里标题,
             f"素材标题应当来自库：{素材库.标题} ≠ {库里标题}")
        _断言(素材库.集号 == int(集行["集号"]),
             f"集号必须取库里的：{素材库.集号} ≠ {集行['集号']}")
        打印(f"\n  【{名字}】库里 = 《{库里标题}》 季 {集行['季号']} 集 {集行['集号']}"
            f"（集标题「{集行['标题'] or ''}」）")
        打印(f"    {说明}")
        出 = _取弹幕(素材库, 源们, f"用资料库身份取弹幕（{名字}）")
        出["库集号"] = int(集行["集号"])
        出["文件名"] = 名字
        库结果们.append(出)

    # ---- 4) 对比：**改造前**那种"用文件名搜" ----
    # 改造前播放页只给文件名（网盘直链没有本地路径，连父目录名都拿不到），
    # 标题就是文件主干 —— 照原样造一份来对比。
    素材名 = 素材信息(文件名=参数.文件名, 标题=Path(参数.文件名).stem, 集号=None,
                   来自资料库=False)
    名结果 = _取弹幕(素材名, 源们, "用文件名搜（改造前的做法）")

    # ---- 5) 结论 ----
    打印("\n⑤ 对比结论")
    采纳名 = 名结果.get("采纳")
    for 出 in 库结果们:
        采纳 = 出.get("采纳")
        打印(f"    资料库身份（{出['文件名']}）："
            f"{('《%s》 集 %s' % (采纳.标题, 采纳.集号 or '-')) if 采纳 else '没采纳'}"
            f"｜弹幕 {出['条数']} 条")
        for 源名, 依据, 标题, 条 in (出.get("分源") or []):
            打印(f"        · {源名}："
                + (f"《{标题}》 → **{条} 条**" if 条 >= 0 else "取弹幕失败（见上文）"))
    打印(f"    只看文件名（{参数.文件名}）："
        f"{('《%s》 集 %s' % (采纳名.标题, 采纳名.集号 or '-')) if 采纳名 else '没采纳/搜不到'}"
        f"｜弹幕 {名结果['条数']} 条")

    # 断言 0：**弹弹play 必须真的出弹幕** —— 这是本次验收的核心，不许含糊过去。
    # 出不来就**红着退出**（返回 4）并把原因和"用户该做什么"写清楚，绝不假装通过。
    弹弹play条数 = [条 for 出 in 库结果们 for 源名, _, _, 条 in (出.get("分源") or [])
                if "弹弹play" in 源名]
    弹弹play好 = [条 for 条 in 弹弹play条数 if 条 > 0]

    # 断言 1：每个文件都被采纳到"同一部作品 + 库里的那一集"
    for 出 in 库结果们:
        采纳 = 出.get("采纳")
        _断言(采纳 is not None,
             f"用库身份没采纳（{出['文件名']}）：{出['决策'].说明}｜{出['错']}")
        _断言(str(出["库集号"]) == str(采纳.集号),
             f"采纳的集号与库里不一致（{出['文件名']}）：{采纳.集号} ≠ {出['库集号']}")
        _断言(库里标题 in (采纳.标题 or "") or (采纳.标题 or "") in 库里标题,
             f"采纳的不是这部作品（{出['文件名']}）：《{采纳.标题}》 vs 库里《{库里标题}》")
    # 断言 2：至少有一集真的从源上取到了弹幕（证明整条路是通的）
    有弹幕 = [x for x in 库结果们 if x["条数"] > 0]
    _断言(bool(有弹幕),
         "库身份这条路一条弹幕都没取到：" + "；".join(
             f"{x['文件名']}→{x['决策'].说明 or x['错']}" for x in 库结果们))
    # 断言 3：只看文件名那条路**明显不是同一部片子**（或压根搜不到）
    名字同名 = bool(采纳名) and (采纳名.标题 or "") == 库里标题
    _断言(not 名字同名,
         f"用文件名竟然也搜到了同一部《{库里标题}》—— 换个 --文件名 再验")

    打印("\n" + "=" * 78)
    if 弹弹play好:
        打印(f"✅ ②真机验收通过（弹弹play 真的出弹幕了）：库里《{库里标题}》的 "
            + "、".join(f"S{x['决策'].采纳.集号}" for x in 库结果们)
            + " → 弹弹play 取到 " + "、".join(f"{条} 条" for 条 in 弹弹play好)
            + f"（合并后 " + "、".join(f"{x['条数']} 条" for x in 库结果们) + "）")
    else:
        打印("⚠️ ②真机验收**未通过（弹弹play 这条路）**：")
        打印(f"    · 资料库身份这条路是通的：库里《{库里标题}》的 "
            + "、".join(f"S{x['决策'].采纳.集号}" for x in 库结果们)
            + " 都采纳到了正确的作品与集号，弹幕由 Animeko 提供 "
            + "、".join(f"{x['条数']} 条" for x in 库结果们))
        打印(f"    · 但**弹弹play 一条都没取到**（每集实测："
            + "、".join("取弹幕失败" if 条 < 0 else f"{条} 条" for 条 in 弹弹play条数)
            + "）")
        打印(f"    · 原因：{自检['说明']}")
        打印("    · 请在 https://dev.dandanplay.com 【管理中心 → 应用管理】核对："
            "①AppId 是否还存在（可能已轮换）②应用是否「已上线」（审核中/已下线都调不通）"
            "③把当前生效的密钥抄进 数据/弹幕源.json 的 dandanplay.appSecret 后重跑本脚本。")
        打印("    · 说明：这条路**没有**放宽任何匹配门槛、也没有伪造条数 —— "
            "上面每个数字都是源上此刻的真实返回。")
    print("=" * 78)
    try:
        库.关闭()
    except Exception:                                                        # noqa: BLE001
        pass
    return 0 if 弹弹play好 else 4


if __name__ == "__main__":
    raise SystemExit(main())
