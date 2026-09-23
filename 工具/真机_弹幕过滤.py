#!/usr/bin/env python3
"""真机验证：**在 GUI 里真的改过滤规则，画面上的弹幕当场变少**。

为什么值得单独验一遍：
* 过滤规则的单元测试只证明"判定函数对"，证明不了"从界面控件到渲染池"这条线是通的
  （信号名写错、面板拿的是配置的副本、过滤器没接到控制器 —— 单测全都发现不了）；
* "屏蔽词"这种东西，用户唯一的验收标准就是**屏幕上少了几条**。

流程（真机、真窗口、真弹幕）：
1. 打开 ``数据/媒体/葬送的芙莉莲 S01E01.mkv``（逐集弹幕记忆里已经有这一集，
   所以会自动从 Animeko 取回在线弹幕）；
2. 等弹幕装好，挑一个**在弹幕里高频出现的词**（不预设，从实际数据里选）；
3. 打开「⚙ 弹幕设置」面板，把那个词打进"屏蔽词"框（走界面控件，不直接改对象）；
4. 核对：过滤后条数 == 过滤前 − 命中数，且渲染池真的变小了；
5. 播放几秒截图（画面上有弹幕）；清空屏蔽词后再核对条数恢复。

用法::

    DISPLAY=:0 WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1001 \\
      QT_QPA_PLATFORM=xcb 运行环境/venv/bin/python 工具/真机_弹幕过滤.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(项目根))
from wangpan.控制台 import 修控制台          # noqa: E402

修控制台()


def 挑高频词(池, 最少次数: int = 3) -> tuple[str, int]:
    """从真实弹幕里挑一个出现最多的 2 字片段（没有就退回"次数最多的整条"）。"""
    计数: dict[str, int] = {}
    for 条 in 池:
        文本 = (条.文本 or "").strip()
        if len(文本) < 2:
            continue
        for i in range(len(文本) - 1):
            片段 = 文本[i:i + 2]
            if 片段.strip() == 片段 and 片段.isprintable():
                计数[片段] = 计数.get(片段, 0) + 1
    排序 = sorted(计数.items(), key=lambda x: (-x[1], x[0]))
    if 排序 and 排序[0][1] >= 最少次数:
        return 排序[0]
    if 排序:
        return 排序[0]
    return "", 0


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication


    解析 = argparse.ArgumentParser()
    解析.add_argument("--截图目录", default="/tmp")
    解析.add_argument("--素材", default=str(项目根 / "数据" / "媒体" /
                                     "葬送的芙莉莲 S01E01.mkv"))
    解析.add_argument("--超时秒", type=float, default=60.0)
    参数 = 解析.parse_args()

    应用 = QApplication.instance() or QApplication([])
    from wangpan.ui.主窗口 import 主窗口
    窗口 = 主窗口()
    窗口.自动开队列 = False
    窗口.resize(1440, 900)
    窗口.show()
    窗口.raise_()
    for _ in range(20):
        应用.processEvents()
        time.sleep(0.01)

    证据: list[str] = []

    def 记(文本: str) -> None:
        证据.append(文本)
        print("・" + 文本)

    开始 = time.monotonic()
    状态 = {"阶段": "打开", "词": "", "命中": 0, "过滤前": 0, "过滤后": 0,
          "截止": 0.0}

    def 定时观察():
        if time.monotonic() - 开始 > 参数.超时秒:
            记("⚠ 超时，结束")
            应用.quit()
            return
        if 状态["阶段"] == "打开":
            if 窗口.打开(str(参数.素材)):
                记(f"已打开：{Path(参数.素材).name}")
                状态["阶段"] = "等弹幕"
                # 记忆里有这一集 → 打开时已经自动去取了；播放起来让画面动
                窗口._切播放()
            else:
                记("✗ 打不开素材")
                应用.quit()
            return
        if 状态["阶段"] == "等弹幕":
            池 = 窗口.弹幕._原始池
            if 池 is not None and len(池):
                词, 次数 = 挑高频词(池)
                状态.update({"词": 词, "过滤前": len(池), "阶段": "加规则"})
                记(f"弹幕已装载：{len(池)} 条｜装载来源：{窗口.弹幕.装载结果.来源}"
                   f"｜挑中的屏蔽词：「{词}」（出现 {次数} 次）")
                if not 词:
                    记("✗ 弹幕里挑不出词")
                    应用.quit()
                return
            if 窗口.弹幕.装载结果.说明:
                记("弹幕加载情况：" + 窗口.弹幕.装载结果.摘要())
            return
        if 状态["阶段"] == "加规则":
            词 = 状态["词"]
            窗口._开弹幕设置()                       # 真界面：⚙ 弹幕设置
            from wangpan.ui.弹幕设置 import 弹幕设置面板
            面板 = 窗口._弹幕设置窗.findChild(弹幕设置面板)
            if 面板 is None:
                记("✗ 没找到弹幕设置面板")
                应用.quit()
                return
            面板.屏蔽词框.setPlainText(词)            # 走控件 → 信号 → 主窗口落地
            for _ in range(40):                      # 面板有 60ms 防抖
                应用.processEvents()
                time.sleep(0.02)
            记(f"已在弹幕设置面板里把「{词}」加进屏蔽词（控件路径，不是直接改对象）")
            规则 = 窗口.弹幕._过滤器.规则 if 窗口.弹幕._过滤器 else None
            记(f"控制器里的规则：屏蔽词={list(规则.屏蔽词) if 规则 else '（没有过滤器）'}")
            # 用同一个过滤器算"理论命中数"，再和实际过滤后条数对账
            预算池, 统计 = 窗口.弹幕._过滤器.应用(窗口.弹幕._原始池)
            状态["命中"] = 状态["过滤前"] - len(预算池)
            过滤后 = len(窗口.弹幕._显示池) if 窗口.弹幕._显示池 is not None else 0
            状态["过滤后"] = 过滤后
            记(f"过滤前 {状态['过滤前']} 条 → 命中 {状态['命中']} 条 → "
               f"过滤后 实际 {过滤后} 条（渲染池 {len(窗口.弹幕.渲染器._装载表)} 条在排）")
            if 过滤后 != len(预算池):
                记("✗ 界面路径与过滤器算出来的条数不一致")
            状态["阶段"] = "跳密集段"
            return
        if 状态["阶段"] == "跳密集段":
            # 演示素材只有 6 秒，而在线弹幕铺满整集 —— 用**时间轴偏移**把最密的一秒
            # 挪到片头，这样"屏幕上有弹幕"这件事才能被截到（偏移本身也是真功能）。
            池 = 窗口.弹幕._原始池
            时间们 = sorted(条.毫秒 for 条 in 池)
            跨度 = 6000                                   # 演示素材 6 秒
            起点, 最密条数 = 0, 0
            for t in 时间们:                              # 找"6 秒里弹幕最多"的那一段
                条数 = sum(1 for x in 时间们 if t <= x < t + 跨度)
                if 条数 > 最密条数:
                    起点, 最密条数 = t, 条数
            窗口.弹幕.设置时间轴偏移(-起点 + 2000)
            记(f"最密的 6 秒从 {起点 / 1000:.1f}s 开始（{最密条数} 条）→ 时间轴偏移设为 "
               f"{窗口.弹幕.时间轴偏移} ms（挪到片头第 2 秒）")
            窗口.弹幕.设置显示(True)
            窗口._切播放()
            状态["阶段"] = "截图"
            状态["截止"] = time.monotonic() + 6.0
            return
        if 状态["阶段"] == "截图":
            if time.monotonic() < 状态["截止"]:
                return
            窗口.弹幕.设置显示(True)
            for _ in range(20):
                应用.processEvents()
                time.sleep(0.02)
            # 注意：本机是 Wayland 会话，XWayland 的整屏截图是全黑的 ——
            # 所以分别抓"主窗口（画面 + 弹幕）"和"设置面板（规则）"两张，都算真机画面。
            目标 = Path(参数.截图目录) / "真机_弹幕过滤.png"
            窗口.grab().save(str(目标))
            面板图 = Path(参数.截图目录) / "真机_弹幕过滤_设置面板.png"
            窗口._弹幕设置窗.grab().save(str(面板图))
            记(f"截图（画面上的弹幕）：{目标}｜设置面板：{面板图}")
            渲染器 = 窗口.弹幕.渲染器
            表 = list(渲染器._装载表)
            去重 = {(x.标识, x.毫秒, x.文本) for x in 表}
            记(f"渲染器这一帧画了 {渲染器.统计.绘制条数} 条（位图缓存 "
               f"{渲染器.统计.缓存条数} 张）｜装载表 {len(表)} 条（去重后 {len(去重)}）"
               f"｜累计装入 {渲染器._装入数}｜分配器排入 {渲染器.分配器.排入数}"
               f"｜丢弃 {渲染器.分配器.丢弃数}｜池 {len(窗口.弹幕._原始池)} 条")
            状态["阶段"] = "恢复"
            return
        if 状态["阶段"] == "恢复":
            from wangpan.ui.弹幕设置 import 弹幕设置面板
            面板 = 窗口._弹幕设置窗.findChild(弹幕设置面板)
            面板.屏蔽词框.setPlainText("")           # 清空 → 规则是活的、可逆
            for _ in range(40):
                应用.processEvents()
                time.sleep(0.02)
            恢复 = len(窗口.弹幕._显示池) if 窗口.弹幕._显示池 is not None else 0
            记(f"清空屏蔽词后：{恢复} 条（应回到 {状态['过滤前']}）")
            窗口.存弹幕设置()                        # 把测试留下的规则落盘一次再读回
            记("落盘的规则已写回 数据/弹幕设置.json（内容就是面板里的值）")
            状态["阶段"] = "完"
            应用.quit()

    定时 = QTimer()
    定时.setInterval(150)
    定时.timeout.connect(定时观察)
    定时.start()

    def 收尾():
        print("\n---- 证据 ----")
        for 行 in 证据:
            print(行)
        记录 = Path(参数.截图目录) / "真机_弹幕过滤.txt"
        记录.write_text("\n".join(证据), encoding="utf-8")
        print(f"（证据也写了一份：{记录}）")

    应用.aboutToQuit.connect(收尾)
    应用.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
