"""网盘页（M6 界面）：左边选网盘/目录，右边列出文件，可播放、可下载到本地。

界面只认 :mod:`wangpan.pan.接口` 的契约，所以"本地文件夹 / HTTP 直链 / 以后新增的
真实网盘"在界面上长得一模一样 —— 这就是把契约先定下来的价值。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
                             QLabel, QMessageBox, QProgressBar, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..pan.接口 import 不支持
from ..transfer.引擎 import 传输引擎
from ..transfer.任务 import 状态

__all__ = ["网盘页"]


class 网盘页(QWidget):
    """一个网盘浏览器 + 传输列表。"""

    要播放 = Signal(str, dict)          # (地址, 请求头) —— 交给主窗口去播

    def __init__(self, 注册表对象, 传输引擎对象: Optional[传输引擎] = None,
                 父=None) -> None:
        super().__init__(父)
        self.注册表 = 注册表对象
        self.传输 = 传输引擎对象 or 传输引擎(注册表对象, 日志回调=self._写日志)
        self.当前网盘 = ""
        self.当前目录 = "/"
        self.子目录栈: list[str] = []

        布局 = QVBoxLayout(self)
        布局.setContentsMargins(8, 8, 8, 8)
        行 = QHBoxLayout()
        行.addWidget(QLabel("网盘"))
        self.下拉 = QComboBox()
        for 标识, 对象 in self.注册表.全部().items():
            self.下拉.addItem(f"{对象.名字}", 标识)
        self.下拉.currentIndexChanged.connect(lambda _i: self._换网盘())
        行.addWidget(self.下拉)
        self.返回按钮 = QPushButton("⬆ 上一级")
        self.返回按钮.clicked.connect(self._上一级)
        行.addWidget(self.返回按钮)
        self.刷新按钮 = QPushButton("🔄 刷新")
        self.刷新按钮.clicked.connect(self.刷新)
        行.addWidget(self.刷新按钮)
        self.路径标签 = QLabel("/")
        行.addWidget(self.路径标签, 1)
        布局.addLayout(行)

        self.表 = QTableWidget(0, 4)
        self.表.setHorizontalHeaderLabels(["名字", "大小", "类型", "下载"])
        self.表.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.表.doubleClicked.connect(lambda _i: self._打开选中())
        布局.addWidget(self.表, 1)

        按钮行 = QHBoxLayout()
        self.播放按钮 = QPushButton("▶ 播放选中")
        self.播放按钮.clicked.connect(self._播放选中)
        按钮行.addWidget(self.播放按钮)
        self.下载按钮 = QPushButton("⬇ 下载选中")
        self.下载按钮.clicked.connect(self._下载选中)
        按钮行.addWidget(self.下载按钮)
        self.全部暂停 = QPushButton("⏸ 全部暂停")
        self.全部暂停.clicked.connect(lambda: self.传输.全部暂停(True))
        按钮行.addWidget(self.全部暂停)
        self.继续 = QPushButton("▶ 继续")
        self.继续.clicked.connect(lambda: self.传输.全部暂停(False))
        按钮行.addWidget(self.继续)
        self.清空 = QPushButton("🧹 清已完成")
        self.清空.clicked.connect(self.传输.清已完成)
        按钮行.addWidget(self.清空)
        布局.addLayout(按钮行)

        self.传输表 = QTableWidget(0, 3)
        self.传输表.setHorizontalHeaderLabels(["任务", "进度", "状态"])
        self.传输表.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        布局.addWidget(QLabel("传输列表"))
        布局.addWidget(self.传输表, 1)

        self.提示 = QLabel("提示：选中文件可播放或下载")
        布局.addWidget(self.提示)

        self._定时器 = QTimer(self)
        self._定时器.setInterval(400)
        self._定时器.timeout.connect(self._刷传输表)
        self._定时器.start()
        if self.下拉.count():
            self._换网盘()

    # ---------------- 列表 ----------------

    def _换网盘(self) -> None:
        self.当前网盘 = self.下拉.currentData() or ""
        self.当前目录 = "/"
        self.子目录栈 = []
        self.刷新()

    def 刷新(self) -> None:
        对象 = self.注册表.取(self.当前网盘)
        self.表.setRowCount(0)
        self.路径标签.setText(f"{self.当前网盘}：{self.当前目录}")
        if 对象 is None:
            self.提示.setText("没有选网盘")
            return
        try:
            条目们 = 对象.列出(self.当前目录)
        except 不支持 as 错:
            self.提示.setText(f"这个网盘不支持列目录：{错}")
            return
        except Exception as 错:  # noqa: BLE001
            self.提示.setText(f"列目录失败：{错}")
            return
        self.表.setRowCount(len(条目们))
        for 行号, 项 in enumerate(条目们):
            self.表.setItem(行号, 0, QTableWidgetItem(
                ("📁 " if 项.是目录 else "🎬 ") + 项.名字))
            self.表.setItem(行号, 1, QTableWidgetItem(项.大小文本()))
            self.表.setItem(行号, 2, QTableWidgetItem("目录" if 项.是目录 else 项.后缀))
            self.表.setItem(行号, 3, QTableWidgetItem("" if 项.是目录 else "双击下载"))
            项目 = self.表.item(行号, 0)
            项目.setData(Qt.ItemDataRole.UserRole, 项)
        self.提示.setText(f"共 {len(条目们)} 项")

    def _选中(self):
        行号 = self.表.currentRow()
        if 行号 < 0:
            return None
        项目 = self.表.item(行号, 0)
        return 项目.data(Qt.ItemDataRole.UserRole) if 项目 else None

    def _打开选中(self) -> None:
        项 = self._选中()
        if 项 is None:
            return
        if 项.是目录:
            self.子目录栈.append(self.当前目录)
            self.当前目录 = 项.路径
            self.刷新()
            return
        self._下载选中()

    def _上一级(self) -> None:
        if self.子目录栈:
            self.当前目录 = self.子目录栈.pop()
        else:
            self.当前目录 = "/"
        self.刷新()

    # ---------------- 播放 / 下载 ----------------

    def _播放选中(self) -> None:
        项 = self._选中()
        if 项 is None or 项.是目录:
            self.提示.setText("先选一个文件（目录不能播）")
            return
        对象 = self.注册表.取(self.当前网盘)
        try:
            直链 = 对象.取直链(项.路径)
        except 不支持 as 错:
            self.提示.setText(f"这个网盘给不出直链：{错}")
            return
        except Exception as 错:  # noqa: BLE001
            self.提示.setText(f"取直链失败：{错}")
            return
        self.提示.setText(f"▶ 交给播放器：{项.名字}")
        self.要播放.emit(直链.地址, dict(直链.请求头))

    def _下载选中(self) -> None:
        项 = self._选中()
        if 项 is None or 项.是目录:
            self.提示.setText("先选一个文件")
            return
        目录 = QFileDialog.getExistingDirectory(self, "下载到哪个目录",
                                            str(Path.home() / "下载"))
        if not 目录:
            return
        落点 = Path(目录) / 项.名字
        任务 = self.传输.加下载(self.当前网盘, 项.路径, 落点, 项.名字, 项.大小)
        self.提示.setText(f"⬇ 已加入传输：{任务.名字}")

    def _刷传输表(self) -> None:
        任务们 = self.传输.任务们()
        self.传输表.setRowCount(len(任务们))
        for 行号, 任务 in enumerate(任务们):
            self.传输表.setItem(行号, 0, QTableWidgetItem(任务.名字))
            self.传输表.setCellWidget(行号, 1, self._进度条(任务))
            self.传输表.setItem(行号, 2, QTableWidgetItem(任务.摘要()))

    def _进度条(self, 任务) -> QProgressBar:
        条 = QProgressBar()
        条.setRange(0, 1000)
        条.setValue(int(任务.进度 * 1000))
        条.setFormat(f"{任务.进度 * 100:.0f}%")
        return 条

    def _写日志(self, 文本: str) -> None:
        self.提示.setText(文本)

    def 关闭(self) -> None:
        try:
            self.传输.关闭()
        except Exception:  # noqa: BLE001
            pass
