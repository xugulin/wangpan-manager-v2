"""HTTP 适配器（M6）：把一个"网页目录/直链列表"或"单个直链"当成网盘。

用途：**网盘直链就是 http(s)** —— 有了它，"下载网盘文件"这条链路在没有任何
真实网盘账号的情况下也能端到端跑通（本地起个 HTTP 服务器就是完整场景）。
只依赖标准库 urllib（V2 不引第三方 HTTP 客户端）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from .接口 import 直链, 条目, 适配器, 不支持

__all__ = ["HTTP适配器", "规范地址"]


def 规范地址(地址: str) -> str:
    """把 URL 里的非 ASCII 字符百分号编码（**必须做**，否则连请求都发不出去）。

    实测踩到：文件名是中文时 ``urllib`` 直接抛
    ``UnicodeEncodeError: 'ascii' codec can't encode characters`` —— 因为 HTTP 请求行
    只能是 ASCII。网盘里的中文文件名太常见了，这一步不能省。
    已经编码过的 ``%XX`` 要保留（``%`` 放进 safe），否则会二次编码。
    """
    文本 = str(地址 or "")
    if not 文本:
        return 文本
    解析 = urllib.parse.urlsplit(文本)
    路径 = urllib.parse.quote(解析.path, safe="/%:@!$&'()*+,;=~-._")
    查询 = urllib.parse.quote(解析.query, safe="=&%:@!$'()*+,;~-._?/")
    return urllib.parse.urlunsplit((解析.scheme, 解析.netloc, 路径, 查询,
                                  解析.fragment))

#: 认这些后缀当"媒体文件"，列目录时用来筛（也可当普通文件列出来）
媒体后缀 = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts", ".flv", ".m4v",
          ".mp3", ".flac", ".aac", ".m4a", ".wav", ".jpg", ".png", ".zip", ".srt")


class HTTP适配器(适配器):
    """基地址 + 可选请求头；``索引地址`` 指向一个 JSON 清单时按清单列目录。"""

    名字 = "HTTP 直链"
    能力 = {"列出", "直链", "下载"}
    #: 告诉传输引擎：这个适配器可以走多段并发下载（引擎会用 分段下载）
    支持分段下载 = True

    def __init__(self, 基地址: str = "", 请求头: Optional[dict] = None,
                 索引地址: str = "") -> None:
        self.基地址 = str(基地址 or "").rstrip("/")
        self.请求头 = dict(请求头 or {})
        self.索引地址 = str(索引地址 or "")
        self._清单: list[条目] = []
        #: 最近一次"服务器不认 Range，只能从头下"的半截大小（诊断用）
        self.上次断点被拒 = 0
        if self.索引地址:
            self._读清单()

    # ---------------- 内部 ----------------

    def _请(self, 地址: str, 头额外: Optional[dict] = None):
        头 = {"User-Agent": "wangpan-v2/2.0", **self.请求头, **(头额外 or {})}
        请求 = urllib.request.Request(规范地址(地址), headers=头)
        return urllib.request.urlopen(请求, timeout=30)

    def _读清单(self) -> None:
        """清单格式：``[{"名字":…, "地址":…, "大小":…, "是目录":false}, …]``。"""
        try:
            with self._请(self.索引地址) as 应答:
                数据 = json.loads(应答.read().decode("utf-8"))
        except Exception as 错:  # noqa: BLE001
            raise 不支持(f"读不了索引清单：{错}")
        项们 = 数据.get("文件") if isinstance(数据, dict) else 数据
        for 项 in (项们 or []):
            地址 = str(项.get("地址") or 项.get("url") or "")
            名字 = str(项.get("名字") or 项.get("name") or Path(地址).name)
            self._清单.append(条目(名字=名字, 路径=地址, 是目录=bool(项.get("是目录")),
                                大小=int(项.get("大小") or 0),
                                标识=地址, 额外={"请求头": 项.get("请求头") or {}}))

    # ---------------- 只读 ----------------

    def 列出(self, 路径: str = "/") -> list[条目]:
        if self._清单:
            return list(self._清单)
        if not self.基地址:
            raise 不支持("没有基地址也没有索引清单，无法列目录")
        # 没有清单时：把基地址当成"单个文件"，列出来只有一个条目
        名字 = Path(urllib.parse.urlparse(self.基地址).path).name or self.基地址
        return [条目(名字=名字, 路径=self.基地址, 是目录=False, 标识=self.基地址)]

    def 取直链(self, 路径: str) -> 直链:
        地址 = str(路径 or "")
        if not 地址.startswith(("http://", "https://")):
            if not self.基地址:
                raise 不支持(f"不是 http 地址：{路径}")
            地址 = urllib.parse.urljoin(self.基地址 + "/", 地址.lstrip("/"))
        头 = dict(self.请求头)
        for 项 in self._清单:
            if 项.路径 == 地址:
                头.update(项.额外.get("请求头") or {})
                break
        # 先用 HEAD 拿大小（很多简单服务器不支持 Range，但都支持 HEAD）；
        # 拿不到再试"Range: bytes=0-0"（CDN 上 HEAD 常被禁，但支持 Range）。
        大小 = 0
        try:
            请求 = urllib.request.Request(
                规范地址(地址), method="HEAD",
                headers={"User-Agent": "wangpan-v2/2.0", **头})
            with urllib.request.urlopen(请求, timeout=20) as 应答:
                大小 = int(应答.headers.get("Content-Length") or 0)
        except Exception:  # noqa: BLE001
            大小 = 0
        if not 大小:
            try:
                应答 = self._请(地址, {"Range": "bytes=0-0"})
                范围 = 应答.headers.get("Content-Range") or ""
                if "/" in 范围:
                    大小 = int(范围.rsplit("/", 1)[-1])
                应答.close()
            except Exception:  # noqa: BLE001
                大小 = 0
        return 直链(地址=地址, 请求头=头, 大小=大小, 备注="HTTP 直链")

    def _日志_断点重来(self, 断点: int) -> None:
        """服务器不支持断点续传时的记录（不打印内容，只说明发生了什么）。"""
        self.上次断点被拒 = 断点

    # ---------------- 写 ----------------

    def 下载(self, 路径: str, 落点, 进度回调: Optional[Callable[[int, int], None]] = None,
          取消=None):
        直链信息 = self.取直链(路径)
        目标 = Path(落点)
        目标.parent.mkdir(parents=True, exist_ok=True)
        临时 = 目标.with_suffix(目标.suffix + ".部分")
        已写 = 0
        断点 = 临时.stat().st_size if 临时.is_file() else 0
        头 = dict(直链信息.请求头)
        if 断点:
            头["Range"] = f"bytes={断点}-"
        总数 = 直链信息.大小 or 0
        try:
            应答 = self._请(直链信息.地址, 头)
            # ⚠️ 关键校验：请求了 Range，但服务器**不认**它、回了 200 整份文件 ——
            #    这时如果还往后追加，文件就会变成"半截 + 整份"（实测 300000 变 400000）。
            #    必须丢掉半截、从头写。
            if 断点 and int(getattr(应答, "status", 200) or 200) != 206:
                self._日志_断点重来(断点)
                断点 = 0
                临时.unlink(missing_ok=True)
            with 应答, open(临时, "ab" if 断点 else "wb") as 写:
                内容长度 = int(应答.headers.get("Content-Length") or 0)
                if not 总数:
                    总数 = 内容长度 + 断点
                if 进度回调 is not None:
                    进度回调(已写 + 断点, 总数)
                while True:
                    if 取消 is not None and 取消():
                        raise 不支持("已取消")
                    块 = 应答.read(256 * 1024)
                    if not 块:
                        break
                    写.write(块)
                    已写 += len(块)
                    if 进度回调 is not None:
                        进度回调(已写 + 断点, 总数)
        except urllib.error.HTTPError as 错:
            # 服务器不认 Range（返回 200 而不是 206）时，从头下
            if 断点 and 错.code in (200, 416):
                临时.unlink(missing_ok=True)
                return self.下载(路径, 落点, 进度回调, 取消)
            raise
        os.replace(临时, 目标)
        return 目标
