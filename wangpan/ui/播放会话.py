"""播放会话：把 V2 的自研播放内核，包装成界面（GUI）所认的那一套"会话"接口。

为什么要有这一层
================
V2 的 :class:`wangpan.player.引擎.播放引擎` 是**纯内核**：它只认"打开/播放/暂停/跳转/
取最新帧/统计"，不认识网盘、不认识 AI、也没有"设置/媒体/探测"这些概念。

而界面要的东西比这多得多（历史包袱来自另一套播放器）：

* **网盘直链**要先解析（取直链 → 探测带宽 → 决定起播参数）再播；
* **AI 播放顾问**要"媒体信息 + 直链探测"这两个 dict 才肯给建议，
  播放完还要回收一张"参数 → 效果"的学习表；
* **界面控件**（控制条/清单/AI 面板/独立窗口）按 20 多个方法名调播放器
  （`进度秒/时长秒/暂停/跳转/设置速率/挂字幕/…`），
  并且**状态是一个固定的中文字符串**（`空闲/打开中/缓冲中/播放中/已暂停/已停止/已结束/出错`）。

所以这一层做三件事：

1. **翻译**：V2 的状态/统计 → 界面认的那套字段名与字符串；
2. **补齐**：网盘直链解析、本地/网络探测、AI 参数决策、效果学习；
3. **不该有的统统没有**：没有窗口句柄、没有播放出口、没有游离窗口巡检 ——
   画面由 :class:`wangpan.ui.视频控件` 自己画，"视频跑到别的窗口里"结构上不可能发生。

整合取舍（相对旧内核的有意改进）
================================
* ``起播(句柄)`` 的 ``句柄`` 参数**保留但忽略**：不再有"把画布交给别人"这一步。
* ``装主线程泵/排空主线程队列`` 保留为**空实现**：V2 引擎自身线程安全，
  后台线程直接调控制方法是安全的（旧内核要靠"在主线程排队"绕开 libvlc 的 SEGV）。
* ``网络缓存毫秒`` 不再映射成播放器私有选项，而是换算成 **libavformat 的读缓冲字节数**；
  ``硬解`` 映射成"允不允许硬解"这一路开关。语义等价，但不再是它自己的黑话。
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

from ..player.引擎 import 播放引擎, 播放状态 as 内核状态

__all__ = [
    "播放会话", "V2播放器", "播放设置", "媒体信息", "探测结果", "音视频轨",
    "会话状态", "规则参数",
]


# ============================================================================
# 状态字符串：界面写死了这 8 个值，不许改
# ============================================================================

class 会话状态:
    """界面认的状态字符串（**硬契约**，独立窗口/自动诊断/AI 面板都按它判断）。

    注意它是"界面语言"，跟内核的 :class:`播放状态` 不是一回事：
    内核只有 5 个状态，界面还需要 `打开中/缓冲中/已停止` 这些中间态。
    """

    空闲 = "空闲"
    打开中 = "打开中"
    缓冲中 = "缓冲中"
    播放中 = "播放中"
    已暂停 = "已暂停"
    已停止 = "已停止"
    已结束 = "已结束"
    出错 = "出错"


#: 界面状态串的固定顺序（``播放器.状态`` 返回的就是它里面的序号）
状态次序 = (会话状态.空闲, 会话状态.打开中, 会话状态.缓冲中, 会话状态.播放中,
          会话状态.已暂停, 会话状态.已停止, 会话状态.已结束, 会话状态.出错)


def _状态序(名字: str) -> int:
    try:
        return 状态次序.index(str(名字))
    except ValueError:
        return 0


#: 内核状态 → 界面状态
_状态对照 = {
    内核状态.空闲: 会话状态.空闲,
    内核状态.播放中: 会话状态.播放中,
    内核状态.暂停: 会话状态.已暂停,
    内核状态.结束: 会话状态.已结束,
    内核状态.出错: 会话状态.出错,
}


# ============================================================================
# 三个数据类：字段名沿用界面的老约定（AI 顾问按这些键取值）
# ============================================================================

class 音视频轨(NamedTuple):
    """一条音轨/字幕轨（界面用它填菜单）。

    为什么是 :class:`typing.NamedTuple` 而不是 dataclass：菜单构建代码会
    ``for 编号, 名称 in 轨道们`` 直接解包，普通 dataclass 解不开（真踩过）。
    NamedTuple 两种用法都成立（解包 + ``.编号/.名称``）。
    """

    编号: int
    名称: str = ""

    def 可读(self) -> str:
        return f"{self.编号}｜{self.名称}" if self.名称 else str(self.编号)


@dataclass
class 播放设置:
    """一路播放的起播参数（AI 顾问给的就是这些字段）。"""

    网络缓存毫秒: int = 3000
    起播等待秒: float = 0.0
    #: 取值：``自动``（有硬解就用）/``软解``（强制 CPU）；旧值 ``none/vaapi/…`` 也认
    硬解: str = "自动"
    #: 旧内核的 libvlc 追加选项；V2 内核没有这个概念，保留字段只为不丢信息
    附加选项: list[str] = field(default_factory=list)
    可流畅播放: bool = True
    理由: str = ""
    风险: str = ""
    来源: str = "规则"
    允许丢帧: bool = True
    视频输出: str = ""

    @property
    def 要软解(self) -> bool:
        """这一路要不要强制软解。"""
        值 = str(self.硬解 or "").strip().lower()
        return 值 in ("软解", "none", "no", "off", "false", "0", "cpu")

    def 摘要(self) -> str:
        return (f"缓存 {self.网络缓存毫秒}ms｜硬解 {self.硬解}"
                f"｜{self.来源}" + (f"｜{self.理由}" if self.理由 else ""))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class 媒体信息:
    """媒体信息（AI 顾问的输入之一）。字段名沿用老内核的约定。"""

    已知: bool = False
    错误: str = ""
    分辨率: str = ""
    宽: int = 0
    高: int = 0
    档位: str = ""
    视频编码: str = ""
    视频码率bps: float = 0.0
    音频编码: str = ""
    音频码率bps: float = 0.0
    时长秒: float = 0.0
    容器: str = ""
    音轨数: int = 0
    字幕轨数: int = 0
    字幕语言: str = ""
    帧率: float = 0.0
    总码率bps: float = 0.0

    def 摘要(self) -> str:
        if not self.已知:
            return f"媒体信息未知（{self.错误}）" if self.错误 else "媒体信息未知"
        部分 = [self.分辨率 or "?", self.视频编码 or "?"]
        if self.视频码率bps > 0:
            部分.append(f"{self.视频码率bps / 1e6:.1f}Mbps")
        if self.音频编码:
            部分.append(self.音频编码)
        if self.时长秒 > 0:
            部分.append(f"{int(self.时长秒 // 60)}:{int(self.时长秒 % 60):02d}")
        return "｜".join(str(x) for x in 部分)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class 探测结果:
    """直链探测结果（AI 顾问的输入之二）。"""

    成功: bool = False
    来源说明: str = ""
    首字节毫秒: float = 0.0
    实测带宽bps: float = 0.0
    range支持: bool = False
    状态码: int = 0
    内容类型: str = ""
    内容长度: int = 0
    已读字节: int = 0
    用时秒: float = 0.0
    错误: str = ""

    def 摘要(self) -> str:
        if not self.成功:
            return f"探测失败（{self.错误 or '未知'}）"
        部分 = [self.来源说明 or "已探测"]
        if self.首字节毫秒 > 0:
            部分.append(f"首字节 {self.首字节毫秒:.0f}ms")
        if self.实测带宽bps > 0:
            部分.append(f"{self.实测带宽bps / 1e6:.1f}Mbps")
        if self.range支持:
            部分.append("支持 Range")
        return "｜".join(str(x) for x in 部分)

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================================
# 播放器门面：界面绕过会话直接调的那 20 多个方法
# ============================================================================

class V2播放器:
    """`会话.播放器` —— 界面把它当"那个播放器"用，实际是 V2 引擎的门面。

    有些方法在 V2 里**没有对应物**（宽高比/缩放/静音），保留是为了让菜单不报错：
    要么给出真实语义（缩放 = 视频控件按比例摆放），要么老实返回一个"没这个功能"。
    绝不假装成功 —— 假装成功会让界面显示一个与实际不符的状态。
    """

    #: 缩放档（V2 的画面是"保持比例居中"，所以只有 1.0 是真话）
    _默认缩放 = 1.0

    def __init__(self, 会话: "播放会话") -> None:
        self._会话 = 会话
        self._缩放 = self._默认缩放
        self._宽高比 = ""
        self._静音 = False
        self._静音前音量 = 100

    # ---------- 时间 ----------

    def 进度秒(self) -> float:
        return float(self._会话.引擎.统计.当前时间秒 or 0.0)

    def 时长秒(self) -> float:
        return float(self._会话.引擎.统计.总时长秒 or 0.0)

    @property
    def 状态(self) -> int:
        """老内核这里是"状态码整数"；这里给的是**界面状态串的稳定序号**。

        ⚠️ 不能反过来去读 ``会话.状态快照()`` —— 快照里要填 ``状态码``，
        那样会自己调自己（真踩过：RecursionError）。
        """
        return _状态序(_状态对照.get(self._会话.引擎.状态, 会话状态.空闲))

    def 有画面(self) -> bool:
        return self._会话.引擎.取最新帧() is not None

    def 帧率(self) -> float:
        return float(self._会话.引擎.统计.帧率 or 0.0)

    # ---------- 画面 ----------

    def 缩放(self) -> float:
        return self._缩放

    def 设置缩放(self, 倍率) -> bool:
        try:
            值 = float(倍率)
        except (TypeError, ValueError):
            return False
        self._缩放 = max(0.25, min(4.0, 值))
        self._会话.缩放 = self._缩放          # 视频控件按它摆画面
        return True

    def 宽高比(self) -> str:
        return self._宽高比

    def 设置宽高比(self, 比例) -> bool:
        """V2 的画面永远保持源比例（这是"不变形"的唯一正解），所以只记账不改变形。"""
        文本 = str(比例 or "").strip()
        if 文本 in ("", "默认", "自动"):
            self._宽高比 = ""
            return True
        # 老内核认 "16:9"/"4:3" 这类；这里接受但只作为显示信息
        if ":" in 文本:
            self._宽高比 = 文本
            return True
        return False

    # ---------- 播放控制 ----------

    def 停止并等待(self, 超时秒: float = 3.0) -> bool:
        try:
            self._会话.引擎.停止()
            return True
        except Exception:  # noqa: BLE001
            return False

    def 绑定窗口(self, 句柄) -> None:
        """V2 不需要窗口句柄（画面自己画）。保留为空实现，不再有"交画布"这一步。"""
        return None

    def 下一帧(self) -> bool:
        return bool(self._会话.引擎.逐帧())

    # ---------- 轨道 ----------

    def 字幕轨(self) -> list[音视频轨]:
        轨道们 = list(getattr(self._会话, "_字幕们", []) or [])
        结果 = [音视频轨(编号=-1, 名称="关闭字幕")]
        for 序号, 轨 in enumerate(轨道们):
            结果.append(音视频轨(编号=序号, 名称=getattr(轨, "名字", "") or f"字幕{序号 + 1}"))
        return 结果

    def 音频轨(self) -> list[音视频轨]:
        # V2 目前只播第一条音轨（多音轨切换在路线图里），如实说明而不是给假选项
        流 = getattr(self._会话.引擎.输入, "音频流", None)
        if 流 is None:
            return []
        return [音视频轨(编号=0, 名称=流.一句话())]

    def 选择字幕(self, 编号: int) -> bool:
        return bool(self._会话.选字幕轨(int(编号)))

    def 选择音频(self, 编号: int) -> bool:
        返回 = int(编号) in (0, -1)
        if not 返回:
            self._会话._日志("[音轨] V2 当前只播第一条音轨，切换在路线图里")
        return 返回

    def 当前字幕(self) -> int:
        return int(getattr(self._会话, "_字幕序号", -1))

    # ---------- 章节 ----------

    def 章节数(self) -> int:
        return len(self._会话.引擎.章节们())

    def 当前章节(self) -> int:
        return int(self._会话.引擎.当前章节())

    def 跳章节(self, 编号: int) -> bool:
        return bool(self._会话.引擎.跳章节(int(编号)))

    def 下一章(self) -> bool:
        列表 = self._会话.引擎.章节们()
        当前 = self._会话.引擎.当前章节()
        if not 列表:
            return False
        return self.跳章节(min(len(列表) - 1, (当前 if 当前 >= 0 else -1) + 1))

    def 上一章(self) -> bool:
        当前 = self._会话.引擎.当前章节()
        if not self._会话.引擎.章节们():
            return False
        return self.跳章节(max(0, (当前 if 当前 >= 0 else 1) - 1))

    # ---------- 音量 / 速率 / 静音 ----------

    def 取速率(self) -> float:
        return float(self._会话.引擎.倍速 or 1.0)

    def 设置速率(self, 倍速) -> bool:
        try:
            self._会话.引擎.设置倍速(float(倍速))
            return True
        except Exception:  # noqa: BLE001
            return False

    def 取音量(self) -> int:
        """界面用 0–100 的整数音量。"""
        return int(round(float(self._会话.引擎.音量 or 0.0) * 100))

    def 设置音量(self, 音量: int) -> bool:
        try:
            值 = max(0, min(100, int(音量)))
        except (TypeError, ValueError):
            return False
        self._静音 = 值 == 0
        self._会话.引擎.设置音量(值 / 100.0)
        return True

    def 是否静音(self) -> bool:
        return bool(self._静音) or self.取音量() == 0

    def 设置静音(self, 静音: bool) -> bool:
        要静音 = bool(静音)
        if 要静音:
            if not self.是否静音():
                self._静音前音量 = max(1, self.取音量())
                self.设置音量(0)
                self._静音 = True
        else:
            self.设置音量(self._静音前音量 or 100)
            self._静音 = False
        return True


# ============================================================================
# 规则参数：没有 AI 时的保底（AI 顾问不在/不启用时用它）
# ============================================================================

def 规则参数(媒体: Optional[媒体信息] = None,
            探测: Optional[探测结果] = None,
            硬解列表: Optional[list[str]] = None) -> 播放设置:
    """按"带宽 / 码率"给一组保守但够用的起播参数。

    为什么要有它：AI 关着的时候播放也得有一组合理参数，而不是硬编码常量。
    """
    设置 = 播放设置(来源="规则")
    码率 = 0.0
    if 媒体 is not None and 媒体.已知:
        码率 = float(媒体.视频码率bps or 媒体.总码率bps or 0.0)
    带宽 = float(getattr(探测, "实测带宽bps", 0.0) or 0.0) if 探测 else 0.0
    if 探测 is not None and not 探测.成功:
        设置.理由 = "直链没探测成功，用保守参数"
        设置.网络缓存毫秒 = 8000
        return 设置
    if 码率 > 0 and 带宽 > 0:
        比值 = 带宽 / max(1.0, 码率)
        if 比值 >= 2.0:
            设置.网络缓存毫秒 = 2000
            设置.理由 = f"带宽是码率的 {比值:.1f} 倍，缓存放小起播更快"
        elif 比值 >= 1.3:
            设置.网络缓存毫秒 = 6000
            设置.理由 = f"带宽是码率的 {比值:.1f} 倍，给中等缓冲"
        else:
            设置.网络缓存毫秒 = 15000
            设置.可流畅播放 = False
            设置.风险 = f"带宽只有码率的 {比值:.1f} 倍，可能反复缓冲"
            设置.理由 = "带宽紧，缓存加大"
    return 设置


# ============================================================================
# 播放会话
# ============================================================================

class 播放会话:
    """一路播放的"总控"：解析源 → 探测 → 决策参数 → 起播 → 统计 → 收尾学习。

    构造参数里只有 ``日志回调`` 是必需的，其余都是可选增强：
    没有 AI、没有网盘、没有探测函数时**照样能播本地文件**。
    """

    #: 判定"卡住了"的阈值：播放中、时间不动超过这么久 → 报"缓冲中"
    缓冲判定秒 = 1.5
    #: 采样并诊断时，两次之间最少隔多久（避免日志刷屏）
    诊断冷却秒 = 90.0

    def __init__(self, *,
                 日志回调: Optional[Callable[[str], None]] = None,
                 取适配器: Optional[Callable[[str], Any]] = None,
                 顾问: Any = None,
                 自动调优: bool = False,
                 本地模型: Any = None,
                 探测媒体: Optional[Callable[..., 媒体信息]] = None,
                 探测直链: Optional[Callable[..., 探测结果]] = None,
                 引擎: Optional[播放引擎] = None) -> None:
        self._日志 = 日志回调 or (lambda _t: None)
        self.取适配器 = 取适配器 or (lambda _标识="": None)
        self.顾问 = 顾问
        self.自动调优 = bool(自动调优)
        self.本地模型 = 本地模型
        #: 外部注入的探测函数（界面侧用 ffprobe / 直链探测实现；不注入就用内核自带的粗略版）
        self._探测媒体 = 探测媒体 or self._内核探测媒体
        self._探测直链 = 探测直链 or self._内核探测直链

        self.引擎 = 引擎 if 引擎 is not None else 播放引擎(日志回调=self._日志)
        self.播放器 = V2播放器(self)

        self.设置 = 播放设置()
        self.媒体 = 媒体信息()
        self.探测 = 探测结果()
        #: ``{"url", "headers", "name", "size"}`` —— AI 面板的语音识别要读 url
        self.直链信息: dict = {}
        self.网盘标识 = ""
        self.远端路径 = ""
        self.标题 = ""
        self.缩放 = 1.0
        self.AI决策状态 = ""

        self._字幕们: list = []
        self._字幕序号 = -1
        self._锁 = threading.RLock()
        self._上次诊断时刻 = 0.0
        self._上次进度 = (0.0, time.monotonic())
        self._卡住 = False
        self._已起播 = False
        self._准备过 = False
        self._学习键 = ""
        self._采样起 = time.monotonic()

    # ------------------------------------------------------------------ 日志

    def _记(self, 文本: str) -> None:
        try:
            self._日志(str(文本))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ 准备

    def 准备(self, 网盘标识: str, 远端路径: str) -> dict:
        """解析这一路源：取直链 → 探测 → 媒体信息 → 起播参数（可能问 AI）。

        返回一份"摘要 dict"给界面显示；**不在这里打开内核**（起播才打开），
        这样界面可以先显示"正在缓冲/正在探测"，用户也能取消。
        """
        开始 = time.monotonic()
        self.网盘标识 = str(网盘标识 or "")
        self.远端路径 = str(远端路径 or "")
        self.标题 = Path(self.远端路径).name or str(self.远端路径)
        self._准备过 = False
        self._已起播 = False

        本地 = self._是不是本地()
        if 本地:
            地址, 头们 = self.远端路径, {}
            self.直链信息 = {"url": 地址, "headers": {}, "name": self.标题, "size": 0}
        else:
            地址, 头们, 大小, 名字 = self._取直链(self.网盘标识, self.远端路径)
            self.直链信息 = {"url": 地址, "headers": dict(头们 or {}),
                            "name": 名字 or self.标题, "size": int(大小 or 0)}
            大小 = int(大小 or 0)

        # ---- 探测（带宽 / 首字节 / Range）----
        try:
            self.探测 = self._探测直链(地址, dict(头们 or {}), 本地=本地,
                                    大小=int(self.直链信息.get("size") or 0))
        except Exception as 错:  # noqa: BLE001
            self.探测 = 探测结果(成功=False, 错误=str(错),
                               来源说明="本地文件" if 本地 else "")
            self._记(f"[探测] 失败（不影响起播）：{错}")

        # ---- 媒体信息 ----
        try:
            self.媒体 = self._探测媒体(地址, dict(头们 or {}), 本地=本地,
                                    大小=int(self.直链信息.get("size") or 0))
        except Exception as 错:  # noqa: BLE001
            self.媒体 = 媒体信息(已知=False, 错误=str(错))

        # ---- 起播参数：本地文件**不问 AI** ----
        self.设置 = self._决策参数(本地=本地)

        # ---- 学习键：这次播放属于"哪一类"（下次同类场景直接复用）----
        try:
            if self.顾问 is not None and hasattr(self.顾问, "生成学习键"):
                self._学习键 = str(self.顾问.生成学习键(
                    self.网盘标识, getattr(self.媒体, "档位", ""), self.媒体.视频编码) or "")
        except Exception:  # noqa: BLE001
            self._学习键 = ""

        用时 = time.monotonic() - 开始
        摘要 = {
            "类型": "准备完成",
            "网盘": self.网盘标识,
            "路径": self.远端路径,
            "标题": self.标题,
            "直链": self.直链信息,
            "媒体摘要": self.媒体.摘要(),
            "探测摘要": self.探测.摘要(),
            "设置摘要": self.设置.摘要(),
            "来源": self.设置.来源,
            "可流畅播放": self.设置.可流畅播放,
            "理由": self.设置.理由,
            "风险": self.设置.风险,
            "用时秒": 用时,
        }
        self._准备过 = True
        self._记(f"[准备] {self.标题}｜{self.探测.摘要()}｜{self.设置.摘要()}"
                f"｜用时 {用时:.2f}s")
        return 摘要

    def _是不是本地(self) -> bool:
        """本地文件 / "本地"网盘实例都当本地（本地文件不经网络，跳过网络那套）。"""
        if self.网盘标识 in ("本地", "", "local", "file"):
            路径 = Path(self.远端路径)
            if 路径.is_file():
                return True
        return Path(self.远端路径).is_file() and not str(self.远端路径).lower().startswith(
            ("http://", "https://"))

    def _取直链(self, 网盘标识: str, 远端路径: str) -> tuple[str, dict, int, str]:
        适配器 = self.取适配器(网盘标识)
        if 适配器 is None:
            raise RuntimeError(f"没有这个网盘：{网盘标识}")
        结果 = 适配器.取播放直链(远端路径) if hasattr(适配器, "取播放直链") \
            else 适配器.取直链(远端路径)
        if isinstance(结果, dict):
            地址 = str(结果.get("url") or 结果.get("地址") or "")
            头们 = dict(结果.get("headers") or 结果.get("请求头") or {})
            大小 = int(结果.get("size") or 结果.get("大小") or 0)
            名字 = str(结果.get("name") or "")
        else:
            地址 = str(getattr(结果, "地址", "") or getattr(结果, "url", ""))
            头们 = dict(getattr(结果, "请求头", {}) or {})
            大小 = int(getattr(结果, "大小", 0) or 0)
            名字 = str(getattr(结果, "备注", "") or "")
        if not 地址:
            raise RuntimeError("网盘没给出可播放的直链")
        return 地址, 头们, 大小, 名字

    def _决策参数(self, *, 本地: bool) -> 播放设置:
        """起播参数：本地文件走规则；网络直链有顾问就问顾问。"""
        if 本地:
            self.AI决策状态 = "本地文件：不走 AI 调参"
            return 播放设置(来源="本地文件", 网络缓存毫秒=0,
                           理由="本地文件没有带宽问题，AI 的保守大缓存反而是负优化")
        if self.顾问 is None or not self.自动调优:
            self.AI决策状态 = "规则参数（AI 未启用）"
            return 规则参数(self.媒体, self.探测)
        入参 = self._顾问入参()
        try:
            建议 = dict(self.顾问.建议起播参数(入参) or {})
        except Exception as 错:  # noqa: BLE001
            self.AI决策状态 = f"顾问出错，回落规则：{错}"
            self._记(f"[AI] 建议起播参数失败（回落规则）：{错}")
            return 规则参数(self.媒体, self.探测)
        if not 建议:
            self.AI决策状态 = "顾问没有建议，回落规则"
            return 规则参数(self.媒体, self.探测)
        设置 = self._套用建议(建议)
        self.AI决策状态 = f"{设置.来源}：{设置.理由 or 设置.摘要()}"
        return 设置

    def _顾问入参(self) -> dict:
        """AI 顾问要的那一大坨（少一个键它就可能给出失真建议）。"""
        入参 = {
            "网盘": self.网盘标识,
            "文件名": self.标题,
            "大小字节": int(self.直链信息.get("size") or 0),
        }
        try:
            入参.update(self.媒体.to_dict())
        except Exception:  # noqa: BLE001
            pass
        try:
            入参.update(self.探测.to_dict())
        except Exception:  # noqa: BLE001
            pass
        入参["本机硬解"] = self._本机硬解()
        入参["缓存目录剩余字节"] = self._缓存剩余()
        return 入参

    def _套用建议(self, 建议: dict) -> 播放设置:
        """把顾问给的 dict 装成 :class:`播放设置`，并做**缓存下限**保护。

        下限保护是踩过坑的：带宽 13Mbps / 码率 12.7Mbps 时 AI 把缓存从 12000ms
        压到 6000ms → 反复缓冲。所以"带宽比码率紧"时缓存只许加大，不许变小。
        """
        设置 = 播放设置()
        for 键 in ("网络缓存毫秒", "起播等待秒", "硬解", "附加选项", "可流畅播放",
                  "理由", "风险", "来源", "允许丢帧", "视频输出"):
            if 键 in 建议 and 建议[键] is not None:
                setattr(设置, 键, 建议[键])
        try:
            设置.网络缓存毫秒 = int(设置.网络缓存毫秒 or 0)
            设置.起播等待秒 = float(设置.起播等待秒 or 0.0)
        except (TypeError, ValueError):
            设置.网络缓存毫秒, 设置.起播等待秒 = 3000, 0.0
        下限 = self.缓存下限()
        if 下限 and 设置.网络缓存毫秒 < 下限:
            self._记(f"[AI] 建议缓存 {设置.网络缓存毫秒}ms 低于下限 {下限}ms，"
                    f"抬到下限（带宽不够时缓存只许加大）")
            设置.网络缓存毫秒 = 下限
        设置.附加选项 = list(设置.附加选项 or [])
        return 设置

    def 缓存下限(self) -> int:
        """带宽不够时允许的最小缓存毫秒；0 = 不做限制。"""
        码率 = float(getattr(self.媒体, "视频码率bps", 0.0) or 0.0)
        带宽 = float(getattr(self.探测, "实测带宽bps", 0.0) or 0.0)
        if 码率 > 0 and 带宽 > 0 and 带宽 < 码率 * 1.3:
            return 15000
        return 0

    def _本机硬解(self) -> list[str]:
        """本机能用的硬解后端（给顾问看的能力清单）。"""
        候选: list[str] = []
        try:
            from ..player import 解码 as _解码
            绑定 = self.引擎.输入.绑定 if self.引擎.输入 is not None else None
            if 绑定 is not None:
                流 = getattr(self.引擎.视频, "流", None)
                if 流 is not None:
                    候选 = [名 for 名, _号, _设备 in self.引擎.视频._硬解候选()]
        except Exception:  # noqa: BLE001
            候选 = []
        return 候选 or ["auto"]

    def _缓存剩余(self) -> int:
        try:
            目录 = Path(__file__).resolve().parents[2] / "数据" / "缓存"
            import shutil as _shutil
            目录.mkdir(parents=True, exist_ok=True)
            return int(_shutil.disk_usage(目录).free)
        except Exception:  # noqa: BLE001
            return 0

    # ------------------------------------------------------------------ 探测兜底

    def _内核探测媒体(self, 地址: str, 头们: dict, *, 本地: bool = False,
                   大小: int = 0) -> 媒体信息:
        """不注入外部探测器时的粗略媒体信息（**只靠已经打开的输入**）。

        网盘直链要拿"码率/编码/分辨率"最准的是 ffprobe；界面侧会注入那个实现。
        这里只做兜底：用已经打开的内核输入拼一份能用的，绝不为它单独去联网。
        """
        输入 = self.引擎.输入
        if 输入 is None:
            return 媒体信息(已知=False, 错误="还没打开文件")
        视频流, 音频流 = 输入.视频流, 输入.音频流
        时长 = float(输入.时长秒 or 0.0)
        大小字节 = int(大小 or 0)
        if not 大小字节 and 本地:
            try:
                大小字节 = int(Path(地址).stat().st_size)
            except Exception:  # noqa: BLE001
                大小字节 = 0
        总码率 = (大小字节 * 8.0 / 时长) if (大小字节 and 时长 > 0) else 0.0
        信息 = 媒体信息(
            已知=True, 时长秒=时长, 容器=Path(地址).suffix.lstrip(".").lower(),
            音轨数=len([s for s in 输入.流们 if s.是音频]),
            总码率bps=总码率,
        )
        if 视频流 is not None:
            信息.宽, 信息.高 = int(视频流.宽), int(视频流.高)
            信息.分辨率 = f"{信息.宽}x{信息.高}"
            信息.视频编码 = str(视频流.编码名 or "")
            信息.帧率 = float(视频流.帧率 or 0.0)
            信息.视频码率bps = 总码率
        if 音频流 is not None:
            信息.音频编码 = f"{音频流.编码名} {音频流.采样率}Hz {音频流.声道数}ch"
        信息.档位 = _分辨率档(信息.分辨率)
        return 信息

    def _内核探测直链(self, 地址: str, 头们: dict, *, 本地: bool = False,
                   大小: int = 0) -> 探测结果:
        """不注入外部探测器时的兜底：本地文件直接算"能播"，网络不做实测。"""
        if 本地:
            return 探测结果(成功=True, 来源说明="本地文件", range支持=True,
                          内容长度=int(大小 or 0))
        return 探测结果(成功=False, 来源说明="未探测",
                      错误="没有注入直链探测器（不影响起播，只是 AI 拿不到带宽）",
                      内容长度=int(大小 or 0))

    # ------------------------------------------------------------------ 起播

    def 起播(self, 窗口句柄: int = 0) -> bool:
        """真正打开并起播。``窗口句柄`` 参数保留但忽略（画面自己画）。"""
        地址 = str(self.直链信息.get("url") or "")
        if not 地址:
            self._记("[起播] 没有可播的地址（先调 准备()）")
            return False
        头们 = dict(self.直链信息.get("headers") or {})
        设置 = self.设置
        缓冲字节 = self._缓存毫秒转字节(设置.网络缓存毫秒)
        try:
            self.引擎.打开(地址, 请求头=头们,
                        不要硬解=bool(getattr(设置, "要软解", False)),
                        缓冲字节=缓冲字节)
        except Exception as 错:  # noqa: BLE001
            self._记(f"[起播] 打不开：{错}")
            self.引擎.统计.状态 = 内核状态.出错
            return False
        try:
            self.引擎.播放()
        except Exception as 错:  # noqa: BLE001
            self._记(f"[起播] 起播失败：{错}")
            return False
        self._已起播 = True
        self._采样起 = time.monotonic()
        self._上次进度 = (0.0, time.monotonic())
        self._记(f"[起播] {self.标题}｜{self.媒体.摘要()}｜{设置.摘要()}")
        return True

    def _缓存毫秒转字节(self, 毫秒) -> int:
        """把"网络缓存毫秒"换算成 libavformat 的读缓冲字节数。

        为什么这么换：libvlc 的 ``:network-caching`` 是"先攒够这么多再给你"，
        而 libavformat 的 ``buffer_size`` 是"一次读多少"。要表达同一个意思，
        得按**码率**折算：缓冲秒数 × 码率 ÷ 8 = 该攒的字节数。
        码率未知时按 8Mbps 估（常见 1080p 直链的量级），并夹在 256KB–32MB。
        """
        try:
            毫秒 = int(毫秒 or 0)
        except (TypeError, ValueError):
            毫秒 = 0
        if 毫秒 <= 0:
            return 0
        码率 = float(getattr(self.媒体, "视频码率bps", 0.0)
                   or getattr(self.媒体, "总码率bps", 0.0) or 0.0)
        if 码率 <= 0:
            码率 = 8e6
        字节 = int(毫秒 / 1000.0 * 码率 / 8.0)
        return max(256 * 1024, min(32 * 1024 * 1024, 字节))

    # ------------------------------------------------------------------ 控制

    def 暂停(self) -> None:
        self.引擎.设置暂停(True)

    def 设置暂停(self, 暂停: bool) -> None:
        self.引擎.设置暂停(bool(暂停))

    def 暂停切换(self) -> bool:
        return bool(self.引擎.暂停切换())

    def 跳转(self, 秒: float) -> None:
        self.引擎.跳转(max(0.0, float(秒 or 0.0)))

    def 相对跳转(self, 秒: float) -> None:
        self.跳转(float(self.引擎.统计.当前时间秒 or 0.0) + float(秒 or 0.0))

    def 跳到结尾(self) -> None:
        总 = float(self.引擎.统计.总时长秒 or 0.0)
        if 总 > 0:
            self.跳转(max(0.0, 总 - 1.0))

    def 逐帧(self) -> bool:
        return bool(self.引擎.逐帧())

    def 设置音量(self, 音量: int) -> None:
        self.播放器.设置音量(音量)

    def 设置速率(self, 倍速: float) -> None:
        self.引擎.设置倍速(float(倍速))

    def 截图(self, 保存路径: str) -> bool:
        try:
            return bool(self.引擎.截图(str(保存路径)))
        except Exception as 错:  # noqa: BLE001
            self._记(f"[截图] 失败：{错}")
            return False

    # ------------------------------------------------------------------ 字幕

    def 装字幕们(self, 轨道们: list) -> None:
        """界面把"找到了哪几条字幕"告诉会话（供轨道菜单显示与切换）。"""
        self._字幕们 = list(轨道们 or [])
        self._字幕序号 = 0 if self._字幕们 else -1

    def 挂字幕(self, 路径: str, 选中: bool = True) -> bool:
        """挂一路字幕文件（界面读好文件之后调这个）。"""
        try:
            from ..subtitle import 读字幕文件
            轨道 = 读字幕文件(str(路径))
        except Exception as 错:  # noqa: BLE001
            self._记(f"[字幕] 读不了 {路径}：{错}")
            return False
        现有 = [t for t in self._字幕们 if getattr(t, "名字", "") == getattr(轨道, "名字", "")]
        if not 现有:
            self._字幕们.append(轨道)
        self._字幕序号 = self._字幕们.index(现有[0] if 现有 else 轨道)
        if 选中:
            self.设置当前字幕轨道(self._字幕序号)
        return True

    def 设置当前字幕轨道(self, 序号: int) -> bool:
        """把第 ``序号`` 条字幕交给界面显示（-1 = 关字幕）。"""
        self._字幕序号 = int(序号)
        self.界面字幕回调(self.取当前字幕轨道())
        return True

    def 选字幕轨(self, 编号: int) -> bool:
        return self.设置当前字幕轨道(int(编号))

    def 切换字幕(self) -> int:
        """轮换字幕轨（含"关"这一档）。返回新的序号。"""
        if not self._字幕们:
            self._字幕序号 = -1
            self.界面字幕回调(None)
            return -1
        self._字幕序号 = (self._字幕序号 + 1) % (len(self._字幕们) + 1)
        self.界面字幕回调(self.取当前字幕轨道())
        return self._字幕序号

    def 取当前字幕轨道(self):
        if 0 <= self._字幕序号 < len(self._字幕们):
            return self._字幕们[self._字幕序号]
        return None

    @property
    def 字幕们(self) -> list:
        return list(self._字幕们)

    @property
    def 字幕序号(self) -> int:
        return int(self._字幕序号)

    #: 界面挂上来的"请把这条字幕画出来"回调（由播放页设置）
    界面字幕回调: Callable[[Any], None] = staticmethod(lambda _轨道: None)

    # ------------------------------------------------------------------ 状态

    def _算缓冲中(self) -> bool:
        """用"时间戳有没有在走"判断是不是卡住了。

        V2 内核没有"缓冲中"这个状态（它只在等数据），但界面与 AI 顾问都需要它：
        AI 顾问明确要求"正在缓冲时不许把缓存改小"。所以这里用播放时钟是否推进
        来推断 —— 播放中 + 时间超过阈值没动 = 缓冲中。
        """
        if self.引擎.状态 != 内核状态.播放中:
            self._上次进度 = (float(self.引擎.统计.当前时间秒 or 0.0), time.monotonic())
            self._卡住 = False
            return False
        当前 = float(self.引擎.统计.当前时间秒 or 0.0)
        上次值, 上次时刻 = self._上次进度
        if 当前 > 上次值 + 0.01:
            self._上次进度 = (当前, time.monotonic())
            self._卡住 = False
        elif time.monotonic() - 上次时刻 > self.缓冲判定秒:
            self._卡住 = True
        return self._卡住

    def 状态文本(self) -> str:
        """只要状态串时的轻量入口（界面每 16ms 刷一次，不必拼整份快照）。"""
        统计 = self.引擎.统计
        缓冲中 = self._算缓冲中()
        状态 = _状态对照.get(统计.状态, 会话状态.空闲)
        if 状态 == 会话状态.播放中 and 缓冲中:
            状态 = 会话状态.缓冲中
        if 统计.状态 == 内核状态.空闲 and self._准备过 and not self._已起播:
            状态 = 会话状态.已停止
        return 状态

    def 状态快照(self) -> dict:
        """界面与 AI 都读这个（字段名是硬契约，见 docs/整合/02 §4.2）。"""
        统计 = self.引擎.统计
        状态 = self.状态文本()
        缓冲中 = 状态 == 会话状态.缓冲中
        音量 = int(round(float(self.引擎.音量 or 0.0) * 100))
        return {
            "状态": 状态,
            "状态码": _状态序(状态),
            "进度秒": float(统计.当前时间秒 or 0.0),
            "时长秒": float(统计.总时长秒 or 0.0),
            "比例": (float(统计.当前时间秒 or 0.0) / float(统计.总时长秒)
                   if 统计.总时长秒 else 0.0),
            "缓冲中": bool(缓冲中),
            "有画面": bool(self.播放器.有画面()),
            "丢帧": int(统计.已丢视频帧 or 0),
            "丢帧增量": int(getattr(self, "_上次丢帧", 0) and 0 or 0),
            "输入码率bps": float(统计.码率bps or 0.0),
            "解复码率bps": float(统计.码率bps or 0.0),
            "读字节": int(统计.读入字节 or 0),
            "已解码视频": int(统计.已解视频帧 or 0),
            "已显示帧": int(统计.已解视频帧 or 0),
            "帧率": float(统计.帧率 or 0.0),
            "音量": 音量,
            "倍速": float(self.引擎.倍速 or 1.0),
            "字幕": self._字幕序号,
            "字幕轨": self.播放器.字幕轨(),
            "音频轨": self.播放器.音频轨(),
            "已播秒": float(统计.已播音频秒 or 0.0),
            "参数": self.设置.to_dict(),
        }

    def 取丢帧增量(self) -> int:
        """两次调用之间的新增丢帧数（自动诊断用它判断"是不是真的在掉帧"）。"""
        现在 = int(self.引擎.统计.已丢视频帧 or 0)
        上次 = int(getattr(self, "_上次丢帧", 0))
        self._上次丢帧 = 现在
        return max(0, 现在 - 上次)

    # ------------------------------------------------------------------ AI 顾问

    def 流畅优先(self) -> 播放设置:
        """一键"先让我看得下去"：缓存拉大、允许丢帧、必要时软解。"""
        设置 = 播放设置(
            网络缓存毫秒=max(12000, int(self.设置.网络缓存毫秒 or 0)),
            起播等待秒=max(2.0, float(self.设置.起播等待秒 or 0.0)),
            硬解=self.设置.硬解,
            可流畅播放=True,
            理由="流畅优先：缓存拉大、允许丢帧，先保证不断流",
            风险="画质/清晰度可能略降（解码让位于流畅）",
            来源="流畅优先",
            允许丢帧=True,
        )
        return 设置

    def 规则诊断(self) -> Optional[dict]:
        """不走 AI 的规则诊断（AI 不在时也有话说）。"""
        统计 = self.引擎.统计
        快照 = self.状态快照()
        采样 = {
            "状态": 快照["状态"],
            "缓冲中": bool(快照["缓冲中"]),
            "丢帧": self.取丢帧增量(),
            "缓冲等待次数": 1 if 快照["缓冲中"] else 0,
            "已播秒": float(统计.当前时间秒 or 0.0),
            "当前缓冲秒": float(self.设置.网络缓存毫秒 or 0) / 1000.0,
            "平均码率bps": float(统计.码率bps or 0.0),
            "硬解生效": not bool(getattr(self.设置, "要软解", False)),
            "CPU占用": 0.0,
        }
        if not (采样["缓冲中"] or 采样["丢帧"] > 0):
            return None
        if 采样["缓冲中"]:
            return {"动作": "缓存加大", "新参数": self.流畅优先().to_dict(),
                    "理由": "正在缓冲，缓存只许加大"}
        return {"动作": "保持", "新参数": None,
                "理由": f"有丢帧（{采样['丢帧']}）但没在缓冲，先观察"}

    def 采样并诊断(self) -> Optional[dict]:
        """采一次样问顾问；有建议就应用。界面/自动调优都调它。"""
        现在 = time.monotonic()
        if 现在 - self._上次诊断时刻 < self.诊断冷却秒:
            return None
        self._上次诊断时刻 = 现在
        快照 = self.状态快照()
        统计 = self.引擎.统计
        丢帧增量 = self.取丢帧增量()
        采样 = {
            "状态": 快照["状态"],
            "缓冲中": bool(快照["缓冲中"]),
            "丢帧": 丢帧增量,
            "缓冲等待次数": 1 if 快照["缓冲中"] else 0,
            "已播秒": float(统计.当前时间秒 or 0.0),
            "当前缓冲秒": float(self.设置.网络缓存毫秒 or 0) / 1000.0,
            "平均码率bps": float(统计.码率bps or 0.0),
            "硬解生效": not bool(getattr(self.设置, "要软解", False)),
            "CPU占用": 0.0,
        }
        诊断 = None
        if self.顾问 is not None:
            try:
                诊断 = self.顾问.诊断卡顿(采样, self.设置.to_dict(), self.媒体.to_dict())
            except Exception as 错:  # noqa: BLE001
                self._记(f"[AI] 诊断卡顿失败（回落规则）：{错}")
        if not 诊断:
            诊断 = self.规则诊断()
        if not 诊断:
            return None
        新参数 = 诊断.get("新参数")
        if isinstance(新参数, dict) and 新参数:
            新参数 = self._套用建议(dict(self.设置.to_dict(), **新参数))
            诊断["新参数"] = 新参数.to_dict()
            self.应用新参数(诊断["新参数"], 自动重载=True)
        return 诊断

    def 应用新参数(self, 新参数: dict, 自动重载: bool = True) -> bool:
        """改起播参数。V2 的"缓存/硬解"也是起播前生效，所以要带着位置重开。"""
        老的 = self.设置.to_dict()
        设置 = self._套用建议(dict(老的, **(新参数 or {})))
        变了吗 = (
            abs(int(设置.网络缓存毫秒) - int(self.设置.网络缓存毫秒)) >= 1000
            or str(设置.硬解) != str(self.设置.硬解)
        )
        self.设置 = 设置
        self.AI决策状态 = f"{设置.来源}：{设置.理由 or 设置.摘要()}"
        if not 变了吗 or not 自动重载:
            self._记(f"[AI] 参数更新（不重载）：{设置.摘要()}")
            return True
        位置 = float(self.引擎.统计.当前时间秒 or 0.0)
        在播 = self.引擎.状态 in (内核状态.播放中, 内核状态.暂停)
        self._记(f"[AI] 换了起播参数（{设置.摘要()}），从 {位置:.1f}s 重开")
        if self.起播(0):
            if 位置 > 0.5:
                self.跳转(位置)
            if 在播:
                self.引擎.播放()
            return True
        return False

    def 安全回退画面(self, 标题: str = "", 尺寸: str = "") -> bool:
        """画面出问题时退到最保守的一套（强制软解）。V2 里没有"输出模块"可换。"""
        self._记(f"[回退] {标题 or self.标题}：改用软解重开（{尺寸}）")
        self.设置 = self._套用建议(dict(self.设置.to_dict(), **{
            "硬解": "软解", "来源": "安全回退",
            "理由": "硬解可能不兼容，改用软解", "风险": "CPU 占用会升高"}))
        return self.应用新参数(self.设置.to_dict(), 自动重载=True)

    # ------------------------------------------------------------------ 学习闭环

    def 上报效果(self, 卡顿: bool = False) -> None:
        """把这次播放的实际效果记进顾问的学习库（**每次播放都要报**）。"""
        if self.顾问 is None or not self._学习键:
            return
        try:
            指标 = {
                "是否卡顿": bool(卡顿),
                "平均码率bps": float(self.引擎.统计.码率bps or 0.0),
                "丢帧": int(self.引擎.统计.已丢视频帧 or 0),
                "缓冲等待次数": 1 if 卡顿 else 0,
                "已播秒": time.monotonic() - self._采样起,
            }
            self.顾问.记录效果(self._学习键, self.设置.to_dict(), 指标)
        except Exception as 错:  # noqa: BLE001
            self._记(f"[AI] 上报效果失败（不影响播放）：{错}")

    # ------------------------------------------------------------------ 线程 / 收尾

    def 装主线程泵(self) -> None:
        """空实现：V2 引擎线程安全，不需要"非界面线程必须排队"那套。"""
        return None

    def 排空主线程队列(self, 最多: int = 40) -> int:
        return 0

    def 在主线程(self, 函数: Callable, *参数, 等: bool = True,
              超时秒: float = 8.0):
        """直接调用（见 :meth:`装主线程泵`）。保留签名只为兼容调用方。"""
        return 函数(*参数)

    def 关闭(self) -> None:
        """收尾：把这一次的效果学掉，再停内核。"""
        try:
            self.上报效果(卡顿=self._卡住)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.引擎.停止()
        except Exception:  # noqa: BLE001
            pass
        self._已起播 = False


def _分辨率档(分辨率: str) -> str:
    """"1920x1080" → "1080p"。AI 顾问按这个档位分组学习。"""
    try:
        高 = int(str(分辨率).lower().split("x")[1])
    except (IndexError, ValueError):
        return ""
    for 门槛, 名 in ((2000, "4K"), (1400, "1440p"), (1000, "1080p"),
                    (700, "720p"), (500, "576p"), (400, "480p")):
        if 高 >= 门槛:
            return 名
    return f"{高}p"
