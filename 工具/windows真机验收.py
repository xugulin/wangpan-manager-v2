#!/usr/bin/env python3
"""Windows 真机验收（CI 里跑）：解码 → 出画面 → 截图 → 网络 Range → 硬解回退。

为什么单独写它（而不是只跑单测）：单测跑在离屏模式下、用的是很小的素材；
这一脚本回答的是"**用户打开一个片子，画面会不会出来**"这类问题：

1. 真用 :class:`播放引擎` 播一段真素材（默认现场用 ffmpeg 造；没有 ffmpeg 就用自带的）；
2. 断言：解出了帧、帧尺寸对、状态栏字段齐全、截图 PNG 能存下来；
3. 报告硬解状态（Windows 上是 D3D11VA；**跑不动必须自动回退软解**，不能崩）；
4. 起一个本地 HTTP 服务器（支持 Range）再播一次，验证网络播放链路；
5. 结果写进 ``数据/真机验收.txt``（CI 里打印出来）。

用法::

    运行环境\\venv\\Scripts\\python.exe 工具\\windows真机验收.py
"""

from __future__ import annotations

import http.server
import os
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(项目根))
from wangpan.控制台 import 修控制台          # noqa: E402 - 必须在打印中文之前

修控制台()
if os.name != "nt":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

报告行: list[str] = []


def 记(文本: str) -> None:
    print(文本, flush=True)
    报告行.append(文本)


def 造素材(目录: Path) -> Path:
    """验收素材：优先用仓库里带的小素材（runner 上通常没有 ffmpeg 命令）。"""
    自带 = 项目根 / "工具" / "测试素材" / "样片.mp4"
    if 自带.is_file():
        return 自带
    目标 = 目录 / "验收素材.mp4"
    if 目标.is_file():
        return 目标
    目录.mkdir(parents=True, exist_ok=True)
    from shutil import which
    if which("ffmpeg"):
        命令 = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
               "-i", "testsrc2=size=640x360:rate=25:duration=6",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
               "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-shortest", "-y", str(目标)]
        子 = subprocess.run(命令, capture_output=True, text=True)
        if 子.returncode == 0 and 目标.is_file():
            return 目标
        raise SystemExit(f"造素材失败：{子.stderr[-400:]}")
    raise SystemExit("仓库里没有自带素材、这台机器也没有 ffmpeg，无法验收")


class 处理器(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    数据 = b""

    def log_message(self, *_a):
        return

    def do_GET(self):                      # noqa: N802
        大小 = len(self.数据)
        范围 = self.headers.get("Range")
        起, 止 = 0, 大小 - 1
        部分 = bool(范围 and 范围.startswith("bytes="))
        if 部分:
            片段 = 范围[6:].split("-")
            起 = int(片段[0]) if 片段[0] else 0
            止 = int(片段[1]) if len(片段) > 1 and 片段[1] else 大小 - 1
            止 = min(止, 大小 - 1)
        块 = self.数据[起:止 + 1]
        self.send_response(206 if 部分 else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(块)))
        if 部分:
            self.send_header("Content-Range", f"bytes {起}-{止}/{大小}")
        self.end_headers()
        try:
            self.wfile.write(块)
        except OSError:
            return


