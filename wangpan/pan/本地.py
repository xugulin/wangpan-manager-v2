"""本地目录适配器：把"本机文件夹"也当成一个网盘。

为什么值得做：界面、传输引擎、播放列表只认契约，本地与网盘走同一条路 ——
这样"下载/上传/播放"三件事只有一套代码，且**离线也能测得动**。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable, Optional

from .接口 import 直链, 条目, 适配器, 不支持

__all__ = ["本地适配器"]


class 本地适配器(适配器):
    名字 = "本地文件夹"
    能力 = {"列出", "直链", "下载", "上传", "删除", "建目录"}

    def __init__(self, 根: str | Path) -> None:
        self.根 = Path(根).expanduser().resolve()
        self.根.mkdir(parents=True, exist_ok=True)

    # ---------------- 路径 ----------------

    def _解析(self, 路径: str | Path) -> Path:
        文本 = str(路径 or "/").replace("\\", "/")
        if not 文本.startswith("/"):
            文本 = "/" + 文本
        目标 = (self.根 / 文本.lstrip("/")).resolve()
        # 防目录穿越（网盘客户端的基本功：路径里带 .. 不能跑出根目录）
        if self.根 != 目标 and self.根 not in 目标.parents:
            raise 不支持(f"路径越界：{路径}")
        return 目标

    # ---------------- 只读 ----------------

    def 列出(self, 路径: str = "/") -> list[条目]:
        目录 = self._解析(路径)
        if not 目录.is_dir():
            raise 不支持(f"不是目录：{路径}")
        结果: list[条目] = []
        for 子 in sorted(目录.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                状态 = 子.stat()
            except OSError:
                continue
            结果.append(条目(名字=子.name, 路径="/" + str(子.relative_to(self.根)),
                          是目录=子.is_dir(), 大小=0 if 子.is_dir() else 状态.st_size,
                          修改时间=状态.st_mtime, 标识=str(子)))
        return 结果

    def 取直链(self, 路径: str) -> 直链:
        文件 = self._解析(路径)
        if not 文件.is_file():
            raise 不支持(f"不是文件：{路径}")
        return 直链(地址=str(文件), 请求头={}, 大小=文件.stat().st_size,
                  备注="本地文件")

    # ---------------- 写 ----------------

    def 下载(self, 路径: str, 落点, 进度回调: Optional[Callable[[int, int], None]] = None,
          取消=None):
        来源 = self._解析(路径)
        目标 = Path(落点)
        目标.parent.mkdir(parents=True, exist_ok=True)
        总数 = 来源.stat().st_size
        已写 = 0
        with open(来源, "rb") as 读, open(目标, "wb") as 写:
            while True:
                if 取消 is not None and 取消():
                    raise 不支持("已取消")
                块 = 读.read(1024 * 1024)
                if not 块:
                    break
                写.write(块)
                已写 += len(块)
                if 进度回调 is not None:
                    进度回调(已写, 总数)
        return 目标

    def 上传(self, 本地路径, 远端路径: str,
          进度回调: Optional[Callable[[int, int], None]] = None, 取消=None):
        来源 = Path(本地路径)
        目标 = self._解析(远端路径)
        目标.parent.mkdir(parents=True, exist_ok=True)
        总数 = 来源.stat().st_size
        已写 = 0
        with open(来源, "rb") as 读, open(目标, "wb") as 写:
            while True:
                if 取消 is not None and 取消():
                    raise 不支持("已取消")
                块 = 读.read(1024 * 1024)
                if not 块:
                    break
                写.write(块)
                已写 += len(块)
                if 进度回调 is not None:
                    进度回调(已写, 总数)
        return 目标

    def 删除(self, 路径: str) -> bool:
        目标 = self._解析(路径)
        if 目标.is_dir():
            shutil.rmtree(目标)
        elif 目标.exists():
            目标.unlink()
        return True

    def 建目录(self, 路径: str) -> bool:
        self._解析(路径).mkdir(parents=True, exist_ok=True)
        return True
