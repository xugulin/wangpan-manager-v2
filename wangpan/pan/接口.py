"""网盘适配器接口（M6）：V2 自己的契约，不与任何其它项目共用代码。

设计目标
========
* **一种契约，多种后端**：本地目录、HTTP 直链、以及后续的真实网盘（百度/夸克/光鸭）
  都实现同一组方法，界面与传输引擎只认契约；
* **能力可声明**：每个适配器自报"能不能列目录 / 能不能取直链 / 能不能上传"，
  界面据此灰掉不支持的按钮，而不是点了才发现不支持；
* **不假装支持**：没实现的直接抛 :class:`不支持`，绝不返回空列表让人以为是"空目录"。

契约（最小集，够播放与下载用）
==============================
``列出(路径) -> list[条目]``、``取直链(路径) -> 直链``、``下载(路径, 落点, 进度回调)``、
``上传?``、``删除?``、``信息() -> dict``。

条目字段：``名字 / 路径 / 是目录 / 大小 / 修改时间 / 标识``（标识用于删除/重命名等）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

__all__ = ["条目", "直链", "不支持", "适配器", "注册表"]


class 不支持(Exception):
    """这个能力适配器没有实现（界面据此提示，而不是静默失败）。"""


@dataclass
class 条目:
    """网盘里的一个文件或目录。"""

    名字: str
    路径: str = ""
    是目录: bool = False
    大小: int = 0
    修改时间: float = 0.0
    标识: str = ""                 # 后端自己的 id（删除/直链用；本地=路径）
    额外: dict = field(default_factory=dict)

    @property
    def 后缀(self) -> str:
        return ("." + self.名字.rsplit(".", 1)[-1].lower()) if "." in self.名字 else ""

    def 可播放(self) -> bool:
        return not self.是目录 and self.后缀 in (
            ".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts", ".flv", ".m4v", ".wmv",
            ".mp3", ".flac", ".aac", ".m4a", ".wav", ".ogg")

    def 大小文本(self) -> str:
        if self.是目录:
            return "目录"
        大小 = float(self.大小 or 0)
        for 单位 in ("B", "KB", "MB", "GB", "TB"):
            if 大小 < 1024 or 单位 == "TB":
                return f"{大小:.0f} {单位}" if 单位 == "B" else f"{大小:.1f} {单位}"
            大小 /= 1024
        return f"{大小:.1f} TB"


@dataclass
class 直链:
    """一个可以直接播/直接下的地址。"""

    地址: str
    请求头: dict = field(default_factory=dict)
    大小: int = 0
    有效秒: float = 0.0            # 0 = 不知道；网盘签名链接一般几分钟
    备注: str = ""

    def 过期了吗(self, 已用秒: float) -> bool:
        return bool(self.有效秒 and 已用秒 >= self.有效秒 * 0.9)


class 适配器:
    """适配器基类：默认什么都不支持，子类按能力覆盖。"""

    #: 显示名（界面用）
    名字 = "未命名"
    #: 能力声明（界面据此灰按钮）
    能力: set[str] = set()
    #: 上传能否从"远端已有的大小"接着传（本地/对象存储可以，普通 HTTP 表单不行）
    支持续传上传 = False

    def 信息(self) -> dict:
        return {"名字": self.名字, "能力": sorted(self.能力)}

    # ---------------- 只读 ----------------

    def 列出(self, 路径: str = "/") -> list[条目]:
        raise 不支持(f"{self.名字} 不支持列目录")

    def 取直链(self, 路径: str) -> 直链:
        raise 不支持(f"{self.名字} 不支持取直链")

    # ---------------- 写 ----------------

    def 下载(self, 路径: str, 落点, 进度回调: Optional[Callable[[int, int], None]] = None,
          取消=None):
        raise 不支持(f"{self.名字} 不支持下载")

    def 上传(self, 本地路径, 远端路径: str,
          进度回调: Optional[Callable[[int, int], None]] = None, 取消=None):
        raise 不支持(f"{self.名字} 不支持上传")

    def 删除(self, 路径: str) -> bool:
        raise 不支持(f"{self.名字} 不支持删除")

    def 建目录(self, 路径: str) -> bool:
        raise 不支持(f"{self.名字} 不支持建目录")

    def 关闭(self) -> None:
        return


class 注册表:
    """适配器登记处：界面按名字取用（谁注册谁负责关）。"""

    def __init__(self) -> None:
        self._适配器: dict[str, 适配器] = {}

    def 注册(self, 适配器对象: 适配器, 标识: str = "") -> str:
        键 = 标识 or 适配器对象.名字
        self._适配器[键] = 适配器对象
        return 键

    def 取(self, 标识: str) -> Optional[适配器]:
        return self._适配器.get(标识)

    def 全部(self) -> dict[str, 适配器]:
        return dict(self._适配器)

    def 关闭(self) -> None:
        for 项 in list(self._适配器.values()):
            try:
                项.关闭()
            except Exception:  # noqa: BLE001
                pass
        self._适配器.clear()
