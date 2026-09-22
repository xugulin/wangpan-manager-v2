"""控制台编码：让中文能打到 Windows 控制台里（否则整个程序会因为一句 print 崩掉）。

为什么需要（真机踩到，GitHub Actions 的 Windows runner 上）::

    UnicodeEncodeError: 'charmap' codec can't encode characters in position 0-1

Windows 控制台默认是 cp936/cp1252，Python 的 stdout 按那个编码输出，**中文一 print 就抛异常**。
我们的日志、状态栏文案全是中文，所以这一步是必需的，不是"锦上添花"。

做法：把 stdout/stderr 重新配置成 UTF-8（``errors="replace"`` 兜底，绝不因为编码问题崩），
并把 ``PYTHONIOENCODING``/``PYTHONUTF8`` 写进环境变量，让**子进程**也一致。
"""

from __future__ import annotations

import os
import sys

__all__ = ["修控制台", "已修"]

已修 = False


def 修控制台() -> bool:
    """把当前进程（以及将来的子进程）的输出编码统一成 UTF-8。"""
    global 已修
    if 已修:
        return True
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    for 流名 in ("stdout", "stderr"):
        流 = getattr(sys, 流名, None)
        if 流 is None:
            continue
        try:
            流.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - 有些环境（被重定向/被嵌入）改不了
            try:
                流.reconfigure(errors="replace")                # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
    已修 = True
    return True


def 装自检钩子() -> None:
    """给"直接跑脚本"的场景用：导入时就修好（工具脚本比库更需要它）。"""
    修控制台()
