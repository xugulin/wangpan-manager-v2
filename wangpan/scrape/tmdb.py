"""TMDB 客户端（V2 从零实现：只用标准库 ``urllib`` + 文件 JSON 缓存，不引第三方 HTTP 库）。

照着官方 API 的事实写（要点都写在代码里，方便以后核对）：

* 接口基地址 ``https://api.themoviedb.org/3``；图片 CDN 基地址从 ``/configuration`` 拿
  （形如 ``https://image.tmdb.org/t/p/``），图片 URL = 基地址 + 尺寸 + 远端路径；
* 两种鉴权都支持：``api_key`` 查询参数（v3）与 ``Authorization: Bearer <token>``
  （v3/v4 通用，**推荐**）。**两种同时配了优先 Bearer**；
* **绝不硬编码 API Key**：从环境变量 ``V2_TMDB_TOKEN`` / ``V2_TMDB_API_KEY`` 或
  ``数据/刮削.json`` 读；日志与异常里只出现"有没有配置"，不出现密钥本身；
* 图片语言参数用 ``zh`` **不是** ``zh-CN``（官方明说图片不支持区域变体），
  且**必须带 ``null``**（``include_image_language=zh,null``）—— 海报/背景大量是无语言的；
* 语言回退 ``zh-CN → zh-TW → en-US → 原始语言``：先按首选语言请求，**字段为空才**再请求
  一次补空，不是每条数据都请求两次；
* 限流：旧规则（40 次/10 秒）已取消，现行约 40 请求/秒且随时会变 → 收到 **429 要退避重试**
  （读 ``Retry-After``，没有就指数退避，最多 3 次）；
* 缓存合规：官方要求"**不得缓存超过 6 个月**"，所以每条缓存记录都带 ``抓取时间``，
  且**超过 180 天一律视为过期**（默认 TTL 7 天，可配；设为 0 就是不用缓存）。

为什么把"解析"也放在这个文件里：原始 JSON → 内存模型 的映射是"官方字段名"的唯一出口，
散到各处就会出现两套字段假设（比如剧集阵容到底读 ``credits`` 还是 ``aggregate_credits``）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .模型 import (人物, 人物工种, 参演, 图片, 图片类型, 媒体条目, 媒体类型, 季, 集,
                分级, 匹配候选)
from .代理 import 代理错误, 解析代理, 经代理取

__all__ = ["署名", "TMDB配置", "TMDB客户端", "请求失败", "默认配置路径", "默认缓存目录",
           "最长缓存秒", "接口基地址", "默认图片基地址", "国家回退顺序"]

#: 官方要求的署名文案（界面上要显示这句，不能只写"数据来自 TMDB"）
署名 = "This product uses the TMDB API but is not endorsed or certified by TMDB."

#: 接口基地址（官方 v3）
接口基地址 = "https://api.themoviedb.org/3"
#: 图片 CDN 基地址的兜底值（``/configuration`` 拿不到时用它拼图，形如 …/t/p/w500/abc.jpg）
默认图片基地址 = "https://image.tmdb.org/t/p/"
#: 官方合规上限：**不得缓存超过 6 个月**（180 天，按秒算）
最长缓存秒 = 180 * 86400
#: 一次请求最多重试几次（429 限流时）
最多重试 = 3
#: 指数退避的基数与上限（秒）；不抖（可测），429 带 Retry-After 时优先听它的
退避基数 = 1.0
退避上限 = 30.0
#: 详情要带的附加数据（官方 append_to_response）
电影附加 = "credits,release_dates,images,videos,external_ids"
剧集附加 = "aggregate_credits,content_ratings,images,external_ids,videos"
季附加 = "images,credits"

#: 官方电影分级 ``release_dates[].type`` 的优先级（3 影院 > 2 限定影院 > 4 数字 > 5 实物 > 6 电视 > 1 首映）
电影分级类型优先 = (3, 2, 4, 5, 6, 1)

#: 分级取用的国家回退顺序。
#: ⚠️ **TMDB 没有中国大陆（CN）分级**（电影 45 国、剧集 40 国的数据里都不含 CN），
#: 所以这里必须按 CN → HK → TW → US → JP → 其它 回退，都没有就返回空列表，
#: 界面显示"暂无分级"—— 不是我们漏取了，是源里就没有。
国家回退顺序 = ("CN", "HK", "TW", "US", "JP")

#: 图片种类名 → 模型里的图片类型（官方 /images 的键名是 posters/backdrops/logos/stills/profiles）
图片种类映射 = {"poster": 图片类型.海报, "backdrop": 图片类型.背景, "logo": 图片类型.标志,
           "still": 图片类型.剧照, "profile": 图片类型.人物}
_中文种类 = {"海报": 图片类型.海报, "背景": 图片类型.背景, "标志": 图片类型.标志,
          "剧照": 图片类型.剧照, "人物": 图片类型.人物, "头像": 图片类型.人物}

日志 = logging.getLogger("wangpan.scrape.tmdb")


# ============================ 配置 ============================


def 默认配置路径() -> Path:
    return Path(__file__).resolve().parents[2] / "数据" / "刮削.json"


def 默认缓存目录() -> Path:
    return Path(__file__).resolve().parents[2] / "数据" / "缓存" / "tmdb"


@dataclass
class TMDB配置:
    """一次刮削要用的全部配置（可注入，便于单测离线跑）。"""

    token: str = ""                  # Bearer（优先）
    api_key: str = ""                # v3 api_key（查询参数）
    language: str = "zh-CN"
    image_language: str = "zh"       # 注意是 zh，不是 zh-CN
    超时秒: float = 20.0
    缓存目录: Optional[Path] = None
    缓存TTL秒: float = 7 * 86400     # 默认 7 天；0 = 不用缓存；硬上限 180 天
    #: 接口基地址（空 = 官方 https://api.themoviedb.org/3）。
    #: 只在"真机验证 / 自建代理"时填：指向 tests/假TMDB服务.py 的环回地址，
    #: 这样整条链路（urllib、鉴权头、JSON、图片基地址）都能离线真跑一遍。
    接口基地址: str = ""
    #: 代理（空 = 直连）。真机实测：这台机器直连到不了 api.themoviedb.org
    #: （DNS 被解析到无关地址），但本机 v2rayN 的 SOCKS5 口是通的 —— 见 代理.py。
    代理: str = ""

    @classmethod
    def 从环境与配置(cls, 配置路径: Optional[Path] = None) -> "TMDB配置":
        """环境变量 + ``数据/刮削.json`` 里读配置（**环境变量优先**，机器上的比仓库里的权威）。

        配置文件长这样::

            {"tmdb": {"token": "...", "apiKey": "...", "language": "zh-CN",
                      "imageLanguage": "zh"}}
        """
        数据 = _读配置文件(配置路径)
        节点 = 数据.get("tmdb") if isinstance(数据.get("tmdb"), dict) else {}
        配置 = cls(
            token=_清理密钥(节点.get("token")),
            api_key=_清理密钥(节点.get("apiKey") or 节点.get("api_key")),
            language=str(节点.get("language") or "zh-CN").strip() or "zh-CN",
            image_language=str(节点.get("imageLanguage") or 节点.get("image_language")
                               or "zh").strip() or "zh",
            超时秒=float(节点.get("timeout") or 20.0),
        )
        for 键 in ("apiBase", "api_base", "接口基地址"):
            if 节点.get(键):
                配置.接口基地址 = str(节点[键]).strip()
                break
        if 节点.get("cacheDir"):
            配置.缓存目录 = Path(str(节点["cacheDir"])).expanduser()
        if 节点.get("cacheTtlSeconds") is not None:
            配置.缓存TTL秒 = float(节点["cacheTtlSeconds"])
        elif 节点.get("缓存TTL秒") is not None:
            配置.缓存TTL秒 = float(节点["缓存TTL秒"])
        if (值 := _清理密钥(os.environ.get("V2_TMDB_TOKEN"))):
            配置.token = 值
        if (值 := _清理密钥(os.environ.get("V2_TMDB_API_KEY"))):
            配置.api_key = 值
        if (值 := str(os.environ.get("V2_TMDB_API_BASE") or "").strip()):
            配置.接口基地址 = 值         # 指向自建代理 / 环回假服务（真机验证用）
        for 键 in ("proxy", "代理", "httpsProxy", "socksProxy"):
            if 节点.get(键):
                配置.代理 = str(节点[键]).strip()
                break
        if (值 := str(os.environ.get("V2_TMDB_PROXY") or "").strip()):
            配置.代理 = 值               # 环境变量优先（机器上的比仓库里的权威）
        return 配置

    def 密钥问题(self) -> str:
        """凭据能不能真的放进 HTTP 头里？返回""=没问题，否则是一句人话的原因。

        为什么专门查这个：HTTP 头只能是 latin-1，而用户从网页上复制 token 时
        很容易带上**中文引号、全角空格、零宽字符**。不查的话底层会抛出
        ``'latin-1' codec can't encode characters in position 7-10`` ——
        没人看得懂这句话，也没人猜得到是"复制时多带了一个字符"。
        """
        for 名字, 值 in (("token", self.token), ("apiKey", self.api_key)):
            if not 值:
                continue
            try:
                值.encode("latin-1")
            except UnicodeEncodeError:
                坏的 = "".join(sorted({c for c in 值 if ord(c) > 255}))
                return (f"{名字} 里有非 ASCII 字符（{坏的!r}）—— "
                        "多半是复制时带上了中文引号/全角空格；请重新复制粘贴")
        return ""

    def 鉴权方式(self) -> str:
        """给界面/日志看的：只说"用哪种鉴权"，**不回显密钥**。"""
        if self.token:
            return "Bearer"
        if self.api_key:
            return "api_key"
        return "无"

    def 可用(self) -> bool:
        return bool(self.token or self.api_key)


def _读配置文件(配置路径: Optional[Path]) -> dict:
    路径 = Path(配置路径) if 配置路径 else 默认配置路径()
    try:
        数据 = json.loads(路径.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return 数据 if isinstance(数据, dict) else {}


#: 复制粘贴密钥时最容易混进来的字符：各种空白 + 零宽 + 中文引号 + 常见包裹符号
_密钥杂字符 = ("\u3000", "\u200b", "\u200c", "\u200d", "\ufeff", "\u00a0",
           "\u2018", "\u2019", "\u201c", "\u201d", "“", "”", "‘", "’", "'", '"',
           "＜", "＞", "<", ">")


def _清理密钥(原文) -> str:
    """把密钥里"复制时带进来的杂质"去掉（空白、零宽字符、包裹用的引号/尖括号）。"""
    文本 = str(原文 or "")
    for 杂 in _密钥杂字符:
        文本 = 文本.replace(杂, "")
    return 文本.strip()


class 请求失败(Exception):
    """一次 TMDB 请求失败。

    ``消息`` 里**不含** token / api_key（客户端统一脱敏后再抛），可以安全打日志。
    """

    def __init__(self, 状态码: int = 0, 消息: str = "", 重试后: str = "") -> None:
        super().__init__(消息 or f"HTTP {状态码}")
        self.状态码 = 状态码
        self.重试后 = 重试后


# ============================ 客户端 ============================


class TMDB客户端:
    """TMDB 的只读客户端：搜索 / 详情 / 季 / 人物 / 分级 / 图片 / 下图片。

    ``请求`` 可注入（测试用假传输），签名 ``请求(方法, 地址, 参数: dict, 头: dict) -> dict``；
    另外三个关键字参数（``现在``/``睡眠``/``下载``）是为了**离线可测**：注入"现在"就能验
    180 天硬过期，注入"睡眠"就不会让单测真的睡几秒。
    """

    def __init__(self, 配置: Optional[TMDB配置] = None, 请求=None, *,
                 现在: Optional[Callable[[], float]] = None,
                 睡眠: Optional[Callable[[float], None]] = None,
                 下载: Optional[Callable[[str], bytes]] = None) -> None:
        self.配置 = 配置 if 配置 is not None else TMDB配置.从环境与配置()
        self._请求 = 请求 if 请求 is not None else self._默认请求
        self._现在 = 现在 if 现在 is not None else time.time
        self._睡眠 = 睡眠 if 睡眠 is not None else time.sleep
        self._下载器 = 下载
        self.日志 = 日志
        self._图片基地址缓存 = ""
        self.已关闭 = False

    # ---------------- 对外：能力与配置 ----------------

    def 可用(self) -> bool:
        """有没有配鉴权（没配就别去请求，白挨 401）。"""
        return self.配置.可用()

    def _基地址(self) -> str:
        """接口基地址（默认官方 v3；``配置.接口基地址`` 可指到代理/环回假服务上）。

        为什么要有这个口子：真机验证时用 ``tests/假TMDB服务.py`` 在环回口上顶替网络，
        走的还是**真的 urllib + 真的 HTTP + 真的鉴权头**，而不是"注入一个假对象"
        （那样 JSON 解析、状态码、图片基地址这些环节全被绕过去了）。
        """
        自定义 = str(getattr(self.配置, "接口基地址", "") or "").strip()
        return (自定义 or 接口基地址).rstrip("/")

    def 配置摘要(self) -> dict:
        """给界面/日志用的一句话配置说明（**密钥只报"有没有"**）。"""
        代理 = ""
        try:
            已解析 = self.代理配置()
            代理 = 已解析.可读() if 已解析 is not None else ""
        except 请求失败 as 错:
            代理 = f"（配置有问题：{错}）"
        return {"可用": self.可用(), "鉴权": self.配置.鉴权方式(),
                "语言": self.配置.language, "图片语言": self.图片语言参数(),
                "缓存目录": str(self.配置.缓存目录 or 默认缓存目录()),
                "缓存TTL天": round(self.配置.缓存TTL秒 / 86400.0, 3),
                "超时秒": self.配置.超时秒, "代理": 代理 or "直连"}

    def 配置信息(self) -> dict:
        """``/configuration``：图片基地址与尺寸表（给界面拼图用）。"""
        数据 = self._取一份("/configuration", {}, "configuration", "api",
                         语言="通用", 发送语言=False)
        self._记住图片基地址(数据)
        return 数据

    def 图片地址(self, 远端路径: str, 尺寸: str = "w500") -> str:
        """拼图片 URL：基地址 + 尺寸 + 远端路径。

        **不为拼一个 URL 去发请求**：基地址来自 ``/configuration``（调过 ``配置信息()``
        或缓存里有就用它），拿不到就用官方默认值。远端路径本身是完整 URL 时原样返回。
        """
        路径 = (远端路径 or "").strip()
        if not 路径:
            return ""
        if 路径.startswith(("http://", "https://")):
            return 路径
        基 = self.图片基地址()
        if not 基.endswith("/"):
            基 += "/"
        尺 = (尺寸 or "").strip().strip("/")
        尾 = 路径.lstrip("/")
        return f"{基}{尺}/{尾}" if 尺 else f"{基}{尾}"

    def 图片基地址(self) -> str:
        if self._图片基地址缓存:
            return self._图片基地址缓存
        数据 = self._读缓存(self._缓存路径("configuration", "通用", "api"))
        return self._记住图片基地址(数据 or {})

    def 图片语言参数(self) -> str:
        """``include_image_language`` 的值。

        官方明确：图片**不支持区域变体**（要 ``zh`` 不要 ``zh-CN``），而且**必须带 null**
        ——``null`` 表示"无语言的通用图"，海报/背景里一大半是无语言的，不带就少了半壁江山。
        """
        原始 = (self.配置.image_language or "zh").strip() or "zh"
        部分 = [x.strip().split("-")[0] for x in re.split(r"[,\s]+", 原始) if x.strip()]
        部分 = [x for x in 部分 if x]
        if "null" not in 部分:
            部分.append("null")
        return ",".join(dict.fromkeys(部分))

    def _记住图片基地址(self, 数据: dict) -> str:
        图片节点 = 数据.get("images") if isinstance(数据.get("images"), dict) else {}
        基 = str(图片节点.get("secure_base_url") or 图片节点.get("base_url") or "").strip()
        if 基:
            self._图片基地址缓存 = 基 if 基.endswith("/") else 基 + "/"
        return self._图片基地址缓存 or 默认图片基地址

    # ---------------- 对外：搜索 ----------------

    def 搜电影(self, 标题: str, 年份: Optional[int] = None) -> list[匹配候选]:
        return self._搜索("/search/movie", 媒体类型.电影, 标题, 年份, "movie", "year")

    def 搜剧集(self, 标题: str, 年份: Optional[int] = None) -> list[匹配候选]:
        # 剧集用的参数名是 first_air_date_year（不是 year），别抄错
        return self._搜索("/search/tv", 媒体类型.剧集, 标题, 年份, "tv", "first_air_date_year")

    def _搜索(self, 路径: str, 类型: 媒体类型, 标题: str, 年份: Optional[int],
             种类: str, 年份键: str) -> list[匹配候选]:
        标题 = (标题 or "").strip()
        if not 标题:
            return []
        参数: dict = {"query": 标题, "include_adult": "false"}
        if 年份:
            参数[年份键] = str(int(年份))
        键 = f"{种类}-{_指纹(参数)}"
        份们 = self._取带回退(路径, 参数, f"search-{种类}", 键, 判空=_搜索缺字段)
        候选们 = _解析搜索(份们[0], 类型)
        for 补 in 份们[1:]:
            _补空候选(候选们, _解析搜索(补, 类型))
        return 候选们

    # ---------------- 对外：详情 ----------------

    def 取电影(self, id: str) -> 媒体条目:
        参数 = {"append_to_response": 电影附加,
              "include_image_language": self.图片语言参数()}
        份们 = self._取带回退(f"/movie/{id}", 参数, "movie", str(id), 判空=_影视缺字段)
        条目 = _解析电影(份们[0], str(id), self.配置.image_language)
        for 补 in 份们[1:]:
            _补空条目(条目, _解析电影(补, str(id), self.配置.image_language))
        return 条目

    def 取剧集(self, id: str) -> 媒体条目:
        参数 = {"append_to_response": 剧集附加,
              "include_image_language": self.图片语言参数()}
        份们 = self._取带回退(f"/tv/{id}", 参数, "tv", str(id), 判空=_影视缺字段)
        条目 = _解析剧集(份们[0], str(id), self.配置.image_language)
        for 补 in 份们[1:]:
            _补空条目(条目, _解析剧集(补, str(id), self.配置.image_language))
        return 条目

    def 取季(self, 剧id: str, 季号: int) -> 季:
        参数 = {"append_to_response": 季附加,
              "include_image_language": self.图片语言参数()}
        键 = f"{剧id}-season-{int(季号)}"
        份们 = self._取带回退(f"/tv/{剧id}/season/{int(季号)}", 参数, "tv", 键,
                          判空=_影视缺字段)
        结果 = _解析季(份们[0], int(季号), self.配置.image_language)
        for 补 in 份们[1:]:
            _补空季(结果, _解析季(补, int(季号), self.配置.image_language))
        return 结果

    def 取人物(self, id: str) -> 人物:
        参数 = {"append_to_response": "combined_credits,images"}
        份们 = self._取带回退(f"/person/{id}", 参数, "person", str(id), 判空=_人物缺字段)
        人物结果 = _解析人物(份们[0], str(id), self.配置.image_language)
        for 补 in 份们[1:]:
            _补空人物(人物结果, _解析人物(补, str(id), self.配置.image_language))
        return 人物结果

    # ---------------- 对外：分级 / 图片 ----------------

    def 取分级(self, 类型: 媒体类型, id: str) -> list[分级]:
        """按 CN → HK → TW → US → JP → 其它 的顺序返回（**TMDB 没有 CN**，见常量注释）。

        怎么做到不重复请求：分级来自详情里的 ``release_dates`` / ``content_ratings``，
        而详情用的是同一个 URL + 同一套 append_to_response，所以**和 ``取电影``/``取剧集``
        共用一份缓存**（先刮过详情再取分级 = 0 次请求）。
        """
        if 类型 is 媒体类型.剧集:
            return _解析剧集分级(self._详情数据("tv", str(id), 剧集附加))
        return _解析电影分级(self._详情数据("movie", str(id), 电影附加))

    def 取图片(self, 类型: 媒体类型, id: str, 种类: str = "") -> list[图片]:
        """取图片列表（可按 海报/背景/标志/剧照/人物 或官方英文键名筛）。同样复用详情缓存。"""
        是剧 = 类型 is 媒体类型.剧集
        数据 = self._详情数据("tv" if 是剧 else "movie", str(id),
                          剧集附加 if 是剧 else 电影附加)
        图片们 = _解析图片组(数据.get("images"))
        if 种类:
            键 = 种类.strip().lower()
            想要 = 图片种类映射.get(键) or _中文种类.get(种类.strip())
            if 想要 is not None:
                图片们 = [x for x in 图片们 if x.类型 is 想要]
        return 排序图片(图片们, self.配置.image_language)

    def _详情数据(self, 种类: str, id: str, 附加: str) -> dict:
        参数 = {"append_to_response": 附加, "include_image_language": self.图片语言参数()}
        return self._取一份(f"/{种类}/{id}", 参数, 种类, id, 语言=self.配置.language)

    # ---------------- 对外：下图片 ----------------

    def 下载图片(self, 远端路径: str, 落点: Path, 尺寸: str = "w500") -> Optional[Path]:
        """把一张图下到 ``落点``；失败返回 ``None``（**不抛异常**：一张图不该毁掉一次刮削）。"""
        落点 = Path(落点)
        try:
            if 落点.is_file() and 落点.stat().st_size > 0:
                return 落点            # 已经下过就不再下一遍（图片是刮削里最费流量的部分）
        except OSError:
            pass
        地址 = self.图片地址(远端路径, 尺寸)
        if not 地址:
            return None
        try:
            字节 = (self._下载器 or self._默认下载)(地址)
        except Exception as 异常:                     # noqa: BLE001 - 下载失败一律吞掉
            self.日志.warning("TMDB 图片下载失败：%s（%s）", 地址, self._脱敏(str(异常)))
            return None
        if isinstance(字节, str):
            字节 = 字节.encode("utf-8")
        if not 字节:
            return None
        try:
            落点.parent.mkdir(parents=True, exist_ok=True)
            临时 = 落点.with_name(落点.name + ".part")
            临时.write_bytes(字节)
            临时.replace(落点)
        except OSError as 异常:
            self.日志.warning("TMDB 图片落盘失败：%s（%s）", 落点, self._脱敏(str(异常)))
            return None
        return 落点

    def 关闭(self) -> None:
        """释放资源（urllib 没有连接池，这里主要是给"注入的传输"一个收尾机会）。"""
        if self.已关闭:
            return
        self.已关闭 = True
        for 属性 in ("close", "关闭"):
            收尾 = getattr(self._请求, 属性, None)
            if callable(收尾):
                try:
                    收尾()
                except Exception:                     # noqa: BLE001 - 收尾失败不影响主流程
                    pass
                break

    # ---------------- 缓存 ----------------

    def _缓存路径(self, 种类: str, 语言: str, 键: str) -> Path:
        """``缓存目录/种类/语言/键.json``（官方量纲：按类型与语言分层，别互相覆盖）。

        缓存**按接口基地址分区**：同一份缓存目录可能先后被"官方 API / 自建代理 /
        离线的环回假服务"用过（真机验证就是这么跑的）。不分区的话，验证时缓存下来的
        假数据会被当成 TMDB 的真数据喂给海报墙 —— 这种错很难看出来。
        """
        基 = Path(self.配置.缓存目录) if self.配置.缓存目录 else 默认缓存目录()
        安全 = re.sub(r"[^0-9A-Za-z._\-]", "_", str(键))[:80] or "空"
        语言层 = re.sub(r"[^0-9A-Za-z._\-]", "_", str(语言 or "通用")) or "通用"
        端点层 = "官方" if self._基地址().rstrip("/") == 接口基地址 else \
            "端点_" + hashlib.sha1(self._基地址().encode("utf-8")).hexdigest()[:10]
        return (基 / 端点层 / re.sub(r"[^0-9A-Za-z._\-]", "_", 种类) / 语言层
                / f"{安全}.json")

    def _读缓存(self, 路径: Path) -> Optional[dict]:
        if self.配置.缓存TTL秒 <= 0:
            return None
        try:
            记录 = json.loads(路径.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None         # 缓存坏了当没有：绝不因为缓存让刮削失败
        if not isinstance(记录, dict) or "抓取时间" not in 记录:
            return None
        try:
            抓取 = float(记录["抓取时间"])
        except (TypeError, ValueError):
            return None
        # 有效期 = min(配置 TTL, 180 天)：官方"不得缓存超过 6 个月"是硬线，不许被配置突破
        有效期 = min(float(self.配置.缓存TTL秒), float(最长缓存秒))
        if self._现在() - 抓取 > 有效期:
            return None
        数据 = 记录.get("数据")
        return 数据 if isinstance(数据, dict) else None

    def _写缓存(self, 路径: Path, 数据: dict) -> None:
        if self.配置.缓存TTL秒 <= 0 or not isinstance(数据, dict):
            return
        try:
            路径.parent.mkdir(parents=True, exist_ok=True)
            临时 = 路径.with_name(路径.name + ".part")
            临时.write_text(json.dumps({"抓取时间": self._现在(), "数据": 数据},
                                     ensure_ascii=False), encoding="utf-8")
            临时.replace(路径)      # 原子替换：半截 JSON 比没有缓存更糟
        except OSError as 异常:
            self.日志.debug("TMDB 缓存写入失败：%s（%s）", 路径, 异常)

    # ---------------- 请求 ----------------

    def _取一份(self, 路径: str, 参数: dict, 种类: str, 键: str,
              语言: Optional[str] = None, 发送语言: bool = True) -> dict:
        """带缓存的一次请求。

        ``语言=None`` → 用配置的首选语言；``发送语言=False`` → 只把它当缓存分层，不放进
        请求参数（``/configuration`` 这种跟语言无关的接口就别乱塞 language）。
        """
        参数 = dict(参数 or {})
        层 = 语言 if 语言 is not None else (self.配置.language or "zh-CN")
        if 发送语言:
            参数["language"] = 层
        缓存 = self._缓存路径(种类, 层, 键)
        if (命中 := self._读缓存(缓存)) is not None:
            self.日志.debug("TMDB 命中缓存：%s/%s", 种类, 层)
            return 命中
        数据 = self._调用(路径, 参数)
        self._写缓存(缓存, 数据)
        return 数据

    def _回退语言们(self, 主数据: dict) -> list[str]:
        """首选语言取完还缺字段时，按 ``en-US`` → 原始语言 的顺序补（各最多一次）。"""
        主语言 = self.配置.language or "zh-CN"
        候选 = ["en-US", str(主数据.get("original_language") or "").strip()]
        结果: list[str] = []
        for 语言 in 候选:
            if not 语言 or _同一种语言(语言, 主语言):
                continue
            if any(_同一种语言(语言, 已有) for 已有 in 结果):
                continue
            结果.append(语言)
        return 结果

    def _取带回退(self, 路径: str, 参数: dict, 种类: str, 键: str, 判空) -> list[dict]:
        """按语言链取数据，**只在还缺字段时才多请求**（"别每条数据都请求两次"）。

        返回若干份数据：第一份是首选语言，后面是补空用的（可能 0 份）。调用方负责把
        空字段补上（每一份都进各自的缓存，第二次调用不花请求）。
        """
        主语言 = self.配置.language or "zh-CN"
        份们 = [self._取一份(路径, 参数, 种类, 键, 语言=主语言)]
        for 语言 in self._回退语言们(份们[0]):
            if not 判空(份们[-1]):
                break
            self.日志.debug("TMDB %s 语言回退到 %s（首选语言有字段是空的）", 路径, 语言)
            份们.append(self._取一份(路径, 参数, 种类, 键, 语言=语言))
        return 份们

    def _调用(self, 路径: str, 参数: dict) -> dict:
        """发一次请求（429 退避重试），返回 JSON。异常里**不会**出现 token/api_key。"""
        if (问题 := self.配置.密钥问题()):
            # 别让 latin-1 的编码错误冒到用户面前（那句话没人看得懂，见 密钥问题()）
            raise 请求失败(0, f"TMDB 凭据有问题（{路径}）：{问题}")
        全参数 = {k: v for k, v in (参数 or {}).items() if v is not None}
        头 = {"Accept": "application/json", "User-Agent": "WangPanV2/2.0"}
        if self.配置.token:
            头["Authorization"] = f"Bearer {self.配置.token}"
        elif self.配置.api_key:
            全参数["api_key"] = self.配置.api_key
        地址 = self._基地址() + 路径
        for 次 in range(最多重试 + 1):
            try:
                return self._请求("GET", 地址, 全参数, 头)
            except Exception as 异常:                  # noqa: BLE001 - 统一转成 请求失败
                if not _是限流(异常):
                    raise self._包装(异常, 路径) from None
                if 次 >= 最多重试:
                    self.日志.warning("TMDB 限流重试 %d 次仍失败：%s", 最多重试, 路径)
                    raise 请求失败(429, f"TMDB 限流（429），重试 {最多重试} 次仍失败：{路径}",
                               _重试后原文(异常)) from None
                等待 = _退避秒(异常, 次)
                self.日志.warning("TMDB 限流（429）：%.1f 秒后第 %d 次重试（%s）",
                                等待, 次 + 1, 路径)
                self._睡眠(等待)
        raise 请求失败(429, f"TMDB 限流：{路径}")        # pragma: no cover - 循环必然返回或抛出

    #: 网络类错误的关键字（这些不是"Key 写错了"，而是"连不上"，提示要不一样）
    _网络类错误 = ("Network is unreachable", "timed out", "timeout", "Connection refused",
              "Temporary failure in name resolution", "Name or service not known",
              "getaddrinfo", "No route to host", "Connection reset")

    def _包装(self, 异常: Exception, 路径: str) -> 请求失败:
        """把任意底层异常转成脱敏的 :class:`请求失败`，并**给出可操作的提示**。

        为什么要专门认"网络类错误"：TMDB 的 **API 域名**在部分网络环境下连不上
        （实测某网络把它解析到一个无关 IP，IPv4/IPv6 都超时），而**图片 CDN 却是通的**
        —— 这时如果只报"请求失败"，用户会以为 Key 写错了，白折腾半天。
        """
        if isinstance(异常, 请求失败):
            文本 = self._脱敏(str(异常))
            # 已经是"请求失败"也要再认一次网络关键字：底层可能先包装过一遍，
            # 不补提示的话用户看到的还是干巴巴的 "Network is unreachable"。
            if any(词.lower() in 文本.lower() for 词 in self._网络类错误):
                return 请求失败(异常.状态码, 文本 + "\n" + self._连不上提示(), 异常.重试后)
            return 请求失败(异常.状态码, 文本, 异常.重试后)
        文本 = self._脱敏(str(异常))
        if any(词.lower() in 文本.lower() for 词 in self._网络类错误):
            return 请求失败(0,
                         f"连不上 TMDB 的 API 域名（{路径}）：{文本}\n"
                         "    这不是 Key 的问题，而是网络到不了 api.themoviedb.org。\n"
                         "    ① 若你有代理：设环境变量 https_proxy（例如 "
                         "export https_proxy=http://127.0.0.1:7890）后重试；\n"
                         "    ② 图片 CDN（image.tmdb.org）与 API 是两个域名，可能只有 API 不通 —— "
                         "此时仍可用本地 NFO/图片刮削；\n"
                         "    ③ 自检：运行 工具/检查刮削凭据.py 会分别报两个域名的连通性。")
        return 请求失败(int(getattr(异常, "code", 0) or 0),
                     f"TMDB 请求失败（{路径}）：{文本}")

    @staticmethod
    def _连不上提示() -> str:
        """连不上 API 时的统一提示（图片 CDN 与 API 是两个域名，这点最容易误判）。"""
        return ("    这不是 Key 的问题，而是网络到不了 api.themoviedb.org。\n"
                "    ① 若你有代理：设 V2_TMDB_PROXY（例如 "
                "socks5://127.0.0.1:10808 —— v2rayN 的默认 SOCKS 口），"
                "或设 https_proxy（http://127.0.0.1:7890）后重试；\n"
                "    ② 图片 CDN（image.tmdb.org）与 API 是两个域名，可能只有 API 不通 —— "
                "此时仍可用本地 NFO/图片刮削；\n"
                "    ③ 自检：运行 工具/检查刮削凭据.py 会分别报两个域名的连通性。")

    def _脱敏(self, 文本: str) -> str:
        """把 token/api_key 从任何要打日志或抛异常的文本里抹掉（配置里也不许出现明文）。"""
        结果 = str(文本 or "")
        for 密钥 in (self.配置.token, self.配置.api_key):
            if 密钥:
                结果 = 结果.replace(密钥, "***")
        结果 = re.sub(r"(api_key=)[^&\s\"']+", r"\1***", 结果, flags=re.I)
        return 结果

    # ---------------- 默认传输（urllib / 代理） ----------------

    def 代理配置(self):
        """解析好的代理对象（没配就是 None）。**坏代理**会在这里抛 请求失败。"""
        原文 = str(getattr(self.配置, "代理", "") or "").strip()
        if not 原文:
            return None
        try:
            return 解析代理(原文)
        except 代理错误 as 错:
            raise 请求失败(0, f"TMDB 代理配置有问题：{错}") from None

    def _默认请求(self, 方法: str, 地址: str, 参数: dict, 头: dict) -> dict:
        """标准库实现的默认传输：``请求(方法, 地址, 参数, 头) -> dict``。

        配了代理就走 :mod:`~wangpan.scrape.代理`（标准库自己实现的 SOCKS5/HTTP 隧道）——
        真机实测直连到不了 api.themoviedb.org，而本机 SOCKS5 口是通的。
        """
        查询 = urllib.parse.urlencode({k: v for k, v in (参数 or {}).items() if v is not None})
        完整 = f"{地址}?{查询}" if 查询 else 地址
        代理 = self.代理配置()
        if 代理 is not None:
            try:
                应答 = 经代理取(完整, float(self.配置.超时秒), 代理, 头)
            except 代理错误 as 错:
                raise 请求失败(0, f"TMDB 请求走代理失败：{self._脱敏(str(错))}") from None
            if 应答.状态码 >= 400:
                重试后 = 应答.头.get("retry-after", "")
                raise 请求失败(int(应答.状态码),
                           f"TMDB HTTP {应答.状态码}：{地址}", 重试后)
            try:
                return json.loads(应答.文本())
            except ValueError:
                raise 请求失败(0, f"TMDB 响应不是 JSON：{地址}") from None
        请求对象 = urllib.request.Request(完整, headers=dict(头 or {}), method=方法 or "GET")
        try:
            with urllib.request.urlopen(请求对象, timeout=float(self.配置.超时秒)) as 响应:
                文本 = 响应.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as 异常:
            重试后 = ""
            try:
                重试后 = 异常.headers.get("Retry-After", "") if 异常.headers else ""
            except Exception:                          # noqa: BLE001 - 头读不到就算了
                重试后 = ""
            # 注意：消息里只放路径，不放完整 URL —— URL 上可能挂着 api_key
            raise 请求失败(int(异常.code or 0), f"TMDB HTTP {异常.code}：{地址}", 重试后) \
                from None
        except urllib.error.URLError as 异常:
            raise 请求失败(0, f"TMDB 网络错误：{self._脱敏(str(异常.reason))}") from None
        try:
            return json.loads(文本)
        except ValueError:
            raise 请求失败(0, f"TMDB 响应不是 JSON：{地址}") from None

    def _默认下载(self, 地址: str) -> bytes:
        代理 = self.代理配置()
        if 代理 is not None:
            try:
                应答 = 经代理取(地址, float(self.配置.超时秒), 代理,
                            {"Accept": "image/*,*/*"})
            except 代理错误 as 错:
                raise 请求失败(0, f"图片走代理失败：{self._脱敏(str(错))}") from None
            if 应答.状态码 >= 400:
                raise 请求失败(int(应答.状态码), f"图片 HTTP {应答.状态码}：{地址}")
            return 应答.体
        请求对象 = urllib.request.Request(地址, headers={
            "User-Agent": "WangPanV2/2.0", "Accept": "image/*,*/*"})
        with urllib.request.urlopen(请求对象, timeout=float(self.配置.超时秒)) as 响应:
            return 响应.read()


# ============================ 限流工具 ============================


def _是限流(异常: Exception) -> bool:
    """是不是 429。

    为什么要这么宽：注入的传输可能是自己的异常类型（``请求失败``），也可能是
    ``urllib.error.HTTPError``；测试里的假传输还可能只抛个带 "429" 的普通异常。
    三种都认，才不会漏掉退避。
    """
    if isinstance(异常, 请求失败) and 异常.状态码 == 429:
        return True
    for 属性 in ("状态码", "status", "code", "status_code", "http_status"):
        值 = getattr(异常, 属性, None)
        if isinstance(值, int) and 值 == 429:
            return True
    return "429" in str(异常)


def _重试后原文(异常: Exception) -> str:
    值 = getattr(异常, "重试后", "") or getattr(异常, "retry_after", "")
    if 值:
        return str(值)
    try:
        头 = getattr(异常, "headers", None)
        if 头:
            return str(头.get("Retry-After", "") or "")
    except Exception:                                  # noqa: BLE001
        pass
    return ""


def _退避秒(异常: Exception, 次: int) -> float:
    """退避多久：听 ``Retry-After``，没有就指数退避（不抖，方便单测钉住）。"""
    原文 = _重试后原文(异常).strip()
    if 原文:
        try:
            return max(0.0, min(退避上限, float(原文)))
        except ValueError:
            pass          # Retry-After 也可以是 HTTP 日期，这里不解析（不引 email 模块）
    return min(退避上限, 退避基数 * (2 ** 次))


def _指纹(参数: dict) -> str:
    """查询参数 → 短指纹（搜索结果的缓存键；**不含密钥**，密钥是发请求时才加的）。"""
    文本 = json.dumps({k: v for k, v in sorted((参数 or {}).items())}, ensure_ascii=False)
    return hashlib.sha1(文本.encode("utf-8")).hexdigest()[:16]


def _同一种语言(甲: str, 乙: str) -> bool:
    """``en`` 与 ``en-US`` 算同一种（回退时不要为同一个语言发两次）。"""
    return (甲 or "").split("-")[0].lower() == (乙 or "").split("-")[0].lower()


def _年份(文本) -> Optional[int]:
    """``2019-05-01`` → 2019（TMDB 的日期字段都是这个形状）。"""
    if not 文本:
        return None
    m = re.match(r"\s*(\d{4})", str(文本))
    return int(m.group(1)) if m else None


def _整数(值, 默认: int = 0) -> int:
    try:
        return int(值)
    except (TypeError, ValueError):
        return 默认


def _浮点(值, 默认: float = 0.0) -> float:
    try:
        return float(值)
    except (TypeError, ValueError):
        return 默认


# ============================ 原始 JSON → 内存模型 ============================


def _解析搜索(数据: dict, 类型: 媒体类型) -> list[匹配候选]:
    是剧 = 类型 is 媒体类型.剧集
    候选们: list[匹配候选] = []
    for 条 in (数据 or {}).get("results") or []:
        标题 = str(条.get("name" if 是剧 else "title") or "").strip()
        原名 = str(条.get("original_name" if 是剧 else "original_title") or "").strip()
        日期 = 条.get("first_air_date" if 是剧 else "release_date") or ""
        候选 = 匹配候选(
            来源标识=str(条.get("id") or ""),
            标题=标题 or 原名,          # 首选语言没有标题时用原名兜着（别给界面一个空行）
            原名=原名,
            年份=_年份(日期),
            类型=类型,
            简介=str(条.get("overview") or "").strip(),
            海报远端=str(条.get("poster_path") or ""),
            热度=_浮点(条.get("popularity")),
            额外={"评分": _浮点(条.get("vote_average")),      # 0–10，和"评分票数"配套看
                "票数": _整数(条.get("vote_count")),
                "原始语言": 条.get("original_language") or "",
                "上映日期": 日期},
        )
        候选们.append(候选)
    return 候选们


def _解析电影(数据: dict, id: str, 图片语言: str = "zh") -> 媒体条目:
    数据 = 数据 or {}
    条目 = 媒体条目(
        类型=媒体类型.电影,
        标题=str(数据.get("title") or "").strip(),
        原名=str(数据.get("original_title") or "").strip(),
        年份=_年份(数据.get("release_date")),
        简介=str(数据.get("overview") or "").strip(),
        标签=[str(g.get("name")) for g in 数据.get("genres") or [] if g.get("name")],
        时长分钟=_整数(数据.get("runtime")),            # 官方单位就是分钟
        评分=_浮点(数据.get("vote_average")),           # 0–10
        评分票数=_整数(数据.get("vote_count")),
        状态=str(数据.get("status") or ""),
        原始语言=str(数据.get("original_language") or ""),
        制片公司=[str(c.get("name")) for c in 数据.get("production_companies") or []
              if c.get("name")],
        外部ID={"tmdb": str(数据.get("id") or id)},
    )
    # 电影详情里的 imdb_id 可能在顶层，也可能在 append 的 external_ids 里
    外部 = 数据.get("external_ids") if isinstance(数据.get("external_ids"), dict) else {}
    if (imdb := str(数据.get("imdb_id") or 外部.get("imdb_id") or "").strip()):
        条目.外部ID["imdb"] = imdb
    条目.分级们 = _解析电影分级(数据)
    条目.图片们 = _解析图片组(数据.get("images"))
    条目.海报 = 选最佳图片(条目.图片们, 图片类型.海报, 图片语言)
    条目.背景 = 选最佳图片(条目.图片们, 图片类型.背景, 图片语言)
    条目.标志 = 选最佳图片(条目.图片们, 图片类型.标志, 图片语言)
    条目.参演们 = _解析演职员(数据)
    return 条目


def _解析剧集(数据: dict, id: str, 图片语言: str = "zh") -> 媒体条目:
    数据 = 数据 or {}
    条目 = 媒体条目(
        类型=媒体类型.剧集,
        标题=str(数据.get("name") or "").strip(),
        原名=str(数据.get("original_name") or "").strip(),
        年份=_年份(数据.get("first_air_date")),
        简介=str(数据.get("overview") or "").strip(),
        标签=[str(g.get("name")) for g in 数据.get("genres") or [] if g.get("name")],
        时长分钟=_单集时长(数据),
        评分=_浮点(数据.get("vote_average")),
        评分票数=_整数(数据.get("vote_count")),
        状态=str(数据.get("status") or ""),
        原始语言=str(数据.get("original_language") or ""),
        制片公司=[str(c.get("name")) for c in 数据.get("production_companies") or []
              if c.get("name")],
        外部ID={"tmdb": str(数据.get("id") or id)},
    )
    外部 = 数据.get("external_ids") if isinstance(数据.get("external_ids"), dict) else {}
    # ⚠️ 官方的剧集详情里**没有 imdb_id**（只有 tvdb_id）；别去猜、也别拿电影的字段套
    if (tvdb := str(外部.get("tvdb_id") or "").strip()):
        条目.外部ID["tvdb"] = tvdb
    条目.季们 = _解析季列表(数据)
    条目.分级们 = _解析剧集分级(数据)
    条目.图片们 = _解析图片组(数据.get("images"))
    条目.海报 = 选最佳图片(条目.图片们, 图片类型.海报, 图片语言)
    条目.背景 = 选最佳图片(条目.图片们, 图片类型.背景, 图片语言)
    条目.标志 = 选最佳图片(条目.图片们, 图片类型.标志, 图片语言)
    条目.参演们 = _解析演职员(数据)
    return 条目


def _单集时长(数据: dict) -> int:
    """剧集的"时长分钟"取单集时长：``episode_run_time[0]``，没有就看最新一集。"""
    时长们 = 数据.get("episode_run_time") or []
    if isinstance(时长们, list) and 时长们:
        if (值 := _整数(时长们[0])):
            return 值
    最近 = 数据.get("last_episode_to_air") if isinstance(数据.get("last_episode_to_air"), dict) else {}
    return _整数(最近.get("runtime"))


def _解析季列表(数据: dict) -> list[季]:
    """``seasons[]`` → 季们。

    两个官方坑：
    * **季号必须读 ``season_number``** —— ``seasons[].name`` 会随语言变成 "Staffel 1"，
      拿它当季号就废了；
    * ``number_of_episodes`` 与各季 ``episode_count`` 之和对不上（第 0 季特别篇会污染），
      **不要拿它做校验**，这里只用各季自己的 ``episode_count``。
    """
    季们: list[季] = []
    for 条 in 数据.get("seasons") or []:
        if not isinstance(条, dict):
            continue
        号 = _整数(条.get("season_number"), 0)
        季对象 = 季(季号=号,
                  标题=str(条.get("name") or "").strip(),
                  简介=str(条.get("overview") or "").strip(),
                  集数=_整数(条.get("episode_count")),
                  播出日期=str(条.get("air_date") or ""))
        if (海报 := str(条.get("poster_path") or "")):
            季对象.海报 = 图片(类型=图片类型.海报, 远端路径=海报, 来源="tmdb")
        季们.append(季对象)
    季们.sort(key=lambda s: s.季号)
    return 季们


def _解析季(数据: dict, 季号: int, 图片语言: str = "zh") -> 季:
    数据 = 数据 or {}
    结果 = 季(季号=_整数(数据.get("season_number"), 季号),
             标题=str(数据.get("name") or "").strip(),
             简介=str(数据.get("overview") or "").strip(),
             播出日期=str(数据.get("air_date") or ""),
             集数=len(数据.get("episodes") or []))
    图片们 = _解析图片组(数据.get("images"))
    结果.海报 = 选最佳图片(图片们, 图片类型.海报, 图片语言)
    集们: list[集] = []
    for 条 in 数据.get("episodes") or []:
        if not isinstance(条, dict):
            continue
        集对象 = 集(季号=结果.季号,
                  集号=_整数(条.get("episode_number"), 0),
                  标题=str(条.get("name") or "").strip(),
                  简介=str(条.get("overview") or "").strip(),
                  播出日期=str(条.get("air_date") or ""),
                  时长分钟=_整数(条.get("runtime")),
                  评分=_浮点(条.get("vote_average")),
                  外部ID=str(条.get("id") or ""),
                  集号到=0)
        if (剧照 := str(条.get("still_path") or "")):
            集对象.剧照 = 图片(类型=图片类型.剧照, 远端路径=剧照,
                          宽=_整数(条.get("still_width")), 高=_整数(条.get("still_height")))
        集们.append(集对象)
    集们.sort(key=lambda e: e.集号)
    结果.集们 = 集们
    return 结果


def _解析人物(数据: dict, id: str, 图片语言: str = "zh") -> 人物:
    数据 = 数据 or {}
    别名 = [str(x).strip() for x in 数据.get("also_known_as") or [] if str(x).strip()]
    结果 = 人物(标识=str(数据.get("id") or id),
              名字=str(数据.get("name") or "").strip(),
              # TMDB 没有"人物原名"这个概念，用 also_known_as 的第一条兜着（界面可显示别名）
              原名=别名[0] if 别名 else "")
    头像 = 选最佳图片(_解析图片组(数据.get("images")), 图片类型.人物, 图片语言)
    if 头像 is None and (路径 := str(数据.get("profile_path") or "")):
        头像 = 图片(类型=图片类型.人物, 远端路径=路径)
    结果.头像 = 头像
    return 结果


def _解析演职员(数据: dict) -> list[参演]:
    """``credits`` / ``aggregate_credits`` → 参演们。

    两种结构都要能解析（官方事实，实测踩过）：

    * ``aggregate_credits.cast[].roles[]``（**全剧阵容，剧集优先用它**；``credits`` 只有最新一季）
    * ``credits.cast[].character``（电影，以及剧集没有 aggregate 时的退路）
    * 职员同理：``jobs[]`` vs 单个 ``job``

    排序看 ``cast[].order``（**不是** ``cast_id``：cast_id 是内部主键，顺序毫无意义）。
    """
    来源 = 数据.get("aggregate_credits") if isinstance(数据.get("aggregate_credits"), dict) else None
    是聚合 = 来源 is not None
    if 来源 is None:
        来源 = 数据.get("credits") if isinstance(数据.get("credits"), dict) else {}
    参演们: list[参演] = []
    for 序号, 条 in enumerate(来源.get("cast") or []):
        if not isinstance(条, dict):
            continue
        角色 = ""
        集数 = 0
        if 条.get("roles"):                       # aggregate_credits 结构
            角色 = str((条["roles"][0] or {}).get("character") or "").strip()
            集数 = _整数(条.get("total_episode_count"))
        else:                                     # credits 结构
            角色 = str(条.get("character") or "").strip()
        排序 = 条.get("order")
        排序 = _整数(排序, 序号) if isinstance(排序, int) else 序号
        人物对象 = 人物(标识=str(条.get("id") or ""),
                    名字=str(条.get("name") or "").strip(),
                    原名=str(条.get("original_name") or "").strip())
        if (头像 := str(条.get("profile_path") or "")):
            人物对象.头像 = 图片(类型=图片类型.人物, 远端路径=头像)
        参演们.append(参演(人物=人物对象, 工种=人物工种.演员, 角色=角色,
                        排序=排序, 集数=集数))
    职员: dict[str, dict] = {}
    for 序号, 条 in enumerate(来源.get("crew") or []):
        if not isinstance(条, dict):
            continue
        标识 = str(条.get("id") or "")
        职务们: list[str] = []
        if 条.get("jobs"):                        # aggregate_credits 结构
            职务们 = [str((x or {}).get("job") or "") for x in 条["jobs"]]
        else:                                     # credits 结构
            职务们 = [str(条.get("job") or "")]
        键 = 标识 or f"序号{序号}"
        记录 = 职员.setdefault(键, {"条": 条, "职务们": [], "序号": 序号})
        # 同一个人可能挂多个职务（导演兼制片），**全都收着**再挑优先级最高的工种 ——
        # 只留第一条会把"导演"漏成"制片"
        记录["职务们"].extend(x for x in 职务们 if x)
    for 记录 in 职员.values():
        条 = 记录["条"]
        职务们 = 记录["职务们"]
        人物对象 = 人物(标识=str(条.get("id") or ""),
                    名字=str(条.get("name") or "").strip(),
                    原名=str(条.get("original_name") or "").strip())
        if (头像 := str(条.get("profile_path") or "")):
            人物对象.头像 = 图片(类型=图片类型.人物, 远端路径=头像)
        参演们.append(参演(人物=人物对象, 工种=_工种(职务们),
                        角色=",".join(dict.fromkeys(职务们))[:60],
                        排序=500 + 记录["序号"],
                        集数=_整数(条.get("total_episode_count"))))
    # 演员按 order 升序在前，职员按录入顺序在后（界面通常只展示前 20 个演员）
    参演们.sort(key=lambda x: (0 if x.工种 is 人物工种.演员 else 1, x.排序))
    return 参演们


def _工种(职务们: list[str]) -> 人物工种:
    """把官方 ``job`` 映射到我们自己的工种枚举（一个人挂多个职务时取"最靠前"的那个）。

    ⚠️ **导演必须精确匹配**：真机查《沙丘》(438631) 时发现，crew 里带 "Director"
    字样的人一大堆 —— "First Assistant Director"（副导演）、"Second Assistant
    Director"、"Casting Director"（选角导演）、"Art Director"（美术指导）、
    "Second Unit Director"（第二摄制组）。按子串匹配会把这些**全算成导演**，
    于是详情页"导演"一栏出现的是副导演，真正的维伦纽瓦反而排在后面。
    官方给导演的 job 就是 ``Director``（剧集里有 ``Series Director``）。
    """
    精准 = {"director": 人物工种.导演, "co-director": 人物工种.导演,
          "series director": 人物工种.导演}
    优先 = ((人物工种.编剧, ("writer", "screenplay", "teleplay", "story", "author")),
          (人物工种.制片, ("producer",)),
          (人物工种.作曲, ("music", "composer", "theme song")))
    #: 含关键词但**不是创作岗**的职务（别把"选角导演"当导演、"故事板画师"当编剧）
    排除词 = ("assistant", "casting", "storyboard", "unit", "coordinator",
           "supervisor", "department", "art director", "art department")
    for 职务 in 职务们:
        小写 = (职务 or "").strip().lower()
        if 小写 in 精准:
            return 精准[小写]
    for 工种, 关键词们 in 优先:
        for 职务 in 职务们:
            小写 = (职务 or "").strip().lower()
            if any(坏 in 小写 for 坏 in 排除词):
                continue
            if any(词 in 小写 for 词 in 关键词们):
                return 工种
    return 人物工种.其它


def _解析电影分级(数据: dict) -> list[分级]:
    """``release_dates`` → 分级们。

    * ``certification`` **大量是空串**（"未分级"就是这个样子），必须过滤；
    * 同一个国家可能有多条（影院/数字/电视），按 ``type`` 优先级挑：
      3 影院 > 2 限定影院 > 4 数字 > 5 实物 > 6 电视 > 1 首映；
    * **TMDB 没有 CN**，所以返回的列表按 CN → HK → TW → US → JP → 其它 排好序，
      调用方（或 :meth:`媒体条目.取分级`）拿第一个能用的就是最佳回退结果。
    """
    表: dict[str, list[tuple[int, str]]] = {}
    for 国 in ((数据 or {}).get("release_dates") or {}).get("results") or []:
        if not isinstance(国, dict):
            continue
        国家 = str(国.get("iso_3166_1") or "").upper()
        if not 国家:
            continue
        for 条 in 国.get("release_dates") or []:
            if not isinstance(条, dict):
                continue
            值 = str(条.get("certification") or "").strip()
            if not 值:
                continue                       # 空串占绝大多数，一律丢掉
            类型 = 条.get("type")
            表.setdefault(国家, []).append((_整数(类型, 99), 值))
    结果: list[分级] = []
    for 国家, 候选 in 表.items():
        候选.sort(key=lambda x: _分级类型序(x[0]))
        结果.append(分级(国家=国家, 值=候选[0][1], 来源="tmdb"))
    return _按国家优先排(结果)


def _解析剧集分级(数据: dict) -> list[分级]:
    """``content_ratings`` → 分级们（每个国家一条 ``rating``，同样过滤空串）。"""
    结果: list[分级] = []
    for 国 in ((数据 or {}).get("content_ratings") or {}).get("results") or []:
        if not isinstance(国, dict):
            continue
        国家 = str(国.get("iso_3166_1") or "").upper()
        值 = str(国.get("rating") or "").strip()
        if 国家 and 值:
            结果.append(分级(国家=国家, 值=值, 来源="tmdb"))
    return _按国家优先排(结果)


def _分级类型序(类型: int) -> int:
    顺序 = list(电影分级类型优先)
    return 顺序.index(类型) if 类型 in 顺序 else len(顺序)


def _按国家优先排(分级们: list[分级]) -> list[分级]:
    """按 CN → HK → TW → US → JP → 其它 排序（同国去重，先出现的优先）。"""
    顺序 = {c: i for i, c in enumerate(国家回退顺序)}
    去重: dict[str, 分级] = {}
    for 条 in 分级们:
        if 条.国家 and 条.国家 not in 去重:
            去重[条.国家] = 条
    return sorted(去重.values(), key=lambda x: (顺序.get(x.国家, len(顺序)), x.国家))


def _解析图片组(数据) -> list[图片]:
    """官方 ``images`` 节点 → 图片们（海报/背景/标志/剧照/人物头像都在这个结构里）。"""
    结果: list[图片] = []
    if not isinstance(数据, dict):
        return 结果
    for 键, 类型 in 图片种类映射.items():
        for 条 in 数据.get(键 + "s") or []:
            if not isinstance(条, dict):
                continue
            路径 = str(条.get("file_path") or "")
            if not 路径:
                continue
            结果.append(图片(类型=类型, 远端路径=路径,
                        宽=_整数(条.get("width")), 高=_整数(条.get("height")),
                        语言=str(条.get("iso_639_1") or ""),
                        评分=_浮点(条.get("vote_average")),
                        票数=_整数(条.get("vote_count")), 来源="tmdb"))
    return 结果


def _语言序(语言: str, 偏好: str) -> int:
    """选图的语言优先级：偏好语言 → 无语言（通用图）→ en → 其它。"""
    小写 = (语言 or "").lower()
    偏好小写 = (偏好 or "zh").split("-")[0].split(",")[0].strip().lower()
    if 小写 and 小写 == 偏好小写:
        return 0
    if not 小写:
        return 1
    if 小写 in ("en", "us"):
        return 2
    return 3


def 排序图片(图片们: list[图片], 偏好语言: str = "zh") -> list[图片]:
    """图片排序：语言优先 → 评分高 → 票数多（界面直接按这个顺序展示/下载）。"""
    return sorted(图片们, key=lambda g: (_语言序(g.语言, 偏好语言), -g.评分, -g.票数,
                                      str(g.远端路径)))


def 选最佳图片(图片们: list[图片], 类型: 图片类型,
           偏好语言: str = "zh") -> Optional[图片]:
    """在某一类图里挑一张最好的（有中文用中文，没有就用无语言的通用图）。"""
    候选 = [x for x in 图片们 if x.类型 is 类型]
    return 排序图片(候选, 偏好语言)[0] if 候选 else None


# ============================ 特征判定（决定要不要语言回退） ============================


def _影视缺字段(数据: dict) -> bool:
    """电影/剧集/季：标题或简介是空的就要补一次（简介在 zh-CN 里经常是空的）。"""
    数据 = 数据 or {}
    return not str(数据.get("title") or 数据.get("name") or "").strip() \
        or not str(数据.get("overview") or "").strip()


def _搜索缺字段(数据: dict) -> bool:
    """搜索结果：**所有**条目的标题都空才算"语言没给到"（"没搜到"不算，别再发一次）。"""
    结果们 = (数据 or {}).get("results") or []
    if not 结果们:
        return False
    return all(not str(x.get("title") or x.get("name") or "").strip() for x in 结果们
               if isinstance(x, dict))


def _人物缺字段(数据: dict) -> bool:
    return not str((数据 or {}).get("name") or "").strip()


# ============================ 语言回退时的"补空" ============================


def _补空条目(主: 媒体条目, 补: 媒体条目) -> None:
    """把补空数据里**非空**的字段填到主条目的空字段上（绝不覆盖已有内容）。"""
    for 字段 in ("标题", "原名", "简介", "状态", "原始语言"):
        if not getattr(主, 字段) and getattr(补, 字段):
            setattr(主, 字段, getattr(补, 字段))
    if 主.年份 is None and 补.年份:
        主.年份 = 补.年份
    if not 主.时长分钟 and 补.时长分钟:
        主.时长分钟 = 补.时长分钟
    if not 主.标签 and 补.标签:
        主.标签 = 补.标签
    if not 主.评分 and 补.评分:
        主.评分 = 补.评分
    if not 主.评分票数 and 补.评分票数:
        主.评分票数 = 补.评分票数
    if not 主.分级们 and 补.分级们:
        主.分级们 = 补.分级们
    if not 主.参演们 and 补.参演们:
        主.参演们 = 补.参演们
    if not 主.制片公司 and 补.制片公司:
        主.制片公司 = 补.制片公司
    if not 主.图片们 and 补.图片们:
        主.图片们 = 补.图片们
    for 字段 in ("海报", "背景", "标志"):
        if getattr(主, 字段) is None and getattr(补, 字段) is not None:
            setattr(主, 字段, getattr(补, 字段))
    for 键, 值 in 补.外部ID.items():
        主.外部ID.setdefault(键, 值)
    # 季：按季号把空的标题/简介/海报补上（季名在 zh-CN 里常常整列为空）
    已有 = {s.季号: s for s in 主.季们}
    for 季对象 in 补.季们:
        if (目标 := 已有.get(季对象.季号)) is None:
            主.季们.append(季对象)
            continue
        if not 目标.标题 and 季对象.标题:
            目标.标题 = 季对象.标题
        if not 目标.简介 and 季对象.简介:
            目标.简介 = 季对象.简介
        if not 目标.播出日期 and 季对象.播出日期:
            目标.播出日期 = 季对象.播出日期
        if 目标.集数 == 0 and 季对象.集数:
            目标.集数 = 季对象.集数
        if 目标.海报 is None and 季对象.海报 is not None:
            目标.海报 = 季对象.海报
    主.季们.sort(key=lambda s: s.季号)


def _补空季(主: 季, 补: 季) -> None:
    if not 主.标题 and 补.标题:
        主.标题 = 补.标题
    if not 主.简介 and 补.简介:
        主.简介 = 补.简介
    if not 主.播出日期 and 补.播出日期:
        主.播出日期 = 补.播出日期
    if 主.海报 is None and 补.海报 is not None:
        主.海报 = 补.海报
    已有 = {e.集号: e for e in 主.集们}
    for 集对象 in 补.集们:
        if (目标 := 已有.get(集对象.集号)) is None:
            主.集们.append(集对象)
            continue
        if not 目标.标题 and 集对象.标题:
            目标.标题 = 集对象.标题
        if not 目标.简介 and 集对象.简介:
            目标.简介 = 集对象.简介
    主.集们.sort(key=lambda e: e.集号)
    if not 主.集数:
        主.集数 = len(主.集们)


def _补空人物(主: 人物, 补: 人物) -> None:
    if not 主.名字 and 补.名字:
        主.名字 = 补.名字
    if not 主.原名 and 补.原名:
        主.原名 = 补.原名
    if 主.头像 is None and 补.头像 is not None:
        主.头像 = 补.头像


def _补空候选(主们: list[匹配候选], 补们: list[匹配候选]) -> None:
    """搜索结果补空：按 ``来源标识`` 对齐，只填空的标题/简介/海报。"""
    表 = {x.来源标识: x for x in 主们}
    for 补 in 补们:
        主 = 表.get(补.来源标识)
        if 主 is None:
            continue
        if not 主.标题 and 补.标题:
            主.标题 = 补.标题
        if not 主.原名 and 补.原名:
            主.原名 = 补.原名
        if not 主.简介 and 补.简介:
            主.简介 = 补.简介
        if not 主.海报远端 and 补.海报远端:
            主.海报远端 = 补.海报远端
        if not 主.热度 and 补.热度:
            主.热度 = 补.热度
