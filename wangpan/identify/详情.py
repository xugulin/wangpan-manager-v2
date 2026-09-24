"""候选详情（⑤ 打分的"季集范围 + 名字"取数器）：P5 从评测脚本搬进来的**共用件**。

为什么要搬进 ``wangpan/``（而不是留在 ``工具/识别打分评测.py``）
============================================================
P3 阶段这个类是写在评测脚本里的，因为"取多少条候选的详情"被当成**评测的成本参数**。
P5 要把它接进服务层时出现了两种选择：

* 把脚本里那份**抄一遍**到服务层 —— 于是"生产跑的链"和"评测量的链"变成两份代码，
  以后给其中一份改了权重/取了更多详情，另一份**不会跟着变**，指标就变成了"另一个程序的指标"；
* 搬进包里、两边共用（就是这里）—— 评测脚本与 ``服务.py`` 引用同一个类，
  **报告里的数字就是生产链的数字**。

依赖方向仍然是 ``wangpan`` 内部（不 import ``工具/``，也不 import ``v8_3``）。

这个类凭什么可以在包里存活
==========================
它只依赖客户端的两件事：``取剧集(id)``（公开）与取名字（``取别名们`` 公开方法，
没有就退回带缓存的私有 ``_取一份``；两个都没有就当"拿不到名字"，**软失败**）。
所有异常都被吞掉转成"没有这条信息"，因为"拿不到别名/季集"只该让分数偏低，
绝不能让识别失败 —— 这是 ⑤ 打分"拿不到信息不罚分"那条原则在取数侧的对应物。
"""

from __future__ import annotations

from typing import Mapping

from .打分 import 季集范围

__all__ = ["候选详情"]


