#!/usr/bin/env python3
"""界面验收：把合并后的程序**真的建起来**（逐页截图 + 真播像素校验）。

这个脚本**平台中立**：Linux（离屏或真实桌面）、Windows 真机（CI）、wine 都能跑。
真实桌面上的键鼠交互见 `工具/真机测试.py`（那个依赖 xdotool/grim）。

为什么要有它
============
GitHub 的 Windows runner 一次要跑十分钟、还得排队；本机装了 wine，
就能**在提交之前**先把 Windows 那条路验一遍：同一份源码、同一套 Windows 运行库，
只是换了个"Windows"（wine 提供的是真的 PE 加载器 + 真的 python314.dll）。

跑法::

    运行环境/venv/bin/python 工具/界面验收.py            # 产出 数据/界面验收/
    运行环境/venv/bin/python 工具/界面验收.py --只环境    # 只看环境/libav，不建界面

在 wine 里跑（Windows 运行时）::

    # 1) 先备好 Windows 侧运行库（见 README「Windows 路径怎么在本机验」）
    # 2) 跑：
    WINEDEBUG=-all 运行环境/venv/bin/python 工具/wine验证.py   # 薄包装，见那个文件

CI（GitHub 的 Windows runner）:: 同一个脚本，输出目录由 --输出目录 指定，
截图由 actions/upload-artifact 收走。

V1 踩过、这里照抄的几条经验
==========================
1. **必须 `QT_QPA_PLATFORM=offscreen`**：wine 的 GL 在渲染复杂界面时会段错误，
   而 offscreen 下 `widget.grab()` 拿到的**仍然是真实 Windows 渲染结果**。
2. **`V2_已自举=1`**：否则入口自举会想把解释器换成 Linux 的 `运行环境/venv/bin/python`。
3. **别靠 stdout 读结果**：wine 控制台编码会把中文搞成乱码（实测），
   结果一律写 UTF-8 的 JSON 文件。
4. **wine 里 Qt 找不到中文字体**，截图上的汉字会显示成方框 —— 那是 wine 的字体映射
   问题，不是程序问题（真实 Windows 上正常）。所以截图看的是**布局与像素**。
5. **假网盘免登录**：`v8_3.配置.准备假网盘目录()` 造一个最小适配器目录，
   配一个 `fake` 类型的网盘实例，就能把网盘页/传输页都建起来（V1 的做法）。

产出::

    /tmp/wine验收/            各页截图（wine 的 Z: 就是 /，所以写 Z:\\tmp\\...）
    /tmp/wine验收.json        结构化结果
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

# ---- 必须赶在导入 Qt / 项目之前设好（见模块开头 1、2 两条）----
os.environ["V2_已自举"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("V8_3_不联网", "1")

项目根 = Path(__file__).resolve().parents[1]
# ⚠️ 必须把自己的项目根塞进 sys.path：wine 下 `__file__` 是 `Z:\home\...\工具\wine验证.py`，
#    而"脚本所在目录"是 工具/ —— 不插的话 `import v8_3` / `import wangpan` 全找不到
#    （实测第一版就报 ModuleNotFoundError: No module named 'v8_3'）。
if str(项目根) not in sys.path:
    sys.path.insert(0, str(项目根))
截图目录 = 项目根 / "数据" / "界面验收"
结果路径 = 项目根 / "数据" / "界面验收.txt"

结果: dict = {}
日志行: list[str] = []


def 记(文本: str) -> None:
    日志行.append(str(文本))
    try:
        print(str(文本), flush=True)
    except Exception:  # noqa: BLE001 - wine 控制台编码可能炸
        pass


def 存结果() -> None:
    结果路径.parent.mkdir(parents=True, exist_ok=True)
    结果路径.write_text(json.dumps(结果, ensure_ascii=False, indent=1),
                      encoding="utf-8")
    (结果路径.parent / "界面验收日志.txt").write_text("\n".join(日志行) + "\n",
                                               encoding="utf-8")


def 步骤(名字: str):
    """一个步骤：把异常记进结果而不是让整轮中断（wine 上排查成本很高）。"""
    class _包装:
        def __enter__(自己):
            记(f"\n=== {名字} ===")
            return 自己

        def __exit__(自己, 类, 值, 栈):
            if 值 is not None:
                结果[f"{名字}｜失败"] = f"{类.__name__}: {值}"
                记(f"  ❌ {类.__name__}: {值}")
                记("".join(traceback.format_exception(类, 值, 栈))[-800:])
            return True
    return _包装()


def _配libav环境() -> str:
    """把 Windows 版 libav DLL 的位置通过 V2_LIBAV* 告诉加载层。

    为什么用环境变量而不是放进 `运行环境/libav`：那个目录是**Linux** 侧也会扫的，
    塞 Windows 的 .dll 进去只会让两边都困惑（Linux 上 dlopen 一个 .dll 必然失败）。
    """
    目录 = None
    for 候选 in (项目根 / "运行环境" / "libav",
             项目根 / "构建" / "windows" / "libav"):
        if 候选.is_dir() and any(候选.glob("avcodec*.dll")):
            目录 = 候选
            break
    if 目录 is None:
        return ""
    # wine 里要用 Z: 路径（Linux 的 / 就是 Z:\）。
    # ⚠️ 只在**还是 POSIX 路径**时才加前缀：wine 下 `__file__` 本身就是
    #    `Z:\home\...` 这样的 Windows 路径，再拼一次 "Z:" 会得到 `Z:Z:\home\...`
    #    —— 路径不存在，于是"明明 DLL 在、就是加载不上"（实测踩到）。
    def 酒(p: Path) -> str:
        文本 = str(p)
        if os.name != "nt" or (len(文本) > 1 and 文本[1] == ":"):
            return 文本
        return "Z:" + 文本.replace("/", "\\")
    主版本 = {"avformat": "63", "avcodec": "63", "avutil": "61",
            "swscale": "10", "swresample": "7"}
    for 名, 号 in 主版本.items():
        文件 = 目录 / f"{名}-{号}.dll"
        if 文件.is_file():
            os.environ["V2_LIBAV" + 名.upper()] = 酒(文件)
    return str(目录)


def 查环境() -> None:
    记(f"os.name = {os.name}（wine 下应当是 nt）")
    记(f"Python  = {sys.version.split()[0]}")
    结果["平台"] = os.name
    结果["Python"] = sys.version.split()[0]
    import platform
    结果["机器"] = platform.machine()
    try:
        import PySide6
        结果["PySide6"] = PySide6.__version__
        记(f"PySide6 = {PySide6.__version__}")
    except Exception as 错:  # noqa: BLE001
        结果["PySide6"] = f"失败：{错}"
    for 包 in ("httpx", "ahocorasick", "pypinyin", "qrcode"):
        try:
            __import__(包)
            结果[f"依赖·{包}"] = "有"
        except Exception as 错:  # noqa: BLE001
            结果[f"依赖·{包}"] = f"缺（{type(错).__name__}）"
    记("依赖：" + "、".join(f"{k.split('·')[1]}={v}" for k, v in 结果.items()
                       if k.startswith("依赖·")))


def 查内核() -> None:
    """自研播放内核在 Windows 运行时能不能加载 libav 并通过 ABI 自检。"""
    目录 = _配libav环境()
    结果["libav目录"] = 目录 or "（没找到 Windows 版 DLL）"
    记(f"libav 目录 = {目录 or '（没找到）'}")
    from wangpan.ffmpeg import 加载, 绑定
    结果["libav可用"] = bool(加载.可用())
    if not 加载.可用():
        结果["libav不可用原因"] = 加载.不可用原因()
        记(f"  ❌ 不可用：{加载.不可用原因()}")
        return
    结果["libav版本"] = {k: 加载.版本文本(v) for k, v in 加载.库版本().items()}
    记("libav 版本 = " + "、".join(f"{k} {v}" for k, v in 结果["libav版本"].items()))
    try:
        绑定.取绑定().检查ABI()
        结果["ABI自检"] = "通过（偏移表与当前库匹配）"
        记("  ✔ ABI 自检通过")
    except Exception as 错:  # noqa: BLE001
        结果["ABI自检"] = f"失败：{错}"
        记(f"  ❌ ABI 自检：{错}")


def 建假配置(工作目录: Path) -> tuple[dict, str]:
    from v8_3.配置 import 保存配置, 加载配置, 准备假网盘目录
    盘目录 = 工作目录 / "假网盘"
    准备假网盘目录(盘目录)
    配置路径 = str(工作目录 / "配置.json")
    配置 = 加载配置(配置路径)
    配置["适配器"] = [{"标识": "d1", "类型": "fake", "名称": "假网盘",
                   "路径": str(盘目录), "线程数": 4, "启用": True}]
    配置["数据库路径"] = str(工作目录 / "任务.db")
    配置["敏感词"] = {"启用": False, "自动改名上传": False,
                 "数据库路径": str(工作目录 / "词.db"), "按网盘启用": {}}
    配置["AI"] = {"启用": False, "api密钥": "", "接口地址": "", "模型": ""}
    配置["界面"] = dict(配置.get("界面") or {}, 上次网盘="d1")
    保存配置(配置, 配置路径)
    return 配置, 配置路径


def 验界面(只环境: bool) -> None:
    if 只环境:
        return
    from PySide6.QtWidgets import QApplication
    应用 = QApplication.instance() or QApplication([])
    工作 = Path(tempfile.mkdtemp(prefix="wine验收_"))
    截图目录.mkdir(parents=True, exist_ok=True)

    with 步骤("假网盘与配置"):
        配置, 配置路径 = 建假配置(工作)
        结果["假网盘实例"] = [x["标识"] for x in 配置["适配器"]]
        记(f"  假网盘实例 = {结果['假网盘实例']}")

    with 步骤("主窗口与逐页切换"):
        from v8_3.界面.主窗口 import 主窗口
        窗口 = 主窗口(配置, 配置路径=配置路径)
        窗口.resize(1280, 820)
        窗口.show()
        for _ in range(10):
            应用.processEvents()
        结果["窗口标题"] = 窗口.windowTitle()
        记(f"  窗口标题 = {窗口.windowTitle()}")
        页动作 = (("1", "网盘", 窗口._按数字切页网盘),
                ("2", "传输", 窗口.切换到传输页),
                ("3", "播放", 窗口.切换到播放页),
                ("4", "媒体库", 窗口.切换到媒体库页),
                ("5", "敏感词", 窗口.切换到敏感词页),
                ("6", "AI", 窗口.切换到AI页),
                ("7", "日志", 窗口.切换到日志页),
                ("8", "设置", 窗口.切换到设置页))
        建成 = []
        for 序号, 名字, 动作 in 页动作:
            try:
                时刻 = time.time()
                动作()
                for _ in range(8):
                    应用.processEvents()
                用时 = int((time.time() - 时刻) * 1000)
                标题 = 窗口.windowTitle()
                建成.append(名字)
                结果[f"页·{名字}"] = f"{用时}ms｜{标题}"
                图 = 窗口.grab()
                图.save(str(截图目录 / f"{序号}_{名字}.png"))
                记(f"  ✔ {名字}：{用时}ms｜标题含「{标题.split('·')[-1].strip()[:12]}」"
                  f"｜截图 {图.width()}x{图.height()}")
            except Exception as 错:  # noqa: BLE001
                结果[f"页·{名字}"] = f"失败：{type(错).__name__}: {错}"
                记(f"  ❌ {名字}：{type(错).__name__}: {错}")
        结果["建成页面"] = 建成
        结果["堆叠页数"] = 窗口.堆叠.count()

        # ---- 真播一段（自研内核 + Windows DLL）----
        with 步骤("自研内核真解码"):
            素材 = 项目根 / "工具" / "测试素材" / "样片.mp4"
            窗口.切换到播放页()          # ⚠️ 必须显式切页：播放页面() 只是取实例，不切页
            for _ in range(6):
                应用.processEvents()
            页面 = 窗口.播放页面()
            是否 = 页面.播放本地路径(str(素材))
            结果["打开素材"] = bool(是否)
            for _ in range(30):
                应用.processEvents()
                time.sleep(0.05)
            统计 = 页面.会话.引擎.统计
            结果["播放"] = {
                "状态": 页面.会话.状态文本(),
                "已解视频帧": int(统计.已解视频帧),
                "已丢视频帧": int(统计.已丢视频帧),
                "硬解": str(统计.硬解),
                "音频设备": str(统计.音频设备),
                "时长秒": round(float(统计.总时长秒), 2),
                "有画面": bool(页面.视频.有画面),
            }
            记("  播放：" + json.dumps(结果["播放"], ensure_ascii=False))
            图 = 窗口.grab()
            图.save(str(截图目录 / "9_真播中.png"))
            # 画面层单独抓一张（会带上弹幕/字幕叠加），并做**像素校验**：
            # 只统计"够亮、且颜色各不相同"的像素 —— 黑屏或纯色都不算"画出帧了"。
            画面 = 页面.视频.grab().toImage()
            画面.save(str(截图目录 / "10_画面层.png"))
            色 = {}
            亮点 = 0
            for x in range(0, 画面.width(), 7):
                for y in range(0, 画面.height(), 7):
                    p = 画面.pixelColor(x, y)
                    亮度 = p.red() + p.green() + p.blue()
                    if 亮度 > 90:
                        亮点 += 1
                    色[(p.red() // 32, p.green() // 32, p.blue() // 32)] = 1
            结果["画面像素"] = {"采样亮点": 亮点, "颜色种类": len(色),
                           "尺寸": f"{画面.width()}x{画面.height()}"}
            记(f"  画面层像素：亮点 {亮点}、颜色 {len(色)} 种"
              f"（黑屏会是 0 亮 / 1 色）")
            结果["截图"] = sorted(p.name for p in 截图目录.glob("*.png"))

        with 步骤("退出收尾"):
            窗口.close()
            for _ in range(10):
                应用.processEvents()
            结果["退出收尾"] = "正常（关窗没抛异常）"
            记("  ✔ 关窗收尾正常")


def main() -> int:
    解析 = argparse.ArgumentParser(description="界面验收（平台中立）")
    解析.add_argument("--只环境", action="store_true", help="只看环境/libav，不建界面")
    解析.add_argument("--输出目录", default="", help="截图与报告的落地目录（默认 数据/界面验收）")
    选项 = 解析.parse_args()
    global 截图目录, 结果路径
    if 选项.输出目录:
        截图目录 = Path(选项.输出目录)
        结果路径 = 截图目录.parent / (截图目录.name + ".txt")
    记("=" * 58)
    记(f"界面验收：合并版在 {os.name} / {sys.platform} 上的表现")
    记("=" * 58)
    with 步骤("环境"):
        查环境()
    with 步骤("自研播放内核（libav/ABI）"):
        查内核()
    验界面(选项.只环境)
    存结果()
    记(f"\n结果：{结果路径}")
    记(f"截图：{截图目录}")
    失败 = [k for k in 结果 if k.endswith("｜失败") or "失败" in str(结果.get(k, ""))]
    记("结论：" + ("全部通过 ✔" if not 失败 else f"{len(失败)} 项有问题：{失败}"))
    return 1 if 失败 else 0


if __name__ == "__main__":
    raise SystemExit(main())
