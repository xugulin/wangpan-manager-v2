"""播放页：**V2 自研内核** + V1 的那套播放界面（网盘浏览 / 清单 / 视频信息 / AI 助手）。

这一页是整合的核心：界面骨架、菜单动作表、AI 装配、播放清单都沿用原来那一套
（它们本来就不认识 libvlc —— 见 `docs/整合/02_V1界面架构.md` §4.0），
真正被换掉的只有**画面与播放器**：

* 画面：交给 :class:`wangpan.ui.播放页.播放页`（视频控件自绘 + 弹幕 + 字幕）；
* 播放器：交给 :class:`wangpan.ui.播放会话.播放会话`（自研 libav* 内核）。

随之删掉的东西（它们都是"把画布交给外部播放器"带来的）：
窗口句柄、播放出口、X 窗口映射判定、游离窗口巡检、输出模块回退、卡死看门狗。
画面跑到别的窗口里播放这种事现在**结构上不可能发生**。

界面仍然按老名字调用会话（`状态快照 / 采样并诊断 / 应用新参数 / …`），
所以 AI 播放顾问、字幕助手、播放清单、独立窗口都不用重写。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from wangpan.ui.播放会话 import 播放会话, 规则参数
from wangpan.ui.播放页 import 播放页 as 内核播放页

from ..播放.媒体信息 import 是视频文件
from .AI播放面板 import AI播放面板, AI字幕动作
from .全屏助手 import 全屏助手
from .定时 import 安全单发
from .播放清单 import 播放清单, 播放项
from .路径选择对话框 import 路径选择对话框
from .vlc风格 import 构建菜单栏
from .悬浮面板 import 悬浮面板窗口

#: 悬浮面板的宽度范围（原来钉在 splitter 里时的 260~460，现在可稍宽一点）
最小面板宽 = 260
最大面板宽 = 520
#: 第一次进播放页是否自动把悬浮面板摆出来。
#: **默认不摆**：面板是独立窗口，一进播放页就弹一个窗太打扰，而且真机实测
#: 它在启动阶段抢过一次焦点（键盘快捷键一度全进不去）。要就点「🗂 面板」
#: 或按 Ctrl+Shift+P —— 那个勾选框的状态与"没摆出来"也是一致的。
初始显示面板 = False


def 项目根目录() -> Path:
    """项目根（v8_3/ 的上一级）—— 所有项目内路径都从这里算，不写死绝对路径。"""
    return Path(__file__).resolve().parents[2]


class _控制条门面:
    """给 `主窗口` / 自检脚本用的兼容壳（它们会读 `播放页面.控制条.设置全屏图标`）。"""

    def __init__(self, 内核页: 内核播放页) -> None:
        self._页 = 内核页

    def 设置全屏图标(self, 全屏: bool) -> None:
        try:
            self._页.全屏按钮.setText("🗗 退出全屏" if 全屏 else "⛶ 全屏")
        except Exception:  # noqa: BLE001
            pass

    def 设置暂停图标(self, 播放中: bool) -> None:
        try:
            self._页.播放按钮.setText("⏸ 暂停" if 播放中 else "▶ 继续")
        except Exception:  # noqa: BLE001
            pass


class 播放页面(QWidget):
    """播放页：一个播放会话 + 一个内核播放页 + 一块 AI 面板。"""

    状态更新 = Signal(dict)
    #: 日志信号：探测/语音识别/AI 都会从各自线程回调日志，
    #: 直接在别的线程里碰控件是跨线程操作 —— 实测偶发段错误，所以走信号回界面线程。
    日志追加 = Signal(str)

    def __init__(self, 主窗口, 父=None):
        super().__init__(父)
        self.主窗口 = 主窗口
        self.动作 = 主窗口.动作
        self.配置 = getattr(主窗口, "配置", {})
        self.顾问 = None
        self.字幕助手 = None
        self.语音识别器 = None
        self.AI来源 = "规则（无可用 AI）"
        self.日志追加.connect(self._写日志, Qt.ConnectionType.QueuedConnection)
        self._诊断中 = False
        self._上次诊断时间 = 0.0
        self._上次诊断丢帧 = 0
        self._独立窗口 = None
        self._本次独立窗口 = False
        self._待跳章节: list = []
        self._全屏助手实例 = None

        # ---- 内核会话：把 V1 的探测/顾问/适配器注入进去 ----
        self.会话 = 播放会话(
            日志回调=self._AI写,
            取适配器=self.动作.适配器,
            顾问=None,                      # 在 _准备AI() 里装配
            自动调优=True,
            探测媒体=self._注入探测媒体,
            探测直链=self._注入探测直链,
        )
        # AI 观影动作（翻译/生成字幕/总结/诊断）—— 与独立窗口共用同一套实现
        self.AI动作 = AI字幕动作(
            取会话=lambda: self.会话,
            日志=self._AI写,
            状态=lambda 数据: self.状态更新.emit(dict(数据 or {})))

        self._构建()
        self.右栏 = self.左面板
        self.刷新网盘列表()
        self.定时器 = QTimer(self)
        self.定时器.setInterval(500)
        self.定时器.timeout.connect(self._刷新状态)
        self.定时器.start()
        self._准备AI()

    # ==================== 注入给内核的探测函数 ====================

    def _注入探测媒体(self, 地址, 头们, *, 本地: bool = False, 大小: int = 0):
        """媒体信息：本地文件与直链都用 ffprobe 探测（AI 顾问按这些字段决策）。"""
        from ..播放.媒体信息 import 探测媒体
        if 本地:
            return 探测媒体(地址)
        return 探测媒体(地址, dict(头们 or {}))

    def _注入探测直链(self, 地址, 头们, *, 本地: bool = False, 大小: int = 0):
        """直链探测：实测首字节/带宽/Range（**纯 Python**，不起外部命令）。"""
        from ..播放.直链探测 import 探测结果, 探测直链
        if 本地:
            return 探测结果(成功=True, 来源说明="本地文件", range支持=True,
                          内容长度=int(大小 or 0))
        return 探测直链(地址, dict(头们 or {}))

    # ==================== 构建 ====================

    def _构建(self) -> None:
        布局 = QVBoxLayout(self)
        布局.setContentsMargins(0, 0, 0, 0)
        布局.setSpacing(6)

        self.菜单栏 = 构建菜单栏(self._动作表(), self, 带侧栏按钮=True)
        布局.addWidget(self.菜单栏)
        self.工具栏 = None

        # ---- 路径栏（形态与「跨网盘传输」页的源/目标一致）----
        表单 = QHBoxLayout()
        表单.setSpacing(8)
        表单.addWidget(QLabel("网盘："))
        self.网盘框 = QComboBox()
        self.网盘框.setMinimumWidth(190)
        表单.addWidget(self.网盘框)
        表单.addWidget(QLabel("视频路径："))
        self.路径框 = QLineEdit("/")
        self.路径框.setPlaceholderText("网盘里的视频文件，例如 /电影/xxx.mp4")
        self.路径框.returnPressed.connect(self._播放)
        表单.addWidget(self.路径框, 1)
        self.浏览按钮 = QPushButton("📂 浏览…")
        self.浏览按钮.setToolTip("和传输页一样：在网盘目录里进进出出，选中视频文件")
        self.浏览按钮.clicked.connect(self._浏览)
        表单.addWidget(self.浏览按钮)
        布局.addLayout(表单)

        # ---- 主体：左＝画面与控制条（内核页），右＝视频信息/清单/AI ----
        主体 = QSplitter(Qt.Orientation.Horizontal)
        self.内核页 = 内核播放页(self.会话, 日志回调=self._AI写)
        # 兼容旧名字：`视频` 就是画面控件本身
        self.视频 = self.内核页.视频
        self.控制条 = _控制条门面(self.内核页)
        self.内核页.提示.connect(self._状态显示)
        self.内核页.要打开本地.connect(self._打开本地)
        self.内核页.要全屏.connect(self._设置全屏)
        self.内核页.标题变了.connect(lambda 名: self.状态标签.setText(f"🎬 {名}"))
        self.内核页.播放状态变了.connect(self._播放状态变了)

        # ---- 面板不再钉在右侧：它是个**独立悬浮窗口**（用户要求），
        #      所以主体里只有画面 + 控制条，画面能吃满主窗口宽度。----
        self.左面板 = QTabWidget()
        self.左面板.setMinimumWidth(最小面板宽)
        self.左面板.setMaximumWidth(最大面板宽)
        self._建信息面板()
        self.清单 = 播放清单(self.左面板)
        self.清单.请求播放.connect(self._清单选了某项)
        self.左面板.addTab(self.清单, "📋 播放清单")
        self.左面板.addTab(self._建AI面板(), "🤖 AI 助手")
        # ⚠️ 原先这里接了 `currentChanged → 安全单发(self, 60, self.重排)`，
        #    而 `重排` = `按视频比例调整窗口`，里面 `窗口.resize(...)` ——
        #    也就是说**在面板里换个标签页都会把主窗口改大小**（和"点面板窗口变形"
        #    是同一个 bug，只是入口不同）。现在面板是独立窗口，更不该动主窗口。
        self._面板窗口: 悬浮面板窗口 | None = None

        主体.addWidget(self.内核页)
        主体.setStretchFactor(0, 1)
        self.主体 = 主体
        布局.addWidget(主体, 1)

        self.状态标签 = QLabel("就绪")
        self.状态标签.setStyleSheet("font-size: 11px; color: #95a5a6;")
        布局.addWidget(self.状态标签)

        # ---- 兼容旧名字（自检/其它代码按这些名字找控件）----
        self.播放按钮 = self.内核页.播放按钮
        self.播放暂停按钮 = self.内核页.播放按钮
        self.停止按钮 = None
        self.上一个按钮 = self.内核页.上一集
        self.下一个按钮 = self.内核页.下一集
        self.进度条 = self.内核页.进度
        self.时间标签 = self.内核页.时间标签
        self.音量条 = self.内核页.音量
        self.倍速框 = self.内核页.倍速框
        self.字幕按钮 = self.内核页.字幕按钮
        self.截图按钮 = self.内核页.截图按钮
        self.全屏按钮 = self.内核页.全屏按钮
        self.独立窗口按钮 = QPushButton("🗗 独立窗口")
        self.独立窗口按钮.setToolTip("把画面搬到独立窗口播放（共享同一次播放）")
        self.独立窗口按钮.clicked.connect(self.独立窗口播放)
        self.本地按钮 = None
        # 交给内核页自己往"那一行"插（别自己去猜布局，会插到预览图上方）
        self.内核页.加控制按钮(self.独立窗口按钮, 在谁前面=self.内核页.全屏按钮)

    def _建信息面板(self) -> QWidget:
        页 = QWidget()
        竖 = QVBoxLayout(页)
        竖.setContentsMargins(4, 4, 4, 4)
        竖.setSpacing(4)
        self.信息栏 = QLabel("🅥 自研播放内核就绪（画面由本程序自己绘制）")
        self.信息栏.setWordWrap(True)
        self.信息栏.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.信息栏.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.信息栏.setStyleSheet(
            "font-size: 12px; padding: 6px; border-radius: 4px;"
            "background: #2c3e50; color: #ecf0f1;")
        竖.addWidget(self.信息栏)
        竖.addStretch(1)
        self.左面板.addTab(页, "ℹ️ 视频信息")
        return 页

    def _建AI面板(self) -> QWidget:
        self.AI面板 = AI播放面板(self, 自动调优=True, 紧凑=True)
        self.自动调优框 = self.AI面板.自动调优框
        self.翻译按钮 = self.AI面板.翻译按钮
        self.生字幕按钮 = self.AI面板.生字幕按钮
        self.总结按钮 = self.AI面板.总结按钮
        self.诊断按钮 = self.AI面板.诊断按钮
        self.AI输出 = self.AI面板.AI输出
        self.AI面板.翻译.connect(self._AI翻译字幕)
        self.AI面板.生成字幕.connect(self._AI生成字幕)
        self.AI面板.总结.connect(self._AI总结)
        self.AI面板.诊断.connect(self._手动诊断)
        self.自动调优框.toggled.connect(
            lambda 开: setattr(self.会话, "自动调优", bool(开)))
        return self.AI面板

    # ==================== VLC 风格动作表（菜单/工具栏共用） ====================

    def _动作表(self) -> dict:
        return {
            "打开网盘": self._浏览,
            "打开本地": self._打开本地,
            "加入清单": self.把当前加入清单,
            "添加本地到清单": self._本地加入清单,
            "退出": lambda: self.主窗口.close(),
            "播放暂停": self._播放暂停,
            "停止": self._停止,
            "上一个": self.上一个,
            "下一个": self.下一个,
            "逐帧": self.逐帧,
            "快进": lambda: self.相对跳转(10.0),
            "快退": lambda: self.相对跳转(-10.0),
            "到开头": lambda: self._跳转绝对(0.0),
            "跳到结尾": self.跳到结尾,
            "速度列表": lambda: [(f"{x}×", x) for x in
                            (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)],
            "当前速度": self.当前速度,
            "设置速度": self._设置速度,
            "章节数": self.章节数,
            "当前章节": self.当前章节,
            "跳章节": self.跳章节,
            "下一章": self.下一章,
            "上一章": self.上一章,
            "音量加": lambda: self._调音量(5),
            "音量减": lambda: self._调音量(-5),
            "静音切换": self._设置静音,
            "是否静音切换": self._是否静音,
            "音量": self.当前音量,
            "设置音量": self._设置音量,
            "音频轨列表": self._音频轨列表,
            "当前音频轨": lambda: self._当前轨("音频"),
            "选择音频轨": self._选择音频轨,
            "全屏": self._设置全屏,
            "是否全屏": lambda: bool(self.window().isFullScreen()),
            "截图": self._截图,
            "宽高比": self.当前宽高比,
            "设置宽高比": self._设置宽高比,
            "缩放": self.当前缩放,
            "设置缩放": self._设置缩放,
            "置顶窗口": self._切换置顶,
            "添加字幕文件": self._添加字幕文件,
            "字幕轨列表": self._字幕轨列表,
            "当前字幕轨": lambda: self._当前轨("字幕"),
            "选择字幕轨": self._选择字幕轨,
            "切换清单": self._切换清单,
            "切换AI面板": self._切换AI面板,
            "播放参数": self._显示播放参数,
            "AI诊断": self._手动诊断,
            "流畅优先": self.流畅优先,
            "用VLC自带窗口": self.用VLC自带窗口播放,
            # 独立窗口：整合后**一直没有界面入口**（方法在、菜单里没有），
            # 用户要"独立窗口播放时适应各种屏幕"，先得能打开它。
            "独立窗口": self.打开独立窗口,
            "收回独立窗口": self._收回独立窗口,
            "适应视频比例": self.按视频比例调整窗口,
            "切换自动调优": lambda 选中: self.自动调优框.setChecked(bool(选中)),
            "AI翻译字幕": self._AI翻译字幕,
            "AI生成字幕": self._AI生成字幕,
            "AI总结": self._AI总结,
            "AI帮助": lambda: self._AI写(
                "🤖 AI：翻译字幕 / 生成字幕 / 内容总结 / 卡顿诊断 / 自动换参数重载。"
                "免费离线走本地模型，没有则回落规则。"),
            "切换侧栏": self.切换侧栏,
            "循环切换": self._切换循环,
            "随机切换": self._切换随机,
        }

    # ==================== 网盘列表 / 选片 ====================

    def 刷新网盘列表(self) -> None:
        当前 = self.网盘框.currentData()
        self.网盘框.clear()
        try:
            规格表 = dict(self.动作.规格表 or {})
        except Exception:  # noqa: BLE001
            规格表 = {}
        for 标识, 规格 in 规格表.items():
            if not 标识:
                continue
            self.网盘框.addItem(str(getattr(规格, "显示名", "") or 标识), 标识)
        self.网盘框.insertItem(0, "💻 本地文件", "本地")
        if 当前:
            idx = self.网盘框.findData(当前)
            if idx >= 0:
                self.网盘框.setCurrentIndex(idx)
        elif self.网盘框.count():
            self.网盘框.setCurrentIndex(0)

    def _浏览(self) -> None:
        """用「跨网盘传输」页同款的路径选择对话框挑视频文件。"""
        标识 = str(self.网盘框.currentData() or "")
        if 标识 in ("", "本地"):
            self._打开本地()
            return
        规格 = self.动作.规格(标识) if 标识 else None
        if 规格 is None:
            self.状态标签.setText("⚠️ 请先选择网盘（左下角可新增网盘）")
            return
        try:
            适配器 = self.动作.适配器(标识)
        except Exception as 错:  # noqa: BLE001
            self.状态标签.setText(f"❌ 适配器不可用：{错}")
            return
        初始 = self.路径框.text().strip() or "/"
        # 路径框里大概率是**正在播的那个文件**；直接拿它当目录去列，桥会回"不是目录"。
        # 退到它的父目录，交互上也更符合直觉：从片子所在目录开始挑。
        if 是视频文件(Path(初始).name) or 初始.lower().endswith(
                (".srt", ".ass", ".ssa", ".vtt")):
            父 = str(Path(初始).parent)
            初始 = 父 if 父 and 父 != "." else "/"
            if not 初始.startswith("/"):
                初始 = "/" + 初始
        对话框 = 路径选择对话框(
            适配器, 标识, 初始路径=初始,
            允许选择文件=True,
            允许新建=False,
            标题=f"选择要播放的视频 · {规格.显示名}",
            说明="双击进入文件夹；选中视频文件后点「确定」即可播放。",
            只要文件=True,
            名字过滤=是视频文件,
            过滤提示="不是常见的视频文件（音频/图片/文档会被灰掉、不可选）",
            网盘列表=self.动作.规格表,
            取适配器=self.动作.适配器,
            父窗口=self)
        if 对话框.exec() == QDialog.DialogCode.Accepted and 对话框.选中路径:
            self.路径框.setText(对话框.选中路径)
            选中盘 = str(getattr(对话框, "选中网盘标识", "") or "")
            if 选中盘 and 选中盘 != 标识:
                idx2 = self.网盘框.findData(选中盘)
                if idx2 >= 0:
                    self.网盘框.setCurrentIndex(idx2)
            self._播放()

    def 播放指定视频(self, 标识: str, 远端路径: str, 独立窗口: bool = False) -> None:
        """外部入口（网盘页双击视频就走这里）。"""
        idx = self.网盘框.findData(标识)
        if idx >= 0:
            self.网盘框.setCurrentIndex(idx)
        self.路径框.setText(str(远端路径))
        self._播放(独立窗口=独立窗口)

    def _打开本地(self) -> None:
        路径, _ = QFileDialog.getOpenFileName(
            self, "选择本地视频", str(Path.home()),
            "视频 (*.mp4 *.mkv *.avi *.mov *.flv *.ts *.webm *.m4v);;所有文件 (*)")
        if not 路径:
            return
        self.路径框.setText(路径)
        self._播放(本地=True)

    def 播放本地路径(self, 路径: str) -> bool:
        """外部入口：直接播一个本地文件（媒体库海报墙/详情页点「播放」走这条）。"""
        self.路径框.setText(str(路径))
        idx = self.网盘框.findData("本地")
        if idx >= 0:
            self.网盘框.setCurrentIndex(idx)
        self._本次独立窗口 = False
        self._本次本地 = True
        好 = bool(self.内核页.打开(str(路径)))
        if 好:
            self._显示媒体信息()
            self.状态标签.setText(f"🎬 正在播放：{self.会话.标题}")
        return 好

    # ==================== 播放 ====================

    def _播放(self, 本地: bool = False, 独立窗口: bool = False) -> None:
        """起播入口：本地文件直接开，网盘文件先在后台"准备"（取直链+探测+问 AI）。"""
        标识 = "本地" if 本地 else str(self.网盘框.currentData() or "")
        路径 = self.路径框.text().strip()
        if not 路径:
            self.状态标签.setText("⚠️ 先填一个视频路径（或点「浏览…」）")
            return
        if 本地 and not Path(路径).is_file():
            self.状态标签.setText(f"⚠️ 本地文件不存在：{路径}")
            return
        self._本次独立窗口 = bool(独立窗口)
        self._本次本地 = bool(本地)
        self.播放按钮.setEnabled(False)

        if 本地:
            # 本地文件走内核页那条完整流程（同名字幕 / 本地弹幕 / 逐集记忆 / 续播 / 播放列表）
            self.状态更新.emit({"类型": "准备完成", "本地": True})
            return

        self.状态标签.setText(f"⏳ 正在准备：{路径}")
        self._AI写(f"[播放] 准备 {标识}：{路径}")
        会话 = self.会话

        def 干():
            try:
                摘要 = 会话.准备(标识, 路径)
                摘要["类型"] = "准备完成"
                self.状态更新.emit(摘要)
            except Exception as 错:  # noqa: BLE001
                self.状态更新.emit({"类型": "准备失败", "错误": str(错)})

        线程 = threading.Thread(target=干, name="播放准备", daemon=True)
        线程.start()

    def _真正起播(self) -> None:
        """准备完成之后真正打开内核并起播（在界面线程里做）。"""
        self.播放按钮.setEnabled(True)
        if getattr(self, "_本次本地", False):
            # 本地文件：交给内核页的完整流程（字幕/弹幕/续播/列表都由它打理）
            好 = self.内核页.打开(self.路径框.text().strip())
            if not 好:
                self.状态标签.setText("❌ 打不开这个本地文件")
                self.信息栏.setText("❌ 打不开这个本地文件")
                return
            self._显示媒体信息()
            self.状态标签.setText(f"🎬 正在播放：{self.会话.标题}")
            return
        if self._本次独立窗口 and self._独立窗口 is not None:
            self.独立窗口播放()
            return
        self.状态标签.setText(f"⏳ 正在起播：{self.会话.标题}")
        if not self.会话.起播(0):
            self.状态标签.setText("❌ 起播失败（看 AI 面板里的日志）")
            self.信息栏.setText("❌ 起播失败：打不开这条源，详见日志")
            return
        self.内核页.起播后同步()
        self._显示媒体信息()
        self.状态标签.setText(f"🎬 正在播放：{self.会话.标题}")
        self._AI写(f"[播放] 已起播：{self.会话.标题}"
                 f"（{self.会话.设置.摘要()}）")

    def 处理准备结果(self, 数据: dict) -> None:
        """后台准备线程回到界面线程后调用（由主窗口转发信号）。"""
        类型 = 数据.get("类型")
        if 类型 == "准备失败":
            self.播放按钮.setEnabled(True)
            原文 = str(数据.get("错误") or "")
            友好 = 原文
            if "是目录" in 原文 or "IsADirectoryError" in 原文:
                友好 = f"这是一条目录路径，不能直接播放：{原文}"
            elif "不存在" in 原文 or "FileNotFoundError" in 原文:
                友好 = f"网盘里找不到这个文件（可能被移动/改名）：{原文}"
            elif "超时" in 原文:
                友好 = f"取直链超时了，稍后重试或换一个文件：{原文}"
            self.状态标签.setText(f"❌ 准备失败：{友好[:80]}")
            self.信息栏.setText(f"❌ {友好}")
            self._AI写(f"[播放] 准备失败：{友好}")
            return
        if 类型 == "准备完成":
            self._真正起播()
            return
        if 类型 == "调优建议":
            self._处理调优建议(数据)
            return
        if 类型 == "字幕提示":
            self._AI写(f"[字幕] {数据.get('说明', '')}")
            self.状态标签.setText(f"ℹ️ {str(数据.get('说明', ''))[:60]}")
            if "没找到字幕" in str(数据.get("说明")) or "还没开始播放" in str(
                    数据.get("说明")):
                self._AI按钮可用(True)
            return
        if 类型 in ("翻译完成", "生成字幕完成"):
            self.AI面板.设置忙碌(False)
            self._AI按钮可用(True)
            路径 = str(数据.get("路径") or "")
            self._AI写(f"[字幕] ✅ 已写出：{路径}"
                     + (f"（{数据.get('条数')} 条）" if 数据.get("条数") else ""))
            if 路径:
                self.会话.挂字幕(路径, 选中=True)
                self._AI写("[字幕] 已挂到播放器并选中")
            return
        if 类型 == "总结完成":
            self.总结按钮.setEnabled(True)
            结果 = 数据.get("结果") or {}
            self._AI写("")
            self._AI写(f"📝 摘要（{结果.get('来源', '?')}）：{结果.get('摘要', '')}")
            for 章节 in (结果.get("章节") or [])[:20]:
                self._AI写(f"   [{章节.get('时间')}] {章节.get('标题')}"
                         f"　{章节.get('要点', '')}")
            if 结果.get("标签"):
                self._AI写(f"🏷 标签：{'、'.join(结果['标签'][:8])}")
            章节们 = 结果.get("章节") or []
            if 章节们:
                self._AI写("（点下面的「跳到第一章」可直接跳转）")
                self._待跳章节 = 章节们
                self.总结按钮.setText("⏱ 跳到第一章")
                try:
                    self.总结按钮.clicked.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                self.总结按钮.clicked.connect(self._跳到第一章)
            return
        if 类型 in ("翻译失败", "生成字幕失败", "总结失败"):
            self.AI面板.设置忙碌(False)
            self._AI按钮可用(True)
            self._AI写(f"❌ {类型}：{数据.get('错误')}")

    def _处理调优建议(self, 数据: dict) -> None:
        建议 = 数据.get("建议") or {}
        if not 建议.get("需要调整"):
            # AI 没给建议（本地模型没起来/返回不可解析时很常见）→ 用规则兜底
            规则 = None
            try:
                规则 = self.会话.规则诊断()
            except Exception:  # noqa: BLE001
                规则 = None
            if not 规则:
                return
            建议 = 规则
            self._AI写("[自动调优] AI 没给建议，按规则兜底判断")
        self._AI写(f"[自动调优] {建议.get('理由')} → 重载参数")
        for 动作 in 建议.get("动作") or []:
            self._AI写(f"   · {动作}")
        if 建议.get("新参数"):
            self.会话.应用新参数(建议["新参数"], 自动重载=True)

    def _跳到第一章(self) -> None:
        章节们 = getattr(self, "_待跳章节", [])
        if not 章节们:
            return
        self.会话.跳转(float(章节们[0].get("秒") or 0))
        self._AI写(f"[播放] 跳到 {章节们[0].get('时间')}")

    def _AI按钮可用(self, 可用: bool) -> None:
        for 按钮 in (self.翻译按钮, self.生字幕按钮, self.总结按钮):
            try:
                按钮.setEnabled(bool(可用))
            except Exception:  # noqa: BLE001
                pass

    # ==================== 播放控制 ====================

    def _播放暂停(self) -> None:
        self.内核页.播放暂停()

    def _停止(self) -> None:
        self.内核页.停止()
        self.状态标签.setText("⏹ 已停止")

    def 上一个(self) -> None:
        self.内核页.上一个()

    def 下一个(self) -> None:
        self.内核页.下一个()

    def 相对跳转(self, 秒: float) -> None:
        self.内核页.相对跳转(秒)

    def _跳转绝对(self, 秒: float) -> None:
        self.内核页.跳转(秒)

    def 跳到结尾(self) -> None:
        self.内核页.跳到结尾()

    def 逐帧(self) -> None:
        self.内核页.逐帧()

    def 当前速度(self) -> float:
        return self.内核页.当前倍速()

    def _设置速度(self, 倍速: float) -> None:
        self.内核页.设置倍速(倍速)

    def 当前音量(self) -> int:
        return self.内核页.当前音量()

    def _设置音量(self, 值: int) -> None:
        self.内核页.设置音量(值)

    def _调音量(self, 增减: int) -> None:
        self.内核页.设置音量(self.当前音量() + int(增减))

    def _是否静音(self) -> bool:
        return self.内核页.是否静音()

    def _设置静音(self, 静音: bool) -> None:
        self.内核页.设置静音(bool(静音))
        self.状态标签.setText("🔇 静音" if self.内核页.是否静音() else "🔊 已取消静音")

    def 章节数(self) -> int:
        return self.内核页.章节数()

    def 当前章节(self) -> int:
        return self.内核页.当前章节()

    def 跳章节(self, 编号: int) -> None:
        self.内核页.跳章节(编号)

    def 下一章(self) -> None:
        self.内核页.下一章()

    def 上一章(self) -> None:
        self.内核页.上一章()

    def 当前宽高比(self) -> str:
        return self.会话.播放器.宽高比()

    def _设置宽高比(self, 比例) -> None:
        self.会话.播放器.设置宽高比(比例)

    def 当前缩放(self) -> float:
        return self.会话.播放器.缩放()

    def _设置缩放(self, 倍率) -> None:
        self.会话.播放器.设置缩放(倍率)

    def _切换置顶(self, 选中: bool = False) -> None:
        窗口 = self.window()
        要置顶 = not 窗口.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        if 选中:
            要置顶 = True
        窗口.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, 要置顶)
        窗口.show()
        self.状态标签.setText("📌 已置顶" if 要置顶 else "已取消置顶")

    def _添加字幕文件(self) -> None:
        路径, _ = QFileDialog.getOpenFileName(
            self, "选择字幕文件", str(Path.home()),
            "字幕 (*.srt *.ass *.ssa *.vtt);;所有文件 (*)")
        if 路径:
            self.内核页.挂字幕文件(路径)

    def _字幕轨列表(self) -> list:
        return list(self.会话.播放器.字幕轨())

    def _音频轨列表(self) -> list:
        return list(self.会话.播放器.音频轨())

    def _当前轨(self, 种类: str) -> int:
        if 种类 == "音频":
            return 0 if self.会话.播放器.音频轨() else -1
        return self.会话.播放器.当前字幕()

    def _选择字幕轨(self, 编号: int) -> None:
        self.会话.播放器.选择字幕(编号)

    def _选择音频轨(self, 编号: int) -> None:
        self.会话.播放器.选择音频(编号)

    def _切换字幕(self) -> None:
        self.内核页.切换字幕()

    def _显示播放参数(self) -> None:
        设置 = self.会话.设置
        行 = [
            f"🅥 播放内核：自研 libav*（画面由本程序自绘）",
            f"媒体：{self.会话.媒体.摘要()}",
            f"探测：{self.会话.探测.摘要()}",
            f"起播参数：{设置.摘要()}",
            f"AI 决策：{self.会话.AI决策状态 or '（还没决策）'}",
            f"硬解：{self.引擎统计文本()}",
        ]
        for 文本 in 行:
            self._AI写(文本)
        self.状态标签.setText("ℹ️ 播放参数已写进 AI 面板")

    def 引擎统计文本(self) -> str:
        try:
            return str(self.会话.引擎.统计.摘要())
        except Exception:  # noqa: BLE001
            return "（统计不可用）"

    def 流畅优先(self) -> None:
        """一键"先让我看得下去"：缓存拉大、允许丢帧。"""
        try:
            设置 = self.会话.流畅优先()
            self.会话.应用新参数(设置.to_dict(), 自动重载=True)
            self._AI写(f"[播放] 流畅优先：{设置.摘要()}")
            self.状态标签.setText("🌊 已切到流畅优先")
        except Exception as 错:  # noqa: BLE001
            self._AI写(f"[播放] 流畅优先失败：{错}")

    def _截图(self) -> None:
        路径 = self.内核页.截图()
        if not 路径:
            return
        try:
            from .截图提示 import 截图提示
            if getattr(self, "_截图提示实例", None) is None:
                self._截图提示实例 = 截图提示(self, self.视频)
            self._截图提示实例.显示已保存(路径)
        except Exception:  # noqa: BLE001
            pass

    # ==================== 清单 / 循环 / 随机 ====================

    def 把当前加入清单(self) -> None:
        会话 = self.会话
        if not 会话.直链信息.get("url"):
            self.状态标签.setText("⚠️ 还没有正在播的东西")
            return
        本地 = 会话.网盘标识 in ("本地", "")
        项 = 播放项(
            标题=会话.标题 or "未命名",
            网盘标识="" if 本地 else 会话.网盘标识,
            远端路径="" if 本地 else 会话.远端路径,
            本地路径=会话.远端路径 if 本地 else "",
            大小=int(会话.直链信息.get("size") or 0),
            时长秒=float(会话.引擎.统计.总时长秒 or 0.0),
            来源="本地" if 本地 else (会话.设置.来源 or "手动"))
        if self.清单.添加(项):
            self.状态标签.setText(f"📋 已加入清单：{项.标题}")
        else:
            self.状态标签.setText("📋 清单里已经有这一项了")

    def _本地加入清单(self) -> None:
        路径, _ = QFileDialog.getOpenFileName(
            self, "选择要加入清单的视频", str(Path.home()),
            "视频 (*.mp4 *.mkv *.avi *.mov *.flv *.ts *.webm);;所有文件 (*)")
        if not 路径:
            return
        self.加入清单路径(路径)

    def 加入清单路径(self, 路径: str) -> bool:
        """把一个本地文件加进播放清单（命令行多选、拖放、外部调用都走这条）。"""
        p = Path(str(路径))
        if not p.is_file():
            self.状态标签.setText(f"⚠️ 文件不存在，没加进清单：{p}")
            return False
        项 = 播放项(标题=p.name, 网盘标识="", 远端路径="",
                  本地路径=str(p), 大小=p.stat().st_size, 来源="本地")
        好 = bool(self.清单.添加(项))
        self.状态标签.setText(f"📋 已加入清单：{p.name}" if 好
                          else f"📋 清单里已有：{p.name}")
        return 好

    def _清单选了某项(self, 项) -> None:
        if 项 is None:
            return
        是否本地 = bool(getattr(项, "是本地", False)) or str(项.网盘标识) == "本地"
        self.路径框.setText(str(项.取值路径() if hasattr(项, "取值路径") else 项.远端路径))
        if 是否本地:
            idx = self.网盘框.findData("本地")
            if idx >= 0:
                self.网盘框.setCurrentIndex(idx)
            self._播放(本地=True)
            return
        idx = self.网盘框.findData(项.网盘标识)
        if idx >= 0:
            self.网盘框.setCurrentIndex(idx)
        self._播放()

    def _切换循环(self, *_):
        文本 = self.清单.切换循环()
        self.状态标签.setText(f"🔁 {文本}")

    def _切换随机(self, 选中: bool = False):
        随机 = self.清单.切换随机() if not 选中 else self.清单.随机
        self.状态标签.setText("🔀 随机播放" if 随机 else "➡️ 顺序播放")

    def _切换清单(self, 显示: bool = True) -> None:
        self.左面板.setCurrentWidget(self.清单)
        self.切换侧栏(True)

    def _切换AI面板(self, 显示: bool = True) -> None:
        self.左面板.setCurrentWidget(self.AI面板)
        self.切换侧栏(True)

    # ==================== 侧栏 / 布局 ====================

    def 切换侧栏(self, 显示: bool = True):
        """显示 / 隐藏右侧的「播放清单 + AI 助手」面板。

        ⚠️ 这里**绝不能**顺手去「重排」：``重排`` 就是 ``按视频比例调整窗口``，
        它会 ``窗口.resize(...)`` —— 用户真机反馈的"点一下面板窗口宽高就变了"
        就是这么来的。而窗口一 resize，播放页就会更新解码输出尺寸，
        原来那条路径还会在解码线程和界面线程之间抢缩放器 → 点两次直接崩。

        现在只做"显示/隐藏"：视频区变宽变窄由布局自己处理，
        画面尺寸经 ``视频控件.尺寸变了`` 同步给引擎，跟窗口大小无关。
        """
        目标 = bool(显示)
        if 目标:
            self._显示面板窗口()
        elif self._面板窗口 is not None:
            self._面板窗口.隐藏()
        动作 = getattr(getattr(self, "菜单栏", None), "侧栏动作", None)
        if 动作 is not None:
            try:
                动作.setChecked(目标)
            except Exception:  # noqa: BLE001
                pass
        # 用 [播放] 前缀：它会**同时**进界面日志与 stdout，真机测试靠这行判定
        # "面板真的切了"（不然点歪了也会让"窗口没变形"的断言白白通过）。
        try:
            self._写日志(f"[播放] 侧栏：{'显示' if 目标 else '隐藏'}")
        except Exception:  # noqa: BLE001
            pass

    def _显示面板窗口(self) -> None:
        """把「视频信息 / 播放清单 / AI 助手」显示成独立悬浮窗口。"""
        if self._面板窗口 is None:
            try:
                主 = self.window()
                self._面板窗口 = 悬浮面板窗口(self.左面板, 主窗口=主)
            except Exception as 错:  # noqa: BLE001
                # 起不来就退回"钉在页面里"的老行为，别让用户连面板都看不到
                self._面板窗口 = None
                try:
                    self.主体.addWidget(self.左面板)
                    self.左面板.show()
                    self._写日志(f"[播放] 面板窗口起不来，改回内嵌：{错}")
                except Exception:  # noqa: BLE001
                    pass
                return
        self._面板窗口.显示()

    @property
    def 面板是独立窗口(self) -> bool:
        return self._面板窗口 is not None

    def 面板可见(self) -> bool:
        """面板现在看不看得见（独立窗口与内嵌两种情况都算）。"""
        if self._面板窗口 is not None:
            return bool(self._面板窗口.isVisible())
        return bool(self.左面板.isVisible())

    def 收尾面板(self) -> None:
        """退出前把悬浮面板关掉（不然会留下一个没爹的小窗挂着）。"""
        if self._面板窗口 is not None:
            try:
                self._面板窗口.收尾()
            except Exception:  # noqa: BLE001
                pass
            self._面板窗口 = None

    def showEvent(self, 事件):  # noqa: N802 - Qt 命名
        """第一次显示本页时把悬浮面板摆出来。

        为什么不是一开始就显示：面板现在是**独立窗口**，程序一启动就多弹一个窗
        太打扰；但也不能一直不给 —— 用户原来一进播放页就能看到「播放清单」，
        所以第一次进这一页时自动摆出来，之后完全听用户的（那个勾选框说了算）。
        """
        超级 = getattr(super(), "showEvent", None)
        if 超级 is not None:
            超级(事件)
        if not getattr(self, "_面板初判过", False):
            self._面板初判过 = True
            if 初始显示面板:
                self.切换侧栏(True)

    def 自适应宽度(self, 宽: int) -> None:
        """主窗口变窄时把侧栏收起来（小屏才看得全画面）。"""
        try:
            if int(宽) < 900:
                self.左面板.setMaximumWidth(260)
            else:
                self.左面板.setMaximumWidth(460)
        except Exception:  # noqa: BLE001
            pass

    def 重排(self) -> None:
        """按视频比例重排（保留老名字，菜单/自检都在用）。"""
        self.按视频比例调整窗口()

    def 按视频比例调整窗口(self) -> None:
        """让主窗口按视频宽高比显示（消除黑边）。

        V2 的画面永远保持源比例摆放，所以这里只需把**窗口**调成那个比例。
        """
        媒体 = self.会话.媒体
        try:
            宽 = int(getattr(媒体, "宽", 0) or 0)
            高 = int(getattr(媒体, "高", 0) or 0)
        except Exception:  # noqa: BLE001
            宽 = 高 = 0
        if 宽 <= 0 or 高 <= 0:
            self.状态标签.setText("⚠️ 还不知道视频分辨率（先播放一次）")
            return
        比例 = 宽 / 高
        窗口 = self.window()
        装饰宽 = max(0, 窗口.width() - self.内核页.width())
        装饰高 = max(0, 窗口.height() - self.内核页.height())
        try:
            屏幕 = self.screen() or QGuiApplication.primaryScreen()
            区域 = 屏幕.availableGeometry()
        except Exception:  # noqa: BLE001
            区域 = None
        可用宽 = (int(区域.width() * 0.94) - 装饰宽) if 区域 else 宽
        可用高 = (int(区域.height() * 0.94) - 装饰高) if 区域 else 高
        目标视频宽 = min(宽, max(320, 可用宽))
        目标视频高 = int(round(目标视频宽 / 比例))
        if 可用高 and 目标视频高 > 可用高:
            目标视频高 = 可用高
            目标视频宽 = int(round(目标视频高 * 比例))
        窗口.resize(max(480, 目标视频宽 + 装饰宽), max(320, 目标视频高 + 装饰高))
        self.状态标签.setText(
            f"🖼 已按视频比例调整窗口：目标视频区 {目标视频宽}×{目标视频高}")

    # ==================== 全屏 ====================

    def _设置全屏(self, 全屏: bool) -> None:
        窗口 = self.window()
        if 全屏:
            if self._全屏助手实例 is None:
                self._全屏助手实例 = 全屏助手(窗口, 退出回调=self._退出全屏后的收尾)
            self._全屏助手实例.进入()
        elif self._全屏助手实例 is not None and self._全屏助手实例.是全屏():
            self._全屏助手实例.退出()
        else:
            窗口.showNormal()
        self.控制条.设置全屏图标(bool(全屏))

    def _退出全屏后的收尾(self) -> None:
        self.控制条.设置全屏图标(False)
        self.状态标签.setText("🖥 已退出全屏")

    def _切换全屏(self) -> None:
        self._设置全屏(not bool(self.window().isFullScreen()))

    # ==================== 独立窗口 ====================

    def 打开独立窗口(self) -> None:
        if self._独立窗口 is not None:
            self._独立窗口.show()
            self._独立窗口.raise_()
            self._独立窗口.activateWindow()
            return
        from .播放器窗口 import 播放器窗口
        窗口 = 播放器窗口(self.会话, self.会话.标题 or "独立播放",
                      父窗口=self.主窗口, 日志回调=self._AI写,
                      AI动作=self.AI动作,
                      自动调优=bool(self.自动调优框.isChecked()),
                      内核页=self.内核页,
                      宿主回调={
                          "打开网盘": self._浏览,
                          "打开本地": self._打开本地,
                          "添加本地到清单": self._本地加入清单,
                          "添加字幕文件": self._添加字幕文件,
                          "归还播放": self.收回播放到页面,
                      })
        self._独立窗口 = 窗口
        窗口.destroyed.connect(lambda _o=None: setattr(self, "_独立窗口", None))
        窗口.show()
        self._AI写("[播放] 已打开独立窗口（与主窗口共享同一次播放）")

    def 独立窗口播放(self) -> None:
        """在独立窗口里播当前这一路（不再需要"把画面交接出去"）。"""
        self.打开独立窗口()
        if self._独立窗口 is None:
            return
        if self.会话.引擎.输入 is None:
            self._播放(独立窗口=True)
            return
        self._独立窗口.接管播放()

    def 收回播放到页面(self, 窗口=None) -> None:
        """独立窗口关了：主窗口这边本来就在显示同一路画面，只需收尾。"""
        if 窗口 is not None and 窗口 is self._独立窗口:
            self._独立窗口 = None
        self._显示媒体信息()
        self.状态标签.setText("↩️ 已收回主窗口播放")

    def _收回独立窗口(self) -> None:
        """菜单项「把独立窗口收回主界面」：**真的**关掉那个窗口。

        ⚠️ 不能直接接 ``收回播放到页面``：那个方法是"独立窗口被关之后"的**回调**，
        不传参时它只更新状态、不会去关窗口 —— 菜单点了会没反应（实测踩到）。
        """
        窗口 = self._独立窗口
        if 窗口 is None:
            self.状态标签.setText("ℹ️ 现在没有独立窗口")
            return
        try:
            窗口.关闭()          # 关闭时会回调 收回播放到页面（画面回主窗口）
        except Exception as 错:  # noqa: BLE001
            self._AI写(f"[播放] 收回独立窗口失败：{错}")
        self._独立窗口 = None
        self.状态标签.setText("↩️ 已收回主窗口播放")

    def 用VLC自带窗口播放(self) -> None:
        """老菜单项：V2 内核**没有**"别的窗口"这个概念，如实说明而不是假装切了。"""
        self._AI写("[播放] 画面由本程序自绘，不存在「播放器自己开窗」这种情况"
                 "（老版本的 VLC 自带窗口选项已随 VLC 一起移除）")
        self.状态标签.setText("ℹ️ 画面始终在本窗口里，不需要该选项")

    # ==================== AI 装配 ====================

    def _AI客户端(self):
        """拿 AI 客户端：优先本地 DeepSeek（免费），其次云端。

        ⚠️ 必须把客户端交给字幕助手/播放顾问，否则它们会"规则降级"——
        翻译只会照抄、总结只是截断（实测踩到）。
        """
        AI助手 = None
        本地模型 = None
        try:
            运行时 = self.主窗口.AI运行时()
            if 运行时 is not None:
                AI助手 = getattr(运行时, "助手", None)
                本地模型 = getattr(AI助手, "本地模型", None) if AI助手 else None
        except Exception as 错:  # noqa: BLE001
            self._AI写(f"[AI] 运行时不可用，AI 功能会降级：{错}")
        return AI助手, 本地模型

    def _准备AI(self) -> None:
        """按需装配 AI 播放顾问 / 字幕助手（缺模块也不影响播放）。"""
        AI助手, 本地模型 = self._AI客户端()
        来源 = ("本地模型" if (本地模型 is not None and
                          getattr(本地模型, "配置", None) is not None and
                          本地模型.配置.启用)
              else "云端 AI" if AI助手 is not None and getattr(AI助手, "api密钥", "")
              else "规则（无可用 AI）")
        self.AI来源 = 来源
        try:
            from ..AI.播放顾问 import 播放顾问
            self.顾问 = 播放顾问(AI助手=AI助手, 本地模型=本地模型,
                            日志回调=self._AI写)
        except Exception as 错:  # noqa: BLE001
            self.顾问 = None
            self._AI写(f"[AI] 播放顾问不可用（不影响播放）：{错}")
        try:
            from ..AI.字幕助手 import 字幕助手
            self.字幕助手 = 字幕助手(AI助手=AI助手, 本地模型=本地模型,
                                日志回调=self._AI写)
        except Exception as 错:  # noqa: BLE001
            self.字幕助手 = None
            self._AI写(f"[AI] 字幕助手不可用：{错}")
        self._AI写(f"[AI] 播放助手就绪：决策/翻译/总结走【{来源}】"
                 + ("（本地 CPU 推理较慢，长视频建议分批或改用云端）"
                    if 来源 == "本地模型" else ""))
        try:
            from ..播放.语音识别 import 语音识别器
            识别器 = 语音识别器(日志回调=self._AI写)
            可用, 说明 = 识别器.可用()
            self.语音识别器 = 识别器 if 可用 else None
            if not 可用:
                self._AI写(f"[字幕] 本地语音识别不可用：{说明.splitlines()[0]}")
        except Exception as 错:  # noqa: BLE001
            self.语音识别器 = None
            self._AI写(f"[字幕] 语音识别模块未就绪：{type(错).__name__}")
        # 会话要拿到顾问才会做 AI 参数决策与效果学习
        self.会话.顾问 = self.顾问
        self.会话.自动调优 = bool(self.自动调优框.isChecked())
        # 把装配好的客户端交给"AI 动作"对象（独立窗口也复用它）
        self.AI动作.字幕助手 = self.字幕助手
        self.AI动作.语音识别器 = self.语音识别器

    def _AI翻译字幕(self) -> None:
        self.AI面板.设置忙碌(True)
        self._AI按钮可用(False)
        self.AI动作.翻译字幕()

    def _AI生成字幕(self) -> None:
        self.AI面板.设置忙碌(True)
        self._AI按钮可用(False)
        self.AI动作.生成字幕()

    def _AI总结(self) -> None:
        self.AI面板.设置忙碌(True)
        self._AI按钮可用(False)
        self.AI动作.总结()

    def _手动诊断(self) -> None:
        结果 = self.AI动作.诊断() if self.AI动作 is not None else None
        if 结果 is None:
            self._AI写("[AI] 诊断：没有可诊断的内容（先播一个片子）")

    def _AI写(self, 文本: str) -> None:
        """写一行 AI 日志（**任何线程都可调用**）。

        实现要点：界面线程里直接写（保证立即出现、顺序不乱），别的线程走
        ``日志追加`` 信号转回界面线程 —— 直接跨线程碰控件实测会偶发段错误。
        """
        文本 = str(文本)
        try:
            from PySide6.QtCore import QThread
            if QThread.currentThread() is self.thread():
                self._写日志(文本)
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            self.日志追加.emit(文本)
        except Exception:  # noqa: BLE001
            pass

    #: 这些前缀的诊断行**同时**写进主日志（数据/界面日志.txt），排查时有据可查。
    落盘前缀 = ("[播放]", "[探测]", "[字幕]", "[AI]", "[回退]")

    def _写日志(self, 文本: str) -> None:
        try:
            self.AI输出.appendPlainText(f"[{datetime.now():%H:%M:%S}] {文本}")
            滚动 = self.AI输出.verticalScrollBar()
            滚动.setValue(滚动.maximum())
        except Exception:  # noqa: BLE001
            pass
        try:
            if str(文本).lstrip().startswith(self.落盘前缀):
                self.主窗口.追加日志(str(文本))
        except Exception:  # noqa: BLE001
            pass

    # ==================== 状态刷新 / 自动调优 ====================

    def _状态显示(self, 文本: str) -> None:
        self.状态标签.setText(str(文本)[:90])

    def _播放状态变了(self, 状态: str) -> None:
        if 状态 == "已结束":
            self.状态标签.setText("⏹ 播放结束")
            安全单发(self, 400, self._自动下一集)

    def _自动下一集(self) -> None:
        """播完了自动接下一集（清单里有下一项才走）。"""
        try:
            下 = self.清单.下一个项(自动=True)
        except Exception:  # noqa: BLE001
            下 = None
        if 下 is not None:
            self._清单选了某项(下)

    def _显示媒体信息(self) -> None:
        会话 = self.会话
        self.信息栏.setText(
            f"🎬 {会话.标题 or '（未命名）'}\n"
            f"媒体：{会话.媒体.摘要()}\n"
            f"探测：{会话.探测.摘要()}\n"
            f"参数：{会话.设置.摘要()}\n"
            f"AI：{会话.AI决策状态 or '（未走 AI 决策）'}")

    def _刷新状态(self) -> None:
        if self.会话.引擎.输入 is None:
            return
        统计 = self.会话.引擎.统计
        self.状态标签.setText(
            f"{self.会话.状态文本()}｜{统计.当前时间秒:.1f}/"
            f"{统计.总时长秒:.1f}s｜丢帧 {统计.已丢视频帧}｜{统计.硬解}")
        if self.自动调优框.isChecked():
            self._该自动诊断了吗()

    def _该自动诊断了吗(self) -> None:
        """到点才诊断一次（**90 秒冷却**）。

        为什么是 90 秒：以前每 500ms 无条件诊断一次，本地模型没起来时诊断瞬间返回，
        于是每秒刷一轮日志，用户完全没法看。
        """
        if self._诊断中 or self.会话.引擎.输入 is None:
            return
        现在 = time.monotonic()
        if 现在 - self._上次诊断时间 < 90.0:
            return
        丢帧 = int(self.会话.引擎.统计.已丢视频帧 or 0)
        快照 = self.会话.状态快照()
        真的有问题 = (丢帧 > self._上次诊断丢帧) or bool(快照.get("缓冲中"))
        if not 真的有问题:
            return
        self._上次诊断丢帧 = 丢帧
        self._上次诊断时间 = 现在
        self._诊断中 = True

        def 干():
            try:
                诊断 = self.会话.采样并诊断()
                if 诊断:
                    self.状态更新.emit({"类型": "调优建议",
                                     "建议": dict(诊断, 需要调整=True)})
            except Exception as 错:  # noqa: BLE001
                self._AI写(f"[自动调优] 诊断失败：{错}")
            finally:
                self._诊断中 = False

        threading.Thread(target=干, name="播放诊断", daemon=True).start()

    # ==================== 收尾 ====================

    def 关闭(self) -> None:
        """关页收尾：停定时器、关独立窗口与悬浮面板、关内核页与会话。"""
        self.收尾面板()
        try:
            self.定时器.stop()
        except Exception:  # noqa: BLE001
            pass
        窗口 = self._独立窗口
        if 窗口 is not None:
            try:
                窗口.关闭(不归还=True)
            except Exception:  # noqa: BLE001
                pass
            self._独立窗口 = None
        try:
            self.内核页.关闭()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.顾问 is not None and hasattr(self.顾问, "关闭"):
                self.顾问.关闭()
        except Exception:  # noqa: BLE001
            pass
        # 语音识别是**常驻子进程**（模型一加载就是几百 MB），关页要卸载掉
        try:
            if self.语音识别器 is not None and hasattr(self.语音识别器, "卸载"):
                self.语音识别器.卸载()
                self._AI写("[字幕] 已卸载语音识别工作进程")
        except Exception:  # noqa: BLE001
            pass

    def resizeEvent(self, 事件):  # noqa: N802 - Qt 命名
        super().resizeEvent(事件)
        self.自适应宽度(self.width())
