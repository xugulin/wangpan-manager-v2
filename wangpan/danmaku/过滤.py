"""弹幕过滤：类型/正则/关键词/发送者/时间窗/长度，规则可导入导出。

为什么过滤要"先算出原因"再决定丢不丢
====================================
界面上必须能回答"我发的这条为什么没显示"。所以核心是
:meth:`过滤器.命中原因`：返回空串 = 不屏蔽，否则返回**人能看懂的原因**
（``屏蔽词：加群``、``时间窗外（12.30s）``）。:meth:`过滤器.应用` 只是把
它套到整池上并统计 —— "为什么"和"丢不丢"共用一套判定，永远不会出现
"界面说被屏蔽词挡了、其实是正则挡的"。

为什么过滤只产出**新池**、不改原池
==================================
弹幕是不可变的（见 :mod:`wangpan.danmaku.模型`）：改过滤条件只要重新
:meth:`~过滤器.应用` 一次，不用重新下载。这也是"同一份弹幕多套规则"能共存的前提。

判定顺序（**顺序有意义**）
==========================
屏蔽词 → 正则 → 发送者 → 只显示含关键词 → 模式 → 彩色 → 时间窗 → 太短 → 太长。
先判"用户明确说要挡的"，再判"用户明确说要留的"：一条既命中屏蔽词又命中关键词的
弹幕，报"屏蔽词"比报"不含关键词"更贴近用户意图。

去重不在 :meth:`命中原因` 里
============================
去重需要"整池"这个上下文（哪一条算重复取决于前面的保留结果），单条弹幕自己
回答不了，所以它只在 :meth:`应用` 里做，统计键是 ``重复``。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Optional

from .模型 import 弹幕, 弹幕池, 弹幕模式, 颜色工具

__all__ = ["过滤规则", "过滤器", "默认规则", "统计键们", "规则版本"]

日志 = logging.getLogger(__name__)

#: 导出格式的版本号（以后改字段时用来兼容旧文件）
规则版本 = 1

#: 统计里"按原因"的键（**固定顺序、固定全集**：界面直接照着画表格，
#: 缺键会让界面时有时无地少一行）
统计键们 = ("屏蔽词", "正则", "发送者", "不含关键词", "屏蔽顶部", "屏蔽底部",
          "屏蔽滚动", "屏蔽彩色", "时间窗", "文本太短", "文本太长", "重复")


@dataclass
class 过滤规则:
    """一套过滤规则（字段都是用户可调的，界面直接绑）。"""

    屏蔽词: list[str] = field(default_factory=list)          # 子串匹配
    正则们: list[str] = field(default_factory=list)          # 正则（大小写不敏感）
    屏蔽发送者: list[str] = field(default_factory=list)
    只显示含关键词: list[str] = field(default_factory=list)   # 非空时"只保留命中这些词的"
    屏蔽顶部: bool = False
    屏蔽底部: bool = False
    屏蔽滚动: bool = False
    屏蔽彩色: bool = False
    最小秒: float = 0.0
    最大秒: float = 0.0            # 0 = 不限
    最短文本: int = 0              # 太短的（比如 "。"）可屏蔽
    最长文本: int = 0              # 0 = 不限
    去重: bool = True

    def 有实质规则(self) -> bool:
        """除了去重以外还有没有别的规则（界面上用来提示"当前有过滤"）。"""
        return bool(self.屏蔽词 or self.正则们 or self.屏蔽发送者 or self.只显示含关键词
                    or self.屏蔽顶部 or self.屏蔽底部 or self.屏蔽滚动 or self.屏蔽彩色
                    or self.最小秒 or self.最大秒 or self.最短文本 or self.最长文本)

    def 复制(self, **改动) -> "过滤规则":
        return replace(self, **改动)


def 默认规则() -> 过滤规则:
    """默认规则：**只去重，什么都不屏蔽**。

    为什么默认不塞"加群/关注"之类的常见屏蔽词：默认规则是"用户没表态"时的行为，
    悄悄替他挡掉东西（还是内容审查性质的）是不可接受的；要挡就让用户自己加。
    """
    return 过滤规则(去重=True)


class 过滤器:
    """把一套 :class:`过滤规则` 套到弹幕池上。

    正则**编译失败不会让过滤崩掉**：坏模式记在 :attr:`坏正则` 里、这条正则被跳过，
    其余规则照常生效（用户手写正则写错一个括号是常事，不该整个弹幕功能失效）。
    """

    def __init__(self, 规则: Optional[过滤规则] = None) -> None:
        self.规则 = 规则 if 规则 is not None else 默认规则()
        #: 编译不了的模式（原文），供界面提示
        self.坏正则: list[str] = []
        self._编译好的: list[tuple[str, "re.Pattern"]] = []
        self._编译时的源: Optional[tuple] = None
        self._确保正则()

    # ---- 正则的懒编译 ----

    def _确保正则(self) -> None:
        """规则是可以被外部改的（界面绑着同一个对象），所以编译结果要跟着源变。"""
        源 = tuple(self.规则.正则们 or ())
        if self._编译时的源 == 源:
            return
        self._编译时的源 = 源
        self.坏正则 = []
        self._编译好的 = []
        for 一条 in 源:
            文本 = str(一条 or "")
            if not 文本:
                continue
            try:
                # 大小写不敏感：屏蔽词/正则都不该因为大小写漏网
                self._编译好的.append((文本, re.compile(文本, re.I)))
            except re.error as 错:
                self.坏正则.append(文本)
                日志.warning("过滤规则里的正则用不了，已跳过：%r（%s）", 文本, 错)

    # ---- 单条判定 ----

    def 命中原因(self, 条: 弹幕) -> str:
        """空串 = 不屏蔽；否则是**人能看懂**的原因（界面拿它解释"为什么没显示"）。"""
        return self._判定(条)[1]

    def _判定(self, 条: 弹幕) -> tuple[str, str]:
        """返回 ``(统计键, 原因)``；不屏蔽时键为空串。"""
        self._确保正则()
        规则 = self.规则
        文本 = str(条.文本 or "")

        for 词 in 规则.屏蔽词 or ():
            词 = str(词 or "")
            if 词 and 词 in 文本:
                return "屏蔽词", f"屏蔽词：{词}"
        for 原文, 编好 in self._编译好的:
            if 编好.search(文本):
                return "正则", f"正则：{原文}"
        for 人 in 规则.屏蔽发送者 or ():
            人 = str(人 or "")
            if 人 and 人 == 条.发送者:
                return "发送者", f"发送者：{人}"
        关键词们 = [str(词) for 词 in (规则.只显示含关键词 or ()) if str(词)]
        if 关键词们 and not any(词 in 文本 for 词 in 关键词们):
            return "不含关键词", "不含关键词：" + "／".join(关键词们)

        if 规则.屏蔽滚动 and 条.模式 is 弹幕模式.滚动:
            return "屏蔽滚动", "屏蔽滚动弹幕"
        if 规则.屏蔽顶部 and 条.模式 is 弹幕模式.顶部:
            return "屏蔽顶部", "屏蔽顶部弹幕"
        if 规则.屏蔽底部 and 条.模式 is 弹幕模式.底部:
            return "屏蔽底部", "屏蔽底部弹幕"
        if 规则.屏蔽彩色 and 颜色工具.是彩色(条.颜色):
            return "屏蔽彩色", f"屏蔽彩色（#{条.颜色:06X}）"

        秒 = 条.秒
        if 规则.最小秒 and 秒 < float(规则.最小秒):
            return "时间窗", f"时间窗外（{秒:.2f}s < {float(规则.最小秒):.2f}s）"
        if 规则.最大秒 and 秒 > float(规则.最大秒):
            return "时间窗", f"时间窗外（{秒:.2f}s > {float(规则.最大秒):.2f}s）"

        长度 = len(文本)
        if 规则.最短文本 and 长度 < int(规则.最短文本):
            return "文本太短", f"文本太短（{长度} < {int(规则.最短文本)}）"
        if 规则.最长文本 and 长度 > int(规则.最长文本):
            return "文本太长", f"文本太长（{长度} > {int(规则.最长文本)}）"
        return "", ""

    # ---- 整池应用 ----

    def 应用(self, 池: 弹幕池) -> tuple[弹幕池, dict]:
        """返回 ``(过滤后的新池, 统计)``；统计形如
        ``{"进入":N,"留下":M,"按原因":{…},"坏正则":[…]}}``（原因键固定全集，见
        :data:`统计键们`）。
        """
        进入 = list(池 or [])
        计数 = {键: 0 for 键 in 统计键们}
        留下: list[弹幕] = []
        for 条 in 进入:
            键, _原因 = self._判定(条)
            if 键:
                计数[键] += 1
                continue
            留下.append(条)
        新池 = 弹幕池(留下, (池.来源说明 if 池 is not None else ""))
        if self.规则.去重:
            计数["重复"] = 新池.去重()
        统计 = {"进入": len(进入), "留下": len(新池), "按原因": 计数,
               "坏正则": list(self.坏正则)}
        return 新池, 统计

    # ---- 存取 ----

    def 导出(self) -> str:
        """导出成 JSON（可分享/备份）。**规则是数据，不是代码**，所以不导出编译结果。"""
        return json.dumps({"版本": 规则版本, "规则": asdict(self.规则)},
                         ensure_ascii=False, indent=2)

    @staticmethod
    def 导入(文本: str) -> "过滤器":
        """从 JSON 导入；**坏 JSON / 坏字段一律吞掉并用默认值**（不能让一份坏配置
        导致弹幕功能起不来），坏在哪记在日志里。
        """
        try:
            数据 = json.loads(str(文本 or ""))
        except ValueError as 错:
            日志.warning("过滤规则不是合法 JSON，用默认规则：%s", 错)
            return 过滤器()
        规则们: object = 数据
        if isinstance(数据, dict):
            内层 = 数据.get("规则")
            规则们 = 内层 if isinstance(内层, dict) else 数据
        if not isinstance(规则们, dict):
            日志.warning("过滤规则的结构不认识（顶层应该是对象），用默认规则")
            return 过滤器()
        字段们: dict = {}
        默认 = 过滤规则()
        for 名 in 默认.__dataclass_fields__:
            原值 = getattr(默认, 名)
            if 名 not in 规则们:
                continue
            新值 = 规则们[名]
            if isinstance(原值, bool):
                字段们[名] = _取布尔(新值, 原值)
            elif isinstance(原值, list):
                字段们[名] = _取文本表(新值, 默认=原值, 名=名)
            elif isinstance(原值, float):
                数 = _取数(新值)
                字段们[名] = 原值 if 数 is None else float(数)
            else:
                数 = _取数(新值)
                字段们[名] = 原值 if 数 is None else int(数)
        return 过滤器(过滤规则(**字段们))


# ---------------------------------------------------------------------------
# 导入时的小工具（坏值 → 用默认值，绝不抛）
# ---------------------------------------------------------------------------

def _取布尔(值, 默认: bool) -> bool:
    if isinstance(值, bool):
        return 值
    if isinstance(值, (int, float)):
        return bool(值)
    文本 = str(值 or "").strip().lower()
    if 文本 in ("true", "1", "yes", "on", "是"):
        return True
    if 文本 in ("false", "0", "no", "off", "否", ""):
        return False
    return 默认


def _取数(值) -> Optional[float]:
    if isinstance(值, bool):
        return None
    if isinstance(值, (int, float)):
        return float(值)
    try:
        return float(str(值 or "").strip())
    except ValueError:
        return None


def _取文本表(值, 默认: list, 名: str = "") -> list:
    if not isinstance(值, list):
        日志.warning("过滤规则的 %s 应该是数组，用默认值", 名 or "字段")
        return list(默认)
    return [str(一个) for 一个 in 值 if str(一个 or "").strip()]
