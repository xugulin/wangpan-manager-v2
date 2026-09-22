"""独立性：V2 **不许**依赖 V1 或任何其它项目（用户明确的硬要求）。

判据是"扫源码"：出现 V1 的路径、V1 的包名、V1 的解释器路径就算违规。
（V2 有自己的 运行环境/venv，这里的检查针对的是 **V1** 的绝对路径。）
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.公用 import 项目根

#: 不许出现的东西（V1 的路径/包名）
禁用 = (
    "网盘管理/v8_3",            # V1 的包
    "/home/xgl/python/网盘管理/",  # V1 的绝对路径（注意 V2 是 网盘管理_V2）
    "from v8_3",
    "import v8_3",
)

#: 自己人的源码（不含 tests 里的检查字符串本身）
要查的目录 = ("wangpan", "tools")
要查的文件 = ("启动.py",)


class 独立性测试(unittest.TestCase):
    def _文件们(self):
        for 目录 in 要查的目录:
            基 = 项目根 / 目录
            if 基.is_dir():
                yield from (p for p in 基.rglob("*.py") if "__pycache__" not in p.parts)
        for 名字 in 要查的文件:
            路径 = 项目根 / 名字
            if 路径.is_file():
                yield 路径

    def test_不引用V1(self):
        违规: list[str] = []
        for 路径 in self._文件们():
            文本 = 路径.read_text(encoding="utf-8", errors="replace")
            for 词 in 禁用:
                if 词 in 文本:
                    违规.append(f"{路径.relative_to(项目根)}：出现 {词!r}")
        self.assertEqual(违规, [], "V2 必须完全独立，不许引用 V1：\n" + "\n".join(违规))

    def test_不调用外部播放器命令行(self):
        """播放内核不许靠 ffmpeg/ffprobe/vlc 命令 —— 那就不叫"自己用 libav* 写播放器"。"""
        违规: list[str] = []
        模式 = re.compile(r"""subprocess[^\n]*["'](ffmpeg|ffprobe|vlc|cvlc)["']""")
        for 路径 in self._文件们():
            if "tools" in 路径.parts:      # 造测试素材允许用系统 ffmpeg
                continue
            文本 = 路径.read_text(encoding="utf-8", errors="replace")
            for 行号, 行 in enumerate(文本.splitlines(), start=1):
                if 模式.search(行):
                    违规.append(f"{路径.relative_to(项目根)}:{行号}")
        self.assertEqual(违规, [], "播放内核不许调外部播放器命令行：\n" + "\n".join(违规))

    def test_自带解释器是V2自己的(self):
        """V2 用自己的 venv：venv 的 site-packages 必须在 _V2 里面。

        （注意别用 ``解释器.resolve()`` 判断 —— venv 里的 python 是指向系统解释器的
        符号链接，resolve 之后当然在外面；要看的是 venv 自己的目录。）
        """
        venv = 项目根 / "运行环境" / "venv"
        self.assertTrue((venv / "pyvenv.cfg").is_file(), "V2 应该有自己的一份 venv")
        # venv 布局两端不同：Linux 是 lib/python3.x/site-packages，Windows 是 Lib/site-packages
        站点 = list((venv / "lib").glob("python3*/site-packages")) + \
            list((venv / "Lib").glob("site-packages"))
        self.assertTrue(站点, "venv 里没有 site-packages")
        self.assertIn("网盘管理_V2", str(站点[0].resolve()))
        self.assertTrue((站点[0] / "PySide6").is_dir(), "PySide6 要装在 V2 自己的 venv 里")

    def test_只有标准库与PySide6(self):
        """第三方依赖只允许 PySide6（libav* 是系统库，用 ctypes 直连）。"""
        允许前缀 = ("PySide6", "shiboken6", "tests", "wangpan", "构建", "工具")
        import ast
        违规: list[str] = []
        for 路径 in self._文件们():
            树 = ast.parse(路径.read_text(encoding="utf-8", errors="replace"))
            for 节点 in ast.walk(树):
                名字 = None
                if isinstance(节点, ast.Import):
                    for 别名 in 节点.names:
                        名字 = 别名.name.split(".")[0]
                        if 名字 not in 允许前缀 and 名字 not in (
                                "os", "sys", "time", "ctypes", "queue", "threading",
                                "pathlib", "typing", "dataclasses", "unittest", "re",
                                "tempfile", "shutil", "subprocess", "argparse",
                                "importlib", "ast", "json", "struct", "math",
                                "collections", "functools", "itertools", "logging",
                                "signal", "hashlib", "zipfile", "urllib", "socket"):
                            违规.append(f"{路径.name}: import {名字}")
                elif isinstance(节点, ast.ImportFrom) and 节点.module \
                        and not getattr(节点, "level", 0):
                    # level>0 = 包内相对导入（from ..player import …），本来就该允许
                    根 = 节点.module.split(".")[0]
                    if 根 not in 允许前缀 and 根 not in (
                            "ctypes", "pathlib", "typing", "dataclasses", "queue",
                            "threading", "collections", "functools", "__future__",
                            # 标准库（网络/时间/编码/解析…）：不进"第三方"名单
                            "urllib", "http", "socketserver", "socket", "resource",
                            "struct", "base64", "hashlib", "json", "io", "os", "sys",
                            "time", "re", "ast", "unittest", "tempfile", "shutil",
                            "subprocess", "argparse", "importlib", "logging",
                            "signal", "zipfile", "math", "random", "datetime",
                            "enum", "abc", "contextlib", "traceback", "warnings"):
                        违规.append(f"{路径.name}: from {节点.module}")
        self.assertEqual(sorted(set(违规)), [], "出现了计划外的第三方依赖")
