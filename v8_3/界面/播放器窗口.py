"""独立播放窗口：把**同一个**播放页搬到顶层窗口里，不复制第二份播放器。

与旧版最大的不同
================
旧版是"顶层窗口 + 另一个 VLC 实例"，画面靠 `set_xwindow` 交接过去，于是要写
"先停干净 → 等 vout 消失 → 再绑定句柄"这一整套仪式，还要处理"交接失败画面留在原窗口"。

V2 的内核里画面是控件自己画的，所以**把那个控件搬到新窗口**就够了：
同一个播放会话、同一个弹幕控制器、同一份取帧循环 —— 不存在第二个播放器，
也就不存在交接失败。关掉独立窗口时再把控件搬回主界面。

（`setParent()` 换父窗口是 Qt 的常规操作，控件连同它的定时器与状态一起走，
所以弹幕位置、字幕选择、进度都不会因为"换窗口"而丢。）
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSplitter,
                             QTabWidget, QVBoxLayout, QWidget)

from wangpan.ui.播放页 import 播放页 as 内核播放页

from .播放清单 import 播放清单
from .vlc风格 import 构建菜单栏, 构建工具栏

__all__ = ["播放器窗口"]

#: 全屏时无操作多久自动隐藏控件（毫秒）
自动隐藏毫秒 = 2500
#: 快进/快退步长（秒）
跳转步长秒 = 10.0
#: 默认占屏幕多大
初始占比 = 0.78
#: 速度档位（与 VLC 工具栏一致）
速度档 = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)


class 播放器窗口(QWidget):
    """顶层独立播放窗口（共享宿主那**一个**播放会话与播放页）。"""

    状态更新 = Signal(dict)
    请求播放项 = Signal(object)         # 清单里选了某项（宿主负责取直链/起播）

    #: 宿主注入的回调：{"打开网盘","打开本地","加入清单","添加本地到清单","添加字幕文件","归还播放"}
    宿主回调: dict = {}

    def __init__(self, 会话=None, 标题: str = "", 父窗口=None,
                 日志回调: Optional[Callable[[str], None]] = None,
                 AI动作=None, 自动调优: bool = True, 接管: bool = False,
                 内核页: Optional[内核播放页] = None,
                 宿主回调: Optional[dict] = None) -> None:
        super().__init__(None)                      # 必须是顶层窗口
        self.setWindowTitle(标题 or "独立播放")
        self.会话 = 会话
        self._日志 = 日志回调 or (lambda _t: None)
        self.AI动作 = AI动作
        self.自动调优 = bool(自动调优)
        self._接管 = bool(接管)
        self._父窗口 = 父窗口
        self._内核页 = 内核页
        self._宿主 = dict(type(self).宿主回调 or {})
        if 宿主回调:
            self._宿主.update(宿主回调)
        self._原父 = None
        self._原布局 = None
        self._隐藏定时器 = QTimer(self)
        self._隐藏定时器.setSingleShot(True)
        self._隐藏定时器.setInterval(自动隐藏毫秒)
        self._隐藏定时器.timeout.connect(self.隐藏控件)
        self._正在收回 = False
        self._自适应屏幕()
        self._构建()
        if self._内核页 is not None:
            self._搬入(self._内核页)

    # ------------------------------------------------------------------ 宿主回调

    def 回调(self, 名字: str, *参数):
        函数 = self._宿主.get(str(名字))
        if 函数 is None:
            self.状态标签.setText(f"ℹ️ {名字}：请回主界面操作")
            return None
        return 函数(*参数)

    # ------------------------------------------------------------------ 构建

    def _所在屏幕(self):
        """当前该按哪块屏幕算：**鼠标在哪块屏幕就用哪块**。

        为什么要这样（用户要求"适应各种大小不同的屏幕，不要超出屏幕"）：
        原来写死 `primaryScreen()` —— 双屏且主屏小、副屏大（或反过来）时，
        窗口开出来就可能一大半在屏幕外，或者明明副屏更大却按小屏算。
        鼠标所在屏幕才是用户"想让它开在哪儿"的意图。
        """
        try:
            from PySide6.QtGui import QCursor
            屏幕 = QGuiApplication.screenAt(QCursor.pos())
            if 屏幕 is not None:
                return 屏幕
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.screen() is not None:
                return self.screen()
        except Exception:  # noqa: BLE001
            pass
        return QGuiApplication.primaryScreen()

    def _自适应屏幕(self) -> None:
        try:
            屏幕 = self._所在屏幕()
            区域 = 屏幕.availableGeometry()
            宽 = int(区域.width() * 初始占比)
            高 = int(区域.height() * 初始占比)
        except Exception:  # noqa: BLE001
            区域, 宽, 高 = None, 960, 600
        self.resize(max(360, 宽), max(380, 高))
        self.收回屏幕内()

    def 收回屏幕内(self) -> None:
        """把窗口**收进**屏幕可用区域：既不许超出，也不许整块跑到屏幕外。

        为什么单独有这个：Qt 的 `resize()` 不管你屏幕多大，而用户明确要求
        "不要超出屏幕"。这里同时处理两件事：
        * **尺寸**：宽高各按屏幕可用区域收一次（留一点点边距，别顶着边缘）；
        * **位置**：收完之后如果窗口有一部分在屏幕外，往回挪；
          如果窗口比屏幕还大（比如从大屏搬到小屏），先缩到屏幕大小再挪。
        """
        if self.isFullScreen():
            return                              # 全屏是用户主动要的，别去动它
        try:
            屏幕 = self._所在屏幕()
            区域 = 屏幕.availableGeometry()
        except Exception:  # noqa: BLE001
            return
        # ⚠️ 必须把**窗口边框/标题栏**也算进去：`width()` 是客户区，
        #    `frameGeometry()` 才是屏幕上真正占的地方（实测离屏下边框 4px，
        #    只按客户区收会"看起来收住了、实际还超出 4px"）。
        try:
            装饰宽 = max(0, self.frameGeometry().width() - self.width())
            装饰高 = max(0, self.frameGeometry().height() - self.height())
        except Exception:  # noqa: BLE001
            装饰宽 = 装饰高 = 0
        宽 = min(self.width(), max(240, 区域.width() - 装饰宽))
        高 = min(self.height(), max(200, 区域.height() - 装饰高))
        if (宽, 高) != (self.width(), self.height()):
            self.resize(int(宽), int(高))
        try:
            角 = self.frameGeometry()
        except Exception:  # noqa: BLE001
            return
        x = 角.x()
        y = 角.y()
        if x + 宽 > 区域.x() + 区域.width():
            x = 区域.x() + 区域.width() - 宽
        if y + 高 > 区域.y() + 区域.height():
            y = 区域.y() + 区域.height() - 高
        x = max(区域.x(), x)
        y = max(区域.y(), y)
        if (x, y) != (角.x(), 角.y()):
            self.move(int(x), int(y))

    def resizeEvent(self, 事件):  # noqa: N802 - Qt 命名
        """窗口尺寸一变就保证它还在屏幕内（用户要求：不要超出屏幕）。"""
        超级 = getattr(super(), "resizeEvent", None)
        if 超级 is not None:
            超级(事件)
        if getattr(self, "_正在收回", False):
            return
        self._正在收回 = True
        try:
            self.收回屏幕内()
        finally:
            self._正在收回 = False

    def _构建(self) -> None:
        布局 = QVBoxLayout(self)
        布局.setContentsMargins(4, 4, 4, 4)
        布局.setSpacing(4)
        self.菜单栏 = 构建菜单栏(self._动作表(), self)
        布局.addWidget(self.菜单栏)
        self.工具栏 = 构建工具栏(self._动作表(), self,
                            隐藏项=("打开网盘", "打开本地", "加入清单"))
        布局.addWidget(self.工具栏)

        self.主体 = QSplitter(Qt.Orientation.Horizontal)
        self.画面区 = QWidget()
        画面布局 = QVBoxLayout(self.画面区)
        画面布局.setContentsMargins(0, 0, 0, 0)
        画面布局.setSpacing(0)
        self._画面布局 = 画面布局
        self.主体.addWidget(self.画面区)

        self.左面板 = QTabWidget()
        self.左面板.setMinimumWidth(240)
        self.清单 = 播放清单(self.左面板)
        self.清单.请求播放.connect(self.请求播放项)
        self.左面板.addTab(self.清单, "📋 播放清单")
        if self.AI动作 is not None:
            from .AI播放面板 import AI播放面板
            self.AI面板 = AI播放面板(self, 自动调优=self.自动调优, 紧凑=True)
            self.AI面板.翻译.connect(lambda: self._AI("翻译字幕"))
            self.AI面板.生成字幕.connect(lambda: self._AI("生成字幕"))
            self.AI面板.总结.connect(lambda: self._AI("总结"))
            self.AI面板.诊断.connect(lambda: self._AI("诊断"))
            self.左面板.addTab(self.AI面板, "🤖 AI 助手")
            self.AI输出 = self.AI面板.AI输出
        else:
            self.AI面板 = None
            self.左面板.addTab(QWidget(), "🤖 AI 助手")
            self.AI输出 = None
        self.左面板.setMaximumWidth(520)
        # 面板做成**独立悬浮窗口**（用户要求：主界面改了，独立播放器也要改）。
        # 所以它不钉在本窗口右侧，主体里只有画面。
        self._面板窗口 = None
        self.主体.setStretchFactor(0, 1)
        self.主体.setStretchFactor(1, 0)
        self.主体.setSizes([820, 300])
        布局.addWidget(self.主体, 1)

        self.状态标签 = QLabel("就绪")
        self.状态标签.setStyleSheet("font-size: 11px; color: #95a5a6;")
        布局.addWidget(self.状态标签)

        行 = QHBoxLayout()
        self.归还按钮 = QPushButton("🗗 归还主窗口")
        self.归还按钮.clicked.connect(self.归还)
        行.addWidget(self.归还按钮)
        self.全屏按钮 = QPushButton("⛶ 全屏")
        self.全屏按钮.clicked.connect(self.切换全屏)
        行.addWidget(self.全屏按钮)
        self.关闭按钮 = QPushButton("✖ 关闭")
        self.关闭按钮.clicked.connect(self.close)
        行.addWidget(self.关闭按钮)
        行.addStretch(1)
        布局.addLayout(行)

    # ------------------------------------------------------------------ 搬运内核页

    def _搬入(self, 页: 内核播放页) -> None:
        if 页.parentWidget() is self.画面区:
            return
        self._标搬动(页, True)
        try:
            self._原父 = 页.parentWidget()
            self._原布局 = self._原父.layout() if self._原父 is not None else None
            if self._原布局 is not None:
                self._原布局.removeWidget(页)
            页.setParent(self.画面区)
            self._画面布局.addWidget(页)
            页.show()
            try:
                页.要全屏.disconnect()
            except Exception:  # noqa: BLE001
                pass
            页.要全屏.connect(self.设置全屏)
        finally:
            self._标搬动(页, False, 延迟毫秒=260)

    def _搬回(self) -> None:
        if self._内核页 is None or self._原父 is None:
            return
        页 = self._内核页
        if 页.parentWidget() is not self.画面区:
            return
        self._标搬动(页, True)
        try:
            self._画面布局.removeWidget(页)
            页.setParent(self._原父)
            if self._原布局 is not None:
                self._原布局.insertWidget(0, 页)
            页.show()
        finally:
            self._标搬动(页, False, 延迟毫秒=260)

    def _标搬动(self, 页, 进行中: bool, 延迟毫秒: int = 0) -> None:
        """告诉内核播放页"我正在搬窗口"：这段时间内**不许** AI 自动重开。

        为什么要延迟解除：``setParent`` 之后 Qt 还会连着发一串 resize/布局事件
        （正是"设置输出尺寸 + 重建控件"最密集的时刻），
        所以搬完先按住 260ms，等这波动静过去再放行自动重开。
        （真机 22:04:04 刚开过独立窗口，22:04:08 就崩了 —— 就是这两件事叠在一起。）
        这个标记只影响"自动重开"，不影响手动播放/暂停/拖进度。
        """
        标记 = getattr(页, "标记窗口搬动", None)
        if 标记 is None:
            return
        try:
            标记(bool(进行中))
        except Exception:  # noqa: BLE001
            return
        if 进行中 or 延迟毫秒 <= 0:
            return
        from PySide6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(int(延迟毫秒), lambda: 标记(False))

    def 换会话(self, 会话, 标题: str = "") -> None:
        self.会话 = 会话
        if 标题:
            self.setWindowTitle(标题)

    # ------------------------------------------------------------------ 播放

    def 起播(self) -> bool:
        if self.会话 is None:
            return False
        return bool(self.会话.引擎.输入 is not None or self.会话.起播(0))

    def 接管播放(self) -> bool:
        """把画面搬到本窗口（会话本来就在播，不需要重新打开）。"""
        if self.会话 is None:
            return False
        if self._内核页 is not None and self._内核页.parentWidget() is not self.画面区:
            self._搬入(self._内核页)
        self.show()
        self.raise_()
        self.activateWindow()
        self.状态标签.setText("🎬 已接管播放（关掉本窗口会还给主界面）")
        return True

    def 交接信息(self) -> dict:
        if self.会话 is None:
            return {}
        return {
            "位置秒": float(self.会话.引擎.统计.当前时间秒 or 0.0),
            "网盘标识": self.会话.网盘标识,
            "远端路径": self.会话.远端路径,
            "标题": self.会话.标题,
            "直链信息": dict(self.会话.直链信息 or {}),
            "媒体": getattr(self.会话, "媒体", None),
            "探测": getattr(self.会话, "探测", None),
            "设置": getattr(self.会话, "设置", None),
        }

    # ------------------------------------------------------------------ 画面控制

    def _页(self) -> Optional[内核播放页]:
        return self._内核页

    def _动作(self, 名字: str, *参数):
        页 = self._页()
        if 页 is None:
            return None
        函数 = getattr(页, 名字, None)
        if 函数 is None:
            self.状态标签.setText(f"⚠️ 当前内核没有这个功能：{名字}")
            return None
        return 函数(*参数)

    def 播放暂停(self) -> None:
        self._动作("播放暂停")

    def 停止(self) -> None:
        self._动作("停止")

    def 上一个(self) -> None:
        self.请求播放项.emit(self.清单.上一个项())

    def 下一个(self) -> None:
        self.请求播放项.emit(self.清单.下一个项())

    def 逐帧(self) -> None:
        self._动作("逐帧")

    def 相对跳转(self, 秒: float) -> None:
        self._动作("相对跳转", 秒)

    def 跳转绝对(self, 秒: float) -> None:
        self._动作("跳转", 秒)

    def 跳到结尾(self) -> None:
        self._动作("跳到结尾")

    def 当前速度(self) -> float:
        return float(self._动作("当前倍速") or 1.0)

    def 设置速度(self, 倍速: float) -> None:
        self._动作("设置倍速", 倍速)

    def 当前音量(self) -> int:
        return int(self._动作("当前音量") or 0)

    def 设置音量(self, 值: int) -> None:
        self._动作("设置音量", 值)

    def 是否静音(self) -> bool:
        return bool(self._动作("是否静音"))

    def 设置静音(self, 静音: bool) -> None:
        self._动作("设置静音", 静音)

    def 切换字幕(self) -> None:
        self._动作("切换字幕")

    def 章节数(self) -> int:
        return int(self._动作("章节数") or 0)

    def 当前章节(self) -> int:
        return int(self._动作("当前章节") or -1)

    def 跳章节(self, 编号: int) -> None:
        self._动作("跳章节", 编号)

    def 下一章(self) -> None:
        self._动作("下一章")

    def 上一章(self) -> None:
        self._动作("上一章")

    def 当前缩放(self) -> float:
        if self.会话 is None:
            return 1.0
        return float(self.会话.播放器.缩放())

    def 设置缩放(self, 倍率) -> None:
        if self.会话 is not None:
            self.会话.播放器.设置缩放(倍率)

    def 当前宽高比(self) -> str:
        return self.会话.播放器.宽高比() if self.会话 is not None else ""

    def 设置宽高比(self, 比例) -> None:
        if self.会话 is not None:
            self.会话.播放器.设置宽高比(比例)

    def 切换置顶(self, 选中: bool = False) -> None:
        要置顶 = True if 选中 else not bool(
            self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, 要置顶)
        self.show()

    def 截图(self) -> None:
        self._动作("截图")

    def 把当前加入清单(self) -> None:
        self.回调("加入清单")

    def 切换循环(self) -> None:
        self.清单.切换循环()

    def 切换随机(self) -> None:
        self.清单.切换随机()

    def 清单播完了(self) -> bool:
        return self.清单.当前行() >= len(self.清单) - 1

    def 切换侧栏(self, 显示: bool = True) -> None:
        if 显示:
            self.显示面板窗口()
        else:
            self.隐藏面板窗口()

    def 切换清单(self, 显示: bool = True) -> None:
        self.左面板.setCurrentWidget(self.清单)
        self.切换侧栏(True)

    def 切换AI面板(self, 显示: bool = True) -> None:
        if self.AI面板 is not None:
            self.左面板.setCurrentWidget(self.AI面板)
        self.切换侧栏(True)

    def 切换控件(self, 显示: bool = True) -> None:
        self.工具栏.setVisible(bool(显示))
        self.状态标签.setVisible(bool(显示))
        self.归还按钮.setVisible(bool(显示))
        self.全屏按钮.setVisible(bool(显示))
        self.关闭按钮.setVisible(bool(显示))

    def 流畅优先(self) -> None:
        if self.会话 is None:
            return
        设置 = self.会话.流畅优先()
        self.会话.应用新参数(设置.to_dict(), 自动重载=True)
        self.状态标签.setText(f"🌊 流畅优先：{设置.摘要()}")

    def 显示播放参数(self) -> None:
        if self.会话 is None:
            return
        self._写日志(f"🅥 参数：{self.会话.设置.摘要()}")
        self._写日志(f"🅥 媒体：{self.会话.媒体.摘要()}")
        self._写日志(f"🅥 探测：{self.会话.探测.摘要()}")

    def 设置自动调优(self, 开: bool) -> None:
        self.自动调优 = bool(开)
        if self.会话 is not None:
            self.会话.自动调优 = bool(开)

    def 显示AI帮助(self) -> None:
        self._写日志("🤖 翻译/生成字幕/总结走 AI（本地模型优先，没有则回落规则）；"
                  "「诊断」会采样当前播放状态给出调参建议。")

    def AI结果(self, 数据: dict) -> None:
        """宿主把 AI 动作的状态分发过来时写进本窗口的输出框（跨线程安全由宿主保证）。"""
        try:
            if self.AI输出 is not None and isinstance(数据, dict):
                for 键 in ("说明", "错误", "路径"):
                    if 数据.get(键):
                        self.AI输出.appendPlainText(str(数据[键]))
        except Exception:  # noqa: BLE001
            pass

    def _AI(self, 动作: str) -> None:
        if self.AI动作 is None:
            self._写日志("🤖 AI 动作不可用（宿主没装配）")
            return
        函数 = getattr(self.AI动作, 动作, None)
        if 函数 is not None:
            函数()

    # ------------------------------------------------------------------ 全屏 / 控件隐藏

    def 设置全屏(self, 全屏: bool) -> None:
        if 全屏:
            self.showFullScreen()
            self._隐藏定时器.start()
        else:
            self.showNormal()
            self.切换控件(True)

    def 切换全屏(self) -> None:
        self.设置全屏(not self.isFullScreen())

    def 适应屏幕(self) -> None:
        self._自适应屏幕()

    def 适应视频比例(self) -> None:
        """按视频比例调整窗口，但**一律收进当前屏幕**。

        先按屏幕可用高度的 86% 反推宽度（比"拿当前宽度乘 0.9"更靠谱：
        窗口本来就在屏幕内时，这样算出来的高宽都不会超屏），再收一次边。
        """
        比例 = self.视频比例()
        if 比例 <= 0:
            return
        try:
            区域 = self._所在屏幕().availableGeometry()
        except Exception:  # noqa: BLE001
            区域 = None
        装饰高 = max(0, self.height() - self.画面区.height())
        if 区域 is not None:
            可用高 = max(240, int(区域.height() * 0.86) - 装饰高)
            可用宽 = int(区域.width() * 0.94)
        else:
            可用高, 可用宽 = max(240, self.height() - 装饰高), self.width()
        目标高 = 可用高
        目标宽 = int(目标高 * 比例)
        if 目标宽 > 可用宽:
            目标宽 = 可用宽
            目标高 = int(目标宽 / 比例)
        self.resize(max(360, 目标宽), max(300, 目标高 + 装饰高))
        self.收回屏幕内()

    def 视频比例(self) -> float:
        if self.会话 is None:
            return 0.0
        宽 = int(getattr(self.会话.媒体, "宽", 0) or 0)
        高 = int(getattr(self.会话.媒体, "高", 0) or 0)
        return (宽 / 高) if 宽 and 高 else 0.0

    def 显示控件(self) -> None:
        self.切换控件(True)

    def 隐藏控件(self) -> None:
        self.切换控件(False)

    def 同步全屏图标(self) -> None:
        全屏 = self.isFullScreen()
        self.全屏按钮.setText("🗗 退出全屏" if 全屏 else "⛶ 全屏")

    def 保证视频区可见(self) -> None:
        self.画面区.show()

    def 贴合屏幕(self) -> None:
        self.适应屏幕()

    def 算视频区尺寸(self):
        return self.画面区.size()

    def 写日志(self, 文本: str) -> None:
        self._写日志(文本)

    def _写日志(self, 文本: str) -> None:
        try:
            self._日志(str(文本))
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.AI输出 is not None:
                self.AI输出.appendPlainText(str(文本))
        except Exception:  # noqa: BLE001
            pass

    def 跟着父窗口退出(self, 父窗口) -> None:
        self._父窗口 = 父窗口

    # ------------------------------------------------------------------ 归还 / 关闭

    def 归还(self) -> None:
        self.回调("归还播放", self)
        self.关闭(不归还=True)

    def 显示面板窗口(self) -> None:
        """把「播放清单 / AI 助手」显示成独立悬浮窗口（高度跟随本窗口）。"""
        if self._面板窗口 is None:
            from .悬浮面板 import 悬浮面板窗口
            self._面板窗口 = 悬浮面板窗口(self.左面板, 主窗口=self)
        self._面板窗口.显示()

    def 隐藏面板窗口(self) -> None:
        if self._面板窗口 is not None:
            self._面板窗口.隐藏()

    def 收尾面板窗口(self) -> None:
        if self._面板窗口 is not None:
            try:
                self._面板窗口.收尾()
            except Exception:  # noqa: BLE001
                pass
            self._面板窗口 = None

    def 关闭(self, 不归还: bool = False) -> None:
        self.收尾面板窗口()
        if not 不归还:
            self.回调("归还播放", self)
        self._搬回()
        self.close()

    def closeEvent(self, 事件):  # noqa: N802 - Qt 命名
        self._搬回()
        super().closeEvent(事件)

    def keyPressEvent(self, 事件):  # noqa: N802 - Qt 命名
        键 = 事件.key()
        if 键 == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.设置全屏(False)
            else:
                self.close()
            return
        if 键 == Qt.Key.Key_Space:
            self.播放暂停()
            return
        if 键 == Qt.Key.Key_Left:
            self.相对跳转(-跳转步长秒)
            return
        if 键 == Qt.Key.Key_Right:
            self.相对跳转(跳转步长秒)
            return
        if 键 == Qt.Key.Key_Up:
            self.设置音量(min(100, self.当前音量() + 5))
            return
        if 键 == Qt.Key.Key_Down:
            self.设置音量(max(0, self.当前音量() - 5))
            return
        if 键 == Qt.Key.Key_F:
            self.切换全屏()
            return
        if 键 == Qt.Key.Key_M:
            self.设置静音(not self.是否静音())
            return
        if 键 == Qt.Key.Key_N:
            self.下一个()
            return
        if 键 == Qt.Key.Key_B:
            self.上一个()
            return
        if 键 == Qt.Key.Key_E:
            self.逐帧()
            return
        super().keyPressEvent(事件)

    # ------------------------------------------------------------------ 动作表

    def _动作表(self) -> dict:
        return {
            "打开网盘": lambda: self.回调("打开网盘"),
            "打开本地": lambda: self.回调("打开本地"),
            "加入清单": lambda: self.回调("加入清单"),
            "添加本地到清单": lambda: self.回调("添加本地到清单"),
            "退出": self.close,
            "播放暂停": self.播放暂停,
            "停止": self.停止,
            "上一个": self.上一个,
            "下一个": self.下一个,
            "逐帧": self.逐帧,
            "快进": lambda: self.相对跳转(跳转步长秒),
            "快退": lambda: self.相对跳转(-跳转步长秒),
            "到开头": lambda: self.跳转绝对(0.0),
            "跳到结尾": self.跳到结尾,
            "速度列表": lambda: [(f"{x}×", x) for x in 速度档],
            "当前速度": self.当前速度,
            "设置速度": self.设置速度,
            "章节数": self.章节数,
            "当前章节": self.当前章节,
            "跳章节": self.跳章节,
            "下一章": self.下一章,
            "上一章": self.上一章,
            "音量加": lambda: self.设置音量(min(100, self.当前音量() + 5)),
            "音量减": lambda: self.设置音量(max(0, self.当前音量() - 5)),
            "静音切换": lambda: self.设置静音(not self.是否静音()),
            "是否静音切换": self.是否静音,
            "音量": self.当前音量,
            "设置音量": self.设置音量,
            "全屏": self.设置全屏,
            "是否全屏": self.isFullScreen,
            "截图": self.截图,
            "宽高比": self.当前宽高比,
            "设置宽高比": self.设置宽高比,
            "缩放": self.当前缩放,
            "设置缩放": self.设置缩放,
            "置顶窗口": self.切换置顶,
            "添加字幕文件": lambda: self.回调("添加字幕文件"),
            "切换清单": self.切换清单,
            "切换AI面板": self.切换AI面板,
            "显示控件": self.显示控件,
            "隐藏控件": self.隐藏控件,
            "切换控件": self.切换控件,
            "播放参数": self.显示播放参数,
            "流畅优先": self.流畅优先,
            "适应屏幕": self.适应屏幕,
            "适应视频比例": self.适应视频比例,
            "切换自动调优": self.设置自动调优,
            "AI帮助": self.显示AI帮助,
            "循环切换": self.切换循环,
            "随机切换": self.切换随机,
        }
