"""弹幕源的统一接口：素材信息 / 匹配结果 / 弹幕源基类 / HTTP 传输抽象。

为什么要有这一层
================
* **源可插拔**：弹弹play 是"按文件找节目"，本地文件是"路径即答案"。上层（匹配、
  引擎、界面）只认 :class:`弹幕源` 的三个方法，不认具体实现；加一个源不用改上层。
* **传输必须可注入**：官方接口的签名、路由、HTTP 状态与"业务错误藏在 200 里"这些坑
  必须能**离线测**（CI 不能联网，也不能去打人家的接口）。所以"发一个 HTTP 请求"
  被抽成 :data:`传输函数`：生产用 :func:`urlopen传输`，测试塞个假函数即可。
  **任何源实现里都不许直接调 urllib**，否则这些协议细节就测不了了。
* **匹配结果与素材分开**：:class:`素材信息` 是"我们手里的文件"，
  :class:`匹配结果` 是"某个源说的话"。打分/决策放在 :mod:`~wangpan.danmaku.匹配`，
  源自己不打分 —— 只有一个地方打分，不同源的候选才可比。

依赖约定
========
只用标准库（urllib / hashlib / base64 / json / threading / pathlib），不引第三方。
"""

from __future__ import annotations

import math
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..模型 import 弹幕池

__all__ = [
    "弹幕源", "弹幕源错误", "素材信息", "匹配结果",
    "请求", "应答", "传输函数", "urlopen传输", "用户代理",
    "取整数", "取浮点",
]

#: 请求 UA（有些网关会挡空 UA；带上自己的名字也方便对方识别）
用户代理 = "wangpan-v2/2.0 (danmaku)"


class 弹幕源错误(Exception):
    """源相关的失败（网络、协议、业务错误码、文件坏了）—— 上层统一 catch 这一个。"""


# ---------------------------------------------------------------------------
# 文本取数（两个源的协议字段都是**文本**，解析规则共享一份）
# ---------------------------------------------------------------------------

def 取整数(值, 默认: Optional[int] = None) -> Optional[int]:
    """宽容地取整数：``"4"`` / ``"4.0"`` / ``4`` 都行；取不到给默认值。

    为什么要宽容：官方 ``p`` 字段和 XML 属性都是文本，实测有工具把模式写成
    ``"4.0"``、把字号写成 ``" 25"``；严格 ``int()`` 会把这些整条丢掉。
    ``bool`` 必须单独挡掉 —— Python 里 ``True`` 是 ``int`` 的实例，会把
    "模式=true" 变成模式 1。
    """
    if isinstance(值, bool):
        return 默认
    if isinstance(值, int):
        return 值
    if isinstance(值, float):
        return int(值) if math.isfinite(值) else 默认
    文本 = str(值 if 值 is not None else "").strip()
    if not 文本:
        return 默认
    try:
        return int(文本)
    except ValueError:
        pass
    try:
        数 = float(文本)
    except ValueError:
        return 默认
    return int(数) if math.isfinite(数) else 默认


def 取浮点(值, 默认: Optional[float] = None) -> Optional[float]:
    """宽容地取浮点；``nan`` / ``inf`` 当取不到（它们能"解析成功"但后面一转 int 就炸）。"""
    if isinstance(值, bool):
        return 默认
    try:
        数 = float(str(值 if 值 is not None else "").strip())
    except (TypeError, ValueError):
        return 默认
    return 数 if math.isfinite(数) else 默认


# ---------------------------------------------------------------------------
# HTTP：请求 / 应答 / 传输函数
# ---------------------------------------------------------------------------

@dataclass
class 请求:
    """一次 HTTP 请求（**只描述，不发送** —— 发送是 :data:`传输函数` 的事）。"""

    方法: str = "GET"
    地址: str = ""
    头: dict = field(default_factory=dict)
    体: Optional[bytes] = None
    超时: float = 20.0


@dataclass
class 应答:
    """一次 HTTP 应答。``体`` 允许直接给 str（测试里塞 JSON 字符串方便）。"""

    状态码: int = 200
    体: bytes = b""
    头: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.体, str):
            self.体 = self.体.encode("utf-8")
        self.状态码 = int(self.状态码)

    def 文本(self) -> str:
        # 解码用 replace：对方返回 HTML 错误页时也要能看个大概，不能在这里炸
        return self.体.decode("utf-8", errors="replace")


#: 传输函数：把 :class:`请求` 变成 :class:`应答`。测试注入假实现，生产用 urlopen传输。
传输函数 = Callable[[请求], 应答]


def 可发地址(地址: str) -> str:
    """把 URL 里的非 ASCII 百分号编码。

    实测踩到：URL 里出现中文时 ``urllib`` 直接抛
    ``UnicodeEncodeError: 'ascii' codec can't encode characters``（请求行只能是
    ASCII）。已在 URL 里的 ``%XX`` 必须放进 safe，否则会被二次编码成 ``%25XX``。
    """
    文本 = str(地址 or "")
    if not 文本:
        return 文本
    拆 = urllib.parse.urlsplit(文本)
    路径 = urllib.parse.quote(拆.path, safe="/%:@!$&'()*+,;=~-._")
    查询 = urllib.parse.quote(拆.query, safe="=&%:@!$'()*+,;~-._?/")
    return urllib.parse.urlunsplit((拆.scheme, 拆.netloc, 路径, 查询, 拆.fragment))


