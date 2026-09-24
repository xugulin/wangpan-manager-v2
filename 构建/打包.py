#!/usr/bin/env python3
"""打 Linux 绿色版发布包（解压即用，不装 Python、不装依赖、不写系统目录）。

   运行环境/venv/bin/python 构建/打包.py                 # 打正式包（不含 AI 语音模型）
   运行环境/venv/bin/python 构建/打包.py --含语音模型      # 本机想验完整版时才用
   运行环境/venv/bin/python 构建/打包.py --不打包只准备     # 只把暂存目录铺好，便于排查

为什么要有这个脚本（和 V1 那套的区别）
======================================
V1 的 `构建/打包.py` 是围绕 **VLC** 写的（内置 VLC 运行时、校验插件数量、
Windows 交叉构建）。整合之后播放内核是自研 libav*，**不再需要内置 VLC**；
但多了两件 V1 没有的事，必须在这里做掉：

1. **主 venv 要建在自带解释器上**。开发机上的 `运行环境/venv` 是
   `/usr/bin/python3 -m venv` 建的，`bin/python3 -> /usr/bin/python3` 是**绝对**链接 ——
   换台机器（或换个系统 python 版本）就打不开。发布包里改成：
   用包里自带的 `运行环境/python/bin/python3.14` 重新建 venv，
   再把装好的 `site-packages` 灌进去，最后把符号链接**改成相对的**。
2. **路径自愈**。venv 里天生留构建机绝对路径（`pyvenv.cfg`、`bin/*` 的 shebang）。
   包里统一改写成 `<项目根>` 占位，由 `启动.sh` 在启动时按实际位置补回来
   （见 启动.sh 的 `fix_venv`）。这样用户把解压出来的目录搬到哪儿都能用。

包内容（Linux）
==============
    启动.py / 启动.sh / 一键启动_网盘管理_V2.sh / 图标 / README / 依赖清单 …
    v8_3/ wangpan/ 适配器/ 工具/ tests/ docs/      ← 两套源码 + 三家适配器
    运行环境/python                                 ← 自带独立 CPython（基座）
    运行环境/venv                                   ← 重建在自带 python 上
    运行环境/本地模型                                ← 便携 ollama 基座（**不含模型权重**）
    运行环境/语音识别/{venv,测试音频,…}              ← Whisper 运行环境（**不含模型**）

不带的东西：`数据/`（用户数据）、`配置.json`（含密钥）、`.git/`、`__pycache__`、
AI 语音模型（461 MB，第一次用「AI 生成字幕」时按提示下载）、ollama 模型权重
（AI 页「模型商店」一键装）。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
发布目录 = 项目根 / "构建" / "发布"
暂存目录 = 发布目录 / "_暂存"
#: 包名用英文：GitHub 会把中文资源名清洗成 "-"（V1 的发布记录里踩过）
包名前缀 = "wangpan-manager"

#: 要带进包的源码/文档（相对项目根）
源码清单 = (
    "启动.py", "启动.sh", "启动.bat", "一键启动_网盘管理_V2.sh",
    "pyproject.toml", "requirements.txt", "README.md", "配置.example.json",
    "网盘管理_V2_图标.png", "使用说明.txt",
    "v8_3", "wangpan", "适配器", "工具", "tools", "tests", "docs",
    # ⚠️ 资源/ 是**图标素材**（hicolor 六个尺寸 + .ico）。第一版漏了它，
    #    结果"包内跑测试"时 3 条图标/快捷方式测试红了 —— 正是这一步的价值。
    "资源",
)

#: 一律不带的东西
忽略名 = {
    "__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".idea", ".vscode",
    "数据", "构建", "发布", "_暂存", ".github",
    # 自带 CPython 的 terminfo/man：跟本程序无关，而且 terminfo 里是 1038 个
    # "指向目录之外"的符号链接 —— 解压工具（7z）会为此刷 300 条
    # "Dangerous link path was ignored" 警告，看日志的人会以为包坏了。
    "terminfo", "man",
}

行: list[str] = []


def 说(文本: str = "") -> None:
    print(文本, flush=True)
    行.append(str(文本))


def 读版本() -> str:
    """版本号只从 pyproject.toml 读一处（别在多处写死，改一处漏一处）。"""
    文本 = (项目根 / "pyproject.toml").read_text(encoding="utf-8")
    找到 = re.search(r'^version\s*=\s*"([^"]+)"', 文本, re.M)
    return 找到.group(1) if 找到 else "0.0.0"


def 忽略函数(目录: str, 名称: list[str]) -> set[str]:
    丢 = {n for n in 名称 if n in 忽略名}
    # 模型权重不进包（几百 MB，用户按需自己下）；数据/ 已经在上面的忽略名里
    if Path(目录).name == "模型":
        丢 |= set(名称)
    return 丢


def 拷源码(顶层: Path) -> int:
    个数 = 0
    for 名 in 源码清单:
        源 = 项目根 / 名
        if not 源.exists():
            说(f"  ⚠️ 清单里的 {名} 不存在，跳过")
            continue
        目标 = 顶层 / 名
        if 源.is_dir():
            shutil.copytree(源, 目标, ignore=忽略函数, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(源, 目标)
        个数 += 1
    return 个数


def 建可移植运行环境(顶层: Path) -> None:
    """自带 python + 在它之上重建的 venv（并把符号链接改成相对的）。"""
    源运行环境 = 项目根 / "运行环境"
    目标运行环境 = 顶层 / "运行环境"
    目标运行环境.mkdir(parents=True, exist_ok=True)

    # ---- 自带独立 CPython（基座）----
    自带 = 目标运行环境 / "python"
    if not (自带 / "bin" / "python3.14").exists():
        说("  拷自带 CPython（基座解释器，约 110 MB）…")
        shutil.copytree(源运行环境 / "python", 自带, ignore=忽略函数, symlinks=True)

    # ---- 主 venv：用**包内**的自带 python 建，再灌入装好的包 ----
    venv = 目标运行环境 / "venv"
    if venv.exists():
        shutil.rmtree(venv)
    说("  用包内自带 python 重建主 venv（不联网：--without-pip 后灌 site-packages）…")
    结果 = subprocess.run([str(自带 / "bin" / "python3.14"), "-m", "venv",
                          "--without-pip", str(venv)],
                        capture_output=True, text=True, timeout=300)
    if 结果.returncode != 0:
        raise SystemExit(f"建 venv 失败：{结果.stderr[-400:]}")

    源站点 = 源运行环境 / "venv" / "lib" / "python3.14" / "site-packages"
    目标站点 = venv / "lib" / "python3.14" / "site-packages"
    目标站点.mkdir(parents=True, exist_ok=True)
    说(f"  灌入 site-packages（{sum(1 for _ in 源站点.iterdir())} 项）…")
    for 项 in 源站点.iterdir():
        if 项.name in ("__pycache__",):
            continue
        落点 = 目标站点 / 项.name
        if 项.is_dir() and not 项.is_symlink():
            shutil.copytree(项, 落点, ignore=忽略函数, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(项, 落点, follow_symlinks=False)
    # 源码清单里的 .dist-info 记录着构建机路径，不影响运行；但 pip 的 RECORD 会让
    # "卸载/重装"报错，所以把 venv 里的包管理留给 requirements.txt，不做处理。

    # ---- 把 venv 的 python 符号链接改成**相对**（绝对链接搬走就断）----
    修相对链接(venv, 相对到自带=Path("..") / ".." / "python" / "bin" / "python3.14")

    # ---- 语音识别环境（Whisper）----
    源语音 = 源运行环境 / "语音识别"
    if 源语音.is_dir():
        目标语音 = 目标运行环境 / "语音识别"
        说("  拷语音识别环境（不含模型权重）…")
        for 名 in ("venv", "测试音频", "基准测试.py"):
            源 = 源语音 / 名
            if not 源.exists():
                continue
            if 源.is_dir():
                shutil.copytree(源, 目标语音 / 名, ignore=忽略函数,
                                symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(源, 目标语音 / 名)
        修相对链接(目标语音 / "venv",
                 相对到自带=Path("..") / ".." / ".." / "python" / "bin" / "python3.14")

    # ---- 便携 ollama 基座（不含模型权重）----
    源模型 = 源运行环境 / "本地模型"
    if 源模型.is_dir():
        说("  拷便携 ollama 基座（不含模型权重）…")
        shutil.copytree(源模型, 目标运行环境 / "本地模型",
                        ignore=忽略函数, symlinks=True, dirs_exist_ok=True)


def 修相对链接(venv: Path, 相对到自带: Path) -> None:
    """把 venv/bin 里指向解释器的**绝对**符号链接改成相对链接。

    为什么必须改：`python -m venv` 生成的 `bin/python3.14` 是指向"建 venv 时那个
    解释器"的**绝对**路径。包解压到别的目录后，那个绝对路径就不存在了 ——
    表现是 `运行环境/venv/bin/python: No such file or directory`。
    """
    bin目录 = venv / "bin"
    if not bin目录.is_dir():
        return
    修了 = 0
    for 链接 in bin目录.iterdir():
        if not 链接.is_symlink():
            continue
        目标 = os.readlink(链接)
        if not 目标.startswith("/"):
            continue                      # 已经是相对链接
        # 只认"指向某个 python 可执行文件"的那些（python / python3 / python3.14）
        if "python" not in Path(目标).name:
            continue
        链接.unlink()
        链接.symlink_to(相对到自带)
        修了 += 1
    if 修了:
        说(f"    {venv.relative_to(暂存目录)}：把 {修了} 个 python 符号链接改成相对")


def 抹掉构建机路径(顶层: Path) -> int:
    """把包内**运行环境**里的构建机绝对路径换成 `<项目根>` 占位。

    为什么要做：`pyvenv.cfg`、`bin/*` 的 shebang、`_sysconfigdata` 里都会留
    `/home/xgl/python/网盘管理_V2/...`。既是隐私，也让"搬到别处还能不能用"变得
    靠运气。运行时由 `启动.sh` 的 `fix_venv` 按实际位置补回来。

    ⚠️ 只动 `运行环境/`：源码和文档是项目自己的文本，改了就成假的了。
    """
    import re as _re
    模式 = _re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\Users\\)[^\s\"'`;:)]+")
    改了 = 0
    for 根, 目录们, 文件们 in os.walk(顶层):
        路径根 = Path(根)
        if 路径根 != 顶层 and "运行环境" not in 路径根.parts:
            目录们[:] = []
            continue
        目录们[:] = [d for d in 目录们 if d != "__pycache__"]
        for 名 in 文件们:
            路径 = Path(根) / 名
            try:
                头部 = 路径.open("rb").read(8192)
            except Exception:  # noqa: BLE001
                continue
            if b"\x00" in 头部:            # 二进制跳过
                continue
            try:
                原 = 路径.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                continue
            行们 = 原.split("\n")
            for i, 行_ in enumerate(行们[:3]):        # shebang 可能不在第一行（实测踩过）
                if 行_.startswith("#!") and 模式.search(行_):
                    行们[i] = "#!/usr/bin/env python3"
            新 = 模式.sub("<项目根>", "\n".join(行们))
            if 新 == 原:
                continue
            try:
                路径.write_text(新, encoding="utf-8")
                改了 += 1
            except Exception:  # noqa: BLE001
                continue
    return 改了


def 解压即用自检(顶层: Path) -> tuple[bool, str]:
    """在**暂存目录本身**跑一遍：这是"解压到任意路径能不能用"的最直接证据。

    注意跑的是暂存目录里的解释器与源码，不是开发机的 —— 所以它能抓到
    "符号链接还是绝对路径""依赖没灌进去""shebang 指向构建机"这类问题。
    """
    py = 顶层 / "运行环境" / "venv" / "bin" / "python"
    if not py.exists():
        return False, f"包里没有 venv 解释器：{py}"
    环境 = dict(os.environ)
    环境.update({"QT_QPA_PLATFORM": "offscreen", "PYTHONUNBUFFERED": "1",
                "PYTHONUTF8": "1"})
    环境.pop("DISPLAY", None)
    环境.pop("WAYLAND_DISPLAY", None)
    脚本 = ("import sys;"
          "sys.path.insert(0, '.');"
          "import v8_3.配置 as c, wangpan;"
          "from PySide6 import __version__ as v;"
          "print('PySide6', v);"
          "print('项目根', c.项目根);"
          "print('网盘', [x['标识'] for x in c.网盘实例列表(c.加载配置()) if x['启用']])")
    结果 = subprocess.run([str(py), "-c", 脚本], cwd=str(顶层),
                        capture_output=True, text=True, timeout=300, env=环境)
    if 结果.returncode != 0:
        return False, (结果.stdout + 结果.stderr).strip()[-600:]
    return True, 结果.stdout.strip()


def 打包(顶层: Path, 输出: Path, 压缩级别: int = 9) -> Path:
    输出.parent.mkdir(parents=True, exist_ok=True)
    if 输出.exists():
        输出.unlink()
    说(f"  用 7z 压成 {输出.name}（-mx={压缩级别} -snl，这一步最慢）…")
    开始 = time.time()
    # ⚠️ `-snl` 必须有：不加它，7z 会把**符号链接解引用成文件本体**。
    #    后果一（致命）：`运行环境/venv/bin/python3` 本来是 `-> /usr/bin/python3` 的链接，
    #    被存成 32MB 的 python 二进制副本；解压后那个副本按自己的位置去找 lib/python3.14
    #    → `Could not find platform independent libraries <prefix>` +
    #    `ModuleNotFoundError: No module named 'encodings'`，包直接不可用。
    #    后果二（体积）：`venv/lib64 -> lib` 被整份复制（+756MB），
    #    上千个 Qt/ollama 的 .so 链接也各存一份。
    #    实测：不加 -snl 是 974MB 且**解压即坏**；加了三处都对。
    结果 = subprocess.run(["7z", "a", "-tzip", f"-mx={压缩级别}", "-mmt=on", "-snl",
                          str(输出), "."],
                        cwd=str(顶层), capture_output=True, text=True, timeout=7200)
    if 结果.returncode != 0 or not 输出.is_file():
        raise SystemExit(f"7z 打包失败：{(结果.stdout + 结果.stderr)[-500:]}")
    说(f"  压缩完成：{输出.stat().st_size / 1048576:.1f} MB，"
       f"用时 {time.time() - 开始:.0f}s")
    核对包内是链接不是副本(输出)
    return 输出


def 核对包内是链接不是副本(输出: Path) -> None:
    """钉住"包里的符号链接必须是链接"这条 —— 它是"解压即用"的前提。

    判据：`运行环境/venv/bin/python3` 在 zip 里应该只有几十字节（链接内容），
    而不是 30 MB 级的可执行文件本体。
    """
    import zipfile
    要查 = ("运行环境/venv/bin/python3", "运行环境/venv/lib64")
    try:
        with zipfile.ZipFile(输出) as 包:
            条目 = {i.filename.rstrip("/"): i for i in 包.infolist()}
    except Exception as 错:  # noqa: BLE001
        说(f"  ⚠️ 读不了压缩包做链接核对：{错}")
        return
    坏 = []
    for 名 in 要查:
        命中 = next((i for k, i in 条目.items()
                   if k.endswith(名) or k.endswith(名 + "/")), None)
        if 命中 is None:
            continue
        if 命中.file_size > 4096:
            坏.append(f"{名}（包内 {命中.file_size} 字节，看着像被解引用成了本体）")
    if 坏:
        raise SystemExit("❌ 压缩包里符号链接被解引用（多半漏了 -snl）：\n  "
                         + "\n  ".join(坏))
    说("  ✔ 链接核对：包内是符号链接（不是副本）")



def 写使用说明(顶层: Path, 版本: str) -> None:
    (顶层 / "使用说明.txt").write_text(f"""网盘管理 {版本}（Linux 绿色版 · 解压即用）
{'=' * 56}

