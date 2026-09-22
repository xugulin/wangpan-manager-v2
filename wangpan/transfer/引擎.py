"""传输引擎：把任务丢进线程池跑，支持并发上限、暂停/取消、失败重试。

这一层只做"调度 + 状态维护"，真正的字节搬运在适配器里（``适配器.下载/上传``）——
所以本地、HTTP、以及将来的真实网盘**共用同一套调度与进度**。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from ..pan.接口 import 注册表, 不支持
from .任务 import 状态, 任务

__all__ = ["传输引擎"]

#: 默认并发任务数（不是分段数：分段由适配器决定）
默认并发 = 2


class 传输引擎:
    def __init__(self, 注册表对象: 注册表, 并发: int = 默认并发,
                 日志回调: Optional[Callable[[str], None]] = None,
                 变更回调: Optional[Callable[[任务], None]] = None) -> None:
        self.注册表 = 注册表对象
        self.并发 = max(1, int(并发))
        self._日志 = 日志回调 or (lambda _t: None)
        self._变更 = 变更回调 or (lambda _t: None)
        self._任务们: list[任务] = []
        self._运行中: dict[str, threading.Thread] = {}
        self._锁 = threading.RLock()
        self._停 = threading.Event()
        self._调度线程: Optional[threading.Thread] = None

    # ---------------- 提交 ----------------

    @staticmethod
    def _标识(来源: str, 来源路径: str, 落点) -> str:
        return f"{来源}｜{来源路径}→{落点}"

    def 加下载(self, 来源: str, 来源路径: str, 落点,
             名字: str = "", 总字节: int = 0) -> 任务:
        任务对象 = 任务(标识=self._标识(来源, 来源路径, 落点),
                     名字=名字 or Path(str(来源路径)).name, 来源=来源,
                     来源路径=来源路径, 落点=Path(落点), 方向="下载",
                     总字节=int(总字节 or 0))
        return self._加(任务对象)

    def 加上传(self, 来源: str, 本地路径, 远端路径: str, 名字: str = "") -> 任务:
        任务对象 = 任务(标识=self._标识(来源, str(本地路径), 远端路径),
                     名字=名字 or Path(str(本地路径)).name, 来源=来源,
                     来源路径=str(本地路径), 落点=Path(远端路径), 方向="上传",
                     总字节=0)
        return self._加(任务对象)

    def _加(self, 任务对象: 任务) -> 任务:
        """加入队列；**已在跑的同名任务直接返回它**（重复点"下载"不该排两遍）。"""
        with self._锁:
            for 已有 in self._任务们:
                if 已有.标识 == 任务对象.标识 and 已有.状态 not in 状态.终态们:
                    self._日志(f"[传输] 已在列表里：{已有.摘要()}")
                    return 已有
            self._任务们.append(任务对象)
        self._启动调度()
        self._变更(任务对象)
        return 任务对象

    # ---------------- 调度 ----------------

    def _启动调度(self) -> None:
        with self._锁:
            if self._调度线程 is not None and self._调度线程.is_alive():
                return
            self._停.clear()
            self._调度线程 = threading.Thread(target=self._调度, name="V2-传输调度",
                                          daemon=True)
            self._调度线程.start()

    def _调度(self) -> None:
        while not self._停.is_set():
            空闲 = self.并发 - len(self._运行中)
            if 空闲 > 0:
                for 任务对象 in self.待跑():
                    if 空闲 <= 0:
                        break
                    self._开跑(任务对象)
                    空闲 -= 1
            if not self._运行中 and not self.待跑():
                return                      # 没活了就退出调度线程
            time.sleep(0.1)

    def 待跑(self) -> list[任务]:
        with self._锁:
            return [t for t in self._任务们
                    if t.状态 == 状态.等待 and t.标识 not in self._运行中]

    def _开跑(self, 任务对象: 任务) -> None:
        线程 = threading.Thread(target=self._跑一个, args=(任务对象,),
                             name=f"V2-传输-{任务对象.名字[:12]}", daemon=True)
        with self._锁:
            self._运行中[任务对象.标识] = 线程
        线程.start()

    def _跑一个(self, 任务对象: 任务) -> None:
        适配器对象 = self.注册表.取(任务对象.来源)
        任务对象.状态 = 状态.传输中
        任务对象.开始时刻 = time.time()
        self._变更(任务对象)
        try:
            if 适配器对象 is None:
                raise 不支持(f"没有这个网盘：{任务对象.来源}")
            进度 = lambda 已传, 总数=0: (任务对象.记进度(已传, 总数),
                                    self._变更(任务对象))
            if 任务对象.方向 == "下载":
                适配器对象.下载(任务对象.来源路径, 任务对象.落点, 进度,
                            取消=lambda: 任务对象.已取消 or 任务对象.已暂停)
                if 任务对象.已暂停:
                    # 暂停 = 停下等待（部分文件留在磁盘上，恢复时续传）
                    while 任务对象.已暂停 and not 任务对象.已取消:
                        time.sleep(0.1)
                    if 任务对象.已取消:
                        raise 不支持("已取消")
                    适配器对象.下载(任务对象.来源路径, 任务对象.落点, 进度,
                                取消=lambda: 任务对象.已取消)
            else:
                适配器对象.上传(任务对象.来源路径, str(任务对象.落点), 进度,
                            取消=lambda: 任务对象.已取消)
            任务对象.结束(True)
            self._日志(f"[传输] ✓ {任务对象.摘要()}")
        except Exception as 错:  # noqa: BLE001
            if 任务对象.已取消:
                任务对象.结束(False, "已取消")
                self._日志(f"[传输] ⊘ {任务对象.名字} 已取消")
            else:
                任务对象.结束(False, f"{type(错).__name__}: {错}")
                self._日志(f"[传输] ✗ {任务对象.摘要()}")
        finally:
            with self._锁:
                self._运行中.pop(任务对象.标识, None)
            self._变更(任务对象)

    # ---------------- 查询 / 控制 ----------------

    def 任务们(self) -> list[任务]:
        with self._锁:
            return list(self._任务们)

    def 找(self, 标识: str) -> Optional[任务]:
        with self._锁:
            return next((t for t in self._任务们 if t.标识 == 标识), None)

    def 统计(self) -> dict:
        任务们 = self.任务们()
        return {
            "总数": len(任务们),
            "传输中": sum(1 for t in 任务们 if t.状态 == 状态.传输中),
            "等待": sum(1 for t in 任务们 if t.状态 == 状态.等待),
            "完成": sum(1 for t in 任务们 if t.状态 == 状态.完成),
            "失败": sum(1 for t in 任务们 if t.状态 == 状态.失败),
            "已取消": sum(1 for t in 任务们 if t.状态 == 状态.已取消),
            "速率bps": sum(t.速率bps() for t in 任务们 if t.状态 == 状态.传输中),
        }

    def 全部暂停(self, 暂停: bool = True) -> None:
        for 任务对象 in self.任务们():
            if 任务对象.状态 in (状态.传输中, 状态.暂停):
                任务对象.请求暂停(暂停)
                self._变更(任务对象)

    def 取消(self, 任务对象: 任务) -> None:
        任务对象.请求取消()
        self._变更(任务对象)

    def 清已完成(self) -> int:
        with self._锁:
            之前 = len(self._任务们)
            self._任务们 = [t for t in self._任务们 if t.状态 not in 状态.终态们]
            return 之前 - len(self._任务们)

    def 等待全部(self, 超时秒: float = 120.0) -> bool:
        """等所有任务结束（测试与"退出前收尾"用）。"""
        截止 = time.time() + 超时秒
        while time.time() < 截止:
            if all(t.状态 in 状态.终态们 for t in self.任务们()):
                return True
            time.sleep(0.05)
        return False

    def 关闭(self, 超时秒: float = 5.0) -> None:
        self._停.set()
        for 任务对象 in self.任务们():
            if 任务对象.状态 not in 状态.终态们:
                任务对象.请求取消()
        截止 = time.time() + 超时秒
        while time.time() < 截止 and self._运行中:
            time.sleep(0.05)
