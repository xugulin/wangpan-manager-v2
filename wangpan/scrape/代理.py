"""代理支持（**只用标准库**）：让 TMDB/图片 CDN 的请求能走用户自己的代理。

为什么需要它
============
真机实测：这台机器的网络**到不了** `api.themoviedb.org`（DNS 被解析到一个无关的
IPv6 地址、直连全超时），但用户机器上就跑着 v2rayN/xray，本地 SOCKS5 端口是通的
（经它请求 TMDB 会正常返回 401/200 —— 说明"网络对面"没问题，只是直连被挡）。
所以"能不能配代理"直接决定这个刮削模块在真实网络下能不能用。

为什么不是加个 `PySocks` 依赖
=============================
本项目只允许**标准库 + PySide6**（`tests/test_独立性.py` 会检查）。
SOCKS5 的握手只有几十行，用 `socket` + `ssl` 自己走一遍即可，
比引一个第三方依赖更符合这个项目的口径。

支持的形式（``数据/刮削.json`` 的 ``proxy`` 或环境变量 ``V2_TMDB_PROXY``）::

    socks5://127.0.0.1:10808            # 本地解析域名（v2rayN 的默认 SOCKS 口）
    socks5h://127.0.0.1:10808           # 让代理解析域名（等价于上面那种的常见写法）
    socks5://user:pass@127.0.0.1:1080   # 带用户名/密码（RFC 1929）
    http://127.0.0.1:7890               # HTTP 代理（CONNECT 隧道）
    https://127.0.0.1:7890

`直连`（不给代理）与"代理挂了"这两种情况都要能区分开：连不上代理时会抛
:class:`代理错误`，消息里给出**可操作**的提示，而不是一句 URLError。
"""

from __future__ import annotations

import base64
import socket
import ssl
import struct
import urllib.parse
from dataclasses import dataclass
from typing import Optional

__all__ = ["代理配置", "代理错误", "解析代理", "经代理取", "支持"]


class 代理错误(Exception):
    """连不上代理 / 代理拒绝了目标（消息可以直接给用户看）。"""


@dataclass
class 代理配置:
    """一个代理端点（SOCKS5 或 HTTP CONNECT）。"""

    类型: str = "socks5"          # socks5 / http
    主机: str = "127.0.0.1"
    端口: int = 1080
    用户名: str = ""
    密码: str = ""
    代理解析域名: bool = False     # socks5h 时为真（域名交给代理去解析）

    def 可读(self) -> str:
        """给日志/界面看的（**不回显密码**）。"""
        认证 = f"（带认证：{self.用户名}）" if self.用户名 else ""
        return f"{self.类型}://{self.主机}:{self.端口}{认证}"

    def 地址(self) -> tuple[str, int]:
        return (self.主机, int(self.端口))


def 解析代理(文本: str | None) -> Optional[代理配置]:
    """把 ``socks5://…`` / ``http://…`` 解析成 :class:`代理配置`；空串 → None。"""
    原文 = str(文本 or "").strip()
    if not 原文:
        return None
    if "://" not in 原文:                    # 只给 host:port 时按 SOCKS5 理解（v2rayN 的常见口）
        原文 = "socks5://" + 原文
    解析 = urllib.parse.urlsplit(原文)
    方案 = (解析.scheme or "").lower()
    if 方案 in ("socks5", "socks5h", "socks"):
        pass
    elif 方案 in ("http", "https"):
        pass
    else:
        raise 代理错误(f"不认识的代理协议：{方案}（支持 socks5 / socks5h / http）")
    主机 = 解析.hostname or "127.0.0.1"
    try:
        端口 = int(解析.port or (1080 if 方案.startswith("socks") else 8080))
    except ValueError as 错:
        raise 代理错误(f"代理端口不对：{原文}") from 错
    return 代理配置(类型="http" if 方案 in ("http", "https") else "socks5",
                   主机=主机, 端口=端口,
                   用户名=urllib.parse.unquote(解析.username or ""),
                   密码=urllib.parse.unquote(解析.password or ""),
                   代理解析域名=(方案 == "socks5h"))


def 支持() -> bool:
    """这个运行环境能不能用代理（标准库的 socket/ssl 在就一定能）。"""
    return hasattr(socket, "create_connection") and hasattr(ssl, "create_default_context")


# ============================ 底层：建立到目标的 TLS 连接 ============================


def _读净(套接字, 字节数: int) -> bytes:
    数据 = b""
    while len(数据) < 字节数:
        块 = 套接字.recv(字节数 - len(数据))
        if not 块:
            raise 代理错误("代理提前断开连接")
        数据 += 块
    return 数据


