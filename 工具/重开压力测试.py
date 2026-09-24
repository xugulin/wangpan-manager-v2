#!/usr/bin/env python3
"""重开压力测试：把"播放中途重开"和**其它并发动作**压在一起，专门打进程 abort。

为什么要有这个脚本（真机 coredump 的硬证据）
============================================
用户真机三次崩溃里有两次是 **SIGABRT / 崩溃线程 `V2-解封装` / 栈在 libavcodec 内部**：

* 22:04:08 那次紧跟着 `[AI] 换了起播参数（… 从 63.4s 重开）`；
* 18:24 同样是 SIGABRT / `V2-解封装` / libavcodec；
* 17:50 是 SIGSEGV / `V2-缩略图` / `avcodec_send_packet`。

光"本地文件 + 连着重开 5 次"复现不出来 —— 因为**重开本身不是全部**，
真机上是"重开 + 暂停/继续 + 拖进度 + 独立窗口搬进搬出 + 弹幕在跑"叠在一起。
这个脚本就是把这几件事**按真机的时间关系交织起来**，跑够轮次：

* 阶段 A「引擎直开」：``引擎.打开`` → 播放 → （界面线程持续 暂停/继续、跳转、倍速、改输出尺寸）
  → 2~3 秒后重开下一轮。这是最接近"播放中途重开"的那条路。
* 阶段 B「会话 AI 重开」：走真机的 AI 路径 —— 后台线程（真机上叫 `播放诊断`）
  调 ``会话.应用新参数(..., 自动重载=True)``，内部会 ``起播 → 引擎.打开`` 并从当前位置跳回去。
* 阶段 C「独立窗口搬进搬出」（有 Qt 时）：真机上 22:04:04 刚开过独立窗口，
  4 秒后就崩了 —— 窗口搬动会让播放页/视频控件重建、触发 resize → ``设置输出尺寸``。

用法（真机）::

    # 最贴近真机的一条：真网盘 4K60 HEVC + 硬解 + 界面压迫 + AI 重开，25 轮
    QT_QPA_PLATFORM=offscreen 运行环境/venv/bin/python 工具/重开压力测试.py --轮次 25

    # 只压纯内核（不建 Qt，SSH 上也能跑）
    运行环境/venv/bin/python 工具/重开压力测试.py --轮次 30 --无界面

    # 竞态放大器：把"拖进度条/切倍速"的调用频率提高 10 倍
    运行环境/venv/bin/python 工具/重开压力测试.py --轮次 10 --压迫跳转 --压迫倍速

崩溃的判定：**进程非零退出**（abort → 退出码 -6 / 134，segv → -11 / 139）。
脚本每轮都 flush 打印轮次与统计，所以崩了以后看最后一行就知道是第几轮、什么状态。
另外还会注册 faulthandler，把崩溃瞬间的 Python 栈写进
``数据/重开压力_崩溃栈.txt``（只有 Python 侧，但能看住"崩的时候谁在调谁"）。
"""

from __future__ import annotations

import argparse
import faulthandler
import os
import random
import signal
import sys
import threading
import time
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(项目根))

#: 真机上崩的那一部（22:02:34 的日志里就是这个远端路径）
默认远端 = "来自：分享/仙逆.4K高码.SDR.60fps/131.SDR.8bit.2160p.60fps.AAC2.0.WEB-DL.H265.mp4"
默认网盘 = "guangya"
崩溃栈文件 = 项目根 / "数据" / "重开压力_崩溃栈.txt"


def 记(文本: str = "") -> None:
    """带 flush 的打印（崩了以后 stdout 不能丢，否则不知道跑到第几轮）。"""
    print(文本, flush=True)


# ============================================================================
# 界面线程：模拟"用户在播放中乱动"（真机上是 GUI 主线程）
# ============================================================================

