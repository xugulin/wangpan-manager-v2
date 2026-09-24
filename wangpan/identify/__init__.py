"""媒体识别包（P2 阶段：③ 候选生成 + ④ 检索）。

**这个文件在 import 期什么也不做** —— 一行顶层 import 都没有。
为什么：``工具/识别评测.py --实现 新`` 是 ``importlib.import_module("wangpan.identify.管线")``，
Python 执行子模块前**必先执行包的 ``__init__.py``**；只要这里有一个会失败的顶层 import
（哪怕是"某个模块里有 IO"），P1 的评测就会连带炸掉、报成"管线不可用"，故障点还极难定位。
用 :pep:`562` 的模块级 ``__getattr__`` 做惰性转出：``from wangpan.identify import 候选``
照样能用，但只有真去取它时才 import。

``归一化`` / ``结构`` / ``管线`` 由 P1 负责，这里**不去 import 它们** ——
两个阶段并行施工，谁也别把谁拖下水。
"""

from __future__ import annotations

import importlib

__all__ = ["候选", "检索"]


def __getattr__(名字: str):
    """惰性转出子模块（``候选`` / ``检索``）；没见过的名字照常抛 ``AttributeError``。"""
    if 名字 in __all__:
        return importlib.import_module(f".{名字}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {名字!r}")


def __dir__() -> list:
    return sorted(list(globals().keys()) + list(__all__))