def _目标地址(主机: str) -> bytes:
    """SOCKS5 的地址字段：IP 字面量用 0x01/0x04（比塞进域名形式规范，也省一次解析）。"""
    try:
        return b"\x01" + socket.inet_pton(socket.AF_INET, 主机)
    except OSError:
        pass
    try:
        return b"\x04" + socket.inet_pton(socket.AF_INET6, 主机)
    except OSError:
        pass
    域名 = 主机.encode("idna")
    if len(域名) > 255:
        raise 代理错误(f"域名太长，SOCKS5 放不下：{主机[:60]}…")
    return bytes([0x03, len(域名)]) + 域名


def _走socks5(代理: 代理配置, 主机: str, 端口: int, 超时: float):
    """SOCKS5 握手 + CONNECT，返回已连到目标的裸 socket。"""
    try:
        套接字 = socket.create_connection(代理.地址(), timeout=超时)
    except OSError as 错:
        raise 代理错误(f"连不上代理 {代理.可读()}：{错}") from 错
    try:
        套接字.settimeout(超时)
        方法 = [0x00] + ([0x02] if 代理.用户名 else [])       # 0x00 免认证 / 0x02 用户名密码
        套接字.sendall(bytes([0x05, len(方法)]) + bytes(方法))
        版本, 选中 = _读净(套接字, 2)
        if 版本 != 0x05:
            raise 代理错误(f"代理 {代理.可读()} 不像 SOCKS5（版本字节 {版本}）")
        if 选中 == 0x02:
            if not 代理.用户名:
                raise 代理错误(f"代理 {代理.可读()} 要求用户名密码，但配置里没有")
            用户 = 代理.用户名.encode("utf-8")
            口令 = 代理.密码.encode("utf-8")
            套接字.sendall(bytes([0x01, len(用户)]) + 用户
                        + bytes([len(口令)]) + 口令)
            _, 状态 = _读净(套接字, 2)
            if 状态 != 0x00:
                raise 代理错误(f"代理 {代理.可读()} 认证失败（用户名/密码不对）")
        elif 选中 != 0x00:
            raise 代理错误(f"代理 {代理.可读()} 不接受免认证（返回方法 {选中}）")
        套接字.sendall(bytes([0x05, 0x01, 0x00]) + _目标地址(主机)
                    + struct.pack("!H", int(端口)))
        头 = _读净(套接字, 4)
        if 头[1] != 0x00:
            含义 = {0x01: "代理内部错误", 0x02: "规则不允许", 0x03: "网络不可达",
                  0x04: "主机不可达", 0x05: "连接被拒", 0x06: "TTL 超时",
                  0x07: "命令不支持", 0x08: "地址类型不支持"}.get(头[1], f"错误码 {头[1]}")
            raise 代理错误(f"代理 {代理.可读()} 连不到 {主机}:{端口}（{含义}）")
        类型 = 头[3]
        长度 = {0x01: 4, 0x04: 16}.get(类型)
        if 长度 is None:
            if 类型 != 0x03:
                raise 代理错误(f"代理返回了不认识的地址类型 {类型}")
            长度 = _读净(套接字, 1)[0]
        _读净(套接字, 长度 + 2)                                # 绑定地址 + 端口，丢掉
        return 套接字
    except Exception:
        套接字.close()
        raise


def _走http代理(代理: 代理配置, 主机: str, 端口: int, 超时: float):
    """HTTP 代理的 CONNECT 隧道（明文隧道，TLS 由上层再叠）。"""
    try:
        套接字 = socket.create_connection(代理.地址(), timeout=超时)
    except OSError as 错:
        raise 代理错误(f"连不上代理 {代理.可读()}：{错}") from 错
    try:
        套接字.settimeout(超时)
        头 = [f"CONNECT {主机}:{int(端口)} HTTP/1.1", f"Host: {主机}:{int(端口)}"]
        if 代理.用户名:
            令牌 = base64.b64encode(f"{代理.用户名}:{代理.密码}".encode()).decode()
            头.append(f"Proxy-Authorization: Basic {令牌}")
        套接字.sendall(("\r\n".join(头) + "\r\n\r\n").encode("latin-1"))
        应答 = b""
        while b"\r\n\r\n" not in 应答:
            块 = 套接字.recv(4096)
            if not 块:
                raise 代理错误("代理在 CONNECT 阶段断开")
            应答 += 块
            if len(应答) > 65536:
                raise 代理错误("代理的 CONNECT 应答异常地长")
        首行 = 应答.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        段 = 首行.split()
        if len(段) < 2 or not 段[1].startswith("2"):
            raise 代理错误(f"代理 {代理.可读()} 拒绝了 CONNECT：{首行}")
        return 套接字
    except Exception:
        套接字.close()
        raise


def _连到(代理: Optional[代理配置], 主机: str, 端口: int, 超时: float):
    if 代理 is None:
        套接字 = socket.create_connection((主机, 端口), timeout=超时)
        套接字.settimeout(超时)
        return 套接字
    if 代理.类型 == "http":
        return _走http代理(代理, 主机, 端口, 超时)
    return _走socks5(代理, 主机, 端口, 超时)


