"""手动匹配对话框：TMDB 搜不到 / 匹配错的时候，让人自己搜、自己选。

为什么要单独做它（而不是"自动匹配失败就算了"）：
* 自动匹配再准也会有**认错**的时候（同名、翻拍、季数歧义、番剧正片/番外标题极像），
  这时必须有一条"人来定"的路；
* TMDB 的 API 域名在部分网络下**根本连不上**（真机实测：被解析到无关 IP、全超时），
  但**图片 CDN 是通的** —— 所以本对话框把两种情况分开处理：
  ① 能搜的时候：列候选、显示年份与简介，点一下就重新按这个 id 刮；
  ② 连不上/没配 Key 的时候：把"为什么"和"怎么办"直接写在界面上，并引导到
     「✍ 手动填写资料」（那条路完全不需要网络）。

设计取舍
========
* 对话框**只负责"选出 (来源标识, 类型)"**，不自己写库 —— 刮削/落库交给 :class:`刮削服务`，
  这样"手动选的"和"自动选的"走同一条落库路径，不会出现两套行为；
* 搜索引擎注入（`客户端` 参数）：测试用假客户端，界面不关心 TMDB 细节；
* 搜索有**防抖 + 后台线程**（TMDB 一次搜索可能要一两秒，卡界面是大忌）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..scrape.模型 import 媒体类型, 匹配候选

__all__ = ["手动匹配对话框", "搜索任务"]


@dataclass
class 选择结果:
    """用户选定的结果（给刮削用）。"""

    标识: str = ""
    类型: 媒体类型 = 媒体类型.未知
    标题: str = ""
    年份: Optional[int] = None

    def 可读(self) -> str:
        年份 = f"（{self.年份}）" if self.年份 else ""
        return f"{self.标题}{年份}｜{self.类型.value}｜tmdb:{self.标识}"


class 搜索任务(QThread):
    """后台搜索（避免卡界面）；结果通过信号回主线程。"""

    完成 = Signal(list)          # list[匹配候选]
    失败 = Signal(str)

    def __init__(self, 客户端, 关键词: str, 年份: Optional[int], 类型: 媒体类型,
                 父=None) -> None:
        super().__init__(父)
        self.客户端 = 客户端
        self.关键词 = 关键词
        self.年份 = 年份
        self.类型 = 类型

    def run(self) -> None:            # noqa: D102 - QThread 约定
        try:
            if self.类型 is 媒体类型.剧集:
                候选们 = self.客户端.搜剧集(self.关键词, self.年份)
            else:
                候选们 = self.客户端.搜电影(self.关键词, self.年份)
            self.完成.emit(list(候选们 or []))
        except Exception as 错:  # noqa: BLE001 - 任何失败都要变成界面能读的一句话
            self.失败.emit(str(错))


class 手动匹配对话框(QDialog):
    """搜 TMDB（或注入的客户端）→ 选一个 → 返回 (标识, 类型)。"""

    #: 用户点了「改为手动填写资料」（宿主据此打开 :class:`手动资料对话框`）
    要手动填写 = Signal()

    def __init__(self, 客户端=None, 关键词: str = "", 年份: Optional[int] = None,
                 类型: 媒体类型 = 媒体类型.电影, 父=None) -> None:
        super().__init__(父)
        self.setWindowTitle("手动匹配")
        self.resize(720, 520)
        self.客户端 = 客户端
        self.结果: Optional[选择结果] = None
        self._任务: Optional[搜索任务] = None
        self._候选们: list[匹配候选] = []

        布局 = QVBoxLayout(self)
        提示 = QLabel("搜不到或匹配错时，在这里自己搜、自己选。"
                    "（自动匹配认错很常见：同名、翻拍、季数歧义、正片与番外标题很像）")
        提示.setWordWrap(True)
        布局.addWidget(提示)

        条件行 = QHBoxLayout()
        条件行.addWidget(QLabel("关键词"))
        self.关键词框 = QLineEdit(关键词)
        self.关键词框.setPlaceholderText("片名（中英文都行）")
        self.关键词框.returnPressed.connect(self.搜索)
        条件行.addWidget(self.关键词框, 2)
        条件行.addWidget(QLabel("年份"))
        self.年份框 = QSpinBox()
        self.年份框.setRange(0, 2100)
        self.年份框.setSpecialValueText("不限")
        self.年份框.setValue(int(年份 or 0))
        条件行.addWidget(self.年份框)
        self.类型框 = QComboBox()
        self.类型框.addItem("电影", 媒体类型.电影)
        self.类型框.addItem("剧集", 媒体类型.剧集)
        self.类型框.setCurrentIndex(1 if 类型 is 媒体类型.剧集 else 0)
        条件行.addWidget(self.类型框)
        self.搜索按钮 = QPushButton("🔍 搜索")
        self.搜索按钮.clicked.connect(self.搜索)
        条件行.addWidget(self.搜索按钮)
        布局.addLayout(条件行)

        self.列表 = QListWidget()
        self.列表.itemDoubleClicked.connect(lambda _i: self.采纳())
        布局.addWidget(self.列表, 1)

        self.状态标签 = QLabel("填关键词后点搜索；双击结果即可选定。")
        self.状态标签.setWordWrap(True)
        布局.addWidget(self.状态标签)

        按钮行 = QHBoxLayout()
        self.手动填写按钮 = QPushButton("✍ 改为手动填写资料（不需要网络）")
        self.手动填写按钮.clicked.connect(self._要手动填写)
        按钮行.addWidget(self.手动填写按钮)
        按钮行.addStretch(1)
        self.确定按钮 = QPushButton("✅ 就选这个")
        self.确定按钮.clicked.connect(self.采纳)
        self.确定按钮.setEnabled(False)
        按钮行.addWidget(self.确定按钮)
        取消 = QPushButton("取消")
        取消.clicked.connect(self.reject)
        按钮行.addWidget(取消)
        布局.addLayout(按钮行)

        self._防抖 = QTimer(self)
        self._防抖.setSingleShot(True)
        self._防抖.setInterval(120)
        self._防抖.timeout.connect(self._真搜索)
        self.关键词框.textChanged.connect(lambda _t: self._防抖.start())

        if self.客户端 is None:
            self._说明没有客户端()
        elif not self._客户端可用():
            self._说明没有客户端()
        elif 关键词:
            self.搜索()

    # ---------------- 状态 ----------------

    def _客户端可用(self) -> bool:
        try:
            return bool(self.客户端 is not None and self.客户端.可用())
        except Exception:  # noqa: BLE001
            return False

    def _说明没有客户端(self) -> None:
        self.状态标签.setText(
            "⚠️ 现在用不了 TMDB 搜索（没配 API Key，或网络到不了 api.themoviedb.org —— "
            "实测有这个情况：图片 CDN 通、API 不通）。\n"
            "两条路：① 到 https://www.themoviedb.org/settings/api 申请 Key，写进 "
            "数据/刮削.json 或设 V2_TMDB_TOKEN；② 直接点下面的「✍ 改为手动填写资料」，"
            "完全不需要网络。")
        self.搜索按钮.setEnabled(False)

    # ---------------- 搜索 ----------------

    def 搜索(self) -> None:
        if not self._客户端可用():
            self._说明没有客户端()
            return
        关键词 = self.关键词框.text().strip()
        if not 关键词:
            self.状态标签.setText("先填关键词。")
            return
        self.状态标签.setText(f"正在搜索「{关键词}」…")
        self.搜索按钮.setEnabled(False)
        年份 = self.年份框.value() or None
        类型 = self.类型框.currentData()
        self._任务 = 搜索任务(self.客户端, 关键词, 年份, 类型, self)
        self._任务.完成.connect(self._搜索完成)
        self._任务.失败.connect(self._搜索失败)
        self._任务.finished.connect(lambda: self.搜索按钮.setEnabled(True))
        self._任务.start()

    def _真搜索(self) -> None:
        if self.关键词框.text().strip():
            self.搜索()

    def _搜索完成(self, 候选们: list) -> None:
        self._候选们 = list(候选们)
        self.列表.clear()
        if not 候选们:
            self.状态标签.setText("没搜到。换个关键词（试试原名/英文名）或去掉年份限制。")
            self.确定按钮.setEnabled(False)
            return
        for 候选 in 候选们[:50]:
            年份 = f"（{候选.年份}）" if getattr(候选, "年份", None) else ""
            标签 = (f"{候选.标题}{年份}｜{候选.类型.value}"
                  + (f"｜热度 {候选.热度:.0f}" if getattr(候选, "热度", 0) else ""))
            项 = QListWidgetItem(标签)
            if getattr(候选, "简介", ""):
                项.setToolTip(候选.简介[:300])
            项.setData(Qt.ItemDataRole.UserRole, 候选)
            self.列表.addItem(项)
        self.列表.setCurrentRow(0)
        self.确定按钮.setEnabled(True)
        self.状态标签.setText(f"找到 {len(候选们)} 个结果；双击或点「就选这个」选定。")

    def _搜索失败(self, 文本: str) -> None:
        self.状态标签.setText(f"搜索失败：{文本}")
        self.确定按钮.setEnabled(False)

    # ---------------- 选定 ----------------

    def 采纳(self) -> None:
        项 = self.列表.currentItem()
        if 项 is None:
            self.状态标签.setText("先选一个结果。")
            return
        候选 = 项.data(Qt.ItemDataRole.UserRole)
        self.结果 = 选择结果(标识=str(getattr(候选, "来源标识", "") or ""),
                          类型=getattr(候选, "类型", 媒体类型.未知),
                          标题=getattr(候选, "标题", ""),
                          年份=getattr(候选, "年份", None))
        self.accept()

    def _要手动填写(self) -> None:
        self.要手动填写.emit()
        self.accept()

    def 取选择(self) -> Optional[选择结果]:
        return self.结果
