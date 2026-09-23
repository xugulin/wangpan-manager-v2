"""图片缓存（海报/背景/头像/剧照）。

设计取舍
========
* **分级尺寸缓存**：海报墙要的是小图（一屏几十张），详情页要大图。同一张图按
  "尺寸档"分别缓存（``w185`` / ``w500`` …），而不是下原图再缩放 ——
  TMDB 的 CDN 本来就按尺寸给图，下小图又快又省流量；只有"本地生成缩略图"
  才走 QImage 缩放（用于我们自己产生的图，比如截图/自备海报）。
* **文件名可反查**：缓存路径用 ``<尺寸>_<远端路径的哈希>.<后缀>``，
  这样同一个远端路径换尺寸不会互相覆盖，也**不会因为文件名里的中文/特殊字符**出问题
  （远端路径里可能带斜杠、点、百分号）；
* **LRU + 总容量上限**：按"最后访问时间"淘汰，超过上限就删最旧的；
  另有一个"保护期"（刚下过的图不参与淘汰，避免海报墙滚动时反复下同一张）；
* **原子写**：先写 ``.部分`` 再 rename —— 下载中断不会留下半个坏图（海报墙会一直转圈）。
"""

from __future__ import annotations

import hashlib
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage

__all__ = ["图片缓存", "缓存统计", "尺寸档"]

#: 常用尺寸档（TMDB 的官方尺寸名；不是 TMDB 的图也能用，只是会走"原图另存"）
尺寸档 = {
    "海报小": "w185",
    "海报中": "w342",
    "海报大": "w500",
    "背景小": "w300",
    "背景中": "w780",
    "背景大": "w1280",
    "头像小": "w185",
    "头像大": "h632",
    "剧照": "w300",
    "原图": "original",
}


@dataclass
class 缓存统计:
    条数: int = 0
    总字节: int = 0
    命中: int = 0
    下载: int = 0
    失败: int = 0
    淘汰: int = 0

    def 摘要(self) -> str:
        return (f"图片 {self.条数} 张｜{self.总字节 / 1048576:.1f} MB"
                f"｜命中 {self.命中}｜下载 {self.下载}｜失败 {self.失败}"
                f"｜淘汰 {self.淘汰}")


