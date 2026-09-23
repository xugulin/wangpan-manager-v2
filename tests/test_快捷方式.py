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
import unittest
from pathlib import Path

from tests.公用 import 项目根, 临时目录

启动脚本 = 项目根 / "一键启动_网盘管理_V2.sh"
安装脚本 = 项目根 / "工具" / "安装快捷方式.sh"
图标脚本 = 项目根 / "工具" / "生成图标.py"
图标 = 项目根 / "网盘管理_V2_图标.png"


class 启动器测试(unittest.TestCase):
    def test_一键启动脚本存在且可执行(self):
        self.assertTrue(启动脚本.is_file(), "缺少一键启动脚本")
        self.assertTrue(os.access(启动脚本, os.X_OK), "一键启动脚本要有可执行位（双击才跑得起来）")

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
            subprocess.run([str(项目根 / "运行环境" / "venv" / "bin" / "python"),
                        str(图标脚本)], cwd=str(项目根), timeout=180,
                       env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})

    def test_生成的是能解码的真图(self):
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        self.assertTrue(图标.is_file(), "缺少图标（跑 工具/生成图标.py）")
        图 = QImage(str(图标))
        self.assertFalse(图.isNull(), "图标不是能解码的图片")
        self.assertEqual((图.width(), 图.height()), (256, 256))

    def test_图标不是一片空白(self):
        """画歪了/画丢了也要能发现：至少要有明显不同的颜色块（底、三角、方块）。"""
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        图 = QImage(str(图标)).convertToFormat(QImage.Format.Format_RGB32)
        颜色 = {}
        for x in range(0, 图.width(), 3):
            for y in range(0, 图.height(), 3):
                颜色[图.pixel(x, y) & 0xFFFFFF] = 颜色.get(图.pixel(x, y) & 0xFFFFFF, 0) + 1
        self.assertGreater(len(颜色), 200, "颜色太少，不像一张画好的图")
        白 = sum(n for c, n in 颜色.items() if c > 0xE0E0E0)
        蓝 = sum(n for c, n in 颜色.items() if (c & 0xFF) > (c >> 16))
        self.assertGreater(白, 200, "白色播放三角不见了？")
        self.assertGreater(蓝, 2000, "底色不对？")

    def test_图标主题与ICO都齐全(self):
        根 = 项目根 / "资源" / "图标"
        for 尺寸 in (16, 32, 48, 64, 128, 256):
            路径 = 根 / "hicolor" / f"{尺寸}x{尺寸}" / "apps" / "网盘管理_V2.png"
            self.assertTrue(路径.is_file(), f"缺少 {尺寸} 尺寸的图标：{路径}")
        ico = 根 / "网盘管理_V2.ico"
        if ico.is_file():
            with ico.open("rb") as f:
                self.assertEqual(f.read(4)[:4], b"\x00\x00\x01\x00", "ICO 文件头不对")


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
        必需 = {"Type=Application": "Type", "Name=网盘管理 V2": "Name",
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
        for 尺寸 in (16, 256):
            装好 = self.数据 / "icons" / "hicolor" / f"{尺寸}x{尺寸}" / "apps" / "网盘管理_V2.png"
            self.assertTrue(装好.is_file(), f"图标主题缺 {尺寸}")

    def test_重复安装是覆盖_不会报错(self):
        第一次 = self._跑()
        第二次 = self._跑()
        self.assertEqual(第一次.returncode, 0)
        self.assertEqual(第二次.returncode, 0, f"重复安装失败了：{第二次.stderr}")
        self.assertTrue((self.数据 / "applications" / "网盘管理_V2.desktop").is_file())

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
