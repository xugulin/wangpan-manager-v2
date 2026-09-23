"""整合后的架构约束（**替换掉原来那份"V2 与 V1 完全隔离"的判据**）。

背景
====
V2 早期有一条硬路线："与 V1 完全独立，禁止出现 v8_3 的路径/包名"。
现在用户明确要求**整合**：以 V2 仓库为主体，套用 V1 的 GUI / 网盘管理 / AI，
播放页与播放器用 V2 的自研内核。所以"整仓不许提 V1"这条判据已经不成立。

但**内核独立**这条不能丢 —— 它是"自己用 libav* 写播放器"这句话的技术底线。
于是判据改成下面四条：

1. **内核不许依赖界面层**：`wangpan/player|ffmpeg|subtitle|danmaku|scrape|pan|transfer`
   不 import `v8_3`（内核永远能脱离 GUI 单独跑、单独测）；
2. **纯内核壳不许依赖界面层**：`wangpan/ui/主窗口.py` 不 import `v8_3`
   （`启动.py --纯播放` 那条回归路径必须真的独立）；
3. **播放不许调外部播放器命令行**：内核与播放页/会话里不许出现
   `ffmpeg` / `ffprobe` / `vlc` 的 subprocess 调用。
   （`v8_3/播放/媒体信息.py` 用 ffprobe 是**读元数据**给 AI 顾问用，不属于播放路径，
   它也不在扫描范围里 —— 这条区分很重要，别把两件事混起来。）
4. **内核只许用标准库 + PySide6**：`wangpan/` 里不出现第三方依赖。
   整合进来的第三方（httpx 等）只允许出现在 `v8_3/`（V1 那一层）。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from tests.公用 import 项目根

#: V2 内核包（必须保持"不依赖界面层、不引第三方"）
内核目录 = ("wangpan/player", "wangpan/ffmpeg", "wangpan/subtitle",
          "wangpan/danmaku", "wangpan/scrape", "wangpan/pan", "wangpan/transfer")

#: 播放路径上"不许调外部命令行"的目录/文件
播放路径 = ("wangpan/player", "wangpan/ffmpeg")
播放路径文件 = ("wangpan/ui/播放页.py", "wangpan/ui/播放会话.py")

#: 标准库白名单（内核只许用这些 + PySide6 + 自己）
标准库 = {
    "os", "sys", "time", "ctypes", "queue", "threading", "pathlib", "typing",
    "dataclasses", "unittest", "re", "tempfile", "shutil", "subprocess",
    "argparse", "importlib", "ast", "json", "struct", "math", "collections",
    "functools", "itertools", "logging", "signal", "hashlib", "zipfile",
    "urllib", "socket", "xml", "sqlite3", "concurrent", "base64", "io", "http",
    "socketserver", "datetime", "enum", "abc", "contextlib", "traceback",
    "warnings", "random", "unicodedata", "difflib", "statistics", "glob",
    "shlex", "csv", "ssl", "resource", "binascii", "zlib", "gzip", "tarfile",
    "secrets", "string", "textwrap", "operator", "copy", "weakref", "gc",
    "platform", "locale", "asyncio", "inspect", "types", "unittest.mock",
    "__future__",
}

允许前缀 = ("PySide6", "shiboken6", "wangpan", "tests")


def _源码们(目录们):
    for 目录 in 目录们:
        基 = 项目根 / 目录
        if 基.is_dir():
            yield from sorted(p for p in 基.rglob("*.py") if "__pycache__" not in p.parts)


def _文本(路径: Path) -> str:
    return 路径.read_text(encoding="utf-8", errors="replace")


class 内核独立性测试(unittest.TestCase):
    """原来的 ①：内核不许引用整合进来的界面层。"""

    def test_内核不引用V1(self):
        违规: list[str] = []
        for 路径 in _源码们(内核目录):
            文本 = _文本(路径)
            for 词 in ("from v8_3", "import v8_3", "网盘管理/v8_3"):
                if 词 in 文本:
                    违规.append(f"{路径.relative_to(项目根)}：出现 {词!r}")
        self.assertEqual(违规, [], "V2 内核必须能脱离界面层单独跑：\n" + "\n".join(违规))

    def test_纯内核主窗口不引用V1(self):
        路径 = 项目根 / "wangpan" / "ui" / "主窗口.py"
        self.assertTrue(路径.is_file())
        文本 = _文本(路径)
        for 词 in ("from v8_3", "import v8_3"):
            self.assertNotIn(词, 文本, f"纯内核壳（--纯播放）不许依赖界面层：出现 {词!r}")


class 播放内核测试(unittest.TestCase):
    """原来的 ②：播放不许靠外部播放器命令行。"""

    def _要查的文件们(self):
        yield from _源码们(播放路径)
        for 名字 in 播放路径文件:
            路径 = 项目根 / 名字
            if 路径.is_file():
                yield 路径

    def test_不调用外部播放器命令行(self):
        违规: list[str] = []
        模式 = re.compile(r"""subprocess[^\n]*["'](ffmpeg|ffprobe|vlc|cvlc)["']""")
        for 路径 in self._要查的文件们():
            文本 = _文本(路径)
            for 行号, 行 in enumerate(文本.splitlines(), start=1):
                if 模式.search(行):
                    违规.append(f"{路径.relative_to(项目根)}:{行号}  {行.strip()[:80]}")
        self.assertEqual(违规, [], "播放内核不许调外部播放器命令行：\n" + "\n".join(违规))

    def test_没有VLC播放栈(self):
        """VLC 那一整套（绑定/核心/出口/游离窗口）应当已经删干净。"""
        残留 = [名字 for 名字 in (
            "v8_3/播放/vlc绑定.py", "v8_3/播放/播放核心.py", "v8_3/播放/播放出口.py",
            "v8_3/播放/显示环境.py", "v8_3/播放/游离窗口.py", "v8_3/界面/窗口就绪.py",
            "v8_3/界面/游离窗口守护.py", "v8_3/界面/播放控件.py")
            if (项目根 / 名字).is_file()]
        self.assertEqual(残留, [], "VLC 播放栈必须删掉（播放器已换成 V2 内核）：\n"
                                 + "\n".join(残留))


class 环境测试(unittest.TestCase):
    """原来的 ③：自带解释器 + venv 在项目里。"""

    def test_自带解释器是项目自己的(self):
        venv = 项目根 / "运行环境" / "venv"
        self.assertTrue((venv / "pyvenv.cfg").is_file(), "应该有自己的一份 venv")
        站点 = list((venv / "lib").glob("python3*/site-packages")) + \
            list((venv / "Lib").glob("site-packages"))
        self.assertTrue(站点, "venv 里没有 site-packages")
        self.assertTrue(str(站点[0].resolve()).startswith(str(项目根.resolve())),
                        f"site-packages 不在项目里：{站点[0]}")
        self.assertTrue((站点[0] / "PySide6").is_dir(), "PySide6 要装在项目自带的 venv 里")


class 内核依赖测试(unittest.TestCase):
    """原来的 ④：内核只许标准库 + PySide6（整合进来的第三方只在 v8_3 层）。"""

    def test_内核只有标准库与PySide6(self):
        违规: list[str] = []
        for 路径 in _源码们(("wangpan",)):
            树 = ast.parse(_文本(路径))
            for 节点 in ast.walk(树):
                if isinstance(节点, ast.Import):
                    for 别名 in 节点.names:
                        根 = 别名.name.split(".")[0]
                        if 根 not in 允许前缀 and 根 not in 标准库:
                            违规.append(f"{路径.relative_to(项目根)}: import {根}")
                elif isinstance(节点, ast.ImportFrom) and 节点.module \
                        and not getattr(节点, "level", 0):
                    根 = 节点.module.split(".")[0]
                    if 根 not in 允许前缀 and 根 not in 标准库:
                        违规.append(f"{路径.relative_to(项目根)}: from {节点.module}")
        self.assertEqual(sorted(set(违规)), [],
                         "V2 内核不许引第三方依赖（第三方只允许出现在 v8_3/ 那一层）")


if __name__ == "__main__":
    unittest.main()