class 图片缓存:
    """磁盘图片缓存（线程安全够用即可：写用原子替换，读只读文件）。"""

    def __init__(self, 根目录: Optional[Path] = None,
                 容量上限字节: int = 2 * 1024 * 1024 * 1024,
                 保护期秒: float = 120.0,
                 超时秒: float = 30.0,
                 请求头: Optional[dict] = None,
                 代理: str = "") -> None:
        self.根 = Path(根目录) if 根目录 else \
            Path(__file__).resolve().parents[2] / "数据" / "缓存" / "图片"
        self.根.mkdir(parents=True, exist_ok=True)
        self.容量上限 = max(64 * 1024 * 1024, int(容量上限字节))
        self.保护期秒 = max(0.0, float(保护期秒))
        self.超时秒 = float(超时秒)
        self.请求头 = dict(请求头 or {"User-Agent": "wangpan-v2/2.0"})
        #: 代理（空 = 直连）。图片 CDN 和 API 是两个域名，可能只有一个需要代理，
        #: 所以这里单独可配（由 :class:`~wangpan.scrape.服务.刮削服务` 从客户端同步过来）。
        self.代理文本 = str(代理 or "")
        self.统计 = 缓存统计()
        #: 内存里的小图缓存（海报墙滚动时避免反复读盘 + 反复解码）
        self._内存: dict[str, QImage] = {}
        self._内存上限 = 400

    def 设置代理(self, 文本: str) -> None:
        self.代理文本 = str(文本 or "")

    @property
    def 代理(self):
        """解析好的代理对象（没配/配坏了都是 None）。"""
        if not self.代理文本:
            return None
        try:
            from .代理 import 解析代理
            return 解析代理(self.代理文本)
        except Exception:  # noqa: BLE001 - 代理配坏了不该让下图整条崩掉
            return None

    def _取字节(self, 地址: str) -> bytes:
        """下载一个地址的图片字节（走代理或直连）。

        任何失败都转成 ``OSError``：调用方 :meth:`确保` 的契约是"失败返回 None，
        不抛异常"（一张图不该毁掉一次刮削），所以这里要把代理层自己的异常也收进来。
        """
        if self.代理文本:
            代理 = self.代理
            if 代理 is None:
                raise OSError(f"代理配置有问题（{self.代理文本}），这张图不下了")
            from .代理 import 代理错误, 经代理取
            try:
                应答 = 经代理取(地址, self.超时秒, 代理, self.请求头)
            except 代理错误 as 错:
                raise OSError(f"图片走代理失败：{错}") from None
            if 应答.状态码 >= 400:
                raise OSError(f"图片 HTTP {应答.状态码}")
            return 应答.体
        请求 = urllib.request.Request(地址, headers=self.请求头)
        with urllib.request.urlopen(请求, timeout=self.超时秒) as 应答:
            return 应答.read()

    # ---------------- 路径 ----------------

    def _键(self, 远端路径: str, 尺寸: str) -> str:
        摘要 = hashlib.blake2s(远端路径.encode("utf-8"), digest_size=10).hexdigest()
        后缀 = Path(远端路径.split("?")[0]).suffix.lower() or ".jpg"
        if 后缀 not in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"):
            后缀 = ".jpg"
        return f"{尺寸}_{摘要}{后缀}"

    def 路径(self, 远端路径: str, 尺寸: str = "w500") -> Path:
        return self.根 / self._键(远端路径, 尺寸)

    def 已有(self, 远端路径: str, 尺寸: str = "w500") -> bool:
        路径 = self.路径(远端路径, 尺寸)
        return 路径.is_file() and 路径.stat().st_size > 0

    # ---------------- 下载 ----------------

    def 确保(self, 远端路径: str, 尺寸: str = "w500",
           基地址: str = "https://image.tmdb.org/t/p/",
           地址生成: Optional[Callable[[str, str], str]] = None) -> Optional[Path]:
        """确保本地有这张图（返回本地路径；失败返回 None，**不抛异常**）。"""
        if not 远端路径:
            return None
        本地 = self.路径(远端路径, 尺寸)
        if 本地.is_file() and 本地.stat().st_size > 0:
            self.统计.命中 += 1
            try:
                os.utime(本地, None)          # 更新访问时间，LRU 用
            except OSError:
                pass
            return 本地
        地址 = (地址生成(尺寸, 远端路径) if 地址生成
              else f"{基地址.rstrip('/')}/{尺寸}{远端路径}")
        临时 = 本地.with_suffix(本地.suffix + ".部分")
        try:
            数据 = self._取字节(地址)
            if not 数据:
                raise OSError("空响应")
            临时.write_bytes(数据)
            os.replace(临时, 本地)
            self.统计.下载 += 1
            return 本地
        except (urllib.error.URLError, OSError, TimeoutError) as 错:
            self.统计.失败 += 1
            try:
                临时.unlink(missing_ok=True)
            except OSError:
                pass
            self.最后错误 = str(错)
            return None

    def 确保本地文件(self, 来源: Path, 远端路径键: str, 尺寸: str = "本地") -> Optional[Path]:
        """把"已经在本地"的图纳入缓存（例如自备 poster.jpg、我们自己生成的缩略图）。"""
        来源 = Path(来源)
        if not 来源.is_file():
            return None
        目标 = self.路径(远端路径键, 尺寸)
        if 目标.is_file() and 目标.stat().st_size == 来源.stat().st_size:
            return 目标
        try:
            临时 = 目标.with_suffix(目标.suffix + ".部分")
            临时.write_bytes(来源.read_bytes())
            os.replace(临时, 目标)
            self.统计.下载 += 1
            return 目标
        except OSError:
            self.统计.失败 += 1
            return None

    # ---------------- 读图 / 生成缩略图 ----------------

    def 读图(self, 本地路径: Path | str, 目标尺寸: Optional[tuple[int, int]] = None,
           缓存键: str = "") -> Optional[QImage]:
        """读图（可选缩放到目标框内）；带内存缓存，海报墙滚动不反复解码。"""
        路径 = Path(本地路径)
        键 = 缓存键 or f"{路径}:{目标尺寸}"
        命中 = self._内存.get(键)
        if 命中 is not None:
            return 命中
        if not 路径.is_file():
            return None
        图 = QImage(str(路径))
        if 图.isNull():
            return None
        if 目标尺寸 and 目标尺寸[0] > 0 and 目标尺寸[1] > 0:
            图 = 图.scaled(QSize(*目标尺寸), Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        if len(self._内存) >= self._内存上限:
            self._内存.pop(next(iter(self._内存)))
        self._内存[键] = 图
        return 图

    def 生成缩略图(self, 来源: Path | str, 落点: Path | str, 宽: int = 300) -> Optional[Path]:
        """把一张图缩成缩略图存下来（自备海报/截图时用）。"""
        图 = self.读图(来源)
        if 图 is None:
            return None
        缩放 = 图.scaledToWidth(max(32, int(宽)), Qt.TransformationMode.SmoothTransformation)
        目标 = Path(落点)
        目标.parent.mkdir(parents=True, exist_ok=True)
        if 缩放.save(str(目标), "JPEG", 88):
            return 目标
        return None

    # ---------------- 清理 ----------------

    def 清理(self, 目标字节: Optional[int] = None) -> int:
        """LRU 清理到目标容量以下；返回删除的字节数。"""
        目标 = self.容量上限 if 目标字节 is None else int(目标字节)
        文件们: list[tuple[float, int, Path]] = []
        总 = 0
        for 路径 in self.根.glob("*"):
            try:
                if not 路径.is_file():
                    continue
                状态 = 路径.stat()
                文件们.append((状态.st_atime, 状态.st_size, 路径))
                总 += 状态.st_size
            except OSError:
                continue
        self.统计.条数 = len(文件们)
        self.统计.总字节 = 总
        if 总 <= 目标:
            return 0
        文件们.sort()                     # 最久未访问的在前
        现在 = time.time()
        删除 = 0
        for 访问时间, 大小, 路径 in 文件们:
            if 总 - 删除 <= 目标:
                break
            if 现在 - 访问时间 < self.保护期秒:      # 保护期内的不删（可能正在用）
                continue
            try:
                路径.unlink()
                删除 += 大小
                self.统计.淘汰 += 1
            except OSError:
                continue
        self.统计.总字节 = max(0, 总 - 删除)
        return 删除

    def 清空(self) -> int:
        删除 = 0
        for 路径 in self.根.glob("*"):
            try:
                if 路径.is_file():
                    删除 += 路径.stat().st_size
                    路径.unlink()
            except OSError:
                continue
        self._内存.clear()
        self.统计 = 缓存统计()
        return 删除

    def 统计一下(self) -> 缓存统计:
        self.清理(目标字节=self.容量上限)      # 顺带刷新统计
        return self.统计
