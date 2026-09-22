"""M6：网盘适配器契约 + 传输引擎（下载/上传/暂停/取消/续传/并发）。

用**本地适配器 + 本地 HTTP 服务器**跑端到端，不依赖任何真实网盘账号。
"""

from __future__ import annotations

import http.server
import socketserver
import threading
import time
import unittest
from pathlib import Path

from tests.公用 import 有ffmpeg, 造素材, 临时目录

from wangpan.pan import HTTP适配器, 注册表, 本地适配器, 条目, 不支持
from wangpan.transfer import 传输引擎, 状态


class 服务(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        return


class 适配器测试(unittest.TestCase):
    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name) / "云盘"
        self.根.mkdir()
        (self.根 / "影片.mp4").write_bytes(b"x" * 2048)
        (self.根 / "字幕.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n嗨\n",
                                    encoding="utf-8")
        (self.根 / "子目录").mkdir()
        self.盘 = 本地适配器(self.根)

    def test_列目录(self):
        条目们 = self.盘.列出("/")
        self.assertEqual([x.名字 for x in 条目们], ["子目录", "字幕.srt", "影片.mp4"])
        self.assertTrue(条目们[0].是目录)
        self.assertEqual(条目们[-1].大小, 2048)
        self.assertTrue(条目们[-1].可播放())
        self.assertFalse(条目们[1].可播放(), "字幕不是视频")
        self.assertEqual(条目们[0].大小文本(), "目录")

    def test_取直链(self):
        直链 = self.盘.取直链("/影片.mp4")
        self.assertTrue(直链.地址.endswith("影片.mp4"))
        self.assertEqual(直链.大小, 2048)

    def test_路径越界被挡住(self):
        with self.assertRaises(不支持):
            self.盘.列出("/../../etc")

    def test_下载并校验大小(self):
        落点 = Path(self.临时.name) / "落" / "影片.mp4"
        进度: list = []
        self.盘.下载("/影片.mp4", 落点, lambda 已, 总: 进度.append((已, 总)))
        self.assertEqual(落点.stat().st_size, 2048)
        self.assertTrue(进度)
        self.assertEqual(进度[-1], (2048, 2048))

    def test_上传(self):
        本地 = Path(self.临时.name) / "新文件.txt"
        本地.write_text("你好", encoding="utf-8")
        self.盘.上传(本地, "/子目录/新文件.txt")
        self.assertEqual((self.根 / "子目录" / "新文件.txt").read_text(encoding="utf-8"),
                         "你好")

    def test_删除与建目录(self):
        self.盘.建目录("/新目录")
        self.assertTrue((self.根 / "新目录").is_dir())
        self.盘.删除("/新目录")
        self.assertFalse((self.根 / "新目录").exists())

    def test_基类默认不支持并明说(self):
        基 = 条目(名字="x")           # 只是拿个对象
        from wangpan.pan import 适配器
        with self.assertRaises(不支持):
            适配器().列出("/")
        self.assertTrue(基.名字)


