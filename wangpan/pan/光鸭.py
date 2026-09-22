"""光鸭云盘适配器（M6 真实网盘）：V2 自己的实现。

协议来源：光鸭网页端接口（**只看协议事实，不调用、不引入别的项目的代码**）。
用到的接口：

==============================  ====================================================
干什么                          接口
==============================  ====================================================
列目录                          ``POST {基地址}/userres/v1/file/get_file_list``
                                body ``{"parentId","pageSize","orderBy","sortType","page"}``
取直链                          ``POST {基地址}/userres/v1/get_res_download_url``
                                body ``{"fileId"}`` → ``data``（含直链）
刷新令牌（2 小时过期）           ``POST https://account.guangyapan.com/v1/auth/token``
                                body ``{"grant_type":"refresh_token","refresh_token","client_id"}``
==============================  ====================================================

条目字段（实测）：``fileId`` / ``fileName`` / ``fileSize`` / ``resType``（**2 = 目录**，
1 = 文件；``dirType`` 不能用来判目录，目录和文件都可能是 1）/ ``fileType`` / ``ext`` /
``utime``。

令牌存在 **V2 自己的** ``数据/令牌_光鸭.json``（访问令牌 + 刷新令牌 + 过期时间戳），
过期前 5 分钟自动刷新 —— 与其它项目无关。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from .接口 import 直链, 条目, 适配器, 不支持

__all__ = ["光鸭适配器", "光鸭令牌", "默认令牌路径", "认证中心", "客户端ID", "基地址"]

#: 接口基地址（实测：**api.**guangyapan.com 才是接口；www. 是网页，
#:  往 www 发 POST 会得到 HTTP 405 Not Allowed）
基地址 = "https://api.guangyapan.com"
#: 网页来源（服务端会看 origin/referer）
网页地址 = "https://www.guangyapan.com"
#: 认证中心（刷新令牌用）
认证中心 = "https://account.guangyapan.com"
#: 网页端客户端标识（协议事实，公开在网页请求里）
客户端ID = "aMe-8VSlkrbQXpUR"
#: access_token 默认 2 小时；提前这么久就刷新
提前刷新秒 = 300.0


def 默认令牌路径() -> Path:
    return Path(__file__).resolve().parents[2] / "数据" / "令牌_光鸭.json"


class 光鸭令牌:
    """令牌的存取与刷新（**吞异常**：令牌坏了只当没登录，不让程序起不来）。"""

    def __init__(self, 路径: Optional[Path] = None) -> None:
        self.路径 = Path(路径) if 路径 else 默认令牌路径()
        self.访问令牌 = ""
        self.刷新令牌 = ""
        self.过期时间戳 = 0.0
        self.用户标识 = ""
        self._锁 = threading.RLock()
        self.读()

    # ---------------- 读写 ----------------

    def 读(self) -> bool:
        try:
            if not self.路径.is_file():
                return False
            数据 = json.loads(self.路径.read_text(encoding="utf-8"))
            self.访问令牌 = str(数据.get("访问令牌") or "")
            self.刷新令牌 = str(数据.get("刷新令牌") or "")
            self.过期时间戳 = float(数据.get("过期时间戳") or 0.0)
            self.用户标识 = str(数据.get("用户标识") or "")
            return bool(self.访问令牌)
        except Exception:  # noqa: BLE001
            return False

    def 存(self) -> bool:
        try:
            self.路径.parent.mkdir(parents=True, exist_ok=True)
            文本 = json.dumps({"访问令牌": self.访问令牌, "刷新令牌": self.刷新令牌,
                           "过期时间戳": self.过期时间戳,
                           "用户标识": self.用户标识,
                           "保存时间": time.strftime("%Y-%m-%d %H:%M:%S")},
                           ensure_ascii=False, indent=1)
            临时 = self.路径.with_suffix(".tmp")
            临时.write_text(文本, encoding="utf-8")
            os.replace(临时, self.路径)
            try:                       # 令牌是凭据：只给自己看
                os.chmod(self.路径, 0o600)
            except OSError:
                pass
            return True
        except Exception:  # noqa: BLE001
            return False

    def 写(self, 访问令牌: str, 刷新令牌: str = "", 有效期秒: float = 7200.0,
          用户标识: str = "") -> None:
        with self._锁:
            self.访问令牌 = str(访问令牌 or "")
            if 刷新令牌:
                self.刷新令牌 = str(刷新令牌)
            self.过期时间戳 = time.time() + float(有效期秒 or 7200.0)
            if 用户标识:
                self.用户标识 = str(用户标识)
            self.存()

    # ---------------- 过期与刷新 ----------------

    def 有效(self, 余量秒: float = 提前刷新秒) -> bool:
        return bool(self.访问令牌) and time.time() < (self.过期时间戳 - 余量秒)

    def 需要刷新(self) -> bool:
        return bool(self.刷新令牌) and not self.有效()

    def 刷新(self, 请求=None) -> bool:
        """用 refresh_token 换新的 access_token（成功返回 True）。"""
        if not self.刷新令牌:
            return False
        请求 = 请求 or _简单请求
        体 = json.dumps({"grant_type": "refresh_token",
                       "refresh_token": self.刷新令牌,
                       "client_id": 客户端ID}).encode()
        try:
            数据 = 请求("POST", f"{认证中心}/v1/auth/token", 体,
                      {"Content-Type": "application/json"})
        except Exception:  # noqa: BLE001
            return False
        新访问 = str((数据 or {}).get("access_token") or "")
        if not 新访问:
            return False
        self.写(新访问, str((数据 or {}).get("refresh_token") or ""),
               float((数据 or {}).get("expires_in") or 7200.0))
        return True

    def 取(self) -> str:
        """拿一个可用令牌（快过期就先刷新；没有就返回空串）。"""
        with self._锁:
            if not self.有效() and self.需要刷新():
                self.刷新()
            return self.访问令牌 if self.有效(0.0) else ""


def _简单请求(方法: str, 地址: str, 体: Optional[bytes] = None,
          头: Optional[dict] = None) -> dict:
    """最小的 JSON 请求（标准库；V2 不引第三方 HTTP 客户端）。"""
    from urllib.parse import quote, urlsplit, urlunsplit
    解析 = urlsplit(地址)
    地址 = urlunsplit((解析.scheme, 解析.netloc,
                     quote(解析.path, safe="/%:@!$&'()*+,;=~-._"),
                     quote(解析.query, safe="=&%:@!$'()*+,;~-._?/"), ""))
    请求 = urllib.request.Request(
        地址, data=体, method=方法,
        headers={
            # 这些头是网页端同款（少了 origin/referer 服务端会拒绝）
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.8",
            "origin": 网页地址,
            "referer": 网页地址 + "/",
            "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/147.0.0.0 Safari/537.36"),
            **(头 or {})})
    with urllib.request.urlopen(请求, timeout=30) as 应答:
        原文 = 应答.read().decode("utf-8", "replace")
    try:
        return json.loads(原文) if 原文 else {}
    except Exception:  # noqa: BLE001
        return {}


class 光鸭适配器(适配器):
    """光鸭云盘的 V2 实现（列目录 / 取直链 / 下载交给传输引擎）。"""

    名字 = "光鸭云盘"
    能力 = {"列出", "直链", "下载"}
    支持分段下载 = True

    def __init__(self, 令牌: Optional[光鸭令牌] = None, 基: str = 基地址,
                 请求=None, 日志回调=None) -> None:
        self.令牌 = 令牌 or 光鸭令牌()
        self.基 = str(基 or 基地址).rstrip("/")
        self._请求 = 请求 or self._默认请求
        self._日志 = 日志回调 or (lambda _t: None)
        #: 路径 → 父目录 fileId（根是空串）；列目录时顺手填，避免每次都从根爬
        self._路径缓存: dict[str, str] = {"/": ""}

    # ---------------- 请求 ----------------

    def _默认请求(self, 方法: str, 地址: str, 体: Optional[bytes] = None,
               头: Optional[dict] = None) -> dict:
        return _简单请求(方法, 地址, 体, 头)

    def _授权头(self) -> dict:
        """授权头（**在适配器里统一加**：换传输层实现时不会漏掉鉴权）。

        踩坑：一开始把 Bearer 头写在默认传输层里，结果测试注入假传输层后
        请求就**没有授权头**了 —— 鉴权属于"这个网盘的协议"，不该藏在 I/O 层。
        """
        令牌 = self.令牌.取()
        if not 令牌:
            raise 不支持("光鸭未登录：没有可用访问令牌（先在网盘页登录/导入令牌）")
        return {"authorization": f"Bearer {令牌}"}

    def _带客户端(self, 体: dict) -> dict:
        """请求体里补上客户端标识（网页端每个业务请求都带）。"""
        体 = dict(体 or {})
        体.setdefault("clientId", 客户端ID)
        return 体

    def _调(self, 路径: str, 体: dict) -> dict:
        数据 = json.dumps(体).encode("utf-8")
        结果 = self._请求("POST", f"{self.基}{路径}", 数据,
                       {**self._授权头(), "Content-Type": "application/json"})
        if not isinstance(结果, dict):
            raise 不支持(f"光鸭返回了看不懂的内容：{结果!r}")
        代码 = 结果.get("code", 结果.get("status"))
        if 代码 not in (None, 0, 200, "0", "200", "success"):
            raise 不支持(f"光鸭接口报错：{结果.get('msg') or 结果.get('message') or 代码}")
        return 结果.get("data") if isinstance(结果.get("data"), dict) else 结果

    # ---------------- 契约实现 ----------------

    def 列出(self, 路径: str = "/") -> list[条目]:
        parent = self._解析父目录(路径)
        数据 = self._调("/userres/v1/file/get_file_list",
                      self._带客户端({"parentId": parent, "pageSize": 100,
                                 "orderBy": 3, "sortType": 1, "page": 0}))
        列表 = 数据.get("list") or 数据.get("fileList") or 数据.get("records") or []
        if not 列表 and isinstance(数据, list):
            列表 = 数据
        结果: list[条目] = []
        for 项 in 列表:
            if not isinstance(项, dict):
                continue
            fileId = str(项.get("fileId") or 项.get("resId") or "")
            名字 = str(项.get("fileName") or 项.get("name") or fileId)
            是目录 = self._是目录(项)
            路径全 = ("/" if 路径.endswith("/") else 路径 + "/") + 名字
            if 是目录 and fileId:
                self._路径缓存[路径全] = fileId
            结果.append(条目(名字=名字, 路径=路径全, 是目录=是目录,
                          大小=0 if 是目录 else int(项.get("fileSize") or 0),
                          修改时间=self._时间(项.get("utime") or 项.get("ctime")),
                          标识=fileId, 额外={"原始": 项}))
        return 结果

    @staticmethod
    def _是目录(项: dict) -> bool:
        """实测：``resType == 2`` 才是目录（``dirType`` 目录/文件都可能是 1）。"""
        值 = 项.get("resType")
        if 值 is not None:
            try:
                return int(值) == 2
            except (TypeError, ValueError):
                pass
        return 项.get("fileType") in (None, "") and 项.get("fileSize") in (None, "")

    @staticmethod
    def _时间(值) -> float:
        try:
            数字 = float(值)
        except (TypeError, ValueError):
            return 0.0
        if 数字 > 10_000_000_000:          # 毫秒
            数字 /= 1000.0
        return 数字

    def _解析父目录(self, 路径: str) -> str:
        """把 ``/影片/2024`` 变成最后一级目录的 fileId（逐级查并缓存）。"""
        文本 = str(路径 or "/").strip()
        if 文本 in ("", "/"):
            return ""
        文本 = "/" + 文本.strip("/")
        if 文本 in self._路径缓存:
            return self._路径缓存[文本]
        当前 = ""
        已走 = ""
        for 段 in [x for x in 文本.split("/") if x]:
            目标路径 = 已走 + "/" + 段
            if 目标路径 in self._路径缓存:
                当前 = self._路径缓存[目标路径]
                已走 = 目标路径
                continue
            # 列 parentId=当前 这一层，找到同名目录
            数据 = self._调("/userres/v1/file/get_file_list",
                          self._带客户端({"parentId": 当前, "pageSize": 100,
                                     "orderBy": 3, "sortType": 1, "page": 0}))
            列表 = 数据.get("list") or []
            命中 = ""
            for 项 in 列表:
                if str(项.get("fileName") or "") == 段 and self._是目录(项):
                    命中 = str(项.get("fileId") or "")
                    break
            if not 命中:
                raise 不支持(f"光鸭里找不到目录：{目标路径}")
            self._路径缓存[目标路径] = 命中
            当前 = 命中
            已走 = 目标路径
        return 当前

    def _取条目(self, 路径: str) -> 条目:
        """把文件路径解析成条目（列出父目录再匹配名字）。"""
        文本 = "/" + str(路径 or "").strip("/")
        父, _, 名字 = 文本.rpartition("/")
        for 项 in self.列出(父 or "/"):
            if 项.名字 == 名字:
                return 项
        raise 不支持(f"光鸭里找不到文件：{路径}")

    def 取直链(self, 路径: str) -> 直链:
        """取直链：只认 ``fileId``（路径要先解析成 id）。"""
        文本 = str(路径 or "")
        条目对象 = None
        if 文本 and not 文本.startswith("/") and " " not in 文本 and len(文本) > 12:
            # 直接给的就是 fileId（网盘页里选中后用标识）
            条目对象 = 条目(名字=文本, 路径=文本, 标识=文本)
        if 条目对象 is None:
            条目对象 = self._取条目(文本)
        if not 条目对象.标识:
            raise 不支持("这一项没有 fileId，取不了直链")
        数据 = self._调("/userres/v1/get_res_download_url",
                      self._带客户端({"fileId": 条目对象.标识}))
        # 实测：光鸭返回的字段叫 **signedURL**（不是 downloadUrl/url）——
        # 这条是从响应体里看出来的，别按"想当然"的字段名写。
        地址 = (数据.get("signedURL") or 数据.get("signedUrl")
              or 数据.get("downloadUrl") or 数据.get("url")
              or 数据.get("download_url") or "")
        if not 地址 and isinstance(数据.get("urls"), list) and 数据["urls"]:
            地址 = 数据["urls"][0] if isinstance(数据["urls"][0], str) else \
                 (数据["urls"][0] or {}).get("url", "")
        if not 地址:
            raise 不支持(f"光鸭没给出直链：{str(数据)[:200]}")
        头 = {}
        for 键 in ("headers", "header"):
            if isinstance(数据.get(键), dict):
                头.update({str(k): str(v) for k, v in 数据[键].items()})
        备注 = "光鸭直链（签名会过期，过期后重新取）"
        return 直链(地址=str(地址), 请求头=头,
                  大小=int(条目对象.大小 or 数据.get("fileSize") or 0),
                  有效秒=float(数据.get("expiresIn") or 0.0) or 600.0,
                  备注=备注)

    # ---------------- 登录（可供界面用） ----------------

    def 登录状态(self) -> dict:
        return {"已登录": bool(self.令牌.取()),
                "用户标识": self.令牌.用户标识,
                "过期剩余秒": max(0.0, self.令牌.过期时间戳 - time.time()),
                "有刷新令牌": bool(self.令牌.刷新令牌)}

    def 用令牌登录(self, 访问令牌: str, 刷新令牌: str = "",
               有效期秒: float = 7200.0) -> bool:
        """粘贴令牌登录（网页端 F12 里能拿到；也用于手工续期）。"""
        self.令牌.写(访问令牌, 刷新令牌, 有效期秒)
        return self.登录状态()["已登录"]

    def 从文件导入令牌(self, 别的令牌文件) -> bool:
        """从**用户自己的**令牌文件导入（迁移用；不依赖那个项目的代码）。"""
        try:
            数据 = json.loads(Path(别的令牌文件).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return False
        访问 = str(数据.get("访问令牌") or 数据.get("access_token") or "")
        刷新 = str(数据.get("刷新令牌") or 数据.get("refresh_token") or "")
        过期 = float(数据.get("过期时间戳") or 0.0)
        有效期 = max(60.0, 过期 - time.time()) if 过期 else 7200.0
        if not 访问:
            return False
        self.令牌.写(访问, 刷新, 有效期,
                    str(数据.get("用户标识") or ""))
        return True
