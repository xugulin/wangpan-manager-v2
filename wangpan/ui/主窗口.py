"""主窗口（纯内核壳）：播放页 + 网盘页 + 媒体库页。

这一版把"播放相关的一切"抽到了 :class:`~wangpan.ui.播放页.播放页`，
把"媒体库那一整套"抽到了 :class:`~wangpan.ui.媒体库页.媒体库页` ——
这个窗口本身只负责**把三页拼起来 + 状态栏 + 退出收尾**。

为什么还要留着它
================
整合后的主界面走的是另一套 GUI（顶栏网盘导航 + 左栏 + 堆叠页）。
但 V2 的内核需要一条**不依赖那套 GUI 的独立回归路径**：

* `启动.py --纯播放` 用它，能验证"内核单独也能用"；
* 617 条测试里有一批直接构造它、检查状态栏/退出收尾/刮削线程；
* 排查问题时可以绕过一切界面逻辑，只看内核。

所以它不追求功能多，追求**薄**：页面里能做的都别在这儿再做一遍。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QFileDialog, QLabel, QMainWindow,
                             QTabWidget, QWidget)

from ..pan.工厂 import 建注册表
from .媒体库页 import 媒体库页
from .播放页 import 播放页, 时间文本
from .网盘页 import 网盘页

__all__ = ["主窗口", "时间文本"]


class 主窗口(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("网盘管理 · 自研播放内核")
        self.resize(1100, 680)
        self._日志行: list[str] = []
        self._刮削线程 = None            # 刮削线程的句柄（退出收尾要等它；测试会直接赋值）
        self._收尾过 = False

        # ---- 播放页（画面 + 控制条 + 弹幕 + 字幕 + 预览）----
        self.播放页 = 播放页(日志回调=self._写日志)
        # 兼容老名字：测试与外部脚本一直读这些
        self.引擎 = self.播放页.引擎
        self.视频 = self.播放页.视频
        self.弹幕 = self.播放页.弹幕
        self.记录本 = self.播放页.记录本
        self.播放页.提示.connect(self.提示)
        self.播放页.日志追加.connect(self._写日志)
        self.播放页.要打开本地.connect(self._选文件)
        self.播放页.要全屏.connect(self._设置全屏)

        # ---- 网盘页：左边网盘、右边传输，双击/播放按钮把直链交给播放器 ----
        self.注册表 = 建注册表()
        self.网盘页 = 网盘页(self.注册表)
        self.网盘页.要播放.connect(self.播放直链)

        self.标签 = QTabWidget()
        self.标签.addTab(self.播放页, "▶ 播放")
        self.标签.addTab(self.网盘页, "☁ 网盘")
        播放页序号 = 0
        self.播放页序号 = 播放页序号

        # ---- 媒体库页 ----
        self.媒体库 = 媒体库页(日志回调=self._写日志, 提示回调=self.提示)
        self.媒体库.要播放.connect(self._播放库里的文件)
        if self.媒体库.可用:
            self.标签.addTab(self.媒体库, "🎞 媒体库")
        self.setCentralWidget(self.标签)

        self.状态 = self.statusBar()
        # ⚠️ 播放统计是**每 16ms 刷一次**的常驻信息，必须放 permanent 部件；
        # 之前它走 showMessage，把"刮削完成/待确认/已装弹幕"这些提示在 16 毫秒内
        # 全盖掉了（真机点「待确认」时看到的还是播放统计，压根没看到提示）。
        self.统计标签 = QLabel("")
        self.状态.addPermanentWidget(self.统计标签)
        self.提示("就绪：打开一个视频文件即可播放（画面由本程序自己绘制）")

        self._定时器 = QTimer(self)
        self._定时器.setInterval(200)
        self._定时器.timeout.connect(self._刷新)
        self._定时器.start()
        # 任何退出路径（关窗 / quit / 会话注销）都要先停线程再关库，否则进程会 abort
        应用 = QApplication.instance()
        if 应用 is not None:
            应用.aboutToQuit.connect(self.退出前收尾)

    # ---------------- 播放：全部转给播放页 ----------------

    def 打开(self, 路径: str, 续播: bool = True) -> bool:
        """打开一个本地文件（字幕/弹幕/续播/播放列表都在播放页里做）。"""
        好 = self.播放页.打开(str(路径), 续播=续播)
        if 好:
            self.标签.setCurrentIndex(self.播放页序号)
            self.setWindowTitle(f"网盘管理 · {Path(路径).name}")
        return 好

    def 播放直链(self, 地址: str, 请求头: Optional[dict] = None) -> bool:
        """播一路直链（网盘页/外部调用）。"""
        好 = self.播放页.播放直链(地址, 请求头 or {})
        if 好:
            self.标签.setCurrentIndex(self.播放页序号)
        return 好

    def _播放库里的文件(self, 路径: str) -> None:
        self.标签.setCurrentIndex(self.播放页序号)
        self.打开(路径)

    def _选文件(self) -> None:
        路径, _ = QFileDialog.getOpenFileName(
            self, "选一个视频文件", str(Path.home()),
            "视频 (*.mp4 *.mkv *.mov *.webm *.avi *.ts *.flv);;所有文件 (*)")
        if 路径:
            self.打开(路径)

    def _设置全屏(self, 全屏: bool) -> None:
        if 全屏:
            self.showFullScreen()
        else:
            self.showNormal()

    def _截图(self) -> None:
        self.播放页.截图()

    # ---------------- 媒体库：转给媒体库页 ----------------

    @property
    def 资料库(self):
        return self.媒体库.资料库

    @property
    def 图片缓存(self):
        return self.媒体库.图片缓存

    @property
    def 海报墙(self):
        return self.媒体库.海报墙

    @property
    def 自动开队列(self) -> bool:
        return bool(self.媒体库.自动开队列)

    @自动开队列.setter
    def 自动开队列(self, 值: bool) -> None:
        self.媒体库.自动开队列 = bool(值)

    def _刮削路径(self, 路径: str, 强制标识: str = "", 强制类型=None,
               完成后=None) -> None:
        self.媒体库.刮削路径(路径, 强制标识, 强制类型, 完成后)
        self._刮削线程 = self.媒体库._刮削线程

    def _开待确认队列(self, 起始行: int = 0) -> None:
        self.媒体库.开待确认队列(起始行)

    def _开手动匹配(self, 媒体id: int) -> None:
        self.媒体库.开手动匹配(媒体id)

    def _开手动资料(self, 媒体id: int) -> None:
        self.媒体库.开手动资料(媒体id)

    # ---------------- 状态提示 ----------------

    def 提示(self, 文本: str, 毫秒: int = 8000) -> None:
        """在状态栏显示一句**临时**提示（默认 8 秒后自己消失）。

        为什么单独有这个方法：播放统计是常驻部件在刷（见 __init__），
        临时提示必须走 showMessage 才不会互相顶掉；统一收口也方便测试。
        """
        try:
            self.状态.showMessage(str(文本), int(毫秒))
        except Exception:  # noqa: BLE001 - 状态栏不该影响功能
            pass

    def 当前提示(self) -> str:
        """状态栏上正在显示的临时提示（测试/自动化用）。"""
        try:
            return str(self.状态.currentMessage())
        except Exception:  # noqa: BLE001
            return ""

    def _写日志(self, 文本: str) -> None:
        self._日志行.append(str(文本))
        del self._日志行[:-200]

    # ---------------- 每帧（状态栏）----------------

    def _刷新(self) -> None:
        """只刷状态栏统计；画面/进度/弹幕由播放页自己的 16ms 定时器负责。"""
        统计 = self.引擎.统计
        try:
            self.统计标签.setText(统计.摘要() + (f"｜{统计.音视频}" if 统计.音视频 else ""))
        except Exception:  # noqa: BLE001
            pass

    def resizeEvent(self, 事件):  # noqa: N802 - Qt 命名
        super().resizeEvent(事件)
        区域 = self.视频.size()
        if 区域.width() > 0 and 区域.height() > 0:
            try:
                self.引擎.设置输出尺寸(区域.width(), 区域.height())
            except Exception:  # noqa: BLE001
                pass

    def keyPressEvent(self, 事件):  # noqa: N802 - Qt 命名
        if 事件.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
            return
        self.播放页.keyPressEvent(事件)

    # ---------------- 退出收尾 ----------------

    def 退出前收尾(self) -> None:
        """把还在跑的线程停掉、把该存的存掉（**任何退出路径**都要走一遍）。

        为什么要单独有它：`closeEvent` 只在"用户关窗"时跑；而
        **直接 `QApplication.quit()`**（脚本、会话注销、Ctrl+Q）不会走关窗 ——
        于是刮削线程 / 海报墙的图片线程还在跑就被析构 → 进程 abort
        （真机真发生过：验证脚本跑完 restore 数据库时核心转储）。
        """
        if self._收尾过:
            return
        self._收尾过 = True
        try:
            self.播放页.关闭()
        except Exception:  # noqa: BLE001
            pass
        try:
            if getattr(self, "网盘页", None) is not None:
                self.网盘页.关闭()
        except Exception:  # noqa: BLE001
            pass
        # 有些人（测试/外部脚本）会把刮削线程直接挂在窗口上，所以两边都等一遍
        try:
            线 = self._刮削线程
            if 线 is not None and 线.isRunning():
                线.wait(15000)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.媒体库.关闭()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass

    def closeEvent(self, 事件):  # noqa: N802 - Qt 命名
        self.退出前收尾()
        super().closeEvent(事件)
        return