# ============================ 对外：一次 GET ============================


@dataclass
class 应答:
    状态码: int
    体: bytes
    头: dict

    def 文本(self) -> str:
        return self.体.decode("utf-8", "replace")


def 经代理取(地址: str, 超时: float = 20.0, 代理: Optional[代理配置] = None,
          请求头: Optional[dict] = None, 最大字节: int = 64 * 1024 * 1024) -> 应答:
    """GET 一个 https 地址（**经代理或直连**），返回状态码与正文。

    为什么不用 urllib：标准库的 urllib **不认 SOCKS**（要走 SOCKS 就得装 PySocks），
    而这里只有几十行；顺带把 HTTP/1.1 的细节（Host 头、chunked、Content-Length）
    收在一处，比在调用点各写一遍可靠。

    失败一律抛 :class:`代理错误`（消息可读、**不含密钥**）。
    """
    解析 = urllib.parse.urlsplit(地址)
    if 解析.scheme not in ("http", "https"):
        raise 代理错误(f"只支持 http/https：{解析.scheme}")
    主机 = 解析.hostname or ""
    端口 = int(解析.port or (443 if 解析.scheme == "https" else 80))
    路径 = 解析.path or "/"
    if 解析.query:
        路径 += "?" + 解析.query
    裸 = _连到(代理, 主机, 端口, 超时)
    try:
        if 解析.scheme == "https":
            上下文 = ssl.create_default_context()
            套接字 = 上下文.wrap_socket(裸, server_hostname=主机)
        else:
            套接字 = 裸
        套接字.settimeout(超时)
        头 = {"Host": 主机 if 端口 in (80, 443) else f"{主机}:{端口}",
             "Accept": "*/*", "Accept-Encoding": "identity",
             "Connection": "close",
             "User-Agent": "WangPanV2/2.0"}
        for 键, 值 in (请求头 or {}).items():
            if 值 is not None:
                头[键] = str(值)
        请求 = f"GET {路径} HTTP/1.1\r\n" + "".join(
            f"{k}: {v}\r\n" for k, v in 头.items()) + "\r\n"
        套接字.sendall(请求.encode("latin-1"))
        原始 = b""
        while True:
            块 = 套接字.recv(65536)
            if not 块:
                break
            原始 += 块
            if len(原始) > 最大字节:
                raise 代理错误(f"响应超过 {最大字节 // 1048576} MB，已放弃：{主机}{路径}")
    except 代理错误:
        raise
    except ssl.SSLError as 错:
        raise 代理错误(f"和 {主机} 的 TLS 握手失败：{错}") from 错
    except OSError as 错:
        raise 代理错误(f"读取 {主机} 的响应失败：{错}") from 错
    finally:
        try:
            裸.close()
        except OSError:
            pass
    return _解析应答(原始, 主机, 路径)


def _解析应答(原始: bytes, 主机: str, 路径: str) -> 应答:
    if not 原始:
        raise 代理错误(f"{主机} 没有返回任何数据（{路径}）")
    头尾 = 原始.split(b"\r\n\r\n", 1)
    if len(头尾) != 2:
        raise 代理错误(f"{主机} 的响应不是 HTTP（{路径}）")
    头文本, 体 = 头尾
    行们 = 头文本.decode("latin-1", "replace").split("\r\n")
    段 = 行们[0].split()
    try:
        状态码 = int(段[1])
    except (IndexError, ValueError) as 错:
        raise 代理错误(f"{主机} 的状态行看不懂：{行们[0][:80]}") from 错
    头: dict[str, str] = {}
    for 行 in 行们[1:]:
        if ":" in 行:
            键, _, 值 = 行.partition(":")
            头[键.strip().lower()] = 值.strip()
    if 头.get("transfer-encoding", "").lower() == "chunked":
        体 = _解块(体)
    elif "content-length" in 头:
        try:
            长度 = int(头["content-length"])
            if 0 <= 长度 <= len(体):
                体 = 体[:长度]
        except ValueError:
            pass
    return 应答(状态码=状态码, 体=体, 头=头)


def _解块(体: bytes) -> bytes:
    """解开 HTTP/1.1 的分块编码（TMDB 的图片 CDN 有时会给）。"""
    结果 = b""
    位置 = 0
    while True:
        行尾 = 体.find(b"\r\n", 位置)
        if 行尾 < 0:
            break
        大小文本 = 体[位置:行尾].split(b";")[0].strip()
        try:
            大小 = int(大小文本, 16)
        except ValueError:
            break
        位置 = 行尾 + 2
        if 大小 == 0:
            break
        结果 += 体[位置:位置 + 大小]
        位置 += 大小 + 2
    return 结果
