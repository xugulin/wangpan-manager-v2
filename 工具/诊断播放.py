#!/usr/bin/env python3
"""播放诊断（Windows CI 上排查"只出 1 帧"这类问题）：把每一步都打印出来。

它回答的问题比单测更具体：**卡在哪一环**——

* 解封装线程还活着吗？队列里有没有包？
* 视频线程停在哪儿（等时钟 / 队列空 / 解码器没吐帧）？
* 主时钟走了多少？音频写进去多少样本？
* 出错了吗（引擎的日志回调会收到 ``[播放] 出错：…``）？

结论同时写进 ``数据/诊断播放.txt``（CI 里打印，避免被截断）。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(项目根))
from wangpan.控制台 import 修控制台          # noqa: E402

修控制台()

if os.name != "nt":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wangpan.player.引擎 import 播放引擎     # noqa: E402


def 打线程栈(记) -> None:
    """把每个线程**卡在哪一行**打出来（只靠计数猜不出来时，这是最快的证据）。

    为什么需要：Windows CI 上出现过"解封装已退出、视频线程存活、却只解出 1 帧"，
    光看计数完全不知道卡在哪 —— 栈一打就清楚了。
    """
    import sys as _sys
    import traceback
    帧们 = _sys._current_frames()
    for 线程 in threading.enumerate():
        帧 = 帧们.get(线程.ident)
        if 帧 is None:
            continue
        记(f"  线程 {线程.name}：")
        for 行 in traceback.format_stack(帧)[-4:]:
            记("    " + 行.strip().replace("\n", " "))

素材 = 项目根 / "工具" / "测试素材" / "样片.mp4"
行: list[str] = []


def 记(文本: str) -> None:
    print(文本, flush=True)
    行.append(文本)


def main() -> int:
    记(f"素材：{素材}（存在 {素材.is_file()}）")
    if not 素材.is_file():
        return 2
    引擎 = 播放引擎(日志回调=记)
    引擎.打开(str(素材))
    记(f"打开后：{引擎.统计.摘要()}")
    引擎.播放()
    for 轮 in range(10):
        time.sleep(0.5)
        视频队列 = 引擎._视频队列.qsize() if hasattr(引擎._视频队列, "qsize") else -1
        音频队列 = 引擎._音频队列.qsize() if hasattr(引擎._音频队列, "qsize") else -1
        线程们 = "、".join(f"{t.name}{'存活' if t.is_alive() else '已退出'}"
                        for t in 引擎._线程们)
        记(f"[{轮}] 帧 {引擎.统计.已解视频帧}（丢 {引擎.统计.已丢视频帧}）"
          f"｜音频块 {引擎.统计.已解音频块}｜时钟 {引擎.时钟.现在秒():.2f}s"
          f"｜队列 视频{视频队列}/音频{音频队列}｜{线程们}")
        if 引擎.统计.已解视频帧 >= 30:
            break
        if 轮 == 2:
            记("（到第 3 轮还没出够帧，打一次线程栈）")
            打线程栈(记)
    记(f"最终：{引擎.统计.摘要()}")
    引擎.停止()
    报告 = 项目根 / "数据" / "诊断播放.txt"
    报告.parent.mkdir(parents=True, exist_ok=True)
    报告.write_text("\n".join(行) + "\n", encoding="utf-8")
    记(f"结论：{'出画面正常' if 引擎.统计.已解视频帧 >= 30 else '出画面不足（见上面的分步信息）'}")
    return 0 if 引擎.统计.已解视频帧 >= 30 else 1


if __name__ == "__main__":
    raise SystemExit(main())
