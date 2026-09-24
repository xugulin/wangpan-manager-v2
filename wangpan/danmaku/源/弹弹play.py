"""弹弹play 开放弹幕网络（官方协议，只读）。

协议事实（来源：官方开放平台文档 ``https://doc.dandanplay.net/open/``）与实现取舍
================================================================================

接口
----
* 基地址 ``https://api.dandanplay.net``；
* ``POST /api/v2/match``                    —— 按文件找节目（hash + 文件名）；
* ``GET  /api/v2/comment/{episodeId}``      —— 取某一集的弹幕；
* ``GET  /api/v2/search/episodes``          —— 按作品名/TMDB id 搜索（"搜错了，我自己找"，
  也是②"用刮削结果取弹幕"那条路：名字可能被译得不一样，TMDB id 不会）。

鉴权（签名验证模式）
--------------------
请求头三者缺一不可::

    X-AppId:     <AppId>
    X-Timestamp: <Unix 秒>
    X-Signature: base64(sha256(AppId + Timestamp + Path + AppSecret))

⚠️ **``Path`` 是接口路径部分**：以 ``/`` 开头、**不含域名、不含 ``?`` 之后的查询串**、
**不做 URL 编码**。这里踩过的坑就是拿整个 URL（带 ``?`` 和域名）去算签名，服务端算的
是路径，于是永远 401 —— 所以 :func:`纯接口路径` 单独抽出来并有单测钉住。

另有"凭证模式"（``X-AppId`` + ``X-AppSecret``），官方**只建议服务器端**用；
我们**不实现**，因为那等于把密钥放进每一个请求里、被中间人抄走就等于泄露。

AppId/AppSecret 的来源（**不许硬编码**）
----------------------------------------
1. 环境变量 ``V2_DANDANPLAY_APP_ID`` / ``V2_DANDANPLAY_APP_SECRET``；
2. 配置文件 ``数据/弹幕源.json``：``{"dandanplay": {"appId": "...", "appSecret": "..."}}``
   （也兼容把 appId/appSecret 直接写在顶层：手写配置时不容易写错）。
两者都没有 → **只发不带鉴权的请求**（``/match``、``/search``、``/comment`` 都是公开
接口，公开接口不需要鉴权），并把这件事写进日志。**secret 任何情况下都不进日志**。

受限接口
--------
**发弹幕是受限接口，官方明确"当前暂不开放"** → 本模块**只读**，不实现任何发送。

业务错误藏在 HTTP 200 里
------------------------
官方接口业务失败也回 200，成功与否看 ``success`` / ``errorCode``（``errorCode != 0``
即失败）。所以 :func:`检查业务状态` 必须在**每次**解析响应体之前跑一遍，光看状态码
会把"参数不合法"当成"这一集没弹幕"。

缓存与限流（官方建议：一般数据 2–6 小时；禁止批量下载）
--------------------------------------------------------
* :class:`弹弹play源` 有**按 episodeId 的磁盘缓存**（默认 6 小时，可配），命中不发请求；
* **同一个 episodeId 的并发请求合并成一次**（锁 + 在途表）：界面里"两处同时要同一集"
  很常见，不去重就会把对方的接口打成两倍流量（官方明确禁止批量下载）；
* 还带一个"最小请求间隔"节流（默认 0.3 秒，可设 0 关掉），照官方"结合用户实际操作
  调用"的意思来。

哈希
----
``fileHash`` = 文件**前 16MB**（``16*1024*1024`` 字节）的 32 位 MD5（小写）；
文件不足 16MB 就整个算。
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import urllib.parse
from base64 import b64encode
from dataclasses import dataclass, replace
from hashlib import md5, sha256
from pathlib import Path
from typing import Callable, Optional

from ..模型 import 弹幕, 弹幕池, 弹幕模式, 来源标识
from .接口 import (弹幕源, 弹幕源错误, 匹配结果, 素材信息, 请求,
                 传输函数, urlopen传输, 用户代理, 取整数)

__all__ = [
    "弹弹play源", "前16MB的MD5", "生成签名头", "纯接口路径", "解析条目", "解析响应",
    "解析匹配响应", "解析搜索响应", "检查业务状态", "读凭证", "默认缓存目录",
    "官方基地址", "接口_匹配", "接口_搜索", "接口_弹幕", "哈希字节数",
    "默认缓存TTL秒", "环境变量_APPID", "环境变量_密钥", "配置键",
]

日志 = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 官方基地址（协议事实）
官方基地址 = "https://api.dandanplay.net"
#: 按文件找节目
接口_匹配 = "/api/v2/match"
#: 某集的弹幕（后面接 episodeId）
接口_弹幕 = "/api/v2/comment/"
#: 按作品名/节目名搜索（**GET**，查询参数 ``anime`` / ``episode`` / ``tmdbId``）。
#: ⚠️ 原来写的是 ``POST /api/v2/search`` —— 那个路由在官方 v2 里**不存在**（实测 404，
#: 拿假传输的单测看不出来）。官方 Swagger（``https://api.dandanplay.net/swagger/v2/swagger.json``）
#: 里搜索只有 ``/api/v2/search/episodes``（GET，可按 anime 或 **tmdbId** 查）与
#: ``/api/v2/search/anime``（GET）。这里用前者：它一次就能给出"作品 + 那一集的
#: episodeId"，正是取弹幕要的东西。
接口_搜索 = "/api/v2/search/episodes"

#: fileHash 只算前 16MB（官方规定；别整文件读，几百 MB 的视频会白读一遍盘）
哈希字节数 = 16 * 1024 * 1024
#: 读文件算哈希时的块大小
读块字节数 = 1024 * 1024

#: 官方建议一般数据缓存 2–6 小时，取中间值 6 小时
默认缓存TTL秒 = 6 * 3600.0
#: 同一个 episodeId 并发请求合并时，等待者最多等多久（比请求超时多一点）
默认等待秒 = 60.0
#: 默认最小请求间隔（秒）；0 = 不节流。官方禁止批量下载，所以留一点间隔。
默认最小请求间隔秒 = 0.3

#: 凭证来源
环境变量_APPID = "V2_DANDANPLAY_APP_ID"
环境变量_密钥 = "V2_DANDANPLAY_APP_SECRET"
#: 配置文件 数据/弹幕源.json 里的段名
配置键 = "dandanplay"

#: 官方 p 字段里的模式号 → 我们的三种模式。**其它值（2/3/6/7…）一律丢弃**
模式号表 = {1: 弹幕模式.滚动, 4: 弹幕模式.底部, 5: 弹幕模式.顶部}

#: p 里"出现时间"的合法形状（官方：秒，保留两位小数）
时间模式 = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


# ---------------------------------------------------------------------------
# 凭证读取
# ---------------------------------------------------------------------------

def 默认配置路径() -> Path:
    """``数据/弹幕源.json``（和 V2 的其它配置放一起）。"""
    return Path(__file__).resolve().parents[3] / "数据" / "弹幕源.json"


def 默认缓存目录() -> Path:
    """弹幕缓存落盘的地方（``数据/弹幕缓存``）；可以整个删掉，只是下次要重下。"""
    return Path(__file__).resolve().parents[3] / "数据" / "弹幕缓存"


def 读凭证(配置路径: Optional[Path] = None,
         环境: Optional[dict] = None) -> tuple[str, str, str]:
    """读 AppId/AppSecret：**环境变量优先，其次配置文件，都没有就空**。

    返回 ``(app_id, app_secret, 来源说明)``；来源说明只写"从哪来的"，
    **绝不包含 secret 本身**（它会被写进日志）。
    """
    环境 = os.environ if 环境 is None else 环境
    app_id = str(环境.get(环境变量_APPID) or "").strip()
    密钥 = str(环境.get(环境变量_密钥) or "").strip()
    来源 = "环境变量" if (app_id or 密钥) else ""

    路径 = Path(配置路径) if 配置路径 is not None else 默认配置路径()
    if (not app_id or not 密钥) and 路径.is_file():
        try:
            数据 = json.loads(路径.read_text(encoding="utf-8"))
        except Exception as 错:  # noqa: BLE001  # 配置文件坏了不能拖垮播放
            日志.warning("弹幕源配置读不了（当作没配置）：%s：%s", 路径, 错)
            数据 = None
        if isinstance(数据, dict):
            段 = 数据.get(配置键)
            段 = 段 if isinstance(段, dict) else 数据      # 兼容写在顶层的写法
            配置文件_id = str(段.get("appId") or 段.get("app_id") or "").strip()
            配置文件_密钥 = str(段.get("appSecret") or 段.get("app_secret") or "").strip()
            if 配置文件_id or 配置文件_密钥:
                # 环境变量只覆盖它自己那一项：方便临时用环境变量顶掉配置里的 AppId
                app_id = app_id or 配置文件_id
                密钥 = 密钥 or 配置文件_密钥
                来源 = "环境变量+配置文件" if 来源 else f"配置文件 {路径.name}"
    return app_id, 密钥, 来源


# ---------------------------------------------------------------------------
# 签名
# ---------------------------------------------------------------------------

def 纯接口路径(路径: str) -> str:
    """取签名用的 ``Path``：**去域名、去查询串、去锚点**，保证以 ``/`` 开头。

    签名原文里的 Path 就是请求行里的那段路径。查询串**不参与**签名（否则
    ``/comment/1?withRelated=true`` 和 ``?withRelated=false`` 会是两个签名，
    而服务端只认前者），故这里按官方语义剥掉。
    """
    文本 = str(路径 or "").strip()
    if "://" in 文本:
        文本 = urllib.parse.urlsplit(文本).path
    else:
        文本 = 文本.split("#", 1)[0].split("?", 1)[0]
    if not 文本:
        return "/"
    return 文本 if 文本.startswith("/") else "/" + 文本


def 生成签名头(app_id: str, app_secret: str, 路径: str,
             时间戳: Optional[int] = None) -> dict:
    """``X-Signature = base64(sha256(AppId + Timestamp + Path + AppSecret))``。

    ⚠️ 拼接是**直接字符串相接**，中间没有任何分隔符、没有冒号；把时间戳写成
    ``str(时间戳)``（服务端按十进制文本拼）；``Path`` 见 :func:`纯接口路径`。
    返回的字典**只含 AppId/Timestamp/签名**，不含 secret。
    """
    时刻 = int(time.time() if 时间戳 is None else 时间戳)
    原文 = f"{app_id}{时刻}{纯接口路径(路径)}{app_secret}"
    签名 = b64encode(sha256(原文.encode("utf-8")).digest()).decode("ascii")
    return {"X-AppId": str(app_id), "X-Timestamp": str(时刻), "X-Signature": 签名}


# ---------------------------------------------------------------------------
# 哈希
# ---------------------------------------------------------------------------

def 前16MB的MD5(路径) -> str:
    """文件前 16MB 的 MD5（小写十六进制）；不足 16MB 就算整个文件。

    为什么只读前 16MB：官方 ``fileHash`` 的定义如此。整文件读一遍对几百 MB 的视频
    是纯浪费（还要等 IO），而且**结果反而对不上**官方给的期望值。
    """
    摘要 = md5()
    剩 = 哈希字节数
    with open(路径, "rb") as 文件:
        while 剩 > 0:
            块 = 文件.read(min(读块字节数, 剩))
            if not 块:
                break
            摘要.update(块)
            剩 -= len(块)
    return 摘要.hexdigest()


# ---------------------------------------------------------------------------
# 业务状态
# ---------------------------------------------------------------------------

def 检查业务状态(响应体: dict) -> None:
    """⚠️ **HTTP 200 里也可能是失败**：看 ``success`` / ``errorCode``。

    ``errorCode != 0`` 一律当失败（缺字段不算失败 —— ``/comment`` 的响应里压根
    没有这两个字段，只有 ``count``/``comments``）。
    """
    if not isinstance(响应体, dict):
        raise 弹幕源错误("接口返回的不是 JSON 对象")
    错误码 = 响应体.get("errorCode")
    错了 = 响应体.get("success") is False
    if 错误码 is not None:
        try:
            错了 = 错了 or int(错误码) != 0
        except (TypeError, ValueError):
            错了 = True
    if 错了:
        信息 = str(响应体.get("errorMessage") or "").strip()
        raise 弹幕源错误(f"接口报错 errorCode={错误码}"
                       + (f"：{信息}" if 信息 else ""))


# ---------------------------------------------------------------------------
# p 字段解析
# ---------------------------------------------------------------------------

def 解析条目(cid, p: str, m: str) -> Optional[弹幕]:
    """解析一条 ``{"cid":…,"p":"…","m":"…"}``；按官方规则**不合规就丢弃**（返回 None）。

    ``p`` 是 ``出现时间,模式,颜色,用户ID``：

    * 出现时间 = 秒（字符串，保留两位小数）；
    * 模式 ``1=普通(滚动) 4=底部 5=顶部``，**其它值全部丢弃**
      （2/3/6/7/8 是逆向、定位、代码弹幕之类，我们只支持三种）；
    * 颜色 = ``R*256*256 + G*256 + B``，直接就是 0xRRGGBB，不用转换；
    * 用户ID = 字符串（可能是空、可能是数字串，一律按文本存）。

    丢弃规则（严格照官方语义）：字段数 < 4、时间解析不出来、颜色解析不出来、
    模式不在 {1,4,5}。**注意** ``float('nan')`` / ``inf`` 能"解析成功"但一转 int 就
    抛异常（``int(round(nan))`` → ValueError），所以时间必须过 ``math.isfinite``。
    """
    段 = str(p or "").split(",")
    if len(段) < 4:
        return None
    时间文本 = 段[0].strip()
    # 先按官方形状校验，再用 float：这样 "nan"/"inf"/"1e5" 之类会被挡在外面
    if not 时间模式.match(时间文本):
        return None
    try:
        秒 = float(时间文本)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(秒):
        return None
    模式 = 模式号表.get(取整数(段[1], -1))
    if 模式 is None:
        return None
    颜色 = 取整数(段[2])
    if 颜色 is None or not (0 <= 颜色 <= 0xFFFFFF):
        return None
    return 弹幕(毫秒=int(round(秒 * 1000)), 文本=str(m if m is not None else ""),
              模式=模式, 颜色=颜色, 字号=25, 发送者=段[3].strip(),
              来源=来源标识.弹弹play, 标识=str(cid if cid is not None else ""))


def 解析响应(响应体: dict, 来源: 来源标识 = 来源标识.弹弹play) -> 弹幕池:
    """``/api/v2/comment/{id}`` 的响应 → :class:`~wangpan.danmaku.模型.弹幕池`。

    形如 ``{"count":int,"comments":[{"cid":…,"p":"…","m":"…"}]}``。
    ``count`` 只当参考（实测它会包含被我们丢弃的条目），列表以 comments 为准。
    """
    检查业务状态(响应体)
    条目们: list[弹幕] = []
    for 项 in (响应体.get("comments") or []):
        if not isinstance(项, dict):
            continue
        条 = 解析条目(项.get("cid"), 项.get("p"), 项.get("m"))
        if 条 is None:
            continue
        if 来源 is not 来源标识.弹弹play:
            条 = replace(条, 来源=来源)
        条目们.append(条)
    return 弹幕池(条目们, 来源说明=f"弹弹play {len(条目们)} 条")


def _首位数字(文本: str) -> str:
    """从 ``第1话`` / ``01`` / ``OVA`` 里抠出集号；抠不到给空串（**别硬编**）。"""
    匹配 = re.search(r"\d+", str(文本 or ""))
    return str(int(匹配.group())) if 匹配 else ""


def 解析匹配响应(响应体: dict, 素材: Optional[素材信息] = None) -> list[匹配结果]:
    """``/api/v2/match`` 的响应 → 候选列表；``isMatched=false`` 返回 ``[]``。

    ``isMatched=false`` 表示官方也拿不准（或压根没匹配上），此时**不猜**：不给候选，
    交给上层走"手动搜索"或者用本地文件。
    """
    检查业务状态(响应体)
    if not 响应体.get("isMatched"):
        return []
    结果: list[匹配结果] = []
    for 项 in (响应体.get("matches") or []):
        if not isinstance(项, dict):
            continue
        集标题 = str(项.get("episodeTitle") or "")
        标识 = 项.get("episodeId")
        if 标识 is None:
            continue
        try:
            偏移 = float(项.get("shift") or 0.0)
        except (TypeError, ValueError):
            偏移 = 0.0
        结果.append(匹配结果(
            标识=str(int(标识)) if not isinstance(标识, bool) else str(标识),
            标题=str(项.get("animeTitle") or ""), 集标题=集标题,
            集号=_首位数字(集标题), 类型=str(项.get("type") or ""),
            偏移秒=偏移, 原始=dict(项)))
    return 结果


def 解析搜索响应(响应体: dict, 集号: Optional[int] = None) -> list[匹配结果]:
    """``GET /api/v2/search/episodes`` 的响应 → 候选列表（按作品名/TMDB id 搜索用）。

    ``集号`` 给了且在这一部里找得到，就只留那一集；找不到就整部都放出来
    （用户自己挑比我们猜错强）。
    """
    检查业务状态(响应体)
    结果: list[匹配结果] = []
    for 部 in (响应体.get("animes") or []):
        if not isinstance(部, dict):
            continue
        标题 = str(部.get("animeTitle") or "")
        集们 = [集 for 集 in (部.get("episodes") or []) if isinstance(集, dict)]
        选中 = 集们
        if 集号 is not None:
            命中 = [集 for 集 in 集们 if _首位数字(集.get("episodeTitle")) == str(集号)]
            选中 = 命中 or 集们
        for 集 in 选中:
            标识 = 集.get("episodeId")
            if 标识 is None:
                continue
            集标题 = str(集.get("episodeTitle") or "")
            结果.append(匹配结果(
                标识=str(标识), 标题=标题, 集标题=集标题, 集号=_首位数字(集标题),
                类型=str(部.get("type") or ""),
                原始={"animeId": 部.get("animeId"), "episode": dict(集)}))
    return 结果


# ---------------------------------------------------------------------------
# 并发合并用的在途表
# ---------------------------------------------------------------------------

@dataclass
class _在途:
    """一次正在飞的请求：等的人拿同一个结果（**同一集只发一次**）。"""

    事件: threading.Event
    池: Optional[弹幕池] = None
    错: Optional[BaseException] = None


# ---------------------------------------------------------------------------
# 源
# ---------------------------------------------------------------------------

class 弹弹play源(弹幕源):
    """弹弹play 源（只读）。

    :param 传输: HTTP 传输函数（默认 urllib）。**测试必须注入假的**，否则打真接口。
    :param 缓存目录: 磁盘缓存目录；``None`` 用 :func:`默认缓存目录`。
    :param 缓存TTL秒: 缓存有效期；``<= 0`` 表示不用缓存。
    :param chConvert: 0=不转换、1=转简体、2=转繁体（默认 0：原样，用户要的是"原汁原味"）。
    :param 时钟: 取"现在"的函数（默认 ``time.time``）—— 注入它是为了测 TTL 过期。
    :param 睡眠: 节流用的 sleep（默认 ``time.sleep``），测试注入假的就不用真等。
    """

    标识 = "dandanplay"
    名字 = "弹弹play"

    def __init__(self, app_id: str = "", app_secret: str = "", *,
                 传输: Optional[传输函数] = None,
                 基地址: str = 官方基地址,
                 缓存目录: Optional[Path] = None,
                 缓存TTL秒: float = 默认缓存TTL秒,
                 chConvert: int = 0,
                 配置路径: Optional[Path] = None,
                 读环境: bool = True,
                 时钟: Optional[Callable[[], float]] = None,
                 睡眠: Optional[Callable[[float], None]] = None,
                 最小请求间隔秒: float = 默认最小请求间隔秒,
                 超时秒: float = 20.0,
                 读哈希: bool = True) -> None:
        if not app_id and not app_secret:
            app_id, app_secret, 来源 = 读凭证(
                配置路径, os.environ if 读环境 else {})
            self.凭证来源 = 来源
        else:
            self.凭证来源 = "调用方传入"
        self.app_id = str(app_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        self.基地址 = str(基地址 or 官方基地址).rstrip("/")
        self.chConvert = int(chConvert)
        self.超时秒 = float(超时秒 or 20.0)
        self.缓存TTL秒 = float(缓存TTL秒)
        self.缓存目录 = Path(缓存目录) if 缓存目录 is not None else 默认缓存目录()
        self.读哈希 = bool(读哈希)
        self.最小请求间隔秒 = max(0.0, float(最小请求间隔秒 or 0.0))
        self.传输: 传输函数 = 传输 or urlopen传输
        self._时钟 = 时钟 or time.time
        self._睡眠 = 睡眠 or time.sleep

        self._锁 = threading.Lock()
        self._在途: dict[str, _在途] = {}
        self._节流锁 = threading.Lock()
        self._上次请求: Optional[float] = None
        self._已关闭 = False
        #: 诊断用：真发出去的请求数（缓存命中不算）
        self.请求次数 = 0
        self.缓存命中次数 = 0

        if self.能签名():
            日志.info("弹弹play：已配置 AppId（来源：%s），使用**签名验证模式**",
                      self.凭证来源)
        elif self.app_id or self.app_secret:
            # 只配了一半：签名要求两个都在，拿半个去签只会得到 401，不如干脆不签
            日志.warning("弹弹play：AppId/AppSecret 只配了一半（%s/%s 要成对），"
                        "这次按**不带鉴权**的公开请求发；AppSecret 只从环境变量或"
                        " 数据/弹幕源.json 读，不会写进日志",
                        环境变量_APPID, 环境变量_密钥)
        else:
            日志.info("弹弹play：没有配置 %s/%s（环境变量或 数据/弹幕源.json），"
                     "只发**不带鉴权**的公开请求（/match、/search、/comment 都是"
                     "公开接口，不需要签名）", 环境变量_APPID, 环境变量_密钥)

    # ---- 鉴权准备 ----

    def 能签名(self) -> bool:
        """签名需要 AppId **和** AppSecret 两样；只有一半就当没有（别发坏请求）。"""
        return bool(self.app_id and self.app_secret)

    def _头(self, 路径: str) -> dict:
        头 = {"User-Agent": 用户代理, "Accept": "application/json"}
        if self.能签名():
            头.update(生成签名头(self.app_id, self.app_secret, 路径))
        return 头

    # ---- HTTP ----

    def _节流(self) -> None:
        """两次请求之间至少隔 最小请求间隔秒（默认 0.3）—— 官方禁止批量下载。"""
        if self.最小请求间隔秒 <= 0:
            return
        with self._节流锁:
            现在 = float(self._时钟())
            if self._上次请求 is not None:
                等 = self.最小请求间隔秒 - (现在 - self._上次请求)
                if 等 > 0:
                    self._睡眠(等)
            self._上次请求 = float(self._时钟())

    def _请(self, 路径: str, *, 方法: str = "GET",
           参数: Optional[dict] = None, 体: Optional[dict] = None) -> dict:
        """发一次请求并把响应体解析成 JSON（业务错误在这里就抛出来）。"""
        if self._已关闭:
            raise 弹幕源错误("源已关闭")
        地址 = self.基地址 + 纯接口路径(路径)
        if 参数:
            地址 += "?" + urllib.parse.urlencode({k: str(v) for k, v in 参数.items()})
        数据 = None
        头 = self._头(路径)
        if 体 is not None:
            数据 = json.dumps(体, ensure_ascii=False).encode("utf-8")
            # ⚠️ 有请求体就必须声明 Content-Type：不然服务端直接回 **HTTP 415**
            #    （真机联调时踩到过：不带这个头，/api/v2/match 一律 415）。
            头["Content-Type"] = "application/json; charset=utf-8"
        self._节流()
        self.请求次数 += 1
        应答 = self.传输(请求(方法=方法, 地址=地址, 头=头,
                            体=数据, 超时=self.超时秒))
        if int(应答.状态码) != 200:
            提示 = str((应答.头 or {}).get("X-Error-Message")
                     or (应答.头 or {}).get("x-error-message") or "")
            if int(应答.状态码) == 403 and "Authentication" in 提示:
                # 真机实测（2026）：**连 /match 也要求 AppId/签名**，不再是"完全公开"。
                # 这条提示要能直接告诉用户怎么办，而不是甩一个 403。
                raise 弹幕源错误(
                    "弹弹play 要求应用鉴权（" + 提示 + "）：请到 "
                    "https://dev.dandanplay.com 申请 AppId/AppSecret，"
                    "写进 数据/弹幕源.json（{\"dandanplay\": {\"appId\": \"…\", "
                    "\"appSecret\": \"…\"}}）或设环境变量 "
                    "V2_DANDANPLAY_APP_ID / V2_DANDANPLAY_APP_SECRET")
            raise 弹幕源错误(f"{方法} {纯接口路径(路径)} 返回 HTTP {应答.状态码}"
                           + (f"（{提示}）" if 提示 else "")
                           + f"：{应答.文本()[:200]}")
        try:
            响应体 = json.loads(应答.文本() or "{}")
        except ValueError as 错:
            raise 弹幕源错误(f"{方法} {纯接口路径(路径)} 的响应不是 JSON：{错}") from 错
        if not isinstance(响应体, dict):
            raise 弹幕源错误("接口返回的不是 JSON 对象")
        检查业务状态(响应体)
        return 响应体

    # ---- 缓存 ----

    def _缓存文件(self, 标识: str) -> Path:
        # 标识只允许数字/字母/下划线/短横：别让调用方传进来的奇怪字符串跳出缓存目录
        安全 = re.sub(r"[^0-9A-Za-z_-]", "_", str(标识))[:80] or "无"
        return self.缓存目录 / f"弹幕_{安全}.json"

    def _读缓存(self, 标识: str) -> Optional[dict]:
        if self.缓存TTL秒 <= 0:
            return None
        路径 = self._缓存文件(标识)
        try:
            数据 = json.loads(路径.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception as 错:  # noqa: BLE001  # 缓存坏了就当没有，不能拖垮播放
            日志.warning("弹幕缓存读不了，忽略它：%s：%s", 路径.name, 错)
            return None
        if not isinstance(数据, dict) or not isinstance(数据.get("响应"), dict):
            return None
        try:
            写入 = float(数据.get("写入时间") or 0.0)
        except (TypeError, ValueError):
            return None
        if float(self._时钟()) - 写入 > self.缓存TTL秒:
            return None
        self.缓存命中次数 += 1
        return 数据["响应"]

    def _写缓存(self, 标识: str, 响应体: dict) -> None:
        if self.缓存TTL秒 <= 0:
            return
        路径 = self._缓存文件(标识)
        临时 = 路径.with_suffix(".json.部分")
        try:
            路径.parent.mkdir(parents=True, exist_ok=True)
            临时.write_text(json.dumps({"标识": str(标识), "写入时间": float(self._时钟()),
                                      "响应": 响应体}, ensure_ascii=False),
                          encoding="utf-8")
            # 原子替换：写一半被中断时不能留下一个"看着像缓存、其实是半截"的文件
            os.replace(临时, 路径)
        except OSError as 错:
            日志.warning("弹幕缓存写不进去（不影响这次播放）：%s：%s", 路径.name, 错)
            try:
                临时.unlink(missing_ok=True)
            except OSError:
                pass

    # ---- 自己的接口实现 ----

    def 能匹配(self) -> bool:
        return True

    def 匹配(self, 素材: 素材信息) -> list[匹配结果]:
        """有资料库身份就**按作品名搜**；没有身份才退回 ``/api/v2/match``（文件名/哈希）。

        为什么要分这两条路（用户原话："文件名不可信，用户会乱放文件夹乱改名"）：
        ``/match`` 是按"文件名 + 文件哈希"找节目的 —— 文件被改成
        ``146.SDR.8bit.2160p…mp4`` 时（真机实例），它能匹配到的自然和片子不沾边。
        刮削之后我们知道这部作品叫什么（中文名/原名）、是第几季第几集，就该用**这些**去搜。

        为什么搜不到时**不**回落 ``/match``：那等于把"乱改名"的坑又放回来，
        给出一部不相干的片子的弹幕比"没找到"更糟（用户会以为是我们匹配错了）。
        真要兜底也由上层（库里本来就没身份）决定。
        """
        if 素材.来自资料库 and (素材.搜索词们 or 素材.标识):
            出 = self._按作品名搜(素材)
            if 出:
                依据 = str((出[0].原始 or {}).get("按作品名搜") or "")
                日志.info("弹弹play：按资料库身份搜到 %d 条候选（依据 %s）", len(出), 依据)
                return 出
            日志.info("弹弹play：按资料库身份（%s）没搜到；**不用文件名兜底**"
                     "（文件名不可信，改了名会搜到别的片子）",
                     "／".join(素材.搜索词们) or f"tmdb:{素材.标识}")
            return []

        文件名 = 素材.无扩展名
        哈希 = str(素材.额外.get("fileHash") or "")
        if not 哈希 and self.读哈希 and 素材.可读文件():
            try:
                哈希 = 前16MB的MD5(素材.路径)
            except OSError as 错:      # 文件被删/没权限：退回按文件名匹配
                日志.warning("算不了 fileHash（改用文件名匹配）：%s：%s", 素材.路径, 错)
        模式 = "hashAndFileName"
        if not 哈希:
            模式 = "fileNameOnly"
        elif not 文件名:
            模式 = "hashOnly"
        体 = {"fileName": 文件名, "fileHash": 哈希, "fileSize": int(素材.取大小()),
             "videoDuration": int(round(float(素材.时长秒 or 0.0))), "matchMode": 模式}
        响应体 = self._请(接口_匹配, 方法="POST", 体=体)
        return 解析匹配响应(响应体, 素材)

    def _按作品名搜(self, 素材) -> list[匹配结果]:
        """按**资料库身份**搜：先用 TMDB id 精确查，再用中文名/原名搜。

        为什么先 TMDB id：名字会被译得五花八门（中文名/罗马字/英译），TMDB id 不会；
        官方搜索接口就支持 ``tmdbId``（本函数引用的参数名来自官方 Swagger）。
        两个词都试是第二道保险：中文名搜不到时原名还能兜住（反之亦然）。
        集号只能有一个来源 —— 库里的季集；拿文件名里的数字当集号是最容易错的地方
        （真机那个文件叫 ``146…``，而库里那一集其实是别的集号）。
        """
        集号 = 素材.集号
        电影 = str(素材.额外.get("类型") or "") == "movie"
        出: list[匹配结果] = []
        见过: set[str] = set()
        最后一个错: Optional[Exception] = None
        记号 = ""

        def 收(这批, 依据: str) -> None:
            for 候 in 这批:
                if 候.标识 in 见过:
                    continue
                见过.add(候.标识)
                # 标上"这条是按什么搜来的"：上层（待确认队列/日志）能看出依据
                候.原始 = dict(候.原始 or {})
                候.原始["按作品名搜"] = 依据
                出.append(候)

        次序: list[tuple[str, str]] = []
        if 素材.标识:
            次序.append(("", f"tmdb:{素材.标识}"))
        次序.extend((词, 词) for 词 in 素材.搜索词们)
        for 词, 依据 in 次序:
            try:
                这批 = self.搜索(词, 集号, 标识=素材.标识 if not 词 else "",
                                电影=电影)
            except 弹幕源错误 as 错:      # 网络/凭据问题：记下来，别的词还能试
                最后一个错 = 错
                日志.warning("弹弹play：按「%s」搜索失败：%s", 依据, 错)
                continue
            if 这批:
                收(这批, 依据)
                记号 = 依据
                if 依据.startswith("tmdb:"):
                    break                # TMDB id 命中了就不必再按名字猜
        if not 出 and 最后一个错 is not None and len(次序) == 1:
            raise 最后一个错
        return 出

    def 取弹幕(self, 标识: str) -> 弹幕池:
        """取某一集的弹幕：**缓存 → 在途合并 → 真请求**。

        步骤顺序不能反：先看缓存（命中就不打扰对方接口），再看有没有别人正在取
        同一个 episodeId（有就等他的结果 —— 官方禁止批量下载，同一集打两次很难看）。
        """
        键 = str(标识 or "").strip()
        if not 键:
            raise 弹幕源错误("episodeId 不能为空")
        if not 键.isdigit():
            # episodeId 就是整数；挡掉 "1?x=2" 这种拼串，免得拼出别的路径
            raise 弹幕源错误(f"episodeId 必须是整数：{键!r}")
        命中 = self._读缓存(键)
        if 命中 is not None:
            return 解析响应(命中)

        在途, 我是主 = self._登记(键)
        if not 我是主:
            在途.事件.wait(self.超时秒 + 10.0)
            if 在途.池 is not None:
                return 在途.池
            if 在途.错 is not None:
                raise 在途.错
            # 等待超时（主请求卡死了）：自己再来一次，别把调用线程永远挂住
            日志.warning("等同一集（%s）的在途请求超时，改为自己发一次", 键)
            return self._取并缓存(键)
        try:
            池 = self._取并缓存(键)
            在途.池 = 池
            return 池
        except BaseException as 错:
            在途.错 = 错
            raise
        finally:
            with self._锁:
                self._在途.pop(键, None)
            在途.事件.set()

    def _登记(self, 键: str) -> tuple[_在途, bool]:
        """返回 ``(在途, 我是不是发起者)``。发起者有义务 ``事件.set()`` 叫醒等的人。"""
        with self._锁:
            if self._已关闭:
                raise 弹幕源错误("源已关闭")
            已有 = self._在途.get(键)
            if 已有 is not None:
                return 已有, False
            新 = _在途(事件=threading.Event())
            self._在途[键] = 新
            return 新, True

    def _取并缓存(self, 键: str) -> 弹幕池:
        # 二次查缓存：等的人被叫醒后，上一轮可能已经把它写进缓存了
        命中 = self._读缓存(键)
        if 命中 is not None:
            return 解析响应(命中)
        响应体 = self._请(接口_弹幕 + 键,
                        参数={"withRelated": "true", "chConvert": self.chConvert})
        池 = 解析响应(响应体)
        self._写缓存(键, 响应体)
        return 池

    def 搜索(self, 关键词: str, 集号: Optional[int] = None, *,
            标识: str = "", 电影: bool = False) -> list[匹配结果]:
        """``GET /api/v2/search/episodes``：按作品名（或 TMDB id）找那一集。

        :param 标识: 非空时优先用 **tmdbId** 精确查（``tmdbIdType``：0=电视剧、1=电影）。
            这是"用刮削结果取弹幕"最准的一条路 —— 名字可能被译得不一样，
            TMDB id 不会（来源：官方 Swagger 的 ``/api/v2/search/episodes`` 参数表）。
        """
        词 = str(关键词 or "").strip()
        标识 = str(标识 or "").strip()
        if not 词 and not 标识:
            raise 弹幕源错误("搜索关键词不能为空")
        参数 = {"episode": "" if 集号 is None else str(集号)}
        if 标识:
            参数["tmdbId"] = 标识
            参数["tmdbIdType"] = 1 if 电影 else 0
        if 词:
            参数["anime"] = 词
        响应体 = self._请(接口_搜索, 方法="GET", 参数=参数)
        return 解析搜索响应(响应体, 集号)

    def 关闭(self) -> None:
        """关掉源：叫醒还在等的线程（否则它们要等到超时），并清空在途表。"""
        with self._锁:
            self._已关闭 = True
            在途们 = list(self._在途.values())
            self._在途.clear()
        for 一个 in 在途们:
            if 一个.错 is None and 一个.池 is None:
                一个.错 = 弹幕源错误("源已关闭")
            一个.事件.set()

    def 诊断(self) -> dict:
        """给界面/日志用的一份状态（**绝不包含 secret**，只说明"有没有凭证"）。"""
        return {"标识": self.标识, "基地址": self.基地址,
                "有凭证": self.能签名(), "凭证来源": self.凭证来源 or "无",
                "请求次数": self.请求次数, "缓存命中次数": self.缓存命中次数,
                "缓存目录": str(self.缓存目录), "缓存TTL秒": self.缓存TTL秒,
                "chConvert": self.chConvert, "最小请求间隔秒": self.最小请求间隔秒,
                "在途请求数": len(self._在途), "已关闭": self._已关闭}
