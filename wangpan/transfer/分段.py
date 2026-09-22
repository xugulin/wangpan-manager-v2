"""多段并发下载（M6 剩余项）：把一个文件切成 N 段同时下，再拼成完整文件。

为什么要自己写：网盘直链的单连接速度经常被限（CDN 单连接限速很常见），
而多段并发（HTTP Range）几乎总能显著提速；这也是所有网盘客户端的标配。

实现要点（每一条都是踩过的坑）
==============================
* **先探测**：用 ``Range: bytes=0-0`` 确认服务器真的支持 Range 且知道总大小；
  不支持就老实单线程下（不要假装分段，那只会更慢）；
* **写同一份文件的不同偏移**：用 ``os.pwrite``（Linux/macOS 原生）而不是
  "每段一个临时文件最后拼接" —— 拼接要多占一倍磁盘、还得再全量读写一遍；
  Windows 没有 ``os.pwrite`` 时退回 ``seek+write``（加锁）；
* **预分配**：先 ``truncate`` 到总大小，避免边下边扩容造成碎片；
* **每段各自重试**：单段失败只重试那一段（指数退避），不是整个文件重来；
* **可暂停/可取消**：取消时**保留**已下好的部分（.部分 文件），下次续传。
"""

from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from ..pan.http import 规范地址

__all__ = ["分段下载", "探测分段支持", "默认段数"]

#: 默认段数（4 段对绝大多数 CDN 已经吃到带宽；太多反而被服务端限流）
默认段数 = 4
#: 每段最小字节（小于这个大小不值得分段）
最小段字节 = 1 * 1024 * 1024


def 探测分段支持(地址: str, 请求头: Optional[dict] = None,
              超时: float = 20.0) -> tuple[bool, int]:
    """返回 ``(支持分段?, 总字节)``。总字节 0 = 不知道。"""
    头 = {"User-Agent": "wangpan-v2/2.0", "Range": "bytes=0-0", **(请求头 or {})}
    try:
        请求 = urllib.request.Request(规范地址(地址), headers=头)
        with urllib.request.urlopen(请求, timeout=超时) as 应答:
            状态 = int(getattr(应答, "status", 200) or 200)
            范围 = 应答.headers.get("Content-Range") or ""
            if 状态 == 206 and "/" in 范围:
                return True, int(范围.rsplit("/", 1)[-1])
            return False, int(应答.headers.get("Content-Length") or 0)
    except Exception:  # noqa: BLE001
        return False, 0


class _计数:
    """线程安全的进度累计（多段并发时进度要累加，不能各报各的）。"""

    def __init__(self, 已下: int = 0) -> None:
        self.值 = int(已下)
        self._锁 = threading.Lock()

    def 加(self, 增量: int) -> int:
        with self._锁:
            self.值 += int(增量)
            return self.值


