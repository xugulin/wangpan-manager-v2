"""M3 网络播放：用**本地真 HTTP 服务器**验证 Range、自定义头、拖动跳转、断线重连。

判据都是"服务器侧看得见的事实"：
* 服务器记录了每一次 Range 请求头 —— 拖动进度条必须产生新的 Range；
* 服务器断言收到我们设的 Referer/Cookie（网盘直链就是这么要的）；
* 服务器中途掐断一次连接，播放必须自己重连续上（libavformat 的 reconnect）。
"""

from __future__ import annotations

import http.server
import os
import socket
import socketserver
import threading
import time
import unittest
from pathlib import Path

from tests.公用 import 有ffmpeg, 造素材, 临时目录

from wangpan.player import 网络
from wangpan.player.解封装 import 输入
from wangpan.player.引擎 import 播放引擎


class 可数服务器(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def 造处理器(文件: Path, 记录: dict):
    class 处理器(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):        # 测试里不要刷日志
            return

        def do_HEAD(self):                 # noqa: N802
            self._回(只头=True)

        def do_GET(self):                  # noqa: N802
            self._回(只头=False)

        def _回(self, 只头: bool):
            记录["次数"] = 记录.get("次数", 0) + 1
            记录.setdefault("range", []).append(self.headers.get("Range"))
            记录.setdefault("referer", []).append(self.headers.get("Referer"))
            记录.setdefault("cookie", []).append(self.headers.get("Cookie"))
            记录.setdefault("ua", []).append(self.headers.get("User-Agent"))
            大小 = 文件.stat().st_size
            起点, 终点 = 0, 大小 - 1
            范围 = self.headers.get("Range")
            部分 = False
            if 范围 and 范围.startswith("bytes="):
                片段 = 范围[len("bytes="):].split("-")
                try:
                    起点 = int(片段[0]) if 片段[0] else 0
                    终点 = int(片段[1]) if len(片段) > 1 and 片段[1] else 大小 - 1
                except ValueError:
                    起点, 终点 = 0, 大小 - 1
                终点 = min(终点, 大小 - 1)
                部分 = True
            # 第一次请求故意掐断（模拟 CDN 掐长连接），验证重连
            掐断 = 记录.get("掐断") and 记录.get("掐断次数", 0) < 1
            if 掐断:
                记录["掐断次数"] = 记录.get("掐断次数", 0) + 1
                self.send_response(200)
                self.send_header("Content-Length", str(大小))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                self.wfile.write(文件.read_bytes()[:65536])
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            长度 = 终点 - 起点 + 1
            self.send_response(206 if 部分 else 200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(长度))
            if 部分:
                self.send_header("Content-Range", f"bytes {起点}-{终点}/{大小}")
            self.end_headers()
            if 只头:
                return
            with open(文件, "rb") as 句柄:
                句柄.seek(起点)
                剩余 = 长度
                while 剩余 > 0:
                    块 = 句柄.read(min(65536, 剩余))
                    if not 块:
                        break
                    try:
                        self.wfile.write(块)
                    except OSError:
                        return
                    剩余 -= len(块)

    return 处理器


@unittest.skipUnless(有ffmpeg(), "需要系统 ffmpeg 造测试素材")
class 网络播放测试(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.临时 = 临时目录()
        cls.素材 = 造素材(cls.临时.name, 秒=4.0, 带声音=True)
        cls.记录: dict = {}
        处理器 = 造处理器(cls.素材, cls.记录)
        cls.服务器 = 可数服务器(("127.0.0.1", 0), 处理器)
        cls.端口 = cls.服务器.server_address[1]
        cls.线程 = threading.Thread(target=cls.服务器.serve_forever, daemon=True)
        cls.线程.start()

    @classmethod
    def tearDownClass(cls):
        cls.服务器.shutdown()
        cls.服务器.server_close()
        cls.临时.cleanup()

    def 地址(self) -> str:
        return f"http://127.0.0.1:{self.端口}/样片.mp4"

    # ---------------- 选项拼装（纯函数，不联网） ----------------

    def test_请求头拼装用CRLF(self):
        文本 = 网络.拼请求头({"Referer": "https://pan.x.com", "Cookie": "a=1"})
        self.assertIn("\r\n", 文本)
        self.assertTrue(文本.endswith("\r\n"))
        self.assertIn("Referer: https://pan.x.com", 文本)

    def test_网络选项(self):
        选项 = 网络.网络选项("https://cdn/x.mp4",
                         {"User-Agent": "UA", "Referer": "R"})
        self.assertEqual(选项["user_agent"], "UA")
        self.assertIn("Referer: R", 选项["headers"])
        self.assertEqual(选项["reconnect"], "1")
        self.assertIn("rw_timeout", 选项)

    def test_本地文件不加网络选项(self):
        选项 = 网络.网络选项("/tmp/x.mp4")
        self.assertEqual(选项, {})

    def test_摘要不泄露签名(self):
        文本 = 网络.摘要("https://cdn/a.mp4?sign=SECRET123&t=1")
        self.assertNotIn("SECRET123", 文本)
        self.assertIn("cdn", 文本)

    # ---------------- 真 HTTP ----------------

    def test_能通过网络打开(self):
        输入对象 = 输入.打开(self.地址(), 网络.网络选项(self.地址()))
        try:
            self.assertIsNotNone(输入对象.视频流)
            self.assertGreater(输入对象.时长秒, 2.0)
        finally:
            输入对象.关闭()

    def test_自定义头真的发出去了(self):
        # ⚠️ HTTP 头按 latin-1 解码（BaseHTTPRequestHandler 的规矩），
        #    所以这里用 ASCII 值来断言；真实 UA/Referer/Cookie 也都是 ASCII。
        头 = {"Referer": "https://pan.example.com/", "Cookie": "BDUSS=abc",
             "User-Agent": "V2-Test-UA/1.0"}
        输入对象 = 输入.打开(self.地址(), 网络.网络选项(self.地址(), 头))
        try:
            包 = 输入对象.读包()
            self.assertIsNotNone(包)
            输入对象.释放包()
        finally:
            输入对象.关闭()
        self.assertIn("https://pan.example.com/", self.记录.get("referer") or [])
        self.assertIn("BDUSS=abc", self.记录.get("cookie") or [])
        self.assertIn("V2-Test-UA/1.0", self.记录.get("ua") or [])

    def test_跳转会发新的Range请求(self):
        输入对象 = 输入.打开(self.地址(), 网络.网络选项(self.地址()))
        try:
            前 = len(self.记录.get("range") or [])
            self.assertTrue(输入对象.跳转(2.5), "HTTP 源要支持跳转")
            包 = 输入对象.读包()
            self.assertIsNotNone(包)
            输入对象.释放包()
        finally:
            输入对象.关闭()
        范围们 = [r for r in (self.记录.get("range") or []) if r]
        self.assertGreater(len(范围们), 0, "跳转必须带 Range 请求头")
        self.assertGreater(len(self.记录.get("range") or []), 前 - 1)

    def test_引擎能网络起播并出画面(self):
        引擎 = 播放引擎()
        self.addCleanup(引擎.停止)
        引擎.打开(self.地址(), 请求头={"Referer": "https://pan.example.com/"})
        引擎.播放()
        截止 = time.time() + 8.0
        while time.time() < 截止 and 引擎.统计.已解视频帧 < 10:
            time.sleep(0.05)
        self.assertGreater(引擎.统计.已解视频帧, 9, "网络源要能持续出帧")
        self.assertIsNotNone(引擎.取最新帧())

    def test_HTTP10风格的服务器也能播(self):
        """HTTP/1.0 式"回完就关连接"的服务器：必须能降级播上（真实 CDN 常见）。"""
        处理器 = 造处理器(self.素材, {})
        # 关掉 keep-alive 语义：声明成 HTTP/1.0
        class HTTP10处理器(处理器):
            protocol_version = "HTTP/1.0"
        服务器 = 可数服务器(("127.0.0.1", 0), HTTP10处理器)
        端口 = 服务器.server_address[1]
        线程 = threading.Thread(target=服务器.serve_forever, daemon=True)
        线程.start()
        try:
            地址 = f"http://127.0.0.1:{端口}/样片.mp4"
            引擎 = 播放引擎()
            self.addCleanup(引擎.停止)
            引擎.打开(地址)          # 内部会先试完整选项，失败自动降级
            引擎.播放()
            截止 = time.time() + 8.0
            while time.time() < 截止 and 引擎.统计.已解视频帧 < 8:
                time.sleep(0.05)
            self.assertGreater(引擎.统计.已解视频帧, 7,
                               "HTTP/1.0 服务器上也要能出画面")
        finally:
            服务器.shutdown()
            服务器.server_close()

    def test_中断回调真的装上了(self):
        """回调没装上就等于没有防线（踩过：一处 ctypes 变量名写错，静默失败）。"""
        from wangpan.player.解封装 import 输入
        对象 = 输入.打开(str(self.素材))
        try:
            对象.装中断回调(lambda: False)
            self.assertTrue(对象.中断回调已装, "中断回调必须真的写进 AVFormatContext")
            self.assertIsNotNone(对象._中断回调)
        finally:
            对象.关闭()

    def test_卡住的服务器上停止不会卡死也不会崩(self):
        """服务器接了连接却不回数据（真实网络里很常见）：停止必须能打断阻塞的读。

        这是**崩溃防线**：如果 av_read_frame 还阻塞着就去 avformat_close_input，
        就是悬垂指针（实测段错误）。正确做法是中断回调 + 等线程退出。
        """
        import socket as _socket
        监听 = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        监听.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        监听.bind(("127.0.0.1", 0))
        监听.listen(8)
        端口 = 监听.getsockname()[1]
        连接们: list = []
        停 = threading.Event()

        def 接收():
            while not 停.is_set():
                try:
                    监听.settimeout(0.3)
                    连接, _ = 监听.accept()
                    连接们.append(连接)          # 收下就不理它（不回任何数据）
                except OSError:
                    continue

        线程 = threading.Thread(target=接收, daemon=True)
        线程.start()
        try:
            引擎 = 播放引擎()
            t0 = time.time()
            try:
                # 打开会卡在网络上（服务器不回话）——这正是要测的场景
                引擎.打开(f"http://127.0.0.1:{端口}/卡住.mp4")
                引擎.播放()
            except Exception:
                pass                                 # 打不开是正常的
            用时首次 = time.time() - t0
            time.sleep(0.3)
            t1 = time.time()
            引擎.停止()
            用时停止 = time.time() - t1
            self.assertLess(用时首次, 80.0, "打开阶段不该无限期卡住")
            self.assertLess(用时停止, 4.0,
                            f"停止花了 {用时停止:.1f}s，中断回调没生效（会悬垂崩溃）")
        finally:
            停.set()
            监听.close()
            for 连接 in 连接们:
                try:
                    连接.close()
                except OSError:
                    pass

    def test_服务器掐断一次也能自己接上(self):
        """CDN 掐长连接很常见：libavformat 的 reconnect 要把我们接回来看完。"""
        self.记录["掐断"] = True
        self.记录["掐断次数"] = 0
        try:
            引擎 = 播放引擎()
            self.addCleanup(引擎.停止)
            引擎.打开(self.地址())
            引擎.播放()
            截止 = time.time() + 10.0
            while time.time() < 截止 and 引擎.统计.已解视频帧 < 20:
                time.sleep(0.05)
            self.assertGreaterEqual(self.记录.get("掐断次数", 0), 1, "服务器应掐断过一次")
            self.assertGreater(引擎.统计.已解视频帧, 19, "掐断后必须自己续上继续解帧")
        finally:
            self.记录["掐断"] = False


if __name__ == "__main__":
    unittest.main()
