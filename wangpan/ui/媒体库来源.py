"""媒体库要扫哪些文件夹 —— 可以多个，**包括网盘里的文件夹**。

用户要求
========
> 媒体库改为可添加多个文件夹，包括网盘内的文件夹。

原来只有海报墙上一个「📁 扫描媒体库…」按钮：一次挑一个**本地**目录，挑完就丢了，
下次还得重挑；网盘里的片子根本进不了媒体库。

现在：
* 有一份**清单**（``数据/媒体库来源.json``），可以加任意多个文件夹，本地/网盘混着放；
* 本地文件夹用 ``os.walk`` 扫（原来那套）；
* 网盘文件夹走**注入进来的列目录回调**（``列目录(网盘标识, 路径)``）递归列；
  库里记的路径写成 ``标识:远端路径``（例如 ``guangya:/电影/沙丘.mkv``），
  既进得了库，也能被播放页解析回网盘路径去取直链。

为什么列目录要"注入"而不是直接调
==================================
播放内核与媒体库属于 ``wangpan``，而网盘适配器（登录、直链、列目录）在 ``v8_3``。
本项目的依赖方向是**单向**的（``v8_3 → wangpan``），``wangpan`` 里不许出现
``import v8_3``（有测试盯着）。所以网盘能力由界面层以回调形式注入：

* ``列目录(网盘标识, 远端路径) -> [{"名称","是目录","大小","路径"}, …]``
* ``选文件夹() -> {"网盘": 标识, "显示名": …, "路径": …} | None``
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

__all__ = ["来源", "来源清单", "默认清单路径", "本地视频们", "远端视频们",
           "库路径", "拆库路径"]

#: 媒体库来源清单的默认位置
项目根 = Path(__file__).resolve().parents[2]
默认清单路径 = 项目根 / "数据" / "媒体库来源.json"
#: 扫描时最多往下钻几层（网盘目录可能很深，防止把整盘拖一遍）
默认最大深度 = 6

#: 常见视频后缀（与 ``wangpan.scrape.扫描.视频扩展名`` 对齐）
视频后缀 = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".mts",
          ".flv", ".wmv", ".m4v", ".mpg", ".mpeg", ".rmvb", ".3gp", ".iso"}


@dataclass
class 来源:
    """一个媒体库文件夹。"""

    类型: str = "本地"                     # "本地" / "网盘"
    路径: str = ""
    网盘: str = ""                         # 类型=网盘 时：网盘实例标识
    显示名: str = ""                       # 列表里给人看的名字

    def 一句话(self) -> str:
        if self.类型 == "网盘":
            return f"☁️ {self.显示名 or self.网盘}：{self.路径}"
        return f"💾 {self.显示名 or self.路径}"

    @property
    def 唯一键(self) -> tuple[str, str, str]:
        return (self.类型, self.网盘, self.路径)


def 库路径(网盘标识: str, 远端路径: str) -> str:
    """远端文件在库里的路径写法：``标识:远端路径``。"""
    return f"{网盘标识}:{远端路径}"


def 拆库路径(路径: str, 已知网盘: Iterable[str] = ()) -> tuple[str, str]:
    """把库里的路径拆成 ``(网盘标识, 远端路径)``；本地路径返回 ``("", 原样)``。

    ⚠️ 必须拿**已知的网盘标识**去比：Windows 盘符（``C:\\…``）里也有冒号，
    只按"有没有冒号"判断会把本地路径误判成网盘路径。
    """
    文本 = str(路径 or "")
    for 标识 in 已知网盘 or ():
        前缀 = f"{标识}:"
        if 文本.startswith(前缀):
            return 标识, 文本[len(前缀):]
    return "", 文本


class 来源清单:
    """``数据/媒体库来源.json`` 的读写（可多个文件夹）。"""

    def __init__(self, 路径: Optional[Path | str] = None) -> None:
        self.路径 = Path(路径) if 路径 else 默认清单路径
        if self.路径.is_dir():                   # 传进来一个目录就当默认文件
            self.路径 = self.路径 / "媒体库来源.json"
        self._们: list[来源] = []
        self.读()

    # ---------------- 读写 ----------------

    def 读(self) -> list[来源]:
        self._们 = []
        try:
            数据 = json.loads(self.路径.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 文件不在/坏了都当"还没配"
            return self._们
        原始 = 数据.get("来源") if isinstance(数据, dict) else 数据
        for 项 in (原始 or []):
            if not isinstance(项, dict):
                continue
            try:
                self._们.append(来源(**{k: v for k, v in 项.items()
                                     if k in 来源.__dataclass_fields__}))
            except Exception:  # noqa: BLE001
                continue
        return self._们

    def 写(self) -> None:
        try:
            self.路径.parent.mkdir(parents=True, exist_ok=True)
            self.路径.write_text(json.dumps(
                {"来源": [asdict(x) for x in self._们]}, ensure_ascii=False, indent=1),
                encoding="utf-8")
        except Exception:  # noqa: BLE001 - 存不下不该影响本次扫描
            pass

    # ---------------- 增删查 ----------------

    def 全部(self) -> list[来源]:
        return list(self._们)

    def __len__(self) -> int:
        return len(self._们)

    def 加(self, 项: 来源) -> bool:
        """加一个来源；重复（同类型同路径同网盘）返回 False。"""
        if not 项.路径:
            return False
        if any(x.唯一键 == 项.唯一键 for x in self._们):
            return False
        self._们.append(项)
        self.写()
        return True

    def 加本地(self, 目录: str) -> bool:
        return self.加(来源(类型="本地", 路径=str(目录),
                          显示名=Path(str(目录)).name or str(目录)))

    def 加网盘(self, 网盘标识: str, 远端路径: str, 显示名: str = "") -> bool:
        return self.加(来源(类型="网盘", 网盘=str(网盘标识), 路径=str(远端路径),
                          显示名=显示名 or f"{网盘标识}:{远端路径}"))

    def 移除(self, 下标: int) -> bool:
        try:
            项 = self._们.pop(int(下标))
        except Exception:  # noqa: BLE001
            return False
        self.写()
        return bool(项)


# ============================ 扫描 ============================

def _是视频(名字: str) -> bool:
    return Path(名字).suffix.lower() in 视频后缀


def 本地视频们(目录: str, 最大深度: int = 默认最大深度) -> list[str]:
    """本地目录里的视频文件（绝对路径字符串）。"""
    根 = Path(str(目录)).expanduser()
    if not 根.is_dir():
        return []
    找到: list[str] = []
    根层数 = len(根.parts)
    for 当前, 子目录们, 文件们 in __import__("os").walk(根):
        深度 = len(Path(当前).parts) - 根层数
        if 深度 >= max(1, int(最大深度)):
            子目录们[:] = []
        子目录们[:] = [d for d in 子目录们
                    if not d.startswith(".") and d.lower() not in
                    ("$recycle.bin", "system volume information")]
        for 名 in 文件们:
            if 名.startswith("."):
                continue
            if _是视频(名):
                找到.append(str(Path(当前) / 名))
    return sorted(找到)


def 远端视频们(网盘标识: str, 远端目录: str,
            列目录: Callable[[str, str], list],
            最大深度: int = 默认最大深度,
            进度: Optional[Callable[[str], None]] = None) -> list[str]:
    """网盘目录里的视频文件（返回 ``标识:远端路径`` 字符串）。

    :param 列目录: ``callable(网盘标识, 远端路径) -> [条目]``；条目是 dict，
        至少要有 ``名称`` 与 ``是目录``（``路径`` 可选，没有就用父路径拼）。
    """
    找到: list[str] = []
    # ⚠️ 别给目录硬加结尾斜杠：得**原样**交给列目录回调（适配器按它自己的规则解析），
    #    只有拼子路径时才补斜杠。实测加斜杠会让列目录查不到（自己踩过一次）。
    前缀 = str(远端目录 or "/").rstrip("/") or "/"

    def 走(目录: str, 深度: int) -> None:
        if 深度 > max(1, int(最大深度)):
            return
        try:
            条目们 = 列目录(网盘标识, 目录) or []
        except Exception as 错:  # noqa: BLE001 - 单个目录失败不该中断整轮
            if 进度 is not None:
                进度(f"⚠️ 列不了 {目录}：{错}")
            return
        for 条 in 条目们:
            try:
                名称 = str(条.get("名称") or 条.get("name") or "")
                是目录 = bool(条.get("是目录", 条.get("is_dir", False)))
                路径 = str(条.get("路径") or 条.get("path") or (目录.rstrip("/") + "/" + 名称))
            except Exception:  # noqa: BLE001
                continue
            if not 名称:
                continue
            if 是目录:
                走(路径, 深度 + 1)
            elif _是视频(名称):
                找到.append(库路径(网盘标识, 路径))

    走(前缀, 1)
    return sorted(set(找到))