class 候选详情:
    """按需取候选的**季集范围**与**别名**，进程内缓存 + TMDB 客户端的 7 天磁盘缓存。

    真机是"一部剧几十集、每集都识别一次"，同一个 id 会被反复问到；不缓存就等于
    把同一份详情请求几十遍。缓存的是"范围 + 别名"两样小东西，不是整个媒体条目。
    """

    def __init__(self, 客户端, 上限: int = 3, 取别名: bool = True) -> None:
        self.客户端 = 客户端
        self.上限 = max(0, int(上限))
        self.取别名开关 = bool(取别名)
        self._表: dict[str, 季集范围 | None] = {}
        self._别名表: dict[str, list[str]] = {}
        self.请求数 = 0
        self.名字请求数 = 0
        self.失败们: list[tuple[str, str]] = []

    def _取一份(self, 路径: str, 类型: str, 键: str) -> dict:
        """带缓存的取数：优先用客户端的公开 ``取别名们``，退回它带缓存的私有 ``_取一份``。

        为什么留着私有那条路：单测里的假客户端大多只实现"搜索 + 取详情"，
        给它们补一个别名接口是白搭；而真客户端两条都有。拿不到就当空（软失败）。
        """
        try:
            self.名字请求数 += 1
            return self.客户端._取一份(路径, {}, 类型, 键) or {}
        except Exception as 异常:                                       # noqa: BLE001
            self.失败们.append((f"{键}", f"{type(异常).__name__}: {异常}"))
            return {}

    def 取名字们(self, 标识: str, 类型: str) -> list[str]:
        """TMDB 记着的这条候选的所有名字（别名 + 各语言译名）。

        两个接口的形状不一样，这是官方事实（实测）：
        * ``alternative_titles``：剧集放 ``results``、电影放 ``titles``，字段是 ``title``；
        * ``translations``：``translations[].data.name``（剧集）/ ``.title``（电影）。

        有公开的 ``取别名们`` 就用它（P5 把它补进了 ``TMDB客户端``，见那里的说明）；
        没有（假客户端）就走私有 ``_取一份``，两个都拿不到就是空列表。
        """
        if not self.取别名开关:
            return []
        键 = f"{类型}:{标识}"
        if 键 in self._别名表:
            return self._别名表[键]
        公开 = getattr(self.客户端, "取别名们", None)
        if callable(公开):
            try:
                self.名字请求数 += 1
                出 = [str(x).strip() for x in (公开(类型, 标识) or []) if str(x).strip()]
            except Exception as 异常:                                   # noqa: BLE001
                self.失败们.append((f"{标识}-别名", f"{type(异常).__name__}: {异常}"))
                出 = []
            self._别名表[键] = list(dict.fromkeys(出))
            return self._别名表[键]
        出 = []
        别名数据 = self._取一份(f"/{类型}/{标识}/alternative_titles", 类型, f"{标识}-alt")
        for 桶 in ("results", "titles"):
            for 条 in 别名数据.get(桶) or []:
                if isinstance(条, Mapping):
                    文本 = str(条.get("title") or 条.get("name") or "").strip()
                    if 文本:
                        出.append(文本)
        译名数据 = self._取一份(f"/{类型}/{标识}/translations", 类型, f"{标识}-trans")
        for 条 in 译名数据.get("translations") or []:
            数据 = 条.get("data") if isinstance(条, Mapping) else None
            if isinstance(数据, Mapping):
                文本 = str(数据.get("name") or 数据.get("title") or "").strip()
                if 文本:
                    出.append(文本)
        出 = list(dict.fromkeys(出))
        self._别名表[键] = 出
        return 出

    def 取(self, 候选) -> 季集范围 | None:
        标识 = str(getattr(候选, "标识", "") or "")
        类型 = str(getattr(候选, "类型", "") or "")
        if not 标识:
            return None
        # 名字先挂到候选上：⑤ 打分的"标题/原名各算一次"本来就会读 `别名` 字段，
        # 这里不需要它知道这些名字是从哪儿来的。
        别名们 = self.取名字们(标识, 类型)
        if 别名们:
            try:
                候选.别名 = 别名们
            except Exception:                                          # noqa: BLE001
                pass
        键 = f"{类型}:{标识}"
        if 键 in self._表:
            return self._表[键]
        if 类型 != "tv":
            self._表[键] = None            # 电影没有"季集范围"，不必请求
            return None
        try:
            self.请求数 += 1
            条目 = self.客户端.取剧集(标识)
            对 = {}
            for 季对象 in getattr(条目, "季们", []) or []:
                号, 数 = getattr(季对象, "季号", None), getattr(季对象, "集数", 0)
                if 号 is not None and 数:
                    对[int(号)] = int(数)
            范围 = 季集范围(每季集数=对) if 对 else None
        except Exception as 异常:                                       # noqa: BLE001
            范围 = None
            self.失败们.append((键, f"{type(异常).__name__}: {异常}"))
        self._表[键] = 范围
        return 范围

    def 富化表(self, 预分) -> dict:
        """给"够看的头部候选"补季集信息，返回 ``{标识: 季集范围}``。

        取谁：预打分的**前 N 条**，**不看分数高低**。为什么不设分数门槛：
        别名与季集信息本身就能把低分候选顶上来 —— 真机 ``Sousou no Frieren`` 那条
        预打分只有 24 分（标题 0 分），一旦取回别名「葬送的芙莉莲」，分数直接翻三倍。
        设个"预分 ≥ 30 才取详情"的门槛，等于让"只差一份别名"的正确候选永远翻不了身
        （第一版就踩了这个坑：几条正确的候选被判成了**丢弃**）。
        """
        出: dict[str, 季集范围] = {}
        if self.上限 <= 0:
            return 出
        够看 = list(预分.分数们)[:self.上限]
        for 项 in 够看:
            候选 = 项.候选
            if not getattr(候选, "标识", ""):
                continue
            范围 = self.取(候选)
            if 范围 is not None and 范围.每季集数:
                出[str(候选.标识)] = 范围
        return 出