class HTTP适配器测试(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.临时 = 临时目录()
        cls.目录 = Path(cls.临时.name)
        (cls.目录 / "远端.bin").write_bytes(b"Z" * 300000)
        处理器 = lambda *a, **k: 服务(*a, directory=str(cls.目录), **k)  # noqa: E731
        cls.服务器 = socketserver.ThreadingTCPServer(("127.0.0.1", 0), 处理器)
        cls.服务器.allow_reuse_address = True
        cls.服务器.daemon_threads = True
        cls.端口 = cls.服务器.server_address[1]
        threading.Thread(target=cls.服务器.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.服务器.shutdown()
        cls.服务器.server_close()
        cls.临时.cleanup()

    def _盘(self):
        return HTTP适配器(f"http://127.0.0.1:{self.端口}/远端.bin",
                         {"Referer": "https://pan.example.com/"})

    def test_取直链带请求头(self):
        直链 = self._盘().取直链(f"http://127.0.0.1:{self.端口}/远端.bin")
        self.assertEqual(直链.请求头.get("Referer"), "https://pan.example.com/")
        self.assertEqual(直链.大小, 300000)

    def test_中文文件名也能请求(self):
        """中文文件名的直链必须能请求（要百分号编码；实测 urllib 会直接抛
        UnicodeEncodeError: 'ascii' codec can't encode ...）。"""
        from wangpan.pan.http import 规范地址
        self.assertNotIn("远", 规范地址("http://x/远端.bin"))
        self.assertIn("%E8%BF%9C", 规范地址("http://x/远端.bin"))
        # 已经编码过的不许二次编码
        self.assertEqual(规范地址("http://x/a%20b.bin"), "http://x/a%20b.bin")
        盘 = HTTP适配器(f"http://127.0.0.1:{self.端口}/远端.bin")
        直链 = 盘.取直链(f"http://127.0.0.1:{self.端口}/远端.bin")
        self.assertEqual(直链.大小, 300000)
        落点 = Path(self.临时.name) / "中文" / "远端.bin"
        盘.下载(f"http://127.0.0.1:{self.端口}/远端.bin", 落点)
        self.assertEqual(落点.stat().st_size, 300000)

    def test_下载(self):
        落点 = Path(self.临时.name) / "下载" / "远端.bin"
        self._盘().下载(f"http://127.0.0.1:{self.端口}/远端.bin", 落点)
        self.assertEqual(落点.stat().st_size, 300000)

    def test_续传不会重复写(self):
        """已经有半截 .部分 文件时：必须接着写，而不是从头覆盖。"""
        目标目录 = Path(self.临时.name) / "续传"
        目标目录.mkdir(exist_ok=True)
        落点 = 目标目录 / "远端.bin"
        部分 = 落点.with_suffix(落点.suffix + ".部分")
        部分.write_bytes(b"Z" * 100000)
        进度: list = []
        self._盘().下载(f"http://127.0.0.1:{self.端口}/远端.bin", 落点,
                       lambda 已, 总: 进度.append((已, 总)))
        self.assertEqual(落点.stat().st_size, 300000, "续传后大小必须正好")
        self.assertEqual(部分.exists(), False, "完成后不该留下 .部分")


class 传输引擎测试(unittest.TestCase):
    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.源 = Path(self.临时.name) / "源"
        self.源.mkdir()
        for i in range(3):
            (self.源 / f"文件{i}.bin").write_bytes(bytes([i]) * (2 * 1024 * 1024))
        self.表 = 注册表()
        self.表.注册(本地适配器(self.源), "本地")
        self.引擎 = 传输引擎(self.表, 并发=2)
        self.addCleanup(self.引擎.关闭)

    def test_下载完成与进度(self):
        落点 = Path(self.临时.name) / "出" / "文件0.bin"
        任务 = self.引擎.加下载("本地", "/文件0.bin", 落点, 名字="文件0.bin")
        self.assertTrue(self.引擎.等待全部(30))
        self.assertEqual(任务.状态, 状态.完成)
        self.assertEqual(任务.进度, 1.0)
        self.assertEqual(落点.stat().st_size, 2 * 1024 * 1024)
        self.assertIn("完成", 任务.摘要())

    def test_并发上限被遵守(self):
        落点们 = [Path(self.临时.name) / "出" / f"文件{i}.bin" for i in range(3)]
        for i, 落点 in enumerate(落点们):
            self.引擎.加下载("本地", f"/文件{i}.bin", 落点)
        观察到的并发 = 0
        截止 = time.time() + 20
        while time.time() < 截止:
            观察到的并发 = max(观察到的并发, len(self.引擎._运行中))
            if self.引擎.等待全部(0.05):
                break
        self.assertLessEqual(观察到的并发, 2, "并发上限是 2，不能超")

    def test_取消(self):
        落点 = Path(self.临时.name) / "出" / "文件1.bin"
        任务 = self.引擎.加下载("本地", "/文件1.bin", 落点)
        self.引擎.取消(任务)
        self.assertTrue(self.引擎.等待全部(20) or 任务.状态 in 状态.终态们)
        self.assertIn(任务.状态, (状态.已取消, 状态.完成))

    def test_上传(self):
        本地 = Path(self.临时.name) / "本地.bin"
        本地.write_bytes(b"U" * 1024)
        任务 = self.引擎.加上传("本地", 本地, "/上传后.bin")
        self.assertTrue(self.引擎.等待全部(20))
        self.assertEqual(任务.状态, 状态.完成)
        self.assertEqual((self.源 / "上传后.bin").stat().st_size, 1024)

    def test_重复提交不会跑两遍(self):
        落点 = Path(self.临时.name) / "出" / "文件2.bin"
        self.引擎.加下载("本地", "/文件2.bin", 落点)
        任务2 = self.引擎.加下载("本地", "/文件2.bin", 落点)
        self.assertEqual(len([t for t in self.引擎.任务们()
                          if t.来源路径 == "/文件2.bin"]), 1,
                         "同一来源+落点只应有一条任务")
        self.assertTrue(self.引擎.等待全部(30))
        self.assertEqual(任务2.状态, 状态.完成)

    def test_统计与清理(self):
        落点 = Path(self.临时.name) / "出" / "文件0.bin"
        self.引擎.加下载("本地", "/文件0.bin", 落点)
        self.assertTrue(self.引擎.等待全部(30))
        统计 = self.引擎.统计()
        self.assertEqual(统计["完成"], 1)
        self.assertGreaterEqual(self.引擎.清已完成(), 1)


if __name__ == "__main__":
    unittest.main()


class 分段下载测试(unittest.TestCase):
    """多段并发下载：服务器支持 Range 时并发；不支持时自动退化单线程。"""

    @classmethod
    def setUpClass(cls):
        cls.临时 = 临时目录()
        cls.目录 = Path(cls.临时.name) / "源"
        cls.目录.mkdir(parents=True, exist_ok=True)
        # 8 MB 内容，每个字节都不一样，方便校验"拼出来的文件是不是对的"
        cls.内容 = bytes((i * 7 + (i >> 8)) % 251 for i in range(0, 8 * 1024 * 1024))
        (cls.目录 / "大文件.bin").write_bytes(cls.内容)
        cls.记录: list = []

        # ⚠️ 标准库的 SimpleHTTPRequestHandler **不认 Range**（回 200 整份），
        #    拿它测分段等于没测。这里自己实现 206 + Content-Range。
        class 处理器(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                return

            def do_GET(self):                    # noqa: N802
                范围 = self.headers.get("Range")
                cls.记录.append(范围)
                数据 = cls.内容
                大小 = len(数据)
                起, 止 = 0, 大小 - 1
                部分 = bool(范围 and 范围.startswith("bytes="))
                if 部分:
                    片段 = 范围[6:].split("-")
                    起 = int(片段[0]) if 片段[0] else 0
                    止 = int(片段[1]) if len(片段) > 1 and 片段[1] else 大小 - 1
                    止 = min(止, 大小 - 1)
                块 = 数据[起:止 + 1]
                self.send_response(206 if 部分 else 200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(块)))
                if 部分:
                    self.send_header("Content-Range", f"bytes {起}-{止}/{大小}")
                self.end_headers()
                try:
                    self.wfile.write(块)
                except OSError:
                    return

        def 建(*a, **k):
            # 我们这个处理器自己管数据（BaseHTTPRequestHandler 不认 directory=）
            k.pop("directory", None)
            return 处理器(*a, **k)

        cls.服务器 = socketserver.ThreadingTCPServer(("127.0.0.1", 0), 建)
        cls.服务器.daemon_threads = True
        cls.端口 = cls.服务器.server_address[1]
        threading.Thread(target=cls.服务器.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.服务器.shutdown()
        cls.服务器.server_close()
        cls.临时.cleanup()

    def test_探测分段支持(self):
        from wangpan.transfer.分段 import 探测分段支持
        支持, 大小 = 探测分段支持(f"http://127.0.0.1:{self.端口}/大文件.bin")
        self.assertEqual(大小, len(self.内容))
        self.assertTrue(支持, "这个测试服务器支持 Range，必须探测出来")

    def test_分段下载内容完全一致(self):
        from wangpan.transfer.分段 import 分段下载
        落点 = Path(self.临时.name) / "出" / "大文件.bin"
        self.记录.clear()
        分段下载(f"http://127.0.0.1:{self.端口}/大文件.bin", 落点, 段数=4)
        self.assertEqual(落点.stat().st_size, len(self.内容))
        self.assertEqual(落点.read_bytes(), self.内容, "拼出来的内容必须逐字节一致")
        self.assertFalse(落点.with_suffix(落点.suffix + ".部分").exists())
        # 真的分了 4 段（每段一个 Range 请求，而不是退化成一个整份请求）
        段请求 = [r for r in self.记录 if r and r.startswith("bytes=") and "-" in r
                and not r.endswith("-0")]
        self.assertGreaterEqual(len(段请求), 4, f"应该发出多段 Range 请求：{self.记录}")

    def test_进度回调单调递增(self):
        from wangpan.transfer.分段 import 分段下载
        落点 = Path(self.临时.name) / "出2" / "大文件.bin"
        进度: list = []
        分段下载(f"http://127.0.0.1:{self.端口}/大文件.bin", 落点, 段数=4,
              进度回调=lambda 已, 总: 进度.append(已))
        self.assertGreater(len(进度), 3)
        self.assertEqual(进度, sorted(进度), "进度不能倒退")
        self.assertEqual(进度[-1], len(self.内容))

    def test_引擎走分段并完成(self):
        表 = 注册表()
        表.注册(HTTP适配器(f"http://127.0.0.1:{self.端口}/大文件.bin"), "http")
        引擎 = 传输引擎(表, 并发=1)
        self.addCleanup(引擎.关闭)
        落点 = Path(self.临时.name) / "出3" / "大文件.bin"
        任务 = 引擎.加下载("http", f"http://127.0.0.1:{self.端口}/大文件.bin", 落点,
                        名字="大文件.bin", 总字节=len(self.内容))
        self.assertTrue(引擎.等待全部(60))
        self.assertEqual(任务.状态, 状态.完成)
        self.assertEqual(落点.read_bytes(), self.内容)


class 上传续传测试(unittest.TestCase):
    """上传中断后重试要接着传，而不是从头覆盖（大文件上这很要命）。"""

    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name) / "云盘"
        self.根.mkdir()
        self.盘 = 本地适配器(self.根)
        self.本地 = Path(self.临时.name) / "源.bin"
        self.本地.write_bytes(bytes(range(256)) * 4096)      # 1 MB

    def test_声明了支持续传(self):
        self.assertTrue(self.盘.支持续传上传)

    def test_目标已有半截时接着写(self):
        目标 = self.根 / "已存在.bin"
        总量 = self.本地.stat().st_size
        目标.write_bytes(self.本地.read_bytes()[:总量 // 2])   # 假装上次传了一半
        进度: list = []
        self.盘.上传(self.本地, "/已存在.bin", lambda 已, 总: 进度.append((已, 总)))
        self.assertEqual(目标.read_bytes(), self.本地.read_bytes(), "续传后内容要完全一致")
        self.assertEqual(进度[0][0], 总量 // 2, "第一条进度应该是从已有的大小开始")

    def test_已经传完就不重复写(self):
        目标 = self.根 / "完整.bin"
        目标.write_bytes(self.本地.read_bytes())
        时间戳 = 目标.stat().st_mtime_ns
        进度: list = []
        self.盘.上传(self.本地, "/完整.bin", lambda 已, 总: 进度.append((已, 总)))
        self.assertEqual(目标.stat().st_mtime_ns, 时间戳, "已经完整就不该再写一遍")
        self.assertEqual(进度[-1][0], 目标.stat().st_size)

    def test_目标比源还大时从头来(self):
        目标 = self.根 / "脏.bin"
        目标.write_bytes(b"x" * (self.本地.stat().st_size + 100))
        self.盘.上传(self.本地, "/脏.bin")
        self.assertEqual(目标.read_bytes(), self.本地.read_bytes())