一、怎么启动
    解压到任意目录后，双击 `一键启动_网盘管理_V2.sh`；
    或者在终端里：
        ./启动.sh                     # 图形界面
        ./启动.sh 某个视频.mp4         # 直接开播
        ./一键启动_网盘管理_V2.sh --check   # 只自检（排查"双击没反应"）

    不需要装 Python、不需要装依赖、不写系统目录（全在解压出来的文件夹里）。

二、第一次要自己做的三件事
    1. 网盘登录：打开「网盘」页 → 点「登录 / 管理」→ 扫码或 Cookie 登录。
       （登录凭证只存在本目录的 `适配器/*/数据/` 里）
    2. AI（可选）：AI 页「AI设置」填任一 OpenAI 兼容网关的密钥；
       或者用内置的本地小模型：AI 页「模型商店」一键装（免费、离线、不必联网）。
    3. 刮削/弹幕（可选）：见 README 的「需要你自己提供凭据的两处」。
       不配置也能用：本地 NFO/图片照样入库，弹幕有 Animeko 公益源（零凭据）。

三、没有随包带的东西（按需下载）
    * AI 语音模型（Whisper，约 461 MB）：第一次用「AI 生成字幕」时按提示装；
      运行环境已经在包里了，只差模型权重。
    * 本地大模型权重：AI 页「模型商店」按需装（ollama 基座已随包）。