def main() -> int:
    from wangpan.ffmpeg import 加载, 绑定
    from wangpan.player.引擎 import 播放引擎

    记(f"平台：{sys.platform}｜Python {sys.version.split()[0]}")
    记(f"FFmpeg 可用：{加载.可用()}｜{加载.不可用原因()}")
    记("版本：" + "、".join(f"{k} {加载.版本文本(v)}"
                       for k, v in 加载.库版本().items()))
    记("路径：" + "、".join(f"{k}={加载.路径(k)}"
                       for k in ("avformat", "avcodec", "avutil", "swscale",
                                 "swresample")))
    绑定.取绑定().检查ABI()
    记("ABI 自检：通过")

    素材 = 造素材(项目根 / "数据" / "验收素材")
    记(f"素材：{素材.name}（{素材.stat().st_size / 1024:.0f} KB）")

    def 播一遍(地址: str, 请求头: dict | None = None, 标题: str = "") -> dict:
        引擎 = 播放引擎(日志回调=lambda t: 记("  " + t))
        引擎.打开(地址, 请求头=请求头 or {})
        引擎.设置输出尺寸(320, 180)
        统计0 = dict(引擎.统计.to_dict()) if hasattr(引擎.统计, "to_dict") else {}
        引擎.播放()
        截止 = time.time() + 20
        while time.time() < 截止 and 引擎.统计.已解视频帧 < 20:
            time.sleep(0.05)
        帧 = 引擎.取最新帧()
        结果 = {"帧数": 引擎.统计.已解视频帧, "丢帧": 引擎.统计.已丢视频帧,
              "硬解": 引擎.统计.硬解, "硬解帧": 引擎.统计.硬解帧,
              "输出": 引擎.统计.输出尺寸, "音频设备": 引擎.统计.音频设备,
              "有帧": 帧 is not None,
              "帧尺寸": (帧.宽, 帧.高) if 帧 else None,
              "时长秒": round(引擎.统计.总时长秒, 2)}
        图 = 项目根 / "数据" / f"验收截图_{标题 or 'x'}.png"
        结果["截图"] = bool(引擎.截图(str(图))) and 图.is_file() and 图.stat().st_size > 1000
        # 暂停要停住、跳转要生效（这两条是"真播放器"的门槛）
        引擎.设置暂停(True)
        前 = 引擎.统计.已解视频帧
        time.sleep(0.4)
        结果["暂停有效"] = 引擎.统计.已解视频帧 == 前
        引擎.设置暂停(False)
        引擎.跳转(3.0)
        截止 = time.time() + 6
        while time.time() < 截止 and 引擎.统计.当前时间秒 < 2.5:
            time.sleep(0.05)
        结果["跳转后时间"] = round(引擎.统计.当前时间秒, 2)
        引擎.停止()
        记(f"【{标题}】{结果}")
        return 结果

    本地 = 播一遍(str(素材), None, "本地文件")
    异常: list[str] = []
    if 本地["帧数"] < 20:
        异常.append(f"本地播放只解出 {本地['帧数']} 帧")
    if not 本地["有帧"]:
        异常.append("本地播放没有交出画面")
    if 本地["帧尺寸"] != (320, 180):
        异常.append(f"画面尺寸不对：{本地['帧尺寸']}")
    if not 本地["截图"]:
        异常.append("截图没成功")
    if not 本地["暂停有效"]:
        异常.append("暂停期间还在解码")
    if 本地["跳转后时间"] < 2.5:
        异常.append(f"跳转没生效（{本地['跳转后时间']}s）")

    # ---- 网络（自带 Range 服务器）----
    处理器.数据 = 素材.read_bytes()
    服务器 = socketserver.ThreadingTCPServer(("127.0.0.1", 0), 处理器)
    服务器.daemon_threads = True
    端口 = 服务器.server_address[1]
    threading.Thread(target=服务器.serve_forever, daemon=True).start()
    try:
        网络 = 播一遍(f"http://127.0.0.1:{端口}/素材.mp4",
                    {"Referer": "https://pan.example.com/"}, "网络")
    finally:
        服务器.shutdown()
        服务器.server_close()
    if 网络["帧数"] < 20:
        异常.append(f"网络播放只解出 {网络['帧数']} 帧")
    if not 网络["有帧"]:
        异常.append("网络播放没有交出画面")

    报告 = 项目根 / "数据" / "真机验收.txt"
    报告.parent.mkdir(parents=True, exist_ok=True)
    结论 = "通过" if not 异常 else "失败：" + "；".join(异常)
    记(f"验收结论：{结论}")
    报告.write_text("\n".join(报告行) + "\n", encoding="utf-8")
    return 0 if not 异常 else 1


if __name__ == "__main__":
    raise SystemExit(main())
