"""传输任务：一条"从哪到哪"的记录 + 进度 + 可暂停/可取消。

设计要点
========
* **进度只由字节数算**（不猜时间）：`已传字节 / 总字节`，速率用滑动窗口算；
* **落盘即续传**：写到 ``落点.部分``，完成才 rename 成正式文件 —— 中途退出/断电
  下次接着传，也不会留下"看起来完整其实是半个"的文件；
* 状态机很小：等待 → 传输中 → （暂停 ⇄ 传输中）→ 完成 / 失败 / 已取消。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__all__ = ["状态", "任务"]


class 状态:
    等待 = "等待"
    传输中 = "传输中"
    暂停 = "暂停"
    完成 = "完成"
    失败 = "失败"
    已取消 = "已取消"

    #: 终态（不会再变）
    终态们 = (完成, 失败, 已取消)


@dataclass
class 任务:
    """一个下载/上传任务。"""

    标识: str
    名字: str
    来源: str                      # 网盘标识（注册表里的键）
    来源路径: str
    落点: Path                     # 本地路径（下载）/ 远端路径（上传）
    方向: str = "下载"             # "下载" 或 "上传"
    总字节: int = 0
    已传字节: int = 0
    状态: str = 状态.等待
    错误: str = ""
    开始时刻: float = 0.0
    结束时刻: float = 0.0
    分段: int = 4
    重试: int = 0
    _速率样本: list = field(default_factory=list)
    _锁: threading.RLock = field(default_factory=threading.RLock)
    取消事件: threading.Event = field(default_factory=threading.Event)
    暂停事件: threading.Event = field(default_factory=threading.Event)
    完成事件: threading.Event = field(default_factory=threading.Event)

    # ---------------- 进度 ----------------

    @property
    def 进度(self) -> float:
        if self.总字节 <= 0:
            return 0.0
        return max(0.0, min(1.0, self.已传字节 / self.总字节))

    def 记进度(self, 已传: int, 总数: int = 0) -> None:
        with self._锁:
            self.已传字节 = max(self.已传字节, int(已传 or 0))
            if 总数:
                self.总字节 = int(总数)
            现在 = time.monotonic()
            self._速率样本.append((现在, self.已传字节))
            # 只留最近 5 秒的样本（滑动窗口算速率，不受开头/结尾影响）
            while len(self._速率样本) > 2 and 现在 - self._速率样本[0][0] > 5.0:
                self._速率样本.pop(0)

    def 速率bps(self) -> float:
        with self._锁:
            if len(self._速率样本) < 2:
                return 0.0
            起时刻, 起字节 = self._速率样本[0]
            终时刻, 终字节 = self._速率样本[-1]
            间隔 = max(0.001, 终时刻 - 起时刻)
            return max(0.0, (终字节 - 起字节) / 间隔)

    @property
    def 剩余秒(self) -> float:
        速度 = self.速率bps()
        if 速度 <= 1 or self.总字节 <= 0:
            return 0.0
        return max(0.0, (self.总字节 - self.已传字节) / 速度)

    def 摘要(self) -> str:
        尾巴 = ""
        if self.状态 in (状态.失败, 状态.已取消):
            尾巴 = f"（{self.错误 or self.状态}）"
        elif self.状态 == 状态.传输中:
            尾巴 = (f"｜{self.速率bps() / 1048576:.1f} MB/s"
                  + (f"｜剩 {int(self.剩余秒)}s" if self.剩余秒 else ""))
        return (f"{self.方向} {self.名字}：{self.状态} "
                f"{self.进度 * 100:.0f}%{尾巴}")

    # ---------------- 控制 ----------------

    def 请求暂停(self, 暂停: bool = True) -> None:
        if 暂停:
            self.暂停事件.set()
            if self.状态 == 状态.传输中:
                self.状态 = 状态.暂停
        else:
            self.暂停事件.clear()
            if self.状态 == 状态.暂停:
                self.状态 = 状态.传输中

    def 请求取消(self) -> None:
        self.取消事件.set()
        self.暂停事件.clear()
        if self.状态 not in 状态.终态们:
            self.状态 = 状态.已取消
            self.错误 = self.错误 or "用户取消"
            self.完成事件.set()

    @property
    def 已取消(self) -> bool:
        return self.取消事件.is_set()

    @property
    def 已暂停(self) -> bool:
        return self.暂停事件.is_set()

    def 结束(self, 成功: bool, 错误: str = "") -> None:
        with self._锁:
            self.结束时刻 = time.time()
            self.错误 = 错误 or self.错误
            if not 成功:
                self.状态 = 状态.失败 if not self.已取消 else 状态.已取消
            elif self.状态 not in 状态.终态们:
                self.状态 = 状态.完成
                if self.总字节:
                    self.已传字节 = self.总字节
        self.完成事件.set()
