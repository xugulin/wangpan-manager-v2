#!/usr/bin/env python3
"""验证打出来的发布包：**解压到别处**，用包里的东西跑一遍。

   运行环境/venv/bin/python 构建/验证发布包.py
   运行环境/venv/bin/python 构建/验证发布包.py --包 构建/发布/xxx.zip --保留
   运行环境/venv/bin/python 构建/验证发布包.py --跑真机      # 有图形会话时再跑真机测试

为什么必须做这一步（而不是"在源码树里测过就算"）
================================================
发布包和源码树是**两套东西**：包里的 venv 是用包内自带解释器重建的、符号链接改成了相对的、
`pyvenv.cfg` 里的绝对路径被换成了占位符、`数据/` 与 AI 模型没带。这些差异**只有解压出来跑
才能验到** —— 实测就是靠这一步抓到过"包里漏了 `资源/`（图标素材），3 条测试红"。

验证内容：
    1. 压缩包完整性（7z t）+ SHA256
    2. 解压到**全新路径**（模拟用户解压到任意目录）
    3. 内置解释器与自带 python 能不能跑
    4. `一键启动_网盘管理_V2.sh --check`（依赖 / libav* / 三家网盘目录）
    5. `启动.py --cli 网盘列表`（合并入口 + V1 那一层 + 适配器）
    6. **包内跑完整单元测试**（1147 条）
    7. 离屏真播一段（自研内核在包里能解码出画面）
    8. 占位符是否真的自洽：包里不该再有构建机的家目录路径
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
发布目录 = 项目根 / "构建" / "发布"
报告路径 = 发布目录 / "验证报告.txt"

行: list[str] = []
失败项: list[str] = []


def 说(文本: str = "") -> None:
    print(文本, flush=True)
    行.append(str(文本))


def 断言(条件: bool, 说明: str) -> bool:
    说(f"  {'✔' if 条件 else '❌'} {说明}")
    if not 条件:
        失败项.append(说明)
    return bool(条件)


def 跑(命令, *, 超时: float = 1800.0, 环境: dict | None = None, cwd=None,
      输入: str | None = None) -> subprocess.CompletedProcess:
    环境 = dict(环境 or os.environ)
    环境.setdefault("QT_QPA_PLATFORM", "offscreen")
    环境.setdefault("PYTHONUTF8", "1")
    环境.setdefault("PYTHONIOENCODING", "utf-8")
    环境.pop("DISPLAY", None)
    环境.pop("WAYLAND_DISPLAY", None)
    try:
        return subprocess.run([str(x) for x in 命令], capture_output=True, text=True,
                              timeout=超时, env=环境, cwd=str(cwd) if cwd else None)
    except subprocess.TimeoutExpired as 错:
        return subprocess.CompletedProcess(命令, 124, 错.stdout or "", "超时")


def 算SHA256(路径: Path) -> str:
    摘要 = hashlib.sha256()
    with 路径.open("rb") as 句柄:
        for 块 in iter(lambda: 句柄.read(4 * 1024 * 1024), b""):
            摘要.update(块)
    return 摘要.hexdigest()


def main() -> int:
    解析 = argparse.ArgumentParser(description="验证发布包")
    解析.add_argument("--包", default="", help="默认取 构建/发布 下最新的 zip")
    解析.add_argument("--保留", action="store_true", help="验完不删解压目录")
    解析.add_argument("--跑真机", action="store_true", help="额外在有图形会话时跑真机测试")
    选项 = 解析.parse_args()

    包 = Path(选项.包) if 选项.包 else max(
        发布目录.glob("*.zip"), key=lambda p: p.stat().st_mtime, default=None)
    if 包 is None or not 包.is_file():
        raise SystemExit(f"找不到发布包（{发布目录}/*.zip）")

    说("=" * 62)
    说(f"验证发布包：{包.name}")
    说("=" * 62)
    大小 = 包.stat().st_size
    说(f"[0] 基本信息：{大小 / 1048576:.1f} MB")
    开始 = time.time()
    说("  算 SHA256…")
    摘要 = 算SHA256(包)
    说(f"  SHA256 = {摘要}")
    说(f"  （算摘要用了 {time.time() - 开始:.0f}s）")

    说("[1] 压缩包完整性（7z t，只读不写）")
    结果 = 跑(["7z", "t", str(包)], 超时=3600)
    断言(结果.returncode == 0,
        "压缩包结构完好" if 结果.returncode == 0
        else f"压缩包有问题：{结果.stdout[-300:]}")

    解压目录 = Path("/tmp/验证发布包") / 包.stem
    if 解压目录.exists():
        shutil.rmtree(解压目录)
    解压目录.mkdir(parents=True)
    说(f"[2] 解压到全新路径：{解压目录}")
    开始 = time.time()
    结果 = 跑(["7z", "x", str(包), f"-o{解压目录}", "-y"], 超时=3600)
    提示 = (结果.stdout + 结果.stderr)
    被忽略 = 提示.count("Dangerous link path was ignored")
    关键 = ("启动.py", "一键启动_网盘管理_V2.sh", "v8_3", "wangpan",
          "运行环境/python/bin/python3.14", "运行环境/venv/pyvenv.cfg")
    缺 = [k for k in 关键 if not (解压目录 / k).exists()]
    # ⚠️ 7z 对"指向目录之外的符号链接"会**拒绝创建**并让退出码非 0（这是它的安全策略，
    #    不是包坏了）。这种链接由启动器自愈补回来，所以判据改成"关键文件在不在"。
    断言(not 缺, f"解压完成（{time.time() - 开始:.0f}s）"
              + (f"，7z 忽略了 {被忽略} 个越界链接（启动器会自愈）" if 被忽略 else ""))
    if 缺:
        说(f"  缺关键文件：{缺}；后面的检查没法做")
        报告路径.write_text("\n".join(行) + "\n", encoding="utf-8")
        return 1

    说("[3] 先跑一次启动器（便携自愈：补回被解压工具丢掉的符号链接与 pyvenv.cfg）")
    r = 跑([解压目录 / "一键启动_网盘管理_V2.sh", "--check"], cwd=解压目录, 超时=600,
          输入="\n")
    通过 = "自检结果：通过" in (r.stdout + r.stderr)
    断言(通过, "一键启动 --check 通过（含自愈）")
    for 行_ in (r.stdout + r.stderr).splitlines():
        if any(键 in 行_ for 键 in ("Python", "PySide6", "界面层", "libav", "网盘", "自检结果")):
            说(f"      {行_.strip()}")

    说("[4] 包内解释器（自愈之后）")
    自带 = 解压目录 / "运行环境" / "python" / "bin" / "python3.14"
    venv = 解压目录 / "运行环境" / "venv" / "bin" / "python"
    断言(自带.is_file(), f"自带解释器在：{自带.name}")
    断言(venv.is_file() or venv.is_symlink(), "包内 venv 解释器在")
    链接 = os.readlink(venv) if venv.is_symlink() else "(不是链接)"
    r = 跑([venv, "-c",
          "import sys; print('prefix=' + sys.prefix);"
          "import PySide6; print('PySide6=' + PySide6.__version__)"],
         cwd=解压目录)
    输出 = (r.stdout or r.stderr).strip()
    # 判据：解释器跑起来了、prefix 指向**解压出来的那个目录**、PySide6 能导入。
    # （第一版断言里找字面量 "PySide6"，而命令只打印版本号 → 自己误报失败。）
    好 = (r.returncode == 0 and "PySide6=" in 输出
        and f"prefix={解压目录}" in 输出)
    断言(好, f"包内 venv 能跑：{输出.replace(chr(10), ' | ')[:140]}")
    说(f"      venv/bin/python → {链接}")

    说("[4b] 一键启动自检的详细输出见上")

    r = 跑([解压目录 / "一键启动_网盘管理_V2.sh", "--check"], cwd=解压目录, 超时=600,
          输入="\n")
    通过 = "自检结果：通过" in (r.stdout + r.stderr)
    断言(通过, "一键启动 --check 通过")
    for 行_ in (r.stdout + r.stderr).splitlines():
        if any(键 in 行_ for 键 in ("Python", "PySide6", "界面层", "libav", "网盘", "自检结果")):
            说(f"      {行_.strip()}")

    说("[5] 合并入口的命令行模式（两套血脉 + 适配器）")
    r = 跑([venv, 解压目录 / "启动.py", "--cli", "网盘列表"], cwd=解压目录, 超时=600)
    断言(r.returncode == 0 and "标识" in r.stdout,
        "启动.py --cli 网盘列表 正常")
    for 行_ in r.stdout.splitlines()[:5]:
        说(f"      {行_.strip()}")

    说("[6] 包内跑完整单元测试（这是「发出去的环境被验证过」的证明）")
    开始 = time.time()
    r = 跑([venv, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
          cwd=解压目录, 超时=1800)
    尾部 = (r.stderr or r.stdout).strip().splitlines()
    概况 = [x for x in 尾部 if x.startswith("Ran ") or x in ("OK",) or x.startswith("FAILED")]
    断言(r.returncode == 0, f"包内测试全绿（{time.time() - 开始:.0f}s）：{' ｜ '.join(概况)}")
    if r.returncode != 0:
        for 行_ in 尾部[-12:]:
            说(f"      {行_}")

    说("[7] 离屏真播一段（自研内核在包里能解码出画面）")
    r = 跑([venv, "工具/诊断播放.py"], cwd=解压目录, 超时=600)
    结论 = [x for x in r.stdout.splitlines() if "结论" in x]
    断言(r.returncode == 0 and bool(结论),
        f"诊断播放：{结论[-1].strip() if 结论 else (r.stdout[-160:] or r.stderr[-160:])}")

    说("[8] 包里不该再有构建机的家目录路径（占位符要自洽）")
    命中: list[str] = []
    家目录 = str(Path.home())
    for 根, 目录们, 文件们 in os.walk(解压目录):
        # docs/ 里的研究笔记**故意**留着构建机的真实路径（那是"当时在这台机器实测"的证据），
        # 所以这一项只查运行时与源码，不查 docs。
        目录们[:] = [d for d in 目录们 if d not in ("__pycache__", "docs")]
        for 名 in 文件们:
            路径 = Path(根) / 名
            try:
                头部 = 路径.open("rb").read(4096)
            except Exception:  # noqa: BLE001
                continue
            if b"\x00" in 头部:
                continue
            try:
                文本 = 路径.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                continue
            if 家目录 in 文本:
                命中.append(str(路径.relative_to(解压目录)))
    断言(not 命中, f"没有构建机家目录路径（{len(命中)} 个文件命中）")
    for 项 in 命中[:5]:
        说(f"      {项}")

    if 选项.跑真机:
        说("[9] 真机测试（真实桌面）")
        r = 跑([venv, "工具/真机测试.py", "--平台", "xcb"], cwd=解压目录, 超时=900)
        尾部 = (r.stdout or "").strip().splitlines()[-6:]
        for 行_ in 尾部:
            说(f"      {行_}")
        断言("全部通过" in (r.stdout or ""), "真机测试全部通过")

    说()
    if 失败项:
        说(f"结论：**{len(失败项)} 项没通过**")
        for 项 in 失败项:
            说(f"  · {项}")
    else:
        说("结论：发布包全部验证通过 ✔")
    说(f"\nSHA256：{摘要}")
    说(f"解压目录：{解压目录}" + ("" if 选项.保留 else "（可用 --保留 留着）"))
    报告路径.write_text("\n".join(行) + "\n", encoding="utf-8")

    if not 选项.保留:
        shutil.rmtree(解压目录, ignore_errors=True)
    return 1 if 失败项 else 0


if __name__ == "__main__":
    raise SystemExit(main())
