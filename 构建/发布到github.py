#!/usr/bin/env python3
"""把打好包的东西发到 GitHub Releases（建 Release + 传附件）。

   运行环境/venv/bin/python 构建/发布到github.py                  # 用 构建/发布说明.md + 发布目录里的 zip
   运行环境/venv/bin/python 构建/发布到github.py --版本 v2.1.0
   运行环境/venv/bin/python 构建/发布到github.py --草稿            # 只建草稿（不会公开）
   运行环境/venv/bin/python 构建/发布到github.py --只列附件         # 看这个 Release 已有什么
   运行环境/venv/bin/python 构建/发布到github.py --续传            # 已有同名附件就跳过（断网重来用）

令牌从哪儿来（沿用 V1 的约定，顺序找第一个存在的）::

    GITHUB_TOKEN 环境变量
    ~/python/令牌/网盘管理token.txt
    ~/python/令牌/github-token-网盘管理.txt
    ~/python/令牌/github-token-wangpan.txt
    ~/python/令牌/github-token.txt

**令牌不会进命令行参数**（`ps` 能看到别人的命令行）：HTTP 头是脚本自己拼的，
不上 shell。脚本也不打印令牌内容。

为什么要用这个脚本而不是 `gh release create`：这台机器没装 `gh`，
而 `gh` 传大附件时不会报进度；这里自己按 8 MB 一块流式上传并打印百分比，
断了还能 `--续传` 重来。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
发布目录 = 项目根 / "构建" / "发布"
默认说明 = 项目根 / "构建" / "发布说明.md"

仓库拥有人 = "xugulin"
仓库名 = "wangpan-manager-v2"
仓库 = f"{仓库拥有人}/{仓库名}"

候选令牌 = (
    Path.home() / "python" / "令牌" / "网盘管理token.txt",
    Path.home() / "python" / "令牌" / "github-token-网盘管理.txt",
    Path.home() / "python" / "令牌" / "github-token-wangpan.txt",
    Path.home() / "python" / "令牌" / "github-token.txt",
)

上传块 = 8 * 1024 * 1024          # 8 MB 一块
行: list[str] = []


def 说(文本: str = "") -> None:
    print(文本, flush=True)
    行.append(str(文本))


def 读令牌() -> tuple[str, str]:
    值 = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if 值:
        return 值, "环境变量 GITHUB_TOKEN"
    for 路径 in 候选令牌:
        if 路径.is_file():
            文本 = 路径.read_text(encoding="utf-8").strip()
            if 文本:
                return 文本, str(路径)
    raise SystemExit("找不到 GitHub 令牌：设 GITHUB_TOKEN，或放进 "
                     + " / ".join(str(p) for p in 候选令牌))


def 请求(方法: str, 地址: str, 令牌: str, 数据: bytes | None = None,
       内容类型: str = "application/json") -> tuple[int, dict | list | str]:
    头 = {"Authorization": f"Bearer {令牌}",
         "Accept": "application/vnd.github+json",
         "User-Agent": "wangpan-manager-release",
         "X-GitHub-Api-Version": "2022-11-28"}
    体 = None
    if 数据 is not None:
        头["Content-Type"] = 内容类型
        体 = 数据
    请 = urllib.request.Request(地址, data=体, headers=头, method=方法)
    try:
        with urllib.request.urlopen(请, timeout=90) as 应答:
            原 = 应答.read().decode("utf-8", "replace")
            try:
                return 应答.status, json.loads(原) if 原 else {}
            except json.JSONDecodeError:
                return 应答.status, 原
    except urllib.error.HTTPError as 错:
        原 = 错.read().decode("utf-8", "replace")
        try:
            return 错.code, json.loads(原)
        except json.JSONDecodeError:
            return 错.code, 原


def 找或建Release(令牌: str, 版本: str, 说明: str, 草稿: bool,
              预发布: bool) -> dict:
    标签 = 版本 if 版本.startswith("v") else f"v{版本}"
    状态, 已有 = 请求("GET", f"https://api.github.com/repos/{仓库}/releases/tags/{标签}",
                   令牌)
    if 状态 == 200 and isinstance(已有, dict):
        说(f"  这个 Release 已经存在：{已有.get('html_url')}（改成更新说明）")
        # 同名 tag 只更新说明，不重建（避免把已有附件弄丢）
        请求("PATCH", f"https://api.github.com/repos/{仓库}/releases/{已有['id']}",
            令牌, json.dumps({"body": 说明, "draft": 草稿,
                            "prerelease": 预发布}).encode())
        return dict(已有, body=说明)
    负载 = {
        "tag_name": 标签,
        "name": f"网盘管理 {标签.lstrip('v')}（整合版）",
        "body": 说明,
        "draft": 草稿,
        "prerelease": 预发布,
        "target_commitish": "main",
    }
    状态, 结果 = 请求("POST", f"https://api.github.com/repos/{仓库}/releases",
                    令牌, json.dumps(负载).encode())
    if 状态 not in (200, 201) or not isinstance(结果, dict):
        raise SystemExit(f"建 Release 失败（HTTP {状态}）：{结果}")
    说(f"  已创建 Release：{结果.get('html_url')}")
    return 结果


class _计数读取器:
    """包一层文件对象，顺便报上传进度。"""

    def __init__(self, 句柄, 总字节: int, 名字: str) -> None:
        self._句柄 = 句柄
        self.总 = max(1, 总字节)
        self.已 = 0
        self.名字 = 名字
        self._上次 = 0.0

    def read(self, 大小: int = -1) -> bytes:
        块 = self._句柄.read(大小)
        self.已 += len(块)
        现在 = time.time()
        if 现在 - self._上次 > 5 or self.已 >= self.总:
            self._上次 = 现在
            说(f"    {self.名字}：{self.已 / 1048576:.0f}/{self.总 / 1048576:.0f} MB"
               f"（{self.已 * 100 // self.总}%）")
        return 块


def 传附件(令牌: str, Release: dict, 文件: Path, 续传: bool) -> bool:
    已有 = {a.get("name") for a in (Release.get("assets") or [])}
    if 续传 and 文件.name in 已有:
        说(f"  ⏭ {文件.name} 已经在 Release 上，跳过（--续传）")
        return True
    大小 = 文件.stat().st_size
    说(f"  上传 {文件.name}（{大小 / 1048576:.1f} MB）…")
    地址 = (f"https://uploads.github.com/repos/{仓库}/releases/"
           f"{Release['id']}/assets?name={urllib.parse.quote(文件.name)}")
    头 = {"Authorization": f"Bearer {令牌}",
         "Accept": "application/vnd.github+json",
         "User-Agent": "wangpan-manager-release",
         "Content-Type": "application/zip",
         "Content-Length": str(大小)}
    句柄 = 文件.open("rb")
    请 = urllib.request.Request(地址, data=_计数读取器(句柄, 大小, 文件.name),
                             headers=头, method="POST")
    开始 = time.time()
    try:
        with urllib.request.urlopen(请, timeout=3600) as 应答:
            原 = 应答.read().decode("utf-8", "replace")
            数据 = json.loads(原) if 原 else {}
            用时 = time.time() - 开始
            说(f"  ✔ 上传完成：{数据.get('browser_download_url')}"
               f"（{用时:.0f}s，{大小 / 1048576 / max(用时, 1) * 8:.1f} Mbps）")
            return True
    except urllib.error.HTTPError as 错:
        说(f"  ❌ 上传失败 HTTP {错.code}：{错.read().decode('utf-8', 'replace')[:300]}")
        return False
    except Exception as 错:  # noqa: BLE001
        说(f"  ❌ 上传中断：{type(错).__name__}: {错}")
        说("     断网/超时可以重跑本脚本并加 --续传（已传完的附件会跳过）")
        return False
    finally:
        句柄.close()


def main() -> int:
    解析 = argparse.ArgumentParser(description="发 GitHub Release")
    解析.add_argument("--版本", default="", help="默认从 pyproject.toml 读")
    解析.add_argument("--说明", default=str(默认说明), help="发布说明 markdown")
    解析.add_argument("--附件", nargs="*", default=[],
                   help="要上传的文件（默认：构建/发布/*.zip）")
    解析.add_argument("--草稿", action="store_true")
    解析.add_argument("--预发布", action="store_true")
    解析.add_argument("--续传", action="store_true", help="已有同名附件就跳过")
    解析.add_argument("--只列附件", action="store_true", help="只看这个 Release 有什么")
    解析.add_argument("--发布", action="store_true",
                   help="把已存在的草稿 Release 正式发布（上传完检查无误后再用）")
    选项 = 解析.parse_args()

    版本 = 选项.版本
    if not 版本:
        import re
        文本 = (项目根 / "pyproject.toml").read_text(encoding="utf-8")
        找到 = re.search(r'^version\s*=\s*"([^"]+)"', 文本, re.M)
        版本 = 找到.group(1) if 找到 else "0.0.0"
    令牌, 来源 = 读令牌()
    说("=" * 60)
    说(f"发布到 GitHub：{仓库}｜版本 {版本}")
    说(f"令牌来源：{来源}（长度 {len(令牌)}，不回显）")
    说("=" * 60)

    if 选项.发布:
        标签 = 版本 if 版本.startswith("v") else f"v{版本}"
        状态, 已有 = 请求(
            "GET", f"https://api.github.com/repos/{仓库}/releases/tags/{标签}", 令牌)
        if 状态 != 200 or not isinstance(已有, dict):
            # ⚠️ **草稿**按 tag 查是查不到的（GitHub 要等正式发布才建 tag），
            #    所以这里翻列表找 tag_name 对得上的那条（实测踩到：--发布 报 404）。
            说(f"  按 tag 查不到 {标签}（多半还是草稿），翻列表找…")
            状态2, 列表 = 请求(
                "GET", f"https://api.github.com/repos/{仓库}/releases?per_page=50", 令牌)
            已有 = next((r for r in (列表 if isinstance(列表, list) else [])
                       if r.get("tag_name") == 标签), None)
            if 已有 is None:
                raise SystemExit(f"没找到 Release {标签}（列表里也没有）")
            说(f"  找到草稿：{已有.get('html_url')}")
        状态, 结果 = 请求(
            "PATCH", f"https://api.github.com/repos/{仓库}/releases/{已有['id']}",
            令牌, json.dumps({"draft": False}).encode())
        if 状态 != 200:
            raise SystemExit(f"发布草稿失败（HTTP {状态}）：{结果}")
        说(f"  ✔ 已正式发布：{结果.get('html_url')}")
        for a in (结果.get("assets") or []):
            说(f"    附件 {a['name']}（{a['size'] / 1048576:.1f} MB）")
        (发布目录 / "发布报告.txt").write_text("\n".join(行) + "\n", encoding="utf-8")
        return 0

    if 选项.只列附件:
        状态, 结果 = 请求(
            "GET", f"https://api.github.com/repos/{仓库}/releases", 令牌)
        if 状态 != 200 or not isinstance(结果, list):
            raise SystemExit(f"列 Release 失败（HTTP {状态}）：{结果}")
        for r in 结果[:5]:
            说(f"  {r['tag_name']}｜{r['name']}｜" +
               ("草稿" if r.get("draft") else "已发布") +
               f"｜附件 {len(r.get('assets') or [])} 个")
            for a in (r.get("assets") or []):
                说(f"      - {a['name']}（{a['size'] / 1048576:.1f} MB，"
                   f"下载 {a.get('download_count', 0)} 次）")
        return 0

    说明 = ""
    if Path(选项.说明).is_file():
        说明 = Path(选项.说明).read_text(encoding="utf-8")
        说(f"发布说明：{选项.说明}（{len(说明)} 字）")
    else:
        说(f"⚠️ 没有发布说明文件（{选项.说明}），Release 说明会是空的")

    附件 = [Path(p) for p in 选项.附件] if 选项.附件 \
        else sorted(发布目录.glob("*.zip"))
    附件 = [p for p in 附件 if p.is_file()]
    if not 附件:
        raise SystemExit(f"没有可上传的附件（找过 {发布目录}/*.zip）")
    说("待上传：")
    for p in 附件:
        说(f"  {p.name}（{p.stat().st_size / 1048576:.1f} MB）")

    Release = 找或建Release(令牌, 版本, 说明, 选项.草稿, 选项.预发布)
    好 = True
    for p in 附件:
        好 = 传附件(令牌, Release, p, 选项.续传) and 好

    说()
    说(f"Release 页面：{Release.get('html_url')}")
    (发布目录 / "发布报告.txt").write_text("\n".join(行) + "\n", encoding="utf-8")
    return 0 if 好 else 1


if __name__ == "__main__":
    raise SystemExit(main())
