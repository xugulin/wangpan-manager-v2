#!/usr/bin/env python3
"""①真机验收：刮削进度**真的在动**（光鸭真目录 + 真 TMDB + **真的界面那条路**）。

用户报的问题
============
截图：``0/1（0%）｜成功 0｜待确认 0｜失败 0｜用时 0.0s`` —— 关闭进度框之后媒体页里
海报和剧集信息其实都对。也就是说：**活干完了，进度没上报**。

两个根因（本脚本各验一条）
==========================
1. 分母：进度原来按"任务"（一部剧 = 一个文件夹 = 1）计数，16 集的剧永远是 0/1；
2. 分子与计数：`已完成` 只在任务收尾 +1、`成功/需要确认/失败` 从来没被更新过，
   而界面上那个 `QProgressDialog` 是 ``setRange(0, 0)``（不确定态）+ 一个
   **永远不刷新**的文案（`结果盒.setdefault("进度", …)` 只写第一次）。

这个脚本怎么验（**不是再写一份流水线**）
=====================================
真的去调界面层的 :meth:`wangpan.ui.媒体库页.媒体库页.刮削路径` —— 它内部起 QThread、
建 `QProgressDialog`、连定时器，与用户点「扫描媒体库」时**逐字同一段代码**。为了能看清
它到底画了什么，脚本做三件事（都用运行期替换，不改产品代码）：

* 把 `资料库` / `图片缓存` 换成**临时目录**的（用户的库与图片缓存一个字节都不碰）；
* 给 `刮削服务` 套一层，把**每一次进度回调**原样记下来 → 打印成序列；
* 记下真正的那个 `QProgressDialog`，每 200ms 采样一次它的
  ``minimum/maximum/value/labelText`` → 证明 ``setRange(0, 总数)`` / ``setValue(已完成)``
  真的按真实百分比走了（而不是停在 0/1）。

跑法::

    运行环境/venv/bin/python 工具/进度真机验证.py
    运行环境/venv/bin/python 工具/进度真机验证.py --目录 /来自：分享/仙逆.4K高码.SDR.60fps

退出码：``0`` 全过；``2`` 凭据/登录问题；``3`` 连不上；``5`` 断言失败。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
if str(项目根) not in sys.path:
    sys.path.insert(0, str(项目根))

# 这个脚本要在**没有显示服务**的机器上也能跑（它是靠"真的建一个 QProgressDialog"取证的），
# 所以先把 Qt 指到离屏平台：否则 xcb/wayland 插件初始化失败会直接 core dump。
# ⚠️ 不能用 setdefault：本机环境变量里预置了 `QT_QPA_PLATFORM=wayland;xcb`（没有显示服务
#    时这个值是致命的），必须**显式改掉**（tests/公用.py 里踩过同一个坑）。
if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from wangpan.控制台 import 修控制台                                        # noqa: E402

修控制台()

默认目录 = "/来自：分享/仙逆.4K高码.SDR.60fps"
默认网盘 = "guangya"
#: 采样进度框的间隔（毫秒）——比界面里的 400ms 定时器密一点，好看见每一格
采样毫秒 = 200


def 打印(文本: str = "") -> None:
    print(文本, flush=True)


class _断言失败(Exception):
    pass


def _断言(条件, 说明: str) -> None:
    if not 条件:
        raise _断言失败(说明)


# ============================ 现场保护 ============================


class 现场保护:
    """跑完必须恢复：检索缓存文件 + ``V2_识别学习库`` 环境变量 + 各种运行期替换。"""

    def __init__(self, 临时根: Path) -> None:
        self.临时根 = Path(临时根)
        self.缓存文件 = 项目根 / "数据" / "识别检索缓存.json"
        self._缓存原样: bytes | None = None
        self._学习变量原样: str | None = None
        self._还原表: list = []

    def 备份(self) -> None:
        self.临时根.mkdir(parents=True, exist_ok=True)
        if self.缓存文件.is_file():
            self._缓存原样 = self.缓存文件.read_bytes()
        self._学习变量原样 = os.environ.get("V2_识别学习库")
        # 学习库也指到临时文件：脚本跑的是"真机自动入库"，但万一路径上有人工确认，
        # 也不许写用户那份 数据/识别学习.json。
        os.environ["V2_识别学习库"] = str(self.临时根 / "识别学习.json")

    def 换掉(self, 对象, 属性: str, 新值) -> None:
        """运行期替换一个模块属性，并登记"跑完还原"。"""
        self._还原表.append((对象, 属性, getattr(对象, 属性)))
        setattr(对象, 属性, 新值)

    def 恢复(self) -> None:
        for 对象, 属性, 旧值 in reversed(self._还原表):
            try:
                setattr(对象, 属性, 旧值)
            except Exception:  # noqa: BLE001 - 还原失败不该盖掉真正的结论
                pass
        if self._学习变量原样 is None:
            os.environ.pop("V2_识别学习库", None)
        else:
            os.environ["V2_识别学习库"] = self._学习变量原样
        try:
            if self._缓存原样 is None:
                self.缓存文件.unlink(missing_ok=True)
            else:
                self.缓存文件.write_bytes(self._缓存原样)
        except OSError as 错:                                                # noqa: BLE001
            打印(f"⚠️ 检索缓存恢复失败（请手工检查 {self.缓存文件}）：{错}")


# ============================ 参数 ============================


def _取参数() -> argparse.Namespace:
    解析 = argparse.ArgumentParser(description="①真机验收：刮削进度真的在动")
    解析.add_argument("--目录", default=默认目录, help=f"光鸭目录（默认 {默认目录}）")
    解析.add_argument("--网盘", default=默认网盘, help="网盘标识（默认 guangya）")
    解析.add_argument("--保留现场", action="store_true", help="跑完不删临时目录")
    解析.add_argument("--超时秒", type=float, default=900.0, help="真机跑批的总超时")
    return 解析.parse_args()


# ============================ 主流程 ============================


def main() -> int:
    参数 = _取参数()
    临时根 = Path(tempfile.mkdtemp(prefix="v2进度真机_"))
    保护 = 现场保护(临时根)
    保护.备份()
    打印(f"临时目录：{临时根}（临时库/缓存/学习库都在这里，跑完删）")
    try:
        return _跑(参数, 临时根, 保护)
    except _断言失败 as 错:
        打印(f"\n❌ 断言失败：{错}")
        return 5
    finally:
        保护.恢复()
        if not 参数.保留现场:
            shutil.rmtree(临时根, ignore_errors=True)
            打印(f"（已清理 {临时根}；检索缓存/学习库已还原）")
        else:
            打印(f"（--保留现场：{临时根}）")


def _跑(参数, 临时根: Path, 保护: 现场保护) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QProgressDialog

    import wangpan.scrape.图片 as 图片模块
    import wangpan.scrape.库 as 库模块
    import wangpan.scrape.服务 as 服务模块
    import wangpan.scrape.扫描 as 扫描模块
    import wangpan.scrape.tmdb as tmdb模块
    import wangpan.ui.媒体库页 as 媒体库页模块
    from wangpan.pan.光鸭 import 光鸭适配器
    from wangpan.scrape.tmdb import TMDB客户端, TMDB配置

    # ---- 0) 凭据与登录（连不上就直接说清楚，别装作跑过了）----
    配置 = TMDB配置.从环境与配置()
    if not 配置.可用():
        打印("❌ 没配 TMDB 凭据（数据/刮削.json 或 V2_TMDB_TOKEN）")
        return 2
    客户端 = TMDB客户端(配置)
    打印("TMDB：" + str({k: v for k, v in 客户端.配置摘要().items()
                      if k in ("可用", "鉴权", "语言", "代理")}))
    适配器 = 光鸭适配器()
    状态 = 适配器.登录状态()
    if not 状态.get("已登录"):
        打印("❌ 光鸭没登录（先在网盘页登录/导入令牌）")
        return 2
    打印(f"光鸭：已登录（令牌剩余 {float(状态.get('过期剩余秒') or 0) / 60:.0f} 分钟）")

    # ---- 1) 真列目录 → 造远端单元（与界面同一条路）----
    try:
        条目们 = 适配器.列出(参数.目录)
    except Exception as 错:                                                  # noqa: BLE001
        打印(f"❌ 列目录失败：{type(错).__name__}: {错}")
        return 3
    文件们 = [str(x.路径) for x in 条目们 if not x.是目录]
    打印(f"\n① 列目录：{参数.目录} → {len(文件们)} 个文件")
    if not 文件们:
        打印("❌ 目录里没有文件")
        return 3
    for 名 in sorted(文件们)[:3]:
        打印(f"    {Path(名).name}")
    单元们 = 扫描模块.造远端单元(参数.网盘, 参数.目录, 文件们)
    for 单元 in 单元们:
        打印(f"    [{单元.季号}/{单元.集号}] {Path(str(单元.主视频)).name}"
            f"　标题「{单元.标题}」")

    # ---- 2) 换掉资料库/图片缓存/刮削服务（用户的库一个字节都不碰）----
    临时库 = 临时根 / "资料库.db"
    原资料库 = 库模块.资料库
    原图片缓存 = 图片模块.图片缓存

    class 临时资料库(原资料库):                                              # type: ignore[misc]
        def __init__(自己, 路径=None) -> None:
            super().__init__(路径 or 临时库)

    class 临时图片缓存(原图片缓存):                                          # type: ignore[misc]
        def __init__(自己, 根目录=None, *a, **k) -> None:
            super().__init__(根目录 or (临时根 / "图缓存"), *a, **k)

    服务回调序列: list = []
    当前阶段 = {"名": "准备"}
    原服务 = 服务模块.刮削服务

    class 记录服务(原服务):                                                  # type: ignore[misc]
        """套一层：把每一次进度回调原样记下来（服务层回调 = 界面显示的唯一数据源）。

        记下"这条回调属于哪个阶段"（登记 / 刮削）：两个阶段各有自己的分母，
        混在一起看会以为进度倒退。
        """

        def __init__(自己, *a, **k) -> None:
            原回调 = k.get("进度回调")

            def 转发(进度) -> None:
                服务回调序列.append((当前阶段["名"], 进度.快照()))
                if 原回调 is not None:
                    原回调(进度)

            k["进度回调"] = 转发
            super().__init__(*a, **k)

        def _登记阶段(自己, 方法名, *a, **k):
            当前阶段["名"] = "登记"
            try:
                return getattr(super(), 方法名)(*a, **k)
            finally:
                当前阶段["名"] = "准备"

        # 老通道（按目录归并）与新通道（③ 按文件识别）都拦：界面现在走的是后者
        def 记远端单元(自己, *a, **k):
            return 自己._登记阶段("记远端单元", *a, **k)

        def 扫库(自己, *a, **k):
            return 自己._登记阶段("扫库", *a, **k)

        def 记远端单元按文件(自己, *a, **k):
            return 自己._登记阶段("记远端单元按文件", *a, **k)

        def 扫库按文件(自己, *a, **k):
            return 自己._登记阶段("扫库按文件", *a, **k)

        def 续跑(自己, *a, **k):
            当前阶段["名"] = "刮削"
            try:
                return super().续跑(*a, **k)
            finally:
                当前阶段["名"] = "准备"

        def 刮路径(自己, *a, **k):
            当前阶段["名"] = "刮削"
            try:
                return super().刮路径(*a, **k)
            finally:
                当前阶段["名"] = "准备"

    进度框们: list = []
    框调用: list = []            # 界面**真的**对进度框做的每一次 setRange/setValue/setLabelText
    框状态 = {"起点": 10 ** 9}    # 只记"这一轮跑批"那个框（前面的不确定态框不记）
    原对话框 = QProgressDialog

    class 记录进度框(原对话框):                                              # type: ignore[misc]
        """记下界面**每一次**更新进度框的调用。

        为什么不用"每 200ms 采样一次"：真机上"识别 + 挂文件"那一段（我们要看的 1/16…
        16/16）往往只要几十毫秒，而"下图"要一两分钟 —— 定时采样只会采到最后一帧。
        直接钩住 setValue/setLabelText，拿到的是界面**逐字**画出来的序列。
        """

        def __init__(自己, *a, **k) -> None:
            super().__init__(*a, **k)
            自己.序号 = len(进度框们)
            进度框们.append(自己)

        def _记(自己, 动作: str) -> None:
            if 自己.序号 >= 框状态["起点"]:
                框调用.append((动作, 自己.maximum(), 自己.value(), 自己.labelText()))

        def setRange(自己, 最小, 最大) -> None:
            super().setRange(最小, 最大)
            自己._记("setRange")

        def setValue(自己, 值) -> None:
            super().setValue(值)
            自己._记("setValue")

        def setLabelText(自己, 文本) -> None:
            super().setLabelText(文本)
            自己._记("setLabelText")

    保护.换掉(库模块, "资料库", 临时资料库)
    保护.换掉(图片模块, "图片缓存", 临时图片缓存)
    保护.换掉(服务模块, "刮削服务", 记录服务)
    保护.换掉(媒体库页模块, "QProgressDialog", 记录进度框)

    应用 = QApplication.instance() or QApplication([])
    应用.quitOnLastWindowClosed = False              # 关窗别把事件循环带走

    # ---- 3) 真的走界面：媒体库页.刮削路径（后台线程 + 进度框 + 定时器）----
    from wangpan.ui.媒体库页 import 进度文案
    页 = 媒体库页模块.媒体库页()
    页.自动开队列 = False                            # 别弹模态框（脚本没有人在点）
    页.建TMDB客户端 = lambda: 客户端                  # 用真客户端（但不再读一次配置）
    页._提示 = lambda 文本: 打印(f"    [界面提示] {文本}")   # 信号连不连都无所谓

    采样: list = []
    框状态["起点"] = len(进度框们)                    # 从现在起记的就是这一轮的进度框
    页.刮削路径("", 完成后=lambda: None, 本地目录们=[], 远端单元们=list(单元们))
    本轮框 = [x for x in 进度框们 if x.序号 >= 框状态["起点"]]
    _断言(len(本轮框) == 1, f"这一轮应当恰好有 1 个进度框，实际 {len(本轮框)}")

    线程 = 页._刮削线程
    _断言(线程 is not None, "刮削路径没起后台线程")
    开始 = time.time()
    定时 = QTimer()
    定时.setInterval(采样毫秒)

    def 采一次() -> None:
        """另外再定时采一眼（证明框真的还开着、值真的停在真实百分比上）。"""
        框 = 本轮框[-1]
        if 框.maximum() > 0:
            采样.append((框.maximum(), 框.value(), 框.labelText()))
        应用.processEvents()

    定时.timeout.connect(采一次)
    定时.start()
    while 线程.isRunning() and (time.time() - 开始) < float(参数.超时秒):
        应用.processEvents()
        time.sleep(0.02)
    定时.stop()
    应用.processEvents()
    if 线程.isRunning():
        打印("❌ 真机跑批超时（网络太慢？）")
        return 3

    # ---- 4) 打印结果：服务回调序列 + 进度框调用序列 ----
    打印(f"\n② 服务层进度回调序列（共 {len(服务回调序列)} 次）")
    上一条 = ("", "")
    for 阶段, 快照 in 服务回调序列:
        文案 = 进度文案(快照)
        if (阶段, 文案) != 上一条:
            打印(f"    [{阶段}] {文案}")
            上一条 = (阶段, 文案)

    打印(f"\n③ 界面真的画出来的进度框序列（setValue/setLabelText，共 {len(框调用)} 次）")
    上一条 = None
    for 动作, 最大, 值, 标签 in 框调用:
        if (最大, 值, 标签) != 上一条:
            打印(f"    {动作}→ setRange(0, {最大})｜value={值}｜{标签}")
            上一条 = (最大, 值, 标签)

    打印(f"\n④ 运行期定时采样（每 {采样毫秒}ms 一次，共 {len(采样)} 次；"
        f"去重后 {len({x for x in 采样})} 种状态）")
    for 状态 in sorted({x for x in 采样}):
        打印(f"    setRange(0, {状态[0]})｜value={状态[1]}｜{状态[2]}")

    # ---- 5) 断言：序列必须 1/16 … 16/16 递增，进度框必须真的按真实百分比走 ----
    剧集数 = len(文件们)
    _断言(bool(服务回调序列), "服务层一次进度回调都没有（界面必然是死的）")
    登记段 = [x for 阶段, x in 服务回调序列 if 阶段 == "登记"]
    刮削段 = [x for 阶段, x in 服务回调序列 if 阶段 == "刮削"]
    _断言(bool(登记段), "登记阶段没有进度回调（扫库那几十秒里用户看到的就是死的 0/0）")
    _断言(bool(刮削段), "刮削阶段没有进度回调")
    _断言({x.总数 for x in 登记段} == {剧集数},
         f"登记阶段的分母应当是真实待处理条数 {剧集数}："
         f"收到 { {x.总数 for x in 登记段} }")
    总数们 = {x.总数 for x in 刮削段}
    _断言(总数们 == {剧集数},
         f"刮削阶段的分母应当是真实视频数 {剧集数}（而不是任务数 1）：收到 {总数们}")
    已完成们 = [x.已完成 for x in 刮削段]
    _断言(已完成们 == sorted(已完成们), f"进度不许倒退：{已完成们}")
    _断言(已完成们[-1] == 剧集数, f"跑完必须走满：{已完成们}")
    _断言(sorted(set(已完成们)) == list(range(0, 剧集数 + 1)),
         f"应当逐集推进 1/{剧集数}…{剧集数}/{剧集数}：{已完成们}")
    最后 = 刮削段[-1]
    _断言(最后.成功 >= 1, f"至少要有一条成功入库（否则不是「进度没上报」而是真失败）：{最后.摘要()}")
    _断言(最后.失败 == 0, f"不该有失败：{最后.摘要()}")

    _断言(bool(框调用), "进度框一次都没被更新（setRange/setValue 没走？）")
    框最大们 = {x[1] for x in 框调用}
    _断言(框最大们 == {剧集数},
         f"进度框的 setRange 上界必须是真实总数 {剧集数}：收到 {框最大们}")
    _断言(采样 and 采样[-1][1] == 剧集数,
         f"进度框最后应当停在 100%（值={剧集数}）：{采样[-1] if 采样 else '一次都没采到'}")
    值们 = [x[2] for x in 框调用 if x[0] == "setValue"]
    _断言(sorted(set(值们)) == list(range(0, 剧集数 + 1)),
         f"进度框必须逐格走过 0…{剧集数}（而不是停在 0/1）：{sorted(set(值们))}")
    刮削标签 = [x[3] for x in 框调用 if "正在登记" not in x[3] and "已处理" in x[3]]
    _断言(bool(刮削标签), "没有看到「正在刮削入库」阶段的进度文案")
    _断言(any("已处理 1/" in x for x in 刮削标签),
         f"进度文案必须出现「已处理 1/…」（第一格就要可见）：{刮削标签[:3]}")
    _断言(all("成功" in x and "待确认" in x and "失败" in x for x in 刮削标签),
         f"文案必须带 已处理 X/Y｜成功 a｜待确认 b｜失败 c：{刮削标签[:3]}")

    打印("\n" + "=" * 78)
    打印(f"✅ ①真机验收通过：{剧集数} 集 · 进度逐集递增到 {剧集数}/{剧集数}"
        f"｜进度框走了真实百分比｜成功 {最后.成功}｜待确认 {最后.需要确认}｜失败 {最后.失败}")
    打印("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
