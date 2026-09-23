#!/usr/bin/env python3
"""真机验证：待确认队列（**真实 GUI + 真 HTTP**）。

它在真机上跑完整条链路，并留下截图与文字证据：

1. 在环回口起一个假 TMDB（``tests/假TMDB服务.py``：真 HTTP、真 urllib、真鉴权头、
   真的图片 CDN 路径），把 ``V2_TMDB_API_BASE`` 指过去 —— 于是"网络对面"是可控的，
   而**客户端本身一行没改**（走的是它平时那条 urllib 通路）；
2. 造一个"注定拿不准"的媒体目录（三部片名极像的《沙丘》），用 GUI 里的刮削流程扫它；
3. 刮完自动弹出**待确认队列**，截图 → 选中第二个候选 → 点「采纳」；
4. 采纳会按人选中的 TMDB id 取详情、下图、落库；队列随后清空；
5. 回到海报墙截图（新条目 + 真下下来的海报），并把库里的证据打出来。

用法::

    DISPLAY=:0 WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1001 \\
      QT_QPA_PLATFORM=xcb 运行环境/venv/bin/python 工具/真机_待确认队列.py

跑之前会备份 ``数据/资料库.db``，跑完自动还原（验证不该污染用户的库；
图片缓存里的图留着无所谓，它们只是缓存）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(项目根))
from wangpan.控制台 import 修控制台          # noqa: E402

修控制台()

from tests.假TMDB服务 import 假TMDB服务      # noqa: E402


def 造媒体目录(根: Path) -> Path:
    """造三部片名极像的电影（每个一个文件夹 + 一个够大的文件）。"""
    媒体 = 根 / "媒体"
    for 名字 in ("沙丘 (2021)", "沙丘 2021 (2021)", "沙丘之子 (2003)"):
        目录 = 媒体 / 名字
        目录.mkdir(parents=True, exist_ok=True)
        (目录 / f"{名字}.1080p.mkv").write_bytes(b"V" * (21 * 1024 * 1024))
    return 媒体


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from wangpan.scrape.库 import 资料库

    解析 = argparse.ArgumentParser()
    解析.add_argument("--截图目录", default="/tmp")
    解析.add_argument("--超时秒", type=float, default=90.0)
    参数 = 解析.parse_args()

    临时根 = Path("/tmp/真机_待确认队列")
    if 临时根.exists():
        shutil.rmtree(临时根, ignore_errors=True)
    临时根.mkdir(parents=True, exist_ok=True)
    媒体 = 造媒体目录(临时根)

    假TMDB = 假TMDB服务(临时根 / "图床")
    假TMDB.造图()
    假TMDB.启动()
    print(f"假 TMDB 已在环回口起来：{假TMDB.地址}（真 HTTP，客户端照常走 urllib）")
    os.environ["V2_TMDB_TOKEN"] = "REAL-MACHINE-verify-token"
    os.environ["V2_TMDB_API_BASE"] = 假TMDB.接口基地址
    os.environ.pop("V2_TMDB_API_KEY", None)

    # 备份真实资料库（验证完还原，不污染用户的数据）
    库路径 = 项目根 / "数据" / "资料库.db"
    备份 = 临时根 / "资料库.db.备份"
    if 库路径.is_file():
        shutil.copy2(库路径, 备份)

    应用 = QApplication.instance() or QApplication([])
    from wangpan.ui.主窗口 import 主窗口
    窗口 = 主窗口()
    窗口.resize(1440, 900)
    窗口.show()
    窗口.raise_()
    for _ in range(20):
        应用.processEvents()
        time.sleep(0.01)

    证据: list[str] = []
    状态 = {"阶段": "开始", "已截图队列": False, "已截图海报墙": False}
    开始 = time.monotonic()

    def 记(文本: str) -> None:
        证据.append(文本)
        print("・" + 文本)

    def 截图屏幕(名字: str):
        """整屏截图（模态对话框是独立窗口，只 grab 主窗口是拍不到它的）。"""
        目标 = Path(参数.截图目录) / 名字
        屏幕 = 应用.primaryScreen()
        图 = 屏幕.grabWindow(0) if 屏幕 is not None else 窗口.grab()
        图.save(str(目标))
        return 目标

    def 截图控件(控件, 名字: str):
        目标 = Path(参数.截图目录) / 名字
        控件.grab().save(str(目标))
        return 目标

    def 看模态():
        控件 = 应用.activeModalWidget()
        return 控件

    def 观察():
        if time.monotonic() - 开始 > 参数.超时秒:
            记("⚠ 超时，结束")
            应用.quit()
            return
        控件 = 看模态()
        # ---- 1) 待确认队列弹出来了：截图 + 采纳第二个候选 ----
        from wangpan.ui.待确认 import 待确认对话框
        if isinstance(控件, 待确认对话框) and not 状态["已截图队列"]:
            状态["已截图队列"] = True
            窗口.raise_()
            应用.processEvents()
            图 = 截图屏幕("真机_待确认队列.png")
            图0 = 截图控件(控件, "真机_待确认队列_对话框.png")
            记(f"待确认队列截图（整屏）：{图}｜对话框：{图0}｜共 {控件.条数()} 条")
            for 行 in range(控件.列表.count()):
                项 = 控件._项们[行]
                记(f"  队列[{行}] {项.文件名}｜{项.说明}")
                for 候选 in 项.候选们:
                    记(f"     候选 {候选.可读()}")
            控件.列表.setCurrentRow(0)
            应用.processEvents()
            控件.候选表.setCurrentCell(1, 0)          # 选第二个候选（202）
            应用.processEvents()
            图2 = 截图控件(控件, "真机_待确认队列_选中候选.png")
            选中 = 控件.选中候选()
            记(f"选中候选截图：{图2}｜将采纳 tmdb:{选中.来源标识}《{选中.标题}》")
            状态["阶段"] = "采纳中"
            控件.采纳()                                # 关窗 → 主窗口按这个 id 刮
            return
        # ---- 2) 采纳完成（库里出现条目）→ 看海报墙 ----
        if 状态["阶段"] == "采纳中" and not 状态["已截图海报墙"]:
            库 = 资料库()
            try:
                行们 = 库.全部("SELECT id, 标题, 年份, tmdb_id, 海报 FROM 媒体")
                有图 = any(行["海报"] for 行 in 行们)
            finally:
                库.关闭()
            if 行们 and 有图:
                状态["已截图海报墙"] = True
                状态["阶段"] = "完事"
                窗口.标签.setCurrentIndex(2)           # 媒体库
                if 窗口.海报墙 is not None:
                    窗口.海报墙.刷新()
                    窗口.海报墙.等待图片(4000, 至少几张=1)
                for _ in range(30):
                    应用.processEvents()
                    time.sleep(0.02)
                图3 = 截图控件(窗口, "真机_待确认队列_采纳后海报墙.png")
                记(f"海报墙截图：{图3}")
                记(f"海报墙状态行：{窗口.海报墙.状态行文本() if 窗口.海报墙 else '（无）'}")
                尾部库 = 资料库()
                try:
                    for 行 in 行们:
                        条目 = 尾部库.取媒体(int(行["id"]))
                        海报 = 条目.海报
                        记(f"  库里：id={行['id']}｜{行['标题']}｜{行['年份']}｜"
                           f"tmdb={行['tmdb_id']}｜海报文件="
                           + (f"{Path(海报.本地路径).name}"
                              f"（{Path(海报.本地路径).stat().st_size} 字节，存在="
                              f"{Path(海报.本地路径).is_file()}）" if 海报 else "无"))
                        文件们 = 尾部库.文件们(int(行["id"]))
                        记(f"      文件 {len(文件们)} 个：" + "；".join(
                            f"{Path(r['路径']).name}（存在={Path(r['路径']).is_file()}）"
                            for r in 文件们))
                finally:
                    尾部库.关闭()
                尾部 = 资料库()
                try:
                    记(f"待确认剩余：{尾部.待确认数()} 条"
                       f"（本次只采纳一条，其余留给人工/下次）")
                    记("任务：" + "；".join(
                        f"{Path(r['路径']).name}={r['状态']}" for r in 尾部.取任务()))
                finally:
                    尾部.关闭()
                应用.quit()

    def 收尾():
        # 还原资料库（不污染用户数据）
        if 备份.is_file():
            for 后缀 in ("", "-wal", "-shm"):
                p = Path(str(库路径) + 后缀)
                if p.is_file():
                    p.unlink()
            shutil.copy2(备份, 库路径)
            print(f"（已还原 {库路径.name}；验证用的库在 {临时根}）")
        # 清掉"非官方端点"缓存：那是刚才假 TMDB 留下的（缓存已按端点分区，
        # 它们本来也不会被官方端点读到，但没必要占着盘）
        缓存根 = 项目根 / "数据" / "缓存" / "tmdb"
        for 目录 in 缓存根.glob("端点_*"):
            shutil.rmtree(目录, ignore_errors=True)
            print(f"（已清掉假 TMDB 的缓存分区 {目录.name}）")
        假TMDB.停止()
        print("\n---- 证据 ----")
        for 行 in 证据:
            print(行)
        print(f"假 TMDB 收到 {假TMDB.请求数} 个请求")

    定时 = QTimer()
    定时.setInterval(120)
    定时.timeout.connect(观察)
    定时.start()

    def 开始刮():
        记(f"开始刮削目录：{媒体}")
        状态["阶段"] = "刮削中"
        窗口._刮削路径(str(媒体))          # 真机流程：后台线程 + 进度框 + 自动弹队列

    QTimer.singleShot(600, 开始刮)
    应用.aboutToQuit.connect(收尾)
    应用.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
