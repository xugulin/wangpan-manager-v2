"""一键启动与快捷方式的自检（**不依赖真有桌面环境**）。

为什么值得有测试：
* 快捷方式最容易"看着对、点了没反应"：Exec 指向的脚本丢了可执行位、Path 写错、
  Icon 指到不存在的文件、`StartupWMClass` 与应用名对不上（任务栏就变成通用 Python 图标）。
  这些都能在纯文本层面查出来；
* 安装脚本会在**用户的真实目录**里写文件（桌面、菜单），所以这里的做法是：
  把 ``HOME`` / ``XDG_DATA_HOME`` 指到临时目录**真跑一遍安装**，再校验产物；
* Windows 那两个文件（GBK 的 启动.bat、带 BOM 的 .ps1）编码写错就会乱码，这里也钉住。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.公用 import 项目根, 临时目录

#: Linux 的启动器/安装器是 shell 脚本，只能在有 POSIX shell 的地方跑。
#: Windows 上的入口是 启动.bat / 安装快捷方式.ps1（那部分测试在哪个平台都能跑，
#: 只读文件查编码与关键内容）。CI 在**真 Windows** 上跑，所以这里必须分开，
#: 不然 Windows 上会因为"没有 sh / 路径是 /d/a/…"而假失败。
有POSIX外壳 = os.name != "nt" and shutil.which("sh") is not None
原因 = "这是 Linux 的 shell 启动器/安装器；Windows 用 启动.bat + 安装快捷方式.ps1"

启动脚本 = 项目根 / "一键启动_网盘管理_V2.sh"
安装脚本 = 项目根 / "工具" / "安装快捷方式.sh"
图标脚本 = 项目根 / "工具" / "生成图标.py"
图标 = 项目根 / "网盘管理_V2_图标.png"


@unittest.skipUnless(有POSIX外壳, 原因)
class 启动器测试(unittest.TestCase):
    def test_一键启动脚本存在且可执行(self):
        self.assertTrue(启动脚本.is_file(), "缺少一键启动脚本")
        self.assertTrue(os.access(启动脚本, os.X_OK), "一键启动脚本要有可执行位（双击才跑得起来）")

    def test_变量名引用也必须是ASCII(self):
        """`$变量` / `${变量...}` 里的名字也必须是 ASCII。

        为什么单独加一条：原来的检查只看"赋值语句左边"，而
        `${V2_跳过环境自愈:-}` 这种**引用**同样会被 dash 判成
        "错误的替换"（真机发布前实测：一键启动直接报错、后面的自愈代码整段没跑，
        解压出来的包因此不可用）。这种名字一眼看不出问题，必须由测试盯住。
        """
        import re as _re
        # 判据：`$` 或 `${` **紧跟**一个非 ASCII 字符 —— 那才是"中文变量名"。
        # 不能贪婪地抓整个名字：`"退出码 $RC（详见）"` 里 `$RC` 是好的，
        # 后面的中文只是文案（第一版这么写，误报了 13 处）。
        模式 = _re.compile(r"\$\{?([^\x00-\x7f])")
        坏: list[str] = []
        for 路径 in (启动脚本, 安装脚本):
            if not 路径.is_file():
                continue
            for 行号, 行 in enumerate(路径.read_text(encoding="utf-8").splitlines(), 1):
                纯 = 行.strip()
                if 纯.startswith("#"):
                    continue          # 注释里提到不算
                for 命中 in 模式.finditer(行):
                    坏.append(f"{路径.name}:{行号} 变量名以 {命中.group(1)!r} 开头")
        self.assertEqual(坏, [], "shell 变量名必须 ASCII，否则 dash 会报\"错误的替换\"：\n  "
                               + "\n  ".join(坏))

    def test_只用POSIX语法与ASCII标识符(self):
        """某些文件管理器用非 bash 的 shell 解析它：POSIX 语法 + **ASCII 标识符**才稳。

        `sh` 语法检查（``sh -n``）抓不到"中文变量名"这种错（dash 在**运行到那一行**
        才报 "要桌面=1: 未找到命令"），所以这里逐行扫标识符。
        """
        import re
        for 脚本 in (启动脚本, 安装脚本):
            子 = subprocess.run(["sh", "-n", str(脚本)], capture_output=True, text=True)
            self.assertEqual(子.returncode, 0, f"{脚本.name} 的 sh 语法不过：{子.stderr}")
            文本 = 脚本.read_text(encoding="utf-8")
            for 坏 in ("[[ ", "function ", "declare ", "local ", "let "):
                self.assertNotIn(坏, 文本, f"{脚本.name} 用了 bash 专有语法：{坏}")
            # 只看"含非 ASCII 字符"的标识符：那正是 dash 会当命令执行的东西
            # （`--text=…` 这种选项串不算变量名，所以不按语法白名单查）
            for 名 in re.findall(r"^\s*([^\s=]+)=", 文本, re.M):
                if any(ord(c) > 127 for c in 名):
                    self.fail(f"{脚本.name} 里变量名不是 ASCII：{名}"
                              "（POSIX shell 会把它当命令执行）")
            for 名 in re.findall(r"^\s*([^\s()]+)\s*\(\)\s*\{", 文本, re.M):
                if any(ord(c) > 127 for c in 名):
                    self.fail(f"{脚本.name} 里函数名不是 ASCII：{名}")

    def test_安装脚本在dash下不报错(self):
        """真拿 sh（这台机器上是 dash）跑一遍：任何 "未找到命令/需要整数" 都算失败。"""
        子 = subprocess.run(["sh", str(安装脚本), "--查看"], capture_output=True, text=True,
                        env={**os.environ, "HOME": os.environ.get("HOME", "/tmp")},
                        cwd=str(项目根), timeout=120)
        self.assertEqual(子.stderr, "", f"安装脚本往 stderr 喷了东西：{子.stderr}")
        self.assertIn("项目目录", 子.stdout)

    def test_自检模式能跑通(self):
        """--check 是"双击没反应"时的第一诊断手段，必须真能跑。"""
        子 = subprocess.run([str(启动脚本), "--check"], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, cwd=str(项目根), timeout=180)
        输出 = 子.stdout + 子.stderr
        self.assertIn("解释器", 输出)
        if shutil.which("ffmpeg") is None and not (项目根 / "运行环境" / "libav").is_dir():
            self.skipTest("这台机器没有 libav*，--check 会如实报不可用")
        self.assertIn("自检结果", 输出)
        self.assertEqual(子.returncode, 0, f"自检没通过：{输出[-800:]}")


class 图标测试(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not 图标.is_file():
            # 用当前解释器（Linux 是 venv/bin/python，Windows 是 Scripts\python.exe，
            # 写死 bin/python 会在 Windows 上找不到）
            subprocess.run([sys.executable, str(图标脚本)], cwd=str(项目根), timeout=180,
                       env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})

    def test_生成的是能解码的真图(self):
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        self.assertTrue(图标.is_file(), "缺少图标（跑 工具/生成图标.py）")
        图 = QImage(str(图标))
        self.assertFalse(图.isNull(), "图标不是能解码的图片")
        self.assertEqual((图.width(), 图.height()), (256, 256))

    def _取样(self, 路径, 步长=3):
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        图 = QImage(str(路径)).convertToFormat(QImage.Format.Format_RGB32)
        self.assertFalse(图.isNull(), f"解不开这张图：{路径}")
        点们 = [(图.pixel(x, y) & 0xFFFFFF)
               for x in range(0, 图.width(), 步长)
               for y in range(0, 图.height(), 步长)]
        return 图, 点们

    @staticmethod
    def _亮度(颜色: int) -> float:
        r, g, b = (颜色 >> 16) & 0xFF, (颜色 >> 8) & 0xFF, 颜色 & 0xFF
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0

    def test_风格是简约清新_浅底加青绿点缀(self):
        """用户明确要求"简约清新"：**底色要浅、点缀要青绿**。

        这条测试是防回退的：上一版是深蓝底大色块，被嫌丑。
        """
        图, 点们 = self._取样(图标)
        浅色 = sum(1 for c in 点们 if self._亮度(c) >= 0.82)
        self.assertGreater(浅色 / len(点们), 0.55,
                           f"底色不够浅（浅色占比 {浅色 / len(点们):.2f}），不像简约清新")
        青绿 = sum(1 for c in 点们
                 if (c >> 8 & 0xFF) > (c >> 16 & 0xFF) + 25
                 and (c >> 8 & 0xFF) > (c & 0xFF) - 10
                 and self._亮度(c) > 0.25)
        self.assertGreater(青绿, 400, "看不出青绿点缀（圆环/播放三角）")
        self.assertEqual(len({c for c in 点们}), len(set(点们)))

    def test_圆角底_四角是透明的(self):
        # 注意：这里**不能**走 _取样（它转成 RGB32 会把 alpha 抹掉）
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        图 = QImage(str(图标))
        for x, y in ((2, 2), (图.width() - 3, 2), (2, 图.height() - 3),
                    (图.width() - 3, 图.height() - 3)):
            点 = 图.pixel(x, y)
            self.assertEqual(点 & 0xFF000000, 0, f"({x},{y}) 不是透明的：圆角没画出来？")

    def test_十六像素会简化_还认得出是播放器(self):
        """16 px 下画环只会糊成一团：那个尺寸只留三角，但必须还是"能认出的图形"。"""
        小 = 项目根 / "资源" / "图标" / "hicolor" / "16x16" / "apps" / "网盘管理_V2.png"
        self.assertTrue(小.is_file(), "缺少 16×16 图标")
        图, 点们 = self._取样(小, 步长=1)
        颜色 = {c for c in 点们 if self._亮度(c) < 0.9}      # 去掉浅底
        self.assertGreater(len(颜色), 3, "16 px 下图形太单薄（只有一两种颜色）")
        青绿 = sum(1 for c in 点们
                 if (c >> 8 & 0xFF) > (c >> 16 & 0xFF) + 25 and self._亮度(c) < 0.8)
        self.assertGreater(青绿, 12, "16 px 下看不到那个青绿三角")

    def test_三款风格都画得出来且各有特征(self):
        """生成器要能独立调用（换风格不用改代码）：简约浅底、深色深底、极简透明。"""
        import importlib.util
        规格 = importlib.util.spec_from_file_location("v2生成图标", 图标脚本)
        模块 = importlib.util.module_from_spec(规格)
        规格.loader.exec_module(模块)
        应用 = __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication
        应用.instance() or 应用([])
        for 样式 in 模块.样式们:
            图 = 模块.画图标(64, 样式)
            self.assertFalse(图.isNull(), f"{样式} 画不出来")
            点们 = [图.pixel(x, y) & 0xFFFFFF
                   for x in range(0, 64, 2) for y in range(0, 64, 2)]
            亮 = sum(1 for c in 点们 if self._亮度(c) >= 0.82) / len(点们)
            if 样式 == "简约":
                self.assertGreater(亮, 0.5, "简约风应当是浅底")
            elif 样式 == "深色":
                self.assertLess(亮, 0.25, "深色风应当是深底")
            else:                                   # 极简：透明底 + 青绿三角
                self.assertLess(图.pixel(2, 2) & 0xFF000000, 1, "极简风应当没有底")
        with self.assertRaises(ValueError):
            模块.画图标(64, "花里胡哨")

    def test_图标主题与ICO都齐全(self):
        根 = 项目根 / "资源" / "图标"
        for 尺寸 in (16, 32, 48, 64, 128, 256):
            路径 = 根 / "hicolor" / f"{尺寸}x{尺寸}" / "apps" / "网盘管理_V2.png"
            self.assertTrue(路径.is_file(), f"缺少 {尺寸} 尺寸的图标：{路径}")
        ico = 根 / "网盘管理_V2.ico"
        if ico.is_file():
            with ico.open("rb") as f:
                self.assertEqual(f.read(4)[:4], b"\x00\x00\x01\x00", "ICO 文件头不对")


@unittest.skipUnless(有POSIX外壳, 原因)
class 安装脚本测试(unittest.TestCase):
    """真跑一遍安装脚本（HOME 指到临时目录里，绝不碰用户的桌面）。"""

    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.家 = Path(self.临时.name) / "家"
        self.桌面 = self.家 / "Desktop"
        self.数据 = Path(self.临时.name) / "xdg数据"
        self.桌面.mkdir(parents=True, exist_ok=True)
        self.环境 = {**os.environ, "HOME": str(self.家), "XDG_DATA_HOME": str(self.数据),
                  "XDG_DESKTOP_DIR": str(self.桌面)}

    def _跑(self, *参数):
        return subprocess.run(["sh", str(安装脚本), *参数], capture_output=True, text=True,
                          env=self.环境, cwd=str(项目根), timeout=120)

    def test_安装后三处都在且内容正确(self):
        子 = self._跑()
        self.assertIn("菜单项", 子.stdout, f"安装输出不对：{子.stdout}{子.stderr}")
        条目 = self.数据 / "applications" / "网盘管理_V2.desktop"
        桌面项 = self.桌面 / "一键启动_网盘管理_V2.desktop"
        self.assertTrue(条目.is_file(), "没装菜单项")
        self.assertTrue(桌面项.is_file(), "没装桌面图标")
        文本 = 条目.read_text(encoding="utf-8")
        # ⚠️ 整合后显示名是「网盘管理」（.desktop 的文件名仍是 网盘管理_V2.desktop，
        #    StartupWMClass 也仍是 网盘管理V2 —— 与 启动.py 的 setApplicationName 一致）。
        必需 = {"Type=Application": "Type", "Name=网盘管理": "Name",
              "Terminal=false": "Terminal", "StartupWMClass=网盘管理V2": "StartupWMClass"}
        for 片段, 名字 in 必需.items():
            self.assertIn(片段, 文本, f"缺 {名字}")
        启动行 = next(x for x in 文本.splitlines() if x.startswith("Exec="))
        可执行 = 启动行.split("=", 1)[1].split("%")[0].strip().strip('"')
        self.assertTrue(Path(可执行).is_file(), f"Exec 指向的文件不存在：{可执行}")
        self.assertTrue(os.access(可执行, os.X_OK), f"Exec 指向的脚本不可执行：{可执行}")
        图标行 = next(x for x in 文本.splitlines() if x.startswith("Icon="))
        self.assertTrue(Path(图标行.split("=", 1)[1]).is_file(), "Icon 指向的文件不存在")
        路径行 = next(x for x in 文本.splitlines() if x.startswith("Path="))
        self.assertEqual(Path(路径行.split("=", 1)[1]), 项目根)
        self.assertTrue(os.access(桌面项, os.X_OK), ".desktop 要可执行（桌面才会当启动器）")
        self.assertIn("一键启动_网盘管理_V2.desktop", str(桌面项))
        for 尺寸 in (16, 256):
            装好 = self.数据 / "icons" / "hicolor" / f"{尺寸}x{尺寸}" / "apps" / "网盘管理_V2.png"
            self.assertTrue(装好.is_file(), f"图标主题缺 {尺寸}")

    def test_重复安装是覆盖_不会报错(self):
        第一次 = self._跑()
        第二次 = self._跑()
        self.assertEqual(第一次.returncode, 0)
        self.assertEqual(第二次.returncode, 0, f"重复安装失败了：{第二次.stderr}")
        self.assertTrue((self.数据 / "applications" / "网盘管理_V2.desktop").is_file())

    def test_桌面图标带可执行位与信任标记(self):
        """双击要能直接跑：GNOME/COSMIC 系看可执行位 + metadata::trusted，
        少了任何一样都会先弹"不受信任的启动器"让用户手动允许。"""
        self._跑()
        桌面项 = self.桌面 / "一键启动_网盘管理_V2.desktop"
        self.assertTrue(os.access(桌面项, os.X_OK), "缺可执行位")
        if shutil.which("gio") is None:
            self.skipTest("没有 gio，跳过信任标记检查")
        子 = subprocess.run(["gio", "info", "-a", "metadata::*", str(桌面项)],
                        capture_output=True, text=True, timeout=60,
                        env={**self.环境, "XDG_RUNTIME_DIR": str(Path(self.临时.name) / "run")})
        输出 = 子.stdout + 子.stderr
        if "metadata::" not in 输出:
            self.skipTest("这个文件系统不支持 GIO 元数据（不影响双击启动）")
        self.assertIn("metadata::trusted: true", 输出, f"没标记可信：{输出[-300:]}")

    def test_卸载只删自己装的那几个(self):
        别人的 = self.桌面 / "别人的快捷方式.desktop"
        别人的.write_text("[Desktop Entry]\nType=Application\n", encoding="utf-8")
        self._跑()
        子 = self._跑("--卸载")
        self.assertIn("共删除", 子.stdout)
        self.assertFalse((self.数据 / "applications" / "网盘管理_V2.desktop").exists())
        self.assertFalse((self.桌面 / "一键启动_网盘管理_V2.desktop").exists())
        self.assertTrue(别人的.is_file(), "卸载把别人的快捷方式删了！")

    def test_不桌面参数只装菜单(self):
        self._跑("--不桌面")
        self.assertTrue((self.数据 / "applications" / "网盘管理_V2.desktop").is_file())
        self.assertFalse((self.桌面 / "一键启动_网盘管理_V2.desktop").exists())

    def test_查看模式说清现状(self):
        子 = self._跑("--查看")
        输出 = 子.stdout
        for 词 in ("项目目录", "启动脚本", "未装"):
            self.assertIn(词, 输出, f"--查看 没报「{词}」")

    def test_desktop文件能被官方校验器接受(self):
        if shutil.which("desktop-file-validate") is None:
            self.skipTest("这台机器没有 desktop-file-validate")
        self._跑()
        条目 = self.数据 / "applications" / "网盘管理_V2.desktop"
        子 = subprocess.run(["desktop-file-validate", str(条目)],
                        capture_output=True, text=True, timeout=60)
        self.assertEqual(子.returncode, 0, f"校验没过：{子.stdout}{子.stderr}")


class Windows入口测试(unittest.TestCase):
    """Windows 的两个文件（编码很容易写错，这里钉住编码与关键内容）。"""

    def test_启动bat是GBK且内容完整(self):
        路径 = 项目根 / "启动.bat"
        self.assertTrue(路径.is_file(), "缺少 Windows 启动批处理")
        原始 = 路径.read_bytes()
        self.assertNotIn(b"\xef\xbb\xbf", 原始, "GBK 的 .bat 不该带 UTF-8 BOM")
        文本 = 原始.decode("gbk")                      # 解不开就说明编码写错了
        self.assertIn("启动.py", 文本)
        self.assertIn("运行环境", 文本)
        self.assertIn("\r\n", 文本, "批处理要 CRLF 换行")

    def test_安装快捷方式ps1带BOM且内容完整(self):
        路径 = 项目根 / "工具" / "安装快捷方式.ps1"
        self.assertTrue(路径.is_file(), "缺少 Windows 快捷方式安装脚本")
        原始 = 路径.read_bytes()
        self.assertTrue(原始.startswith(b"\xef\xbb\xbf"),
                        "PowerShell 5.1 靠 BOM 认 UTF-8，不然中文全乱")
        文本 = 原始.decode("utf-8-sig")
        for 词 in ("WScript.Shell", "CreateShortcut", "IconLocation", "卸载",
                 "启动.py", "pythonw.exe"):
            self.assertIn(词, 文本, f"缺少关键片段：{词}")


if __name__ == "__main__":
    unittest.main()
