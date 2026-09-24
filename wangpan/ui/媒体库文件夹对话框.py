"""「媒体库文件夹」管理对话框：加任意多个文件夹（本地 / 网盘）。

用户要求：*媒体库改为可添加多个文件夹，包括网盘内的文件夹*。

这个对话框就是那份清单的界面：

    ┌ 媒体库文件夹 ───────────────────────────────┐
    │ 💾 电影            /mnt/movies              │
    │ 💾 纪录片          /mnt/doc                 │
    │ ☁️ 光鸭·电影        /电影                    │
    │  [＋本地文件夹] [＋网盘文件夹] [移除] [🔄 扫描全部] │
    └────────────────────────────────────────────┘

* 「＋本地文件夹」用系统的选目录对话框；
* 「＋网盘文件夹」调**注入进来的** `选文件夹()` —— 那个回调在 v8_3 那边，
  它才能调网盘适配器（依赖方向是单向的：v8_3 → wangpan，wangpan 里不许 import v8_3）；
* 「🔄 扫描全部」把清单交给媒体库页去扫（本地 os.walk + 网盘递归列目录）。
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel,
                             QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QVBoxLayout, QWidget)

from .媒体库来源 import 来源清单

__all__ = ["媒体库文件夹对话框"]


class 媒体库文件夹对话框(QDialog):
    """管理"媒体库要扫哪些文件夹"。"""

    def __init__(self, 清单: 来源清单, 父=None, *,
                 选网盘文件夹: Optional[Callable[[], Optional[dict]]] = None,
                 要扫描全部: Optional[Callable[[], None]] = None,
                 日志: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(父)
        self.setWindowTitle("媒体库文件夹")
        self.resize(620, 420)
        self.清单 = 清单
        self._选网盘文件夹 = 选网盘文件夹
        self._要扫描全部 = 要扫描全部
        self._日志 = 日志 or (lambda _t: None)

        布局 = QVBoxLayout(self)
        说明 = QLabel(
            "可以加**多个**文件夹，本地和网盘混着放都行。\n"
            "网盘文件夹里的视频会按文件名刮削（网盘上拿不到 nfo/海报，这是正常的）。")
        说明.setWordWrap(True)
        说明.setStyleSheet("color:#888;font-size:11px;")
        布局.addWidget(说明)

        self.列表 = QListWidget()
        self.列表.setAlternatingRowColors(True)
        布局.addWidget(self.列表, 1)

        行 = QHBoxLayout()
        self.加本地按钮 = QPushButton("＋本地文件夹")
        self.加本地按钮.clicked.connect(self.加本地)
        行.addWidget(self.加本地按钮)

        self.加网盘按钮 = QPushButton("＋网盘文件夹")
        self.加网盘按钮.setToolTip("从网盘里挑一个文件夹（需要先登录网盘）")
        self.加网盘按钮.clicked.connect(self.加网盘)
        行.addWidget(self.加网盘按钮)

        self.移除按钮 = QPushButton("－移除")
        self.移除按钮.clicked.connect(self.移除选中)
        行.addWidget(self.移除按钮)

        行.addStretch(1)
        self.扫描按钮 = QPushButton("🔄 扫描全部")
        self.扫描按钮.setToolTip("把清单里所有文件夹都扫一遍（本地 + 网盘）")
        self.扫描按钮.clicked.connect(self.扫描全部)
        行.addWidget(self.扫描按钮)
        self.关闭按钮 = QPushButton("关闭")
        self.关闭按钮.clicked.connect(self.accept)
        行.addWidget(self.关闭按钮)
        布局.addLayout(行)

        self.刷新()

    # ------------------------------------------------------------------ 列表

    def 刷新(self) -> None:
        self.列表.clear()
        项们 = self.清单.全部()
        for i, 项 in enumerate(项们):
            条目 = QListWidgetItem(f"{项.一句话()}"
                                + ("" if 项.类型 == "网盘" else f"　（{项.路径}）"))
            条目.setData(Qt.ItemDataRole.UserRole, i)
            if 项.类型 == "网盘":
                条目.setToolTip(f"网盘实例：{项.网盘}　远端路径：{项.路径}")
            条目.setToolTip((条目.toolTip() + "\n" if 条目.toolTip() else "")
                          + f"路径：{项.路径}")
            self.列表.addItem(条目)
        if not 项们:
            self.列表.addItem(QListWidgetItem("（还没有文件夹：点下面的按钮加一个）"))
        self.扫描按钮.setEnabled(bool(项们))
        网盘可用 = callable(self._选网盘文件夹)
        self.加网盘按钮.setEnabled(网盘可用)
        self.加网盘按钮.setToolTip(
            "从网盘里挑一个文件夹" if 网盘可用
            else "当前界面没接上网盘浏览能力（只能加本地文件夹）")

    # ------------------------------------------------------------------ 增删

    def 加本地(self) -> None:
        目录 = QFileDialog.getExistingDirectory(self, "选一个媒体文件夹", "")
        if not 目录:
            return
        if self.清单.加本地(目录):
            self._日志(f"[媒体库] 已添加本地文件夹：{目录}")
            self.刷新()
        else:
            QMessageBox.information(self, "已经在清单里了", f"{目录}\n\n不用重复添加。")

    def 加网盘(self) -> None:
        if not callable(self._选网盘文件夹):
            QMessageBox.information(self, "暂时不行",
                                "当前界面没有接入网盘浏览能力。")
            return
        try:
            选中 = self._选网盘文件夹() or None
        except Exception as 错:  # noqa: BLE001
            QMessageBox.warning(self, "打开网盘失败", str(错))
            return
        if not 选中:
            return
        网盘 = str(选中.get("网盘") or "")
        路径 = str(选中.get("路径") or "/")
        显示名 = str(选中.get("显示名") or 网盘)
        if not 网盘:
            return
        if self.清单.加网盘(网盘, 路径, f"{显示名}：{路径}"):
            self._日志(f"[媒体库] 已添加网盘文件夹：{网盘}:{路径}")
            self.刷新()
        else:
            QMessageBox.information(self, "已经在清单里了",
                                f"{网盘}:{路径}\n\n不用重复添加。")

    def 移除选中(self) -> None:
        行 = self.列表.currentRow()
        if 行 < 0:
            return
        项 = self.列表.item(行)
        下标 = 项.data(Qt.ItemDataRole.UserRole) if 项 is not None else None
        if 下标 is None:
            return
        if self.清单.移除(int(下标)):
            self._日志("[媒体库] 已移除一个文件夹")
            self.刷新()

    # ------------------------------------------------------------------ 扫描

    def 扫描全部(self) -> None:
        if callable(self._要扫描全部):
            self._要扫描全部()