class 界面压迫(threading.Thread):
    """模拟 GUI 主线程在播放期间的并发动作。

    为什么必须有它：真机崩在"重开 + 暂停/继续 + 拖进度 + 切窗口"叠在一起的时候。
    单线程顺序地 打开→跳转→停止 是很难崩的，因为每一步之间都有 join 把它串起来。
    这个线程就是那**另一只手**：它按固定间隔去碰引擎的公共入口（跟真机 GUI 一样），
    于是"老线程正在用解码器"和"新线程/界面线程在动同一个解码器"就有了重叠窗口。

    ⚠️ 它调用的全是界面上真实存在的操作：``暂停切换``/``跳转``/``设置倍速``/
    ``设置输出尺寸``（进度条拖动、空格暂停、倍速框、窗口 resize 各对应一个）。
    """

    def __init__(self, 会话, *, 间隔: float = 0.05, 压迫跳转: bool = False,
                 压迫倍速: bool = False, 压迫暂停: bool = True,
                 压迫尺寸: bool = True, 跳转间隔秒: float = 0.8) -> None:
        super().__init__(name="压力-GUI", daemon=True)
        self.会话 = 会话
        self.间隔 = float(间隔)
        self.压迫跳转 = 压迫跳转
        self.压迫倍速 = 压迫倍速
        self.压迫暂停 = 压迫暂停
        self.压迫尺寸 = 压迫尺寸
        #: 两次"拖进度条"之间至少隔多久。为什么要这个（而不是每轮都跳）：
        #: 跳得太密（每次 0.05s）会让解封装线程永远在 seek、解码器永远解不出帧 ——
        #: 那样"重开路径"根本没被真正压到（统计里 已解视频帧 = 0 就是证据）。
        #: 默认 0.8s：一秒钟拖一次已经比真人快得多，同时留出真解码的窗口。
        self.跳转间隔秒 = float(跳转间隔秒)
        self.跑 = threading.Event()
        self.跳转次数 = 0
        self.暂停次数 = 0
        self.倍速次数 = 0
        self.尺寸次数 = 0
        self.错误 = ""
        self._倍速档 = (1.0, 1.25, 1.0, 0.5, 1.0, 2.0)

    def 停(self) -> None:
        self.跑.clear()
        self.join(timeout=2.0)

    def run(self) -> None:
        self.跑.set()
        轮 = 0
        上次跳转 = 0.0
        暂停到点 = 0.0
        while self.跑.is_set():
            轮 += 1
            现在 = time.monotonic()
            try:
                # 暂停/继续：按下去 0.25s 再按回来（真人是"空格暂停、想通了再空格继续"）。
                # 为什么不做成"一直暂停"：那样视频线程根本不送包，
                # 也就压不到"跳转冲刷 vs 送包"这条真机上崩掉的路径。
                if self.压迫暂停 and 现在 >= 暂停到点 > 0:
                    self.会话.暂停切换()
                    self.暂停次数 += 1
                    暂停到点 = 0.0
                elif (self.压迫暂停 and 暂停到点 == 0.0 and 轮 % 10 == 0):
                    self.会话.暂停切换()
                    self.暂停次数 += 1
                    暂停到点 = 现在 + 0.25
                if self.压迫跳转 and 现在 - 上次跳转 >= self.跳转间隔秒:
                    # 真机上是拖进度条（0.5~0.9 位置的随机跳）
                    总 = float(self.会话.引擎.统计.总时长秒 or 0.0) or 1200.0
                    self.会话.跳转(random.uniform(1.0, max(2.0, 总 * 0.2)))
                    self.跳转次数 += 1
                    上次跳转 = 现在
                if self.压迫倍速 and 轮 % 8 == 0:
                    self.会话.设置速率(self._倍速档[轮 // 8 % len(self._倍速档)])
                    self.倍速次数 += 1
                if self.压迫尺寸 and 轮 % 6 == 0:
                    # 真机上是窗口 resize / 切侧栏 / 独立窗口搬动
                    self.会话.引擎.设置输出尺寸(1280 + (轮 % 5) * 160,
                                              720 + (轮 % 3) * 90)
                    self.尺寸次数 += 1
            except Exception as 错:  # noqa: BLE001 - 压迫期间的异常不该中断压测
                self.错误 = f"{type(错).__name__}: {错}"
            time.sleep(self.间隔)


# ============================================================================
# AI 重开线程：模拟真机的 `播放诊断` 后台线程
# ============================================================================

class AI重开线程(threading.Thread):
    """后台线程调 ``应用新参数(自动重载=True)`` —— 真机日志里就是它把播放重开的。

    真机证据：``[22:04:08] [AI] 换了起播参数（… 从 63.4s 重开）`` 紧跟着
    ``[播放] 已打开：…``，而这句日志是 `播放会话.应用新参数` 打的，
    它跑在 `播放页面._该自动诊断了吗` 起的 `播放诊断` 线程上。
    """

    def __init__(self, 会话, 轮次: int, 缓存档: tuple[int, int] = (3000, 9000)) -> None:
        super().__init__(name="压力-AI重开", daemon=True)
        self.会话 = 会话
        self.轮次 = int(轮次)
        self.缓存档 = 缓存档
        self.结果: list[str] = []

    def run(self) -> None:
        for i in range(self.轮次):
            缓存 = self.缓存档[i % len(self.缓存档)]
            try:
                self.会话.应用新参数({
                    "网络缓存毫秒": 缓存,
                    "来源": "压力测试",
                    "理由": f"第 {i + 1} 次换缓存（模拟 AI 反复改参数）",
                }, 自动重载=True)
            except Exception as 错:  # noqa: BLE001
                self.结果.append(f"第 {i + 1} 次重开抛异常：{type(错).__name__}: {错}")
            time.sleep(0.4)


# ============================================================================
# 直链 / 会话装配
# ============================================================================

def 取直链(网盘: str, 远端: str) -> tuple[str, dict, int, str]:
    """用真网盘适配器取直链（和界面上点播放走的是同一条路）。

    ``v8_3.配置.动作`` 持有适配器（子进程桥）；``适配器(标识).取播放直链``
    就是 `播放会话._取直链` 内部调的那个方法。
    """
    from v8_3.配置 import 动作
    动作对象 = 动作(日志回调=lambda 消息, 级别="信息": 记(f"[适配器] {消息}"))
    适配器 = 动作对象.适配器(网盘)
    信息 = dict(适配器.取播放直链(远端) or {})
    地址 = str(信息.get("url") or "")
    if not 地址:
        raise RuntimeError(f"{网盘} 没给出直链：{信息}")
    return (地址, dict(信息.get("headers") or {}),
            int(信息.get("size") or 0), str(信息.get("name") or Path(远端).name))


def 读直链文件(路径: str) -> tuple[str, dict]:
    """读"外部给的直链"文件：第 1 行是 url，其余行是 ``Name: Value`` 请求头。

    为什么要支持这个：做**修复前/修复后 A/B 对比**时两次必须用同一条链，
    否则"崩没崩"的差别可能来自链不一样；而且修复前的代码树里没有适配器凭证。
    """
    行 = [x.strip() for x in Path(路径).read_text(encoding="utf-8").splitlines()]
    行 = [x for x in 行 if x]
    if not 行:
        raise RuntimeError(f"直链文件是空的：{路径}")
    头们 = {}
    for 项 in 行[1:]:
        if ":" in 项:
            名, 值 = 项.split(":", 1)
            头们[名.strip()] = 值.strip()
    return 行[0], 头们


def 装会话(地址: str, 头们: dict, 大小: int, 名字: str, *,           远端: str, 网盘: str, 日志回调) -> "object":
    """按真机"准备完成"之后的状态装配一个播放会话。

    为什么要手工填 ``媒体/探测/直链信息``：``播放会话.准备()`` 会联网探测 + 问 AI，
    压测里不需要（而且慢）。这里填的是真机日志里那份**同量级**的数据
    （3840x1632 HEVC 60fps ≈19.1Mbps、时长 1297s），
    这样 ``起播`` 换算出来的缓冲字节、硬解选择跟真机一致。
    """
    from wangpan.ui.播放会话 import 播放会话, 媒体信息, 探测结果, 规则参数
    会话 = 播放会话(日志回调=日志回调, 自动调优=True)
    会话.网盘标识 = 网盘
    会话.远端路径 = 远端
    会话.标题 = 名字
    会话.直链信息 = {"url": 地址, "headers": dict(头们 or {}),
                  "name": 名字, "size": int(大小 or 0)}
    会话.媒体 = 媒体信息(
        已知=True, 分辨率="3840x1632", 宽=3840, 高=1632, 档位="4K",
        视频编码="hevc", 视频码率bps=19.1e6, 音频编码="aac 48000Hz 2ch",
        时长秒=1297.0, 容器="mp4", 音轨数=1, 帧率=59.9, 总码率bps=19.1e6)
    会话.探测 = 探测结果(成功=True, 来源说明="压力测试（直链已取到）",
                     首字节毫秒=395.0, 实测带宽bps=63.0e6, range支持=True)
    会话.设置 = 规则参数(会话.媒体, 会话.探测)
    会话.AI决策状态 = "压力测试：固定参数"
    return 会话


# ============================================================================
# 三个阶段
# ============================================================================

def 阶段A_引擎直开(会话, 地址: str, 头们: dict, 轮次: int, 缓冲字节: int,
                压迫参数: dict, 每轮秒: float) -> None:
    """引擎级：``打开 → 播放 →（界面线程压迫）→ 重开``，重复 N 轮。"""
    引擎 = 会话.引擎
    记(f"\n===== 阶段 A：引擎直开 ×{轮次}（每轮约 {每轮秒:.1f}s）=====")
    空转轮 = 0
    for 轮 in range(1, 轮次 + 1):
        压迫 = 界面压迫(会话, **压迫参数)
        压迫.start()
        开始 = time.monotonic()
        帧前 = int(引擎.统计.已解视频帧)
        try:
            引擎.打开(地址, 请求头=dict(头们 or {}), 缓冲字节=缓冲字节)
            引擎.播放()
        except Exception as 错:  # noqa: BLE001
            记(f"[A{轮}] 打开失败：{type(错).__name__}: {错}")
        # ⚠️ 这一跳是**复现的关键**：真机 `应用新参数` 重开之后紧跟一次
        #    `跳转(位置)`（日志："换了起播参数…，从 63.4s 重开" → "已打开"），
        #    而跳到解封装线程里做冲刷，正好和解码线程的送包撞在一起。
        time.sleep(0.4)
        总 = float(引擎.统计.总时长秒 or 0.0)
        if 总 > 5.0:
            引擎.跳转(random.uniform(2.0, min(总 * 0.2, 120.0)))
        截止 = time.monotonic() + 每轮秒
        while time.monotonic() < 截止:
            time.sleep(0.25)
        统计 = 引擎.统计
        本轮帧 = int(统计.已解视频帧) - 帧前
        if 本轮帧 <= 0:
            空转轮 += 1
        线程们 = "、".join(t.name for t in 引擎._线程们 if t.is_alive())
        记(f"[A{轮}/{轮次}] {统计.摘要()}｜线程[{线程们 or '无'}]"
          f"｜本轮解出 {本轮帧} 帧"
          f"｜压迫 跳转{压迫.跳转次数}/暂停{压迫.暂停次数}/倍速{压迫.倍速次数}"
          f"/尺寸{压迫.尺寸次数}｜{time.monotonic() - 开始:.1f}s"
          + (f"｜压迫异常 {压迫.错误}" if 压迫.错误 else "")
          + ("　⚠️ 本轮一帧都没解出来：压迫太密，重开/冲刷这条路径没被真正压到"
             if 本轮帧 <= 0 else ""))
        压迫.停()
        # 重开前必须真的停干净（这正是修复后的引擎承诺：停止后线程数为 0）
        引擎.停止()
        活 = [t.name for t in threading.enumerate() if t.name.startswith("V2-")]
        记(f"[A{轮}] 停止后 V2 线程：{活 or '无'}")
        if 活:
            记(f"❌ [A{轮}] 停止后还有线程活着：{活}（这就是崩溃的温床）")
    记(f"[A] 有效轮次 {轮次 - 空转轮}/{轮次}"
      + (f"（有 {空转轮} 轮没解出帧，结论要打折看）" if 空转轮 else "（每轮都真解码了）"))


def 阶段B_会话AI重开(会话, 轮次: int, 压迫参数: dict, 每轮秒: float) -> None:
    """会话级：走真机 AI 路径 —— 后台线程反复"换参数重开"。"""
    记(f"\n===== 阶段 B：会话 AI 重开 ×{轮次} =====")
    压迫 = 界面压迫(会话, **压迫参数)
    压迫.start()
    for 轮 in range(1, 轮次 + 1):
        线程 = AI重开线程(会话, 2, 缓存档=(3000, 9000))
        线程.start()
        time.sleep(每轮秒)
        线程.join(timeout=30.0)
        统计 = 会话.引擎.统计
        线程们 = "、".join(t.name for t in 会话.引擎._线程们 if t.is_alive())
        记(f"[B{轮}/{轮次}] {会话.状态文本()}｜{统计.摘要()}｜线程[{线程们 or '无'}]"
          f"｜压迫 跳转{压迫.跳转次数}/暂停{压迫.暂停次数}"
          + (f"｜AI 异常 {线程.结果}" if 线程.结果 else ""))
    压迫.停()
    会话.引擎.停止()
    活 = [t.name for t in threading.enumerate() if t.name.startswith("V2-")]
    记(f"[B] 停止后 V2 线程：{活 or '无'}")


def 阶段C_独立窗口(会话, 轮次: int, 压迫参数: dict, 每轮秒: float) -> None:
    """界面级：真机 22:04:04 刚开过独立窗口 —— 把"搬窗口"也压进来。"""
    记(f"\n===== 阶段 C：独立窗口搬进搬出 ×{轮次} =====")
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from wangpan.ui.播放页 import 播放页 as 内核播放页
        from v8_3.界面.播放器窗口 import 播放器窗口
        Qt应用 = QApplication.instance() or QApplication([])
    except Exception as 错:  # noqa: BLE001
        记(f"[C] 没建起 Qt（跳过这一步，不改其它阶段的结论）：{type(错).__name__}: {错}")
        return

    内核页 = 内核播放页(会话, 日志回调=lambda 文本: None)
    压迫 = 界面压迫(会话, **压迫参数)
    压迫.start()
    for 轮 in range(1, 轮次 + 1):
        try:
            会话.起播(0)
        except Exception as 错:  # noqa: BLE001
            记(f"[C{轮}] 起播失败：{type(错).__name__}: {错}")
        窗口 = None
        try:
            窗口 = 播放器窗口(会话, 会话.标题 or "压力测试",
                          日志回调=lambda 文本: None, 内核页=内核页)
            窗口.show()
            记(f"[C{轮}/{轮次}] 已打开独立窗口（内核页被 setParent 搬走）")
        except Exception as 错:  # noqa: BLE001
            记(f"[C{轮}] 独立窗口打不开：{type(错).__name__}: {错}")
        for _ in range(int(max(1.0, 每轮秒) * 10)):
            Qt应用.processEvents()
            time.sleep(0.1)
        if 窗口 is not None:
            try:
                窗口.关闭()
            except Exception:  # noqa: BLE001
                pass
        Qt应用.processEvents()
        统计 = 会话.引擎.统计
        记(f"[C{轮}] 收回后 {统计.摘要()}")
        线程们 = [t.name for t in 会话.引擎._线程们 if t.is_alive()]
        记(f"[C{轮}] 存活 V2 线程：{线程们 or '无'}")
    压迫.停()
    会话.引擎.停止()
    try:
        内核页.关闭()
    except Exception:  # noqa: BLE001
        pass


# ============================================================================

def main() -> int:
    解析 = argparse.ArgumentParser(description="播放中途重开的崩溃压力测试")
    解析.add_argument("--轮次", type=int, default=20, help="每个阶段的轮次（默认 20）")
    解析.add_argument("--网盘", default=默认网盘, help="网盘标识（默认 guangya）")
    解析.add_argument("--远端", default=默认远端, help="网盘上的远端路径")
    解析.add_argument("--素材", default="", help="改用本地文件压测（给了就不联网）")
    解析.add_argument("--直链", default="",
                    help="直接给直链（跳过适配器取链；做 A/B 对比时保证两次用**同一条链**）")
    解析.add_argument("--直链文件", default="",
                    help="从文件读直链（第 1 行 url，其余行 'Name: Value' 请求头）")
    解析.add_argument("--每轮秒", type=float, default=2.5,
                    help="每轮播放多久再重开（真机日志是 2~4s，默认 2.5）")
    解析.add_argument("--压迫间隔", type=float, default=0.05, help="界面压迫的间隔（秒）")
    解析.add_argument("--压迫跳转", action="store_true", help="界面线程持续拖进度（放大）")
    解析.add_argument("--跳转间隔秒", type=float, default=0.8,
                    help="两次拖动之间至少隔多久（默认 0.8s；太密会变成'一帧都不解'的空转）")
    解析.add_argument("--压迫倍速", action="store_true", help="界面线程持续切倍速（放大）")
    解析.add_argument("--无界面", action="store_true", help="跳过阶段 C（不建 Qt）")
    解析.add_argument("--只界面", action="store_true", help="只跑阶段 C")
    解析.add_argument("--阶段A", type=int, default=-1, help="覆盖阶段 A 轮次")
    解析.add_argument("--阶段B", type=int, default=-1, help="覆盖阶段 B 轮次")
    解析.add_argument("--阶段C", type=int, default=-1, help="覆盖阶段 C 轮次")
    参数 = 解析.parse_args()

    if not 参数.无界面:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    # 崩溃瞬间的 Python 栈（abort 时 faulthandler 能抢在进程消失前写下来）
    崩溃栈文件.parent.mkdir(parents=True, exist_ok=True)
    句柄 = open(崩溃栈文件, "w", encoding="utf-8")
    faulthandler.enable(file=句柄, all_threads=True)
    for 信号 in ("SIGABRT", "SIGSEGV", "SIGBUS", "SIGFPE"):
        号码 = getattr(signal, 信号, None)
        if 号码 is not None:
            try:
                faulthandler.register(号码, file=句柄, all_threads=True, chain=True)
            except Exception:  # noqa: BLE001
                pass

    日志行: list[str] = []

    def 日志(文本: str) -> None:
        日志行.append(str(文本))
        if len(日志行) > 400:
            del 日志行[:200]
        记(f"    · {文本}")

    记(f"项目根：{项目根}")
    记(f"Python：{sys.executable}")
    记(f"崩溃栈会写进：{崩溃栈文件}")

    本地 = str(参数.素材 or "")
    if 本地:
        地址, 头们, 大小, 名字 = 本地, {}, int(Path(本地).stat().st_size), Path(本地).name
        网盘 = "本地"
        远端 = 本地
        记(f"素材（本地文件）：{本地}")
    elif 参数.直链 or 参数.直链文件:
        # 直接给链：A/B 对比（修复前 vs 修复后）要用**同一条链**，否则不算对比
        地址, 头们 = 读直链文件(参数.直链文件) if 参数.直链文件 else (参数.直链, {})
        网盘, 远端 = 参数.网盘, 参数.远端
        名字 = Path(远端).name or "远端.mp4"
        大小 = 0
        记(f"直链（外部给）：{地址.split('?')[0][-60:]}｜带 {len(头们)} 个头")
    else:
        网盘, 远端 = 参数.网盘, 参数.远端
        记(f"取直链：{网盘}：{远端}")
        地址, 头们, 大小, 名字 = 取直链(网盘, 远端)
        记(f"直链拿到：{名字}｜{大小 / 1e6:.1f}MB｜带 {len(头们)} 个头"
          f"｜{地址.split('?')[0][-60:]}")

    会话 = 装会话(地址, 头们, 大小, 名字, 远端=远端, 网盘=网盘, 日志回调=日志)
    缓冲字节 = 会话._缓存毫秒转字节(会话.设置.网络缓存毫秒)
    记(f"起播参数：{会话.设置.摘要()}｜缓冲 {缓冲字节 / 1e6:.1f}MB")

    压迫参数 = dict(间隔=参数.压迫间隔, 压迫跳转=参数.压迫跳转,
                  压迫倍速=参数.压迫倍速, 压迫暂停=True, 压迫尺寸=True,
                  跳转间隔秒=参数.跳转间隔秒)
    记(f"压迫动作：{压迫参数}")

    A = 参数.阶段A if 参数.阶段A >= 0 else 参数.轮次
    B = 参数.阶段B if 参数.阶段B >= 0 else max(1, 参数.轮次 // 2)
    C = 参数.阶段C if 参数.阶段C >= 0 else max(1, 参数.轮次 // 4)
    if 参数.只界面:
        A = B = 0

    if A > 0:
        阶段A_引擎直开(会话, 地址, 头们, A, 缓冲字节, 压迫参数, 参数.每轮秒)
    if B > 0:
        阶段B_会话AI重开(会话, B, 压迫参数, 参数.每轮秒 + 1.0)
    if C > 0 and not 参数.无界面:
        阶段C_独立窗口(会话, C, 压迫参数, 参数.每轮秒)

    # ---- 收尾：还要能干净停止（泄漏型 bug 也会在这里露出来）----
    会话.引擎.停止()
    time.sleep(0.5)
    活 = [t.name for t in threading.enumerate() if t.name.startswith("V2-")]
    if 活:
        记(f"❌ 全程结束后还有 V2 线程：{活}")
    else:
        记("✅ 全程结束：没有残留的 V2 线程")
    记(f"\n✅ 压测跑完没崩（存活线程：{[t.name for t in threading.enumerate()]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