def 分段下载(地址: str, 落点, 总字节: int = 0, 段数: int = 默认段数,
          请求头: Optional[dict] = None,
          进度回调: Optional[Callable[[int, int], None]] = None,
          取消=None, 重试次数: int = 3) -> Path:
    """多段并发下载到 ``落点``（先写 ``落点.部分``，完成后改名）。

    :param 总字节: 0 表示先探测；探测不出就退化为单段（顺序下载）。
    """
    目标 = Path(落点)
    目标.parent.mkdir(parents=True, exist_ok=True)
    临时 = 目标.with_suffix(目标.suffix + ".部分")
    头基 = dict(请求头 or {})
    支持分段, 探测大小 = 探测分段支持(地址, 头基)
    总字节 = int(总字节 or 探测大小 or 0)
    段数 = max(1, int(段数))
    if not 支持分段 or 总字节 <= 0 or 总字节 < 最小段字节 * 2:
        段数 = 1
    已存在 = 临时.stat().st_size if 临时.is_file() else 0
    if 已存在 > 总字节:
        临时.unlink(missing_ok=True)                     # 半截比总数还大 = 脏数据
        已存在 = 0
    if 段数 == 1:
        return _单段下载(地址, 目标, 临时, 总字节, 头基, 进度回调, 取消, 重试次数)

    # 预分配：先按总大小把文件撑开，各段直接往自己的偏移写
    with open(临时, "ab") as 句柄:
        句柄.truncate(总字节)
    每段 = 总字节 // 段数
    区间们 = []
    for i in range(段数):
        起 = i * 每段
        止 = 总字节 - 1 if i == 段数 - 1 else (起 + 每段 - 1)
        区间们.append((起, 止))
    计数 = _计数(已存在)
    错误们: list[str] = []
    锁 = threading.Lock()

    def 跑一段(序号: int, 起: int, 止: int) -> None:
        需要 = 止 - 起 + 1
        # 这一段的续传：从"已下过的部分"之后接着下（用文件里的现有内容判断不了，
        # 所以简单策略是整段重下；预分配后 os.pwrite 覆盖写即可）
        重试 = 0
        while 重试 <= 重试次数:
            if 取消 is not None and 取消():
                return
            头 = {**头基, "Range": f"bytes={起}-{止}",
                 "User-Agent": "wangpan-v2/2.0"}
            try:
                请求 = urllib.request.Request(规范地址(地址), headers=头)
                with urllib.request.urlopen(请求, timeout=60) as 应答:
                    状态 = int(getattr(应答, "status", 200) or 200)
                    if 状态 != 206:
                        raise OSError(f"服务器没按 Range 回（HTTP {状态}）")
                    偏移 = 起
                    剩余 = 需要
                    局部 = 0
                    while 剩余 > 0:
                        if 取消 is not None and 取消():
                            return
                        块 = 应答.read(min(256 * 1024, 剩余))
                        if not 块:
                            raise OSError("连接提前结束")
                        写文件偏移(临时, 块, 偏移)
                        偏移 += len(块)
                        剩余 -= len(块)
                        局部 += len(块)
                        现在 = 计数.加(len(块))
                        if 进度回调 is not None:
                            进度回调(现在, 总字节)
                if 局部 == 需要:
                    return
                raise OSError(f"这一段只下到 {局部}/{需要} 字节")
            except Exception as 错:  # noqa: BLE001
                重试 += 1
                with 锁:
                    错误们.append(f"第{序号 + 1}段第{重试}次：{错}")
                if 重试 > 重试次数:
                    return
                time.sleep(min(4.0, 0.5 * (2 ** (重试 - 1))))

    线程们 = [threading.Thread(target=跑一段, args=(i, 起, 止), daemon=True)
            for i, (起, 止) in enumerate(区间们)]
    for 线 in 线程们:
        线.start()
    for 线 in 线程们:
        线.join()
    if 取消 is not None and 取消():
        raise OSError("已取消（半截文件保留在 .部分，下次可续）")
    if 错误们 and 计数.值 < 总字节:
        raise OSError("分段下载失败：" + "；".join(错误们[-3:]))
    os.replace(临时, 目标)
    return 目标


def 写文件偏移(路径: Path, 数据: bytes, 偏移: int) -> None:
    """写到文件的指定偏移（Windows 没有 os.pwrite 时用 seek+write 兜底）。"""
    if hasattr(os, "pwrite"):
        句柄 = os.open(str(路径), os.O_WRONLY)
        try:
            os.pwrite(句柄, 数据, 偏移)
        finally:
            os.close(句柄)
        return
    with open(路径, "r+b") as 句柄:            # pragma: no cover - Windows 分支
        句柄.seek(偏移)
        句柄.write(数据)


def _单段下载(地址: str, 目标: Path, 临时: Path, 总字节: int, 头基: dict,
           进度回调, 取消, 重试次数: int) -> Path:
    """顺序下载 + 断点续传（服务器不支持 Range 时必须从头，见下面的校验）。"""
    已下 = 临时.stat().st_size if 临时.is_file() else 0
    重试 = 0
    while 重试 <= 重试次数:
        头 = dict(头基)
        头["User-Agent"] = "wangpan-v2/2.0"
        if 已下:
            头["Range"] = f"bytes={已下}-"
        try:
            请求 = urllib.request.Request(规范地址(地址), headers=头)
            with urllib.request.urlopen(请求, timeout=60) as 应答:
                状态 = int(getattr(应答, "status", 200) or 200)
                if 已下 and 状态 != 206:
                    # 服务器不认 Range：必须丢掉半截重来（否则文件变成"半截+整份"）
                    已下 = 0
                    临时.unlink(missing_ok=True)
                if 进度回调 is not None:
                    进度回调(已下, 总字节)
                with open(临时, "ab" if 已下 else "wb") as 写:
                    while True:
                        if 取消 is not None and 取消():
                            raise OSError("已取消")
                        块 = 应答.read(256 * 1024)
                        if not 块:
                            break
                        写.write(块)
                        已下 += len(块)
                        if 进度回调 is not None:
                            进度回调(已下, 总字节)
            if 总字节 and 已下 < 总字节:
                raise OSError(f"只下到 {已下}/{总字节} 字节")
            os.replace(临时, 目标)
            return 目标
        except urllib.error.HTTPError as 错:
            if 错.code == 416:                      # 已经下完了
                os.replace(临时, 目标)
                return 目标
            raise
        except OSError as 错:
            if "已取消" in str(错):
                raise
            重试 += 1
            if 重试 > 重试次数:
                raise
            time.sleep(min(4.0, 0.5 * (2 ** (重试 - 1))))
    raise OSError("下载失败")