四、常见问题
    * 双击没反应：在终端里跑 `一键启动_网盘管理_V2.sh --check` 看自检结果。
    * 换了目录/换了机器：直接搬走整个文件夹即可，启动脚本会自己修正内部路径。
    * 想装额外的 Python 包：
        ./运行环境/venv/bin/python -m pip install <包名>

五、快捷键
    Ctrl+1..8  网盘 / 传输 / 播放 / 媒体库 / 敏感词 / AI / 日志 / 设置
    Ctrl+Q     退出
    空格 播放暂停 ｜ ←/→ 快退快进 ｜ . 逐帧 ｜ [ ] 调速 ｜ F 全屏 ｜ Esc 退出全屏
""", encoding="utf-8")


def main() -> int:
    解析 = argparse.ArgumentParser(description="打 Linux 绿色版发布包")
    解析.add_argument("--含语音模型", action="store_true",
                   help="把 运行环境/语音识别/模型 也带进包（几百 MB，本机验证用）")
    解析.add_argument("--不打包只准备", action="store_true",
                   help="只铺好暂存目录，不压缩（排查问题用）")
    选项 = 解析.parse_args()

    版本 = 读版本()
    说("=" * 60)
    说(f"打 Linux 绿色版：网盘管理 {版本}")
    说("=" * 60)
    if 暂存目录.exists():
        说(f"清掉旧的暂存目录 {暂存目录.relative_to(项目根)} …")
        shutil.rmtree(暂存目录)
    暂存目录.mkdir(parents=True, exist_ok=True)

    说("[1] 拷源码 / 适配器 / 工具 / 测试 / 文档")
    说(f"  带了 {拷源码(暂存目录)} 个顶层条目")

    说("[2] 铺可移植运行环境（自带 python + 重建 venv + ollama + whisper 环境）")
    建可移植运行环境(暂存目录)

    说("[3] 抹掉构建机绝对路径（换成 <项目根> 占位，启动时自愈）")
    说(f"  改了 {抹掉构建机路径(暂存目录)} 个文件")

    说("[4] 写使用说明")
    写使用说明(暂存目录, 版本)

    说("[5] 解压即用自检（用**包内**的解释器与源码跑一遍）")
    好, 说明 = 解压即用自检(暂存目录)
    说(("  ✔ 通过：" if 好 else "  ❌ 失败：") + 说明.replace("\n", " ｜ "))
    if not 好:
        说("  ⚠️ 自检没过就别发布 —— 先把上面的报错修掉")

    说("[6] 体积分布")
    for 名 in ("运行环境/venv", "运行环境/python", "运行环境/语音识别",
             "运行环境/本地模型", "适配器", "v8_3", "wangpan"):
        路径 = 暂存目录 / 名
        if 路径.exists():
            大小 = subprocess.run(["du", "-sh", str(路径)], capture_output=True,
                                text=True).stdout.split()[0]
            说(f"  {名:22s} {大小}")
    总 = subprocess.run(["du", "-sh", str(暂存目录)], capture_output=True,
                      text=True).stdout.split()[0]
    说(f"  {'合计':22s} {总}")

    if 选项.不打包只准备:
        说("\n（--不打包只准备：跳过压缩）")
        说(f"暂存目录：{暂存目录}")
        return 0

    输出 = 发布目录 / f"{包名前缀}-{版本}-Linux.zip"
    说("[7] 压缩")
    打包(暂存目录, 输出)

    (发布目录 / "打包报告.txt").write_text("\n".join(行) + "\n", encoding="utf-8")
    说(f"\n产物：{输出}")
    说(f"报告：{发布目录 / '打包报告.txt'}")
    return 0 if 好 else 1


if __name__ == "__main__":
    raise SystemExit(main())
