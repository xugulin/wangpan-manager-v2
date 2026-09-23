"""Animeko 公益弹幕服务源（**不需要任何凭据**，从零实现）。

为什么要有它：弹弹play 现在连 ``/api/v2/match`` 都要求 AppId 签名（真机实测 403
``Missing Authentication Headers``），而**个人开发者拿 AppId 要审核**，等于"没配就用不了"。
Animeko 的公益弹幕服务读接口是**公开**的（`requiresAuthentication=false`），
实测三步全通、不需要任何 key：

==============================  ==========================================================
干什么                          接口
==============================  ==========================================================
搜索作品                        ``GET {基地址}/v2/subjects/search?q=<关键词>&limit=N``
取作品详情（含集列表）           ``GET {基地址}/v2/subjects/{subjectId}``
取某集弹幕                      ``GET {基地址}/v1/danmaku/{episodeId}?maxCount=&fromTime=&toTime=``
取某集详情                      ``GET {基地址}/v2/subjects/{subjectId}/episodes/{episodeId}``
==============================  ==========================================================

弹幕字段（实测样例）：``{"id": …, "senderId": …, "danmakuInfo": {"playTime": 213992,
"color": -1, "text": "…", "location": "NORMAL"}}``
* ``playTime`` 是**毫秒**；
* ``color`` 是 **ARGB 形式的整数**，白色会返回 **-1**（0xFFFFFFFF）→ 要按 0xFFFFFF 取；
* ``location`` ∈ ``TOP`` / ``BOTTOM`` / ``NORMAL``（与我们的三种模式一一对应）。

限流与礼貌
==========
公益服务，**不要批量抓**：默认两次请求之间至少隔 0.25s、同一个 episodeId 走磁盘缓存
（默认 TTL 6 小时）、同一集的并发请求合并成一次。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..模型 import 弹幕, 弹幕池, 弹幕模式, 来源标识
from .接口 import 弹幕源, 弹幕源错误, 匹配结果, 素材信息

__all__ = ["Animeko源", "解析弹幕", "解析搜索", "默认基地址", "清理缓存"]

#: 公开基地址（Animeko 客户端运行时也是这个；可在配置里覆盖）
默认基地址 = "https://api.animeko.org"
#: 位置枚举 → 我们的模式
位置映射 = {"TOP": 弹幕模式.顶部, "BOTTOM": 弹幕模式.底部, "NORMAL": 弹幕模式.滚动}
#: 默认两次请求的最小间隔（公益服务，别把它当自家 CDN 用）
默认最小间隔秒 = 0.25


def 默认缓存目录() -> Path:
    return Path(__file__).resolve().parents[3] / "数据" / "缓存" / "弹幕" / "animeko"


@dataclass
class _缓存项:
    时刻: float
    数据: dict


class Animeko源(弹幕源):
    """Animeko 公益弹幕：搜索 → 找集 → 取弹幕（全部公开，无需凭据）。"""

    标识 = "animeko"
    名字 = "Animeko 公益弹幕"

    def __init__(self, 基地址: str = 默认基地址, 传输: Optional[Callable] = None,
                 缓存目录: Optional[Path] = None, 缓存TTL秒: float = 6 * 3600.0,
                 最小请求间隔秒: float = 默认最小间隔秒,
                 超时秒: float = 20.0, 日志回调=None) -> None:
        self.基地址 = str(基地址 or 默认基地址).rstrip("/")
        self.传输 = 传输
        self.缓存目录 = Path(缓存目录) if 缓存目录 else 默认缓存目录()
        self.缓存TTL秒 = float(缓存TTL秒)
        self.最小请求间隔秒 = max(0.0, float(最小请求间隔秒))
        self.超时秒 = float(超时秒)
        self._日志 = 日志回调 or (lambda _t: None)
        self._锁 = threading.Lock()
        self._在途: dict[str, threading.Event] = {}
        self._节流锁 = threading.Lock()
        self._上次请求: Optional[float] = None
        self._时钟 = time.time
        self._睡眠 = time.sleep
        self._内存: dict[str, _缓存项] = {}
        self._已关闭 = False
        self.请求次数 = 0

    # ---------------- 契约 ----------------

    def 能匹配(self) -> bool:
        return True

    def 关闭(self) -> None:
        self._已关闭 = True
        with self._锁:
            for 事件 in self._在途.values():
                事件.set()
            self._在途.clear()

    # ---------------- 匹配 ----------------

    def 搜索(self, 关键词: str, 上限: int = 8) -> list[dict]:
        """搜索作品；返回原始条目（含 id/name/nameCn/airDate/tags 等）。"""
        关键词 = str(关键词 or "").strip()
        if not 关键词:
            return []
        数据 = self._取("/v2/subjects/search", {"q": 关键词, "limit": max(1, int(上限))})
        条目们 = 数据.get("items") if isinstance(数据, dict) else None
        return list(条目们 or [])

    def 取作品(self, subjectId) -> dict:
        return self._取(f"/v2/subjects/{subjectId}") or {}

    def 取集列表(self, subjectId) -> list[dict]:
        作品 = self.取作品(subjectId)
        集们 = 作品.get("episodes") or 作品.get("eps") or []
        return [x for x in 集们 if isinstance(x, dict)]

    def 匹配(self, 素材: 素材信息) -> list[匹配结果]:
        """按素材信息找作品与集：搜索 → 逐个作品的集列表里找对应集号。"""
        from ..匹配 import 文件名相似度, 规范化名字
        名字 = 素材.标题 or Path(素材.文件名 or "").stem
        # ⚠️ 查词必须把"季集/年份/发布标签"摘掉：真机实测，拿
        #   "葬送的芙莉莲 S01E01" 去搜，最高分只有 48.7（低于阈值）；
        #   摘成 "葬送的芙莉莲" 就能搜到正片并拿到 100 分。
        # 查词顺序有讲究：**先最干净的那个**（摘掉季集/发布标签/画质的）。
        # 真机实测（同一个文件）：
        #   「葬送的芙莉莲 S01E01」→ 只搜到番外与续作（最高 48.7，低于阈值）
        #   「葬送的芙莉莲」      → 搜到正片并拿到 100 分、选中 ep=1227087
        # 所以先干净查词；只有当最高相似度还不够像时，才继续试别的查词（省 API 调用）。
        干净 = _去季集(名字)
        查询词们: list[str] = []
        for 查询 in (干净, 规范化名字(干净) if 干净 else "", 规范化名字(名字), 名字):
            查询 = str(查询 or "").strip()
            if 查询 and 查询 not in 查询词们:
                查询词们.append(查询)
        if not 查询词们:
            return []
        结果们: list[匹配结果] = []
        作品们: list[dict] = []
        见过: set = set()
        for 查询 in 查询词们[:3]:
            try:
                这一批 = self.搜索(查询, 上限=6)
            except 弹幕源错误 as 错:
                self._日志(f"[animeko] 搜索失败（{查询}）：{错}")
                这一批 = []
            for 作品 in 这一批:
                if 作品.get("id") in 见过:
                    continue
                见过.add(作品.get("id"))
                作品们.append(作品)
            if 这一批:
                self._日志(f"[animeko] 用「{查询}」搜到 {len(这一批)} 个作品")
                最好 = max(文件名相似度(str(x.get("nameCn") or x.get("name") or ""), 干净 or 名字)
                         for x in 这一批)
                if 最好 >= 60:
                    break
        if not 作品们:
            return []
        for 作品 in 作品们[:6]:
            标题 = str(作品.get("nameCn") or 作品.get("name") or "")
            # 比的是"干净的名字"：带 S01E01 的文件名会让所有候选都被压到 50 分上下
            # （真机实测：同一个正片，用干净名比是 100 分，用原名比只有 48.7）
            # nameCn 与 name 都试一遍取最大：有的作品中文名空、有的原名（罗马字）更像用户的英文文件名
            原名 = str(作品.get("name") or "")
            相似 = max(文件名相似度(甲, 干净 or 名字) for 甲 in (标题, 原名) if 甲)
            相似 = max(相似, 文件名相似度(标题, 名字))
            try:
                集们 = self.取集列表(作品.get("id"))
            except 弹幕源错误:
                continue
            if not 集们:
                continue
            目标集 = self._选集(集们, 素材)
            if 目标集 is None and 素材.集号 is not None:
                continue                 # 有集号却对不上 → 这个作品不是它
            if 目标集 is None:
                目标集 = 集们[0]
            结果们.append(匹配结果(
                标识=str(目标集.get("episodeId") or 目标集.get("id") or ""),
                标题=标题, 集标题=str(目标集.get("name") or ""),
                集号=str(目标集.get("sort") or 素材.集号 or ""),
                类型=str(作品.get("type") or ""), 分数=相似,
                理由=f"标题相似度 {相似:.0f}",
                原始={"作品": 作品, "集": 目标集, "源": self.标识}))
        return [x for x in 结果们 if x.标识]

    @staticmethod
    def _选集(集们: list[dict], 素材: 素材信息) -> Optional[dict]:
        """按集号（或集标题）挑一集；挑不到返回 None。"""
        if 素材.集号 is not None:
            想要 = str(int(素材.集号))
            for 集 in 集们:
                排序 = str(集.get("sort") or "").strip()
                if 排序 and 排序.split(".")[0].lstrip("0") == 想要.lstrip("0"):
                    return 集
                if 排序.isdigit() and int(排序) == int(素材.集号):
                    return 集
        if 素材.额外.get("集标题"):
            想要 = str(素材.额外["集标题"])
            for 集 in 集们:
                if str(集.get("name") or "") == 想要:
                    return 集
        return None

    # ---------------- 取弹幕 ----------------

    def 取弹幕(self, 标识: str) -> 弹幕池:
        标识 = str(标识 or "").strip()
        if not 标识:
            raise 弹幕源错误("没有 episodeId，取不了弹幕")
        数据 = self._取(f"/v1/danmaku/{标识}",
                      {"maxCount": 8000, "fromTime": 0, "toTime": -1})
        列 = 数据.get("danmakuList") if isinstance(数据, dict) else None
        池 = 解析弹幕(列 or [], 来源说明=f"animeko:{标识}")
        self._日志(f"[animeko] 第 {标识} 集取到 {len(池)} 条弹幕")
        return 池

    # ---------------- HTTP（带缓存/节流/在途合并） ----------------

    def _取(self, 路径: str, 参数: Optional[dict] = None) -> dict:
        if self._已关闭:
            raise 弹幕源错误("源已关闭")
        地址 = self.基地址 + 路径
        if 参数:
            地址 += "?" + urllib.parse.urlencode({k: str(v) for k, v in 参数.items()
                                              if v is not None})
        缓存键 = hashlib.blake2s(地址.encode("utf-8"), digest_size=12).hexdigest()
        命中 = self._读缓存(缓存键)
        if 命中 is not None:
            return 命中
        # 同一个地址并发只发一次（多人同时点同一集很常见）
        with self._锁:
            if 缓存键 in self._在途:
                事件 = self._在途[缓存键]
            else:
                事件 = None
                self._在途[缓存键] = threading.Event()
        if 事件 is not None:
            if 事件.wait(timeout=self.超时秒 * 2):
                再读 = self._读缓存(缓存键)
                if 再读 is not None:
                    return 再读
            raise 弹幕源错误("等待同一请求超时")
        try:
            self._节流()
            self.请求次数 += 1
            数据 = self._发(地址)
            self._写缓存(缓存键, 数据)
            return 数据
        finally:
            with self._锁:
                完成 = self._在途.pop(缓存键, None)
            if 完成 is not None:
                完成.set()

    def _发(self, 地址: str) -> dict:
        if self.传输 is not None:
            from .接口 import 请求
            应答 = self.传输(请求(方法="GET", 地址=地址,
                            头={"User-Agent": "wangpan-v2/2.0", "Accept": "application/json"},
                            超时=self.超时秒))
            码 = int(getattr(应答, "状态码", 200) or 200)
            文本 = 应答.文本() if hasattr(应答, "文本") else str(getattr(应答, "体", ""))
            if 码 != 200:
                raise 弹幕源错误(f"GET {地址} 返回 HTTP {码}：{str(文本)[:160]}")
            try:
                return json.loads(文本 or "{}")
            except ValueError as 错:
                raise 弹幕源错误(f"响应不是 JSON：{错}") from 错
        请求对象 = urllib.request.Request(
            地址, headers={"User-Agent": "wangpan-v2/2.0", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(请求对象, timeout=self.超时秒) as 回应:
                文本 = 回应.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as 错:
            raise 弹幕源错误(f"GET {地址} 返回 HTTP {错.code}") from None
        except Exception as 错:  # noqa: BLE001
            raise 弹幕源错误(f"连不上 Animeko 弹幕服务：{错}") from None
        try:
            return json.loads(文本 or "{}")
        except ValueError as 错:
            raise 弹幕源错误(f"响应不是 JSON：{错}") from 错

    def _节流(self) -> None:
        if self.最小请求间隔秒 <= 0:
            return
        with self._节流锁:
            现在 = float(self._时钟())
            if self._上次请求 is not None:
                等 = self.最小请求间隔秒 - (现在 - self._上次请求)
                if 等 > 0:
                    self._睡眠(等)
            self._上次请求 = float(self._时钟())

    # ---------------- 缓存 ----------------

    def _读缓存(self, 键: str) -> Optional[dict]:
        if self.缓存TTL秒 <= 0:
            return None
        现在 = float(self._时钟())
        项 = self._内存.get(键)
        if 项 is not None and 现在 - 项.时刻 <= self.缓存TTL秒:
            return 项.数据
        路径 = self.缓存目录 / f"{键}.json"
        try:
            if 路径.is_file():
                原文 = json.loads(路径.read_text(encoding="utf-8"))
                if 现在 - float(原文.get("时刻") or 0) <= self.缓存TTL秒:
                    数据 = 原文.get("数据") or {}
                    self._内存[键] = _缓存项(时刻=float(原文["时刻"]), 数据=数据)
                    return 数据
        except Exception:  # noqa: BLE001 - 缓存坏了当没有
            pass
        return None

    def _写缓存(self, 键: str, 数据: dict) -> None:
        if self.缓存TTL秒 <= 0:
            return
        现在 = float(self._时钟())
        self._内存[键] = _缓存项(时刻=现在, 数据=数据)
        try:
            self.缓存目录.mkdir(parents=True, exist_ok=True)
            临时 = self.缓存目录 / f"{键}.tmp"
            临时.write_text(json.dumps({"时刻": 现在, "数据": 数据}, ensure_ascii=False),
                          encoding="utf-8")
            os.replace(临时, self.缓存目录 / f"{键}.json")
        except OSError:
            pass

    def 诊断(self) -> dict:
        return {"标识": self.标识, "基地址": self.基地址, "请求次数": self.请求次数,
                "缓存目录": str(self.缓存目录), "缓存TTL小时": round(self.缓存TTL秒 / 3600, 1),
                "最小间隔秒": self.最小请求间隔秒, "需要凭据": False}


def _去季集(名字: str) -> str:
    """把文件名里的季集/年份/日期/发布标签摘掉，只留作品名（搜素用）。

    真实文件名长这样：``[Lilith-Raws] Sousou no Frieren - 01 [1080p].mkv``、
    ``葬送的芙莉莲 S01E01.mkv``、``某番 第12话``……
    直接拿去搜会搜不到（或搜到错的东西），所以先摘干净。
    """
    import re as _re
    文本 = _re.sub(r"\[[^\]]*\]", " ", str(名字 or ""))        # 方括号里的组名/画质
    文本 = _re.sub(r"\((?:19|20)\d{2}\)", " ", 文本)            # 年份
    文本 = _re.sub(r"\b[Ss]\d{1,2}[Ee]\d{1,3}(?:-[Ee]\d{1,3})?\b", " ", 文本)
    文本 = _re.sub(r"\b\d{1,2}[xX]\d{1,3}\b", " ", 文本)
    文本 = _re.sub(r"第\s*\d{1,4}\s*[集话話]", " ", 文本)
    文本 = _re.sub(r"\b\d{3,4}[pPiI]\b", " ", 文本)             # 1080p
    文本 = _re.sub(r"(?i)\b(?:web-?dl|bluray|bdrip|hdtv|x264|x265|h\.?264|h\.?265|"
                  r"hevc|aac|flac|10bit|8bit|mkv|mp4|cht|chs|gb|big5)\b", " ", 文本)
    文本 = _re.sub(r"[-_]+", " ", 文本)
    文本 = _re.sub(r"\s+", " ", 文本).strip(" -_.·")
    # 收尾：把末尾孤立的集号去掉（"葬送的芙莉莲 01" → "葬送的芙莉莲"）
    文本 = _re.sub(r"[\s._-]+\d{1,3}$", "", 文本).strip()
    return 文本


# ---------------- 解析 ----------------

def 解析弹幕(列, 来源说明: str = "animeko") -> 弹幕池:
    """把 ``danmakuList`` 解析成弹幕池（字段名与取值都以实测为准）。"""
    池 = 弹幕池(来源说明=来源说明)
    for 项 in 列 or []:
        if not isinstance(项, dict):
            continue
        内容 = 项.get("danmakuInfo") or {}
        文本 = str(内容.get("text") or "").strip()
        if not 文本:
            continue
        try:
            毫秒 = int(内容.get("playTime") or 0)
        except (TypeError, ValueError):
            continue
        if 毫秒 < 0:
            continue
        try:
            颜色 = int(内容.get("color") if 内容.get("color") is not None else -1)
        except (TypeError, ValueError):
            颜色 = -1
        # ⚠️ 实测：白色返回 **-1**（ARGB 全 1）→ 统一按低 24 位取 RGB
        颜色 = 颜色 & 0xFFFFFF
        位置 = str(内容.get("location") or "NORMAL").upper()
        池.条目们.append(弹幕(
            毫秒=毫秒, 文本=文本, 模式=位置映射.get(位置, 弹幕模式.滚动),
            颜色=颜色, 发送者=str(项.get("senderId") or ""),
            来源=来源标识.其它, 标识=str(项.get("id") or "")))
    池.排序()
    return 池


def 解析搜索(数据: dict) -> list[dict]:
    """把搜索响应变成"候选列表"（给界面用）。"""
    结果 = []
    for 项 in (数据 or {}).get("items") or []:
        if not isinstance(项, dict):
            continue
        结果.append({"id": 项.get("id"),
                     "标题": 项.get("nameCn") or 项.get("name") or "",
                     "原名": 项.get("name") or "",
                     "首播": 项.get("airDate") or "",
                     "评分": 项.get("score") or 0,
                     "标签": 项.get("tags") or [],
                     "集数": 项.get("mainEpisodeCount") or 0,
                     "海报": 项.get("imageLarge") or ""})
    return 结果


def 清理缓存(目录: Optional[Path] = None, 保留天数: float = 30.0) -> int:
    """删掉太旧的弹幕缓存（公益服务的数据也会变，别一直留着）。"""
    目录 = Path(目录) if 目录 else 默认缓存目录()
    截止 = time.time() - max(1.0, 保留天数) * 86400.0
    删除 = 0
    if not 目录.is_dir():
        return 0
    for 路径 in 目录.glob("*.json"):
        try:
            if 路径.stat().st_mtime < 截止:
                路径.unlink()
                删除 += 1
        except OSError:
            continue
    return 删除