def urlopen传输(请求: 请求) -> 应答:
    """默认传输：标准库 urllib。

    **HTTP 错误不当异常抛**：4xx/5xx 也返回 :class:`应答`，让上层自己看状态码 ——
    因为官方接口会把业务错误放在 **HTTP 200** 里（见 弹弹play.检查业务状态），
    把"状态码"和"业务码"两件事混在一起会让错误处理到处漏。
    """
    头 = {"User-Agent": 用户代理, **{str(k): str(v) for k, v in (请求.头 or {}).items()}}
    原始 = urllib.request.Request(可发地址(请求.地址), data=请求.体, headers=头,
                                method=str(请求.方法 or "GET").upper())
    try:
        with urllib.request.urlopen(原始, timeout=float(请求.超时 or 20.0)) as 回应:
            return 应答(状态码=int(getattr(回应, "status", 200) or 200),
                       体=回应.read(), 头=dict(getattr(回应, "headers", {}) or {}))
    except urllib.error.HTTPError as 错:
        try:
            体 = 错.read() or b""
        except Exception:  # noqa: BLE001  # 连错误体都读不出来时别把原错误盖掉
            体 = b""
        return 应答(状态码=int(错.code or 0), 体=体, 头=dict(错.headers or {}))
    except urllib.error.URLError as 错:
        raise 弹幕源错误(f"连不上 {请求.地址}：{错.reason}") from 错


# ---------------------------------------------------------------------------
# 素材与匹配结果
# ---------------------------------------------------------------------------

@dataclass
class 素材信息:
    """要匹配/取弹幕的那个东西（一般是本地或网盘上的一个视频文件）。

    ``路径`` 与 ``文件名`` 二者至少有其一：只给路径时文件名自动从路径取；
    只有文件名（比如网盘条目还没下载）也能走"按文件名匹配"。
    """

    路径: Optional[Path] = None
    文件名: str = ""                # 含扩展名
    大小: int = 0
    时长秒: float = 0.0
    标题: str = ""                  # 已知的作品名（可空）
    集号: Optional[int] = None
    季号: Optional[int] = None
    额外: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.路径 is not None and not isinstance(self.路径, Path):
            self.路径 = Path(self.路径)
        if not self.文件名 and self.路径 is not None:
            self.文件名 = self.路径.name
        self.大小 = int(self.大小 or 0)
        self.时长秒 = float(self.时长秒 or 0.0)

    # ---- 派生信息 ----
    @property
    def 无扩展名(self) -> str:
        """不含目录、不含扩展名的文件名（官方 fileHash/match 接口要的就是这个）。"""
        return Path(self.文件名).stem if self.文件名 else ""

    @property
    def 后缀(self) -> str:
        return Path(self.文件名).suffix.lower() if self.文件名 else ""

    def 取大小(self) -> int:
        """字节数：显式给了就用给的，没给而文件在本地就读一次 stat。"""
        if self.大小:
            return self.大小
        if self.路径 is not None:
            try:
                return int(self.路径.stat().st_size)
            except OSError:
                return 0
        return 0

    def 可读文件(self) -> bool:
        return self.路径 is not None and self.路径.is_file()

    def 摘要(self) -> str:
        return (f"{self.文件名 or self.路径}（{self.取大小()} 字节"
                f"／{self.时长秒:.1f}s）")


@dataclass
class 匹配结果:
    """一个源给出的候选（**它说的话，不是结论**；结论由 匹配.自动决策 下）。"""

    标识: str                       # episodeId（弹弹play 里是字符串形式的整数）
    标题: str                       # 作品名
    集标题: str = ""
    集号: str = ""                  # 源给的集号（字符串，可能是 "1" / "第1话"）
    类型: str = ""
    偏移秒: float = 0.0             # 源建议的时间轴偏移（弹弹play 的 shift）
    分数: float = 0.0               # 我们的相似度打分（0-100），由 匹配.排序候选 填
    理由: str = ""
    原始: dict = field(default_factory=dict)

    def 说说(self) -> str:
        集 = self.集标题 or self.集号
        return f"{self.标题}｜{集}" if 集 else str(self.标题)


# ---------------------------------------------------------------------------
# 源基类
# ---------------------------------------------------------------------------

    def 可读(self) -> str:
        """给界面用的单行描述（候选列表里显示）。"""
        集 = f" 第{self.集号}集" if self.集号 else ""
        尾巴 = f"（{self.集标题}）" if self.集标题 else ""
        return f"{self.分数:5.1f} 分｜{self.标题}{集}{尾巴}｜{self.理由}"

class 弹幕源(ABC):
    """一个弹幕源（**只读**：V2 只取弹幕，不发送 —— 发送在官方是受限接口）。

    三个方法的分工：

    * :meth:`能匹配` —— 这个源能不能"按文件找节目"。本地源返回 False（它的
      "匹配"就是路径本身），上层据此决定要不要浪费一次网络请求；
    * :meth:`匹配` —— 返回候选列表；不支持或没匹配上返回 ``[]``，**不抛异常**，
      这样上层可以无脑遍历所有源；
    * :meth:`取弹幕` —— 按 ``匹配结果.标识`` 取弹幕；这个会抛
      :class:`弹幕源错误`（网络/协议问题必须让人知道，不能静默给空池）。
    """

    标识: str = ""                  # "dandanplay" / "local"
    名字: str = ""

    def 能匹配(self) -> bool:
        return False

    def 匹配(self, 素材: 素材信息) -> list[匹配结果]:  # noqa: ARG002 - 接口默认实现
        return []

    @abstractmethod
    def 取弹幕(self, 标识: str) -> 弹幕池:
        """按 ``匹配结果.标识`` 取弹幕。失败抛 :class:`弹幕源错误`。"""

    def 关闭(self) -> None:
        """释放资源（默认无事可做）。加锁/带在途表的源在这里把等待者叫醒。"""
        return None

    def __enter__(self) -> "弹幕源":
        return self

    def __exit__(self, *_: object) -> None:
        self.关闭()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.标识} {self.名字}>"
