"""网络播放（M3）：把"网盘直链"变成能播、能拖、能自愈的一路数据。

三条路，按可靠性排序
====================
1. **交给 libavformat 的 http 协议**（默认）：它自带 Range、分块、重连、
   ``headers``/``user_agent`` 选项 —— 网盘直链最常见的需求（UA / Referer / Cookie）
   用 ``-headers`` 就能满足，不用我们自己写 HTTP 栈；
2. **我们自己的数据源**（``自定义数据源``，见 :mod:`wangpan.player.数据源`）：
   需要"按我们自己的规则鉴权/重试/限速"时把它挂上，libavformat 只当消费者；
3. 本地文件：走同一条打开路径，只是没有网络选项。

这个模块只做**决策与选项拼装**，不做 I/O（I/O 在 libavformat 或 数据源 里），
这样网络行为可以单独用"假服务器"测（见 tests/test_网络.py）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

__all__ = ["网络选项", "是网络地址", "直链信息", "拼请求头", "默认超时毫秒", "摘要"]

#: rw_timeout（读/写超时，毫秒）。网盘直链偶发卡住时不能无限等
默认超时毫秒 = 15000


def 是网络地址(地址: str) -> bool:
    """http/https/ftp 等交给 libavformat 的协议层（本地路径返回 False）。"""
    方案 = urlparse(str(地址 or "")).scheme.lower()
    return 方案 in ("http", "https", "ftp", "rtmp", "rtsp", "hls", "httpproxy")


def 拼请求头(头们: Optional[dict]) -> str:
    """把 ``{名字: 值}`` 拼成 libavformat ``headers`` 选项要的 ``A: B\\r\\n`` 形式。

    注意必须是 **CRLF** 分隔（libavformat 的 http 实现按 CRLF 拆行，
    只用 \\n 时某些服务器/中间层会当成一个畸形头，实测表现为 400/403）。
    """
    if not 头们:
        return ""
    行 = []
    for 名, 值 in 头们.items():
        名 = str(名 or "").strip()
        if not 名:
            continue
        行.append(f"{名}: {值}")
    return "\r\n".join(行) + ("\r\n" if 行 else "")


def 摘要(地址: str, 保留参数: int = 1) -> str:
    """给日志用的地址摘要：**去掉签名参数**（直链里的 token 不能进日志）。"""
    文本 = str(地址 or "")
    解析 = urlparse(文本)
    if not 解析.scheme:
        return 文本
    参数 = [对.split("=", 1)[0] for 对 in (解析.query or "").split("&") if 对]
    尾巴 = ""
    if 参数:
        尾巴 = f"?{'+'.join(参数[:保留参数])}+…（{len(参数)} 个参数）"
    路径 = 解析.path
    if len(路径) > 60:
        路径 = "…" + 路径[-57:]
    return f"{解析.scheme}://{解析.netloc}{路径}{尾巴}"


@dataclass
class 直链信息:
    """一路播放源：地址 + 请求头 + 可选的额外选项。"""

    地址: str = ""
    请求头: dict = field(default_factory=dict)
    名字: str = ""
    大小: int = 0
    过期秒: float = 0.0          # 0 = 不知道；>0 表示签名链接大概多久过期
    备注: str = ""

    @property
    def 是网络(self) -> bool:
        return 是网络地址(self.地址)


def 网络选项(地址: str, 请求头: Optional[dict] = None, *,
          超时毫秒: int = 默认超时毫秒, 允许重连: bool = True,
          缓冲字节: int = 0, 附加: Optional[dict] = None) -> dict:
    """给 libavformat 的网络播放选项。

    * ``headers``：UA/Referer/Cookie 这类直链要求；
    * ``user_agent``：单独给（有些网盘只认 UA，用这个更直观）；
    * ``reconnect`` / ``reconnect_streamed``：直链断开自动重连（网盘 CDN 常掐长连接）；
    * ``rw_timeout``：读写超时，避免"卡住不动也不报错"；
    * ``multiple_requests``：允许复用连接（拖动进度条时少一次握手）。
    """
    选项: dict[str, str] = {}
    头 = dict(请求头 or {})
    # UA 单独拎出来（libavformat 有专门的 user_agent 选项，比塞进 headers 稳）
    ua = ""
    for 键 in ("User-Agent", "user-agent", "UserAgent", "ua"):
        if 键 in 头:
            ua = str(头.pop(键))
            break
    文本 = 拼请求头(头)
    if 文本:
        选项["headers"] = 文本
    if ua:
        选项["user_agent"] = ua
    if 是网络地址(地址):
        选项["rw_timeout"] = str(int(超时毫秒) * 1000)      # 微秒
        选项["multiple_requests"] = "1"
        if 允许重连:
            选项["reconnect"] = "1"
            选项["reconnect_streamed"] = "1"
            选项["reconnect_delay_max"] = "4"
        if 缓冲字节 > 0:
            选项["buffer_size"] = str(int(缓冲字节))
    if 附加:
        选项.update({str(k): str(v) for k, v in 附加.items()})
    return 选项
