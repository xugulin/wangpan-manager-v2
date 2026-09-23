#!/usr/bin/env python3
"""真机验证：**真 TMDB API**（经用户自己的代理）—— 搜索/详情/分级/演职员/图片/落库。

为什么需要"经代理"这条路
========================
真机实测：这台机器**直连到不了** `api.themoviedb.org`（DNS 被解析到一个无关的
IPv6 地址、IPv4/IPv6 全超时），而机器上跑着的 v2rayN/xray 的本地 SOCKS5 口是通的。
所以本项目自己实现了标准库版 SOCKS5/HTTP CONNECT 隧道（`wangpan/scrape/代理.py`，
不引第三方依赖），配 `proxy` 或 `V2_TMDB_PROXY` 即可。

这个脚本干的事（每一步都留证据）：
1. 报配置摘要（鉴权方式、代理、缓存 TTL —— **不回显密钥**）；
2. 真搜一次、真取一次详情、真下一张海报（报字节数）、真读分级与演职员；
3. 用**真 API + 真图片**跑一遍刮削服务（临时库），把条目、集、文件挂接都打出来；
4. 可选：开真窗口把海报墙与详情页截下来（``--开界面``），跑完还原资料库。

用法::

    DISPLAY=:0 WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1001 \\
      QT_QPA_PLATFORM=xcb 运行环境/venv/bin/python 工具/真机_TMDB真API.py \\
      --代理 socks5://127.0.0.1:10808 --开界面
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


def 造媒体目录(根: Path) -> Path:
    """造一份"像真的"媒体库：一部电影（多版本）+ 一部剧（两集）。"""
    媒体 = 根 / "媒体"
    (媒体 / "沙丘 (2021)").mkdir(parents=True, exist_ok=True)
    for 版本 in ("2160p", "1080p"):
        (媒体 / "沙丘 (2021)" / f"沙丘 (2021) {版本}.mkv").write_bytes(
            b"V" * (21 * 1024 * 1024))
    (媒体 / "怪奇物语 (2016)" / "Season 01").mkdir(parents=True, exist_ok=True)
    for 集号 in (1, 2):
        (媒体 / "怪奇物语 (2016)" / "Season 01" /
         f"怪奇物语 S01E{集号:02d}.mkv").write_bytes(b"S" * (25 * 1024 * 1024))
    return 媒体


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from wangpan.scrape.库 import 资料库
    from wangpan.scrape.图片 import 图片缓存
    from wangpan.scrape.服务 import 刮削服务, 刮削设置
    from wangpan.scrape.tmdb import TMDB客户端, TMDB配置

    解析 = argparse.ArgumentParser()
    解析.add_argument("--代理", default="")
    解析.add_argument("--截图目录", default="/tmp")
    解析.add_argument("--开界面", action="store_true")
    解析.add_argument("--队列", action="store_true",
                    help="再验一遍「拿不准 → 队列 → 人工采纳」（用一个年份写错的目录制造歧义）")
    解析.add_argument("--保留库", action="store_true",
                    help="默认跑完还原 数据/资料库.db（不污染用户数据）")
    参数 = 解析.parse_args()

    临时根 = Path("/tmp/真机_TMDB真API")
    if 临时根.exists():
        shutil.rmtree(临时根, ignore_errors=True)
    临时根.mkdir(parents=True, exist_ok=True)
    媒体 = 造媒体目录(临时根)

    配置 = TMDB配置.从环境与配置()
    配置.缓存目录 = 临时根 / "tmdb缓存"          # 别和日常缓存混在一起
    if 参数.代理:
        配置.代理 = 参数.代理
    os.environ.pop("V2_TMDB_API_BASE", None)      # 确保打的是**真** TMDB
    配置.接口基地址 = ""
    if not 配置.可用():
        print("✗ 没配 TMDB 凭据（数据/刮削.json 或 V2_TMDB_TOKEN），没法验证")
        return 2
    客户端 = TMDB客户端(配置)
    print("配置摘要：", 客户端.配置摘要())
    if not 配置.代理:
        print("⚠ 没给代理：直连在本机到不了 api.themoviedb.org（见研究文档）")

    证据: list[str] = []

    def 记(文本: str) -> None:
        证据.append(文本)
        print("・" + 文本)

    # ---- 1) 真搜索 / 真详情 / 真图片 ----
    try:
        候选们 = 客户端.搜电影("沙丘", 2021)
        记(f"[真 API] 搜索「沙丘 2021」→ {len(候选们)} 个候选；"
           + "；".join(f"{c.标题}({c.年份})" for c in 候选们[:4]))
        条目 = 客户端.取电影("438631")
        记(f"[真 API] 详情：《{条目.标题}》{条目.年份}｜评分 {条目.评分}｜"
           f"时长 {条目.时长分钟} 分钟｜状态 {条目.状态}")
        记(f"[真 API] 外链：{条目.外部ID}｜分级：" +
           "，".join(g.可读() for g in 条目.分级们[:5]))
        记(f"[真 API] 导演：{ [x.人物.名字 for x in 条目.导演()] }｜"
           f"演员前 5：{ [x.人物.名字 for x in 条目.演员(5)] }")
        记(f"[真 API] 标签 {条目.标签}｜制片 {条目.制片公司[:3]}｜"
           f"图片 {len(条目.图片们)} 张（含多语言）")
        落点 = 临时根 / "真下载的海报.jpg"
        图 = 客户端.下载图片(条目.海报.远端路径, 落点, "w500") if 条目.海报 else None
        if 图 is not None:
            记(f"[真图片] 海报下到本地：{落点.name}（{落点.stat().st_size} 字节）")
        else:
            记("[真图片] ✗ 海报没下下来")
        剧 = 客户端.取剧集("66732")
        记(f"[真 API] 剧集：《{剧.标题}》{剧.年份}｜评分 {剧.评分}｜状态 {剧.状态}｜"
           f"季 {[(s.季号, s.集数) for s in 剧.季们][:3]}")
        季 = 客户端.取季("66732", 1)
        记(f"[真 API] 第 1 季 {len(季.集们)} 集｜第 1 集《{季.集们[0].标题}》"
           f"（{季.集们[0].播出日期}）")
    except Exception as 错:  # noqa: BLE001
        记(f"✗ 真 API 调用失败：{type(错).__name__}: {错}")
        print("\n".join(证据))
        return 3

    # ---- 2) 真 API 跑一遍刮削流水线（临时库 + 真下图）----
    库 = 资料库(临时根 / "库.db")
    缓存 = 图片缓存(临时根 / "图缓存", 代理=配置.代理)
    服务 = 刮削服务(库, 客户端, 缓存,
                刮削设置(本批上限=10, 最小文件字节=1024, 抓季集详情=True, 写NFO=True),
                日志回调=lambda t: 记("[服务] " + t))
    try:
        服务.扫库([媒体])
        结果 = 服务.续跑()
        记(f"[流水线] {结果.摘要()}")
        for 行 in 库.全部("SELECT id,类型,标题,年份,评分,tmdb_id,海报 FROM 媒体"):
            条目 = 库.取媒体(int(行["id"]))
            海报 = 条目.海报
            记(f"[落库] id={行['id']}｜{行['标题']}（{行['年份']}）｜"
               f"{行['类型']}｜{行['评分']:.1f} 分｜tmdb={行['tmdb_id']}｜"
               f"导演 {[x.人物.名字 for x in 条目.导演()][:2]}｜"
               f"海报 " + (f"{Path(海报.本地路径).stat().st_size} 字节" if 海报
                        and 海报.本地路径 else "无"))
            文件们 = 库.文件们(int(行["id"]))
            记(f"        文件 {len(文件们)} 个：" + "；".join(
                Path(r["路径"]).name for r in 文件们))
        记(f"[缓存] {缓存.统计一下().摘要()}")
        nfo们 = sorted(p.relative_to(媒体) for p in 媒体.rglob("*.nfo"))
        记(f"[NFO] 写出 {len(nfo们)} 个：{ [str(x) for x in nfo们 ] }")
        文件 = 媒体 / "沙丘 (2021)" / "沙丘 (2021).nfo"
        if 文件.is_file():
            for 行 in 文件.read_text(encoding="utf-8").splitlines()[:8]:
                记("[NFO] " + 行.strip())
    finally:
        服务.关闭()
        库.关闭()

    # ---- 2.5) 可选：真 API 上的"拿不准 → 队列 → 人工采纳" ----
    if 参数.队列:
        _验队列(_验队列目录(临时根), 临时根, 记, 配置)

    # ---- 3) 可选：真窗口截图 ----
    if 参数.开界面:
        _开界面截图(参数, 媒体, 记, 配置)

    print("\n---- 证据 ----")
    for 行 in 证据:
        print(行)
    记录 = Path(参数.截图目录) / "真机_TMDB真API.txt"
    记录.write_text("\n".join(证据), encoding="utf-8")
    print(f"（证据也写了一份：{记录}）")
    return 0


def _验队列目录(根: Path) -> Path:
    """造一个"注定拿不准"的目录：片名对得上但**年份写错一年**（真机上就是这么触发的）。"""
    媒体 = 根 / "队列媒体" / "沙丘 (2022)"
    媒体.mkdir(parents=True, exist_ok=True)
    (媒体 / "沙丘 (2022) 1080p.mkv").write_bytes(b"Q" * (21 * 1024 * 1024))
    return 媒体


def _验队列(目录: Path, 根: Path, 记, 配置) -> None:
    from wangpan.scrape.库 import 资料库
    from wangpan.scrape.图片 import 图片缓存
    from wangpan.scrape.服务 import 刮削服务, 刮削设置
    from wangpan.scrape.tmdb import TMDB客户端
    库 = 资料库(根 / "队列库.db")
    缓存 = 图片缓存(根 / "队列图", 代理=配置.代理)
    客户端 = TMDB客户端(配置)
    服务 = 刮削服务(库, 客户端, 缓存,
                刮削设置(最小文件字节=1024, 抓季集详情=False, 写NFO=True),
                日志回调=lambda t: 记("[队列] " + t))
    try:
        服务.扫库([目录])
        结果 = 服务.续跑()
        记(f"[队列] 扫刮结果：{结果.摘要()}")
        项们 = 库.待确认()
        记(f"[队列] 待确认 {len(项们)} 条｜库里条目数 {库.计数()}（拿不准的不该入库）")
        if not 项们:
            记("[队列] ⚠ 这次没触发待确认（换了数据？）")
            return
        项 = 项们[0]
        for 候选 in 项.候选们[:4]:
            记(f"[队列]   候选 {候选.可读()}")
        选中 = 项.候选们[0]
        记(f"[队列] 人工采纳：{选中.标题}（tmdb {选中.来源标识}）")
        结果2 = 服务.采纳候选(项.路径, 选中)
        记(f"[队列] 采纳结果：{结果2.摘要()}｜待确认剩 {库.待确认数()}")
        for 行 in 库.全部("SELECT id,标题,年份,tmdb_id FROM 媒体"):
            记(f"[队列] 入库：id={行['id']}｜{行['标题']}（{行['年份']}）｜"
               f"tmdb={行['tmdb_id']}")
        记(f"[队列] NFO：{ [str(p.name) for p in 目录.rglob('*.nfo')] }")
    except Exception as 错:  # noqa: BLE001
        记(f"[队列] ✗ 验证失败：{type(错).__name__}: {错}")
    finally:
        服务.关闭()
        库.关闭()


def _开界面截图(参数, 媒体: Path, 记, 配置) -> None:
    """用真窗口把海报墙/详情页截下来（**用真 API 真图片**刮完再看）。

    界面用的是默认资料库（数据/资料库.db），所以这里先备份、跑完还原。
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    库路径 = 项目根 / "数据" / "资料库.db"
    备份 = Path(参数.截图目录) / "资料库.db.真API备份"
    if 库路径.is_file():
        shutil.copy2(库路径, 备份)
    # 先清空资料库再开界面：否则海报墙上混着上一次验证留下的旧条目，
    # 截出来的图**分不清哪张是真 TMDB 刮出来的**（真机上就截错过了：
    # 墙上两张都是旧的假 CDN 图，而数据看着一模一样）。
    from wangpan.scrape.库 import 资料库 as _资料库
    _清 = _资料库(库路径)
    try:
        _清.清空()
        记("[界面] 已清空资料库（只留这次真 API 刮出来的），跑完会还原")
    finally:
        _清.关闭()
    os.environ["V2_TMDB_PROXY"] = 配置.代理 or ""
    os.environ["V2_TMDB_TOKEN"] = 配置.token or ""
    if 配置.api_key:
        os.environ["V2_TMDB_API_KEY"] = 配置.api_key
    应用 = QApplication.instance() or QApplication([])
    from wangpan.ui.主窗口 import 主窗口
    窗口 = 主窗口()
    窗口.自动开队列 = False
    窗口.resize(1440, 900)
    窗口.show()
    窗口.raise_()
    状态 = {"阶段": "刮削", "开始": time.monotonic()}

    def 观察():
        if time.monotonic() - 状态["开始"] > 180:
            记("⚠ 界面验证超时")
            应用.quit()
            return
        if 状态["阶段"] == "刮削":
            # 等**刮削真的跑完**（状态栏会说"刮削完成"，见 主窗口.提示），
            # 不是"墙上有东西就截" —— 后者会把上一次留下的旧卡片拍进去。
            线程 = getattr(窗口, "_刮削线程", None)
            还在跑 = 线程 is not None and 线程.isRunning()
            if not 还在跑 and "刮削完成" in 窗口.当前提示():
                状态["阶段"] = "截图"
                状态["开始"] = time.monotonic()
            return
        if 状态["阶段"] == "截图":
            状态["阶段"] = "详情"
            窗口.标签.setCurrentIndex(2)
            窗口.海报墙.刷新()
            窗口.海报墙.等待图片(8000, 至少几张=1)
            应用.processEvents()
            图 = Path(参数.截图目录) / "真机_TMDB真API_海报墙.png"
            窗口.grab().save(str(图))
            记(f"[界面] 海报墙截图：{图}｜{窗口.海报墙.状态行文本()}")
            # 详情页：真演职员 + 真海报 + 真分级
            from wangpan.scrape.库 import 资料库
            库 = 资料库()
            try:
                行们 = 库.全部("SELECT id,标题 FROM 媒体 ORDER BY id")
            finally:
                库.关闭()
            if 行们:
                ident = int(行们[0]["id"])
                窗口.海报墙.弹详情(ident)
                窗 = 窗口.海报墙.弹出中的详情()
                if 窗 is not None:
                    for _ in range(60):
                        应用.processEvents()
                        time.sleep(0.03)
                    图2 = Path(参数.截图目录) / "真机_TMDB真API_详情页.png"
                    窗.grab().save(str(图2))
                    记(f"[界面] 详情页截图：{图2}")
            QTimer.singleShot(300, 应用.quit)
            return

    def 开始():
        记(f"[界面] 用真 API 刮：{媒体}")
        窗口._刮削路径(str(媒体))

    QTimer.singleShot(600, 开始)
    定时 = QTimer()
    定时.setInterval(200)
    定时.timeout.connect(观察)
    定时.start()

    def 诊断(阶段: str) -> None:
        """看看还有哪个 QThread 在跑（QThread 跑着被析构 = 进程直接 abort）。"""
        from PySide6.QtCore import QThread
        还在跑 = [(type(t).__name__, t.objectName()) for t in 应用.findChildren(QThread)
                if t.isRunning()]
        记(f"[退出/{阶段}] 仍在跑的 QThread：{len(还在跑)} 个"
           + ("".join(f" {名}" for 名, _ in 还在跑) if 还在跑 else ""))

    def 收尾():
        诊断("aboutToQuit")
        # 用户关窗时走的就是这条；脚本也照做一次（别只 quit 不关窗）
        try:
            窗口.close()
        except Exception:  # noqa: BLE001
            pass
        诊断("关窗后")
        if 备份.is_file() and not 参数.保留库:
            for 后缀 in ("", "-wal", "-shm"):
                p = Path(str(库路径) + 后缀)
                if p.is_file():
                    p.unlink()
            shutil.copy2(备份, 库路径)
            记(f"[界面] 已还原 {库路径.name}")

    应用.aboutToQuit.connect(收尾)
    应用.exec()
    诊断("exec 返回后")
    # 收尾顺序：先让窗口自己收干净，再交给解释器退出
    应用.processEvents()


if __name__ == "__main__":
    raise SystemExit(main())
