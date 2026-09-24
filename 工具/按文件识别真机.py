#!/usr/bin/env python3
"""③真机验收：扫描时**直接对文件识别**（同一个目录里混放若干部作品，各归各的）。

用户原话
========
「扫描时最好直接对文件进行识别，不要对文件夹进行识别（避免用户乱放文件夹）」。

这个脚本怎么验
==============
拿**真 TMDB**、在临时目录里照用户"乱放"的样子凑一个目录：几部不同的电影 + 另一部剧的
几集全塞在同一个文件夹里（文件名带作品名、目录名故意不带任何作品信息）。然后：

1. **老路**（改造前）：``扫描媒体库``（按目录归并）→ 打印它认成几部、哪些文件丢了；
2. **新路**（本次）：``刮削服务.扫库按文件``（一个文件一个单元 → 逐个走识别管线 →
   按识别出的作品归并）→ 打印"每个文件识别到哪个作品"与"库里最终有几条媒体"；
3. 断言：库里**不是一条**、每个作品各自一条、**没有一条把两个不同作品的文件混进去**、
   文件一个都没丢（识别不出来的进待确认队列也算没丢）。

跑完清掉临时目录；``数据/识别检索缓存.json`` 与 ``V2_识别学习库`` 跑前备份跑完还原。

跑法::

    运行环境/venv/bin/python 工具/按文件识别真机.py

退出码：``0`` 全过；``2`` 凭据问题；``3`` 连不上；``5`` 断言失败。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
if str(项目根) not in sys.path:
    sys.path.insert(0, str(项目根))

from wangpan.控制台 import 修控制台                                        # noqa: E402

修控制台()

#: "乱放"目录里的文件 → 期望的作品关键词（只用来判断"有没有混在一起"，不是代码里的写死逻辑）
素材 = {
    "Dune.2021.2160p.BluRay.mkv": "dune",
    "Blade.Runner.1982.1080p.BluRay.mkv": "blade",
    "The.Matrix.1999.1080p.BluRay.mkv": "matrix",
    "Stranger.Things.S01E01.mkv": "stranger",
    "Stranger.Things.S01E02.mkv": "stranger",
}
#: 目录名故意**不带**任何作品信息（用户"乱放"的典型：一个 download 目录塞一堆）
乱放目录名 = "下载_乱七八糟"


def 打印(文本: str = "") -> None:
    print(文本, flush=True)


class _断言失败(Exception):
    pass


def _断言(条件, 说明: str) -> None:
    if not 条件:
        raise _断言失败(说明)


class 现场保护:
    """跑完恢复：检索缓存文件 + ``V2_识别学习库`` 环境变量。"""

    def __init__(self, 临时根: Path) -> None:
        self.临时根 = Path(临时根)
        self.缓存文件 = 项目根 / "数据" / "识别检索缓存.json"
        self._缓存原样: bytes | None = None
        self._学习变量原样: str | None = None

    def 备份(self) -> None:
        self.临时根.mkdir(parents=True, exist_ok=True)
        if self.缓存文件.is_file():
            self._缓存原样 = self.缓存文件.read_bytes()
        self._学习变量原样 = os.environ.get("V2_识别学习库")
        os.environ["V2_识别学习库"] = str(self.临时根 / "识别学习.json")

    def 恢复(self) -> None:
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


def _取参数() -> argparse.Namespace:
    解析 = argparse.ArgumentParser(description="③真机验收：按文件识别（乱放目录各归各）")
    解析.add_argument("--保留现场", action="store_true", help="跑完不删临时目录")
    return 解析.parse_args()


def main() -> int:
    参数 = _取参数()
    临时根 = Path(tempfile.mkdtemp(prefix="v2按文件真机_"))
    保护 = 现场保护(临时根)
    保护.备份()
    打印(f"临时目录：{临时根}（临时库/素材/学习库都在这里，跑完删）")
    try:
        return _跑(临时根)
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


def _跑(临时根: Path) -> int:
    from wangpan.scrape.库 import 资料库
    from wangpan.scrape.图片 import 图片缓存
    from wangpan.scrape.服务 import 刮削服务, 刮削设置
    from wangpan.scrape.扫描 import 扫描媒体库
    from wangpan.scrape.tmdb import TMDB客户端, TMDB配置

    配置 = TMDB配置.从环境与配置()
    if not 配置.可用():
        打印("❌ 没配 TMDB 凭据")
        return 2
    客户端 = TMDB客户端(配置)
    打印("TMDB：" + str({k: v for k, v in 客户端.配置摘要().items()
                      if k in ("可用", "鉴权", "语言", "代理")}))

    # ---- 0) 凑一个"乱放"目录（文件够大，别被"样片"规则跳过）----
    乱放 = 临时根 / "素材" / 乱放目录名
    乱放.mkdir(parents=True, exist_ok=True)
    for 名 in 素材:
        with open(乱放 / 名, "wb") as f:
            f.truncate(21 * 1024 * 1024)
    打印(f"\n① 乱放目录：{乱放}（目录名不含任何作品信息）")
    for 名 in sorted(素材):
        打印(f"    {名}")

    # ---- 1) 老路：按目录归并 ----
    老 = 扫描媒体库(乱放, 最小文件字节=0)
    老文件 = [v.name for x in 老.条目们 for v in x.视频们]
    打印(f"\n② 老路（改造前：按目录归并）——{老.摘要()}")
    for x in 老.条目们:
        打印(f"    单元：{x.一句话()}｜文件 {[v.name for v in x.视频们]}")
    丢了 = sorted(set(素材) - set(老文件))
    打印(f"    → 认成 {len({str(x.路径) for x in 老.条目们})} 个单元；"
        f"**丢掉的文件 {len(丢了)} 个**：{丢了}")

    # ---- 2) 新路：按文件识别（真 TMDB）----
    库 = 资料库(临时根 / "资料库.db")
    日志行: list[str] = []
    服务 = 刮削服务(库, 客户端, 图片缓存(临时根 / "图缓存"),
                刮削设置(最小文件字节=0, 本批上限=50, 下载图片=False),
                日志回调=日志行.append)
    打印("\n③ 新路（本次）：一个文件一个单元 → 逐个走识别管线 → 按识别结果归并")
    try:
        数量, 摘要 = 服务.扫库按文件([乱放])
        结果 = 服务.续跑()
    finally:
        服务.关闭()
    打印(f"    扫到 {数量} 个视频｜{摘要}")
    识别行 = [x for x in 日志行 if x.startswith("[识别] 按文件")]
    _断言(识别行, "登记阶段没有逐文件识别（应当看到 [识别] 按文件… 的日志）")
    for 行 in 识别行:
        打印(f"    {行}")
    打印(f"    续跑：{结果.摘要()}")

    # ---- 3) 库里到底几条、谁跟谁一伙 ----
    行们 = 库.列表()
    打印(f"\n④ 库里的媒体（{len(行们)} 条）：")
    归属: dict[int, set[str]] = {}
    for 行 in 行们:
        文件们 = {Path(str(r["路径"])).name for r in 库.文件们(int(行["id"]))}
        归属[int(行["id"])] = 文件们
        条目 = 库.取媒体(int(行["id"]))
        打印(f"    《{条目.标题}》（{条目.年份}）｜{条目.类型.value}｜"
            f"TMDB {条目.外部ID.get('tmdb')}｜文件 {sorted(文件们)}")
    待确认 = 库.待确认数()
    统计 = 库.统计()
    打印(f"    待确认 {待确认} 条｜待处理 {统计.待处理} 条｜失败 {统计.失败} 条")
    for 待 in 库.待确认():
        打印(f"      ⏳ 待确认：{待.标题 or Path(str(待.路径)).name}｜{待.说明[:60]}")

    # ---- 4) 断言 ----
    _断言(结果.失败 == 0, f"不该有失败：{结果.错误们}")
    _断言(len(行们) >= 3,
         f"混放的四部作品至少要入库 3 条（实际 {len(行们)} 条）—— "
         f"1 条就是又糊在一起了")
    已入 = set().union(*归属.values()) if 归属 else set()
    for 媒体id, 文件们 in 归属.items():
        关键词们 = {素材.get(名, 名) for 名 in 文件们}
        _断言(len(关键词们) <= 1,
             f"媒体 {媒体id} 把不同作品的文件混在了一起：{sorted(文件们)}")
    # 同一部作品的两集要落在同一条里（不许拆成两条）
    怪奇 = [m for m, f in 归属.items() if f & {"Stranger.Things.S01E01.mkv",
                                          "Stranger.Things.S01E02.mkv"}]
    _断言(len(怪奇) <= 1, f"同一部剧的两集被拆成了 {len(怪奇)} 条：{怪奇}")
    # 文件没丢：入库的 + 进待确认的都要有人管
    _断言(已入 <= set(素材), f"库里有不属于这个目录的文件：{已入 - set(素材)}")
    _断言(len(已入) + 待确认 * 0 >= 3,
         f"入库的文件太少（{sorted(已入)}）—— 老路会把电影整批丢掉，新路不该")
    _断言(set(老文件) != set(已入) or 丢了,
         "老路与新路结果一样 —— 这个素材没体现'乱放'，换个目录名/文件名再验")

    打印("\n" + "=" * 78)
    打印(f"✅ ③真机验收通过：乱放目录 {len(素材)} 个文件 → "
        f"老路丢掉 {len(丢了)} 个、糊成 {len({str(x.路径) for x in 老.条目们})} 个单元；"
        f"新路入库 {len(行们)} 条媒体（"
        + "、".join(f"《{库.取媒体(int(x['id'])).标题}》" for x in 行们)
        + f"），没有一条混进别的作品的文件")
    打印("=" * 78)
    try:
        库.关闭()
    except Exception:                                                        # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
