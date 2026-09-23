"""待确认队列：刮削"拿不准"的条目在这里等人点一下。

为什么必须有这一屏
==================
自动匹配再准也会有拿不准的时候（同名、翻拍、番剧正片与番外、年份差一年、
文件名被压制组改得面目全非）。这些条目**不能猜着入库**（错了就是把别人的
海报/简介挂到你的片子上），也不能就那样丢掉（下次扫描还得再搜一遍 TMDB，
白白挨限流）。所以：

* 刮削时拿不准 → 记进"刮削任务"表（状态 ``需要确认``），**候选列表一起存下来**
  （:meth:`~wangpan.scrape.库.资料库.记任务` 的 ``候选`` 参数，JSON 列）；
* 人打开这一屏 → 看候选、点一个 → 走 :meth:`~wangpan.scrape.服务.刮削服务.采纳候选`
  强制 ID 刮一次，正式入库；
* 不想刮的 → 「忽略这条」，状态变 ``跳过``，**不再自动重试**（也不会占着队列）。

界面纪律（和别处一致）
======================
* 这一屏**只做"看 + 选 + 划掉"**，不联网：真正的刮削由主窗口在后台线程里跑
  （界面线程干重活 = 卡死，这个坑在字幕/截图/刮削上都踩过）；
* 忽略是纯本地写库动作（一条 UPDATE），所以可以就地做完、立刻跳下一条 ——
  这样"清队列"是连续点击，不用每条都关窗再开窗。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView,
                             QLabel, QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..scrape.模型 import 待确认项, 媒体类型, 匹配候选

__all__ = ["待确认对话框", "待确认决定"]


@dataclass
class 待确认决定:
    """用户在队列里做的决定（交给主窗口去执行网络动作）。"""

    动作: str = "稍后"          # 采纳 / 搜索 / 稍后
    路径: str = ""
    标识: str = ""
    类型: Optional[媒体类型] = None
    标题: str = ""
    年份: Optional[int] = None
    行号: int = 0               # 决定时在第几条（重开对话框时接着往下走）

    def 一句话(self) -> str:
        if self.动作 == "采纳":
            return f"采纳 TMDB {self.标识}《{self.标题}》"
        if self.动作 == "搜索":
            return f"重新搜索《{self.标题 or self.路径}》"
        return "稍后再说"


class 待确认对话框(QDialog):
    """左边队列、右边候选；不联网，决定用 :meth:`取决定` 交出去。"""

    def __init__(self, 库, 服务=None, 父=None, 起始行: int = 0) -> None:
        super().__init__(父)
        self.setWindowTitle("待确认的刮削")
        self.resize(980, 620)
        self.库 = 库
        self.服务 = 服务
        self.决定: Optional[待确认决定] = None
        self._项们: list[待确认项] = []

        布局 = QVBoxLayout(self)
        self.标题标签 = QLabel("自动匹配拿不准的条目都在这里。选一个候选 → 采纳，"
                            "就去 TMDB 取详情入库；不想刮就「忽略这条」。")
        self.标题标签.setWordWrap(True)
        布局.addWidget(self.标题标签)

        主体 = QHBoxLayout()
        左列 = QVBoxLayout()
        左列.addWidget(QLabel("待确认队列"))
        self.列表 = QListWidget()
        self.列表.setMinimumWidth(320)
        self.列表.currentRowChanged.connect(self._换行)
        self.列表.itemDoubleClicked.connect(lambda _i: self.采纳())
        左列.addWidget(self.列表, 1)
        主体.addLayout(左列, 2)

        右列 = QVBoxLayout()
        self.路径标签 = QLabel("—")
        self.路径标签.setWordWrap(True)
        self.路径标签.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        右列.addWidget(self.路径标签)
        self.说明标签 = QLabel("")
        self.说明标签.setWordWrap(True)
        右列.addWidget(self.说明标签)
        右列.addWidget(QLabel("候选（双击=采纳）"))
        self.候选表 = QTableWidget(0, 4)
        self.候选表.setHorizontalHeaderLabels(["分数", "标题", "年份", "类型"])
        self.候选表.verticalHeader().setVisible(False)
        self.候选表.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.候选表.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.候选表.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.候选表.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.候选表.itemDoubleClicked.connect(lambda _i: self.采纳())
        右列.addWidget(self.候选表, 1)
        self.理由标签 = QLabel("")
        self.理由标签.setWordWrap(True)
        右列.addWidget(self.理由标签)
        主体.addLayout(右列, 3)
        布局.addLayout(主体, 1)

        self.汇总标签 = QLabel("")
        self.汇总标签.setWordWrap(True)
        布局.addWidget(self.汇总标签)

        按钮行 = QHBoxLayout()
        self.采纳按钮 = QPushButton("✅ 采纳选中的候选（取详情入库）")
        self.采纳按钮.setDefault(True)
        self.采纳按钮.clicked.connect(self.采纳)
        按钮行.addWidget(self.采纳按钮)
        self.搜索按钮 = QPushButton("🔍 换个名字再搜")
        self.搜索按钮.setToolTip("打开手动匹配搜索（自动匹配的关键词往往不够准）")
        self.搜索按钮.clicked.connect(self.要搜索)
        按钮行.addWidget(self.搜索按钮)
        self.忽略按钮 = QPushButton("🗑 忽略这条")
        self.忽略按钮.setToolTip("不刮这一条，也不再自动重试（资料库不动）")
        self.忽略按钮.clicked.connect(lambda: self.忽略当前())
        按钮行.addWidget(self.忽略按钮)
        self.忽略全部按钮 = QPushButton("🗑 忽略全部")
        self.忽略全部按钮.clicked.connect(self.忽略全部)
        按钮行.addWidget(self.忽略全部按钮)
        按钮行.addStretch(1)
        self.关闭按钮 = QPushButton("稍后再说")
        self.关闭按钮.clicked.connect(self.reject)
        按钮行.addWidget(self.关闭按钮)
        布局.addLayout(按钮行)

        self.刷新(起始行)

    # ---------------- 队列 ----------------

    def 刷新(self, 起始行: int = 0) -> int:
        """重新读队列（主窗口刮完一条/忽略之后调用），返回条数。"""
        self._项们 = list(self.库.待确认())
        self.列表.blockSignals(True)
        self.列表.clear()
        for 项 in self._项们:
            年份 = f"（{项.年份}）" if 项.年份 else ""
            条目 = QListWidgetItem(f"{项.标题 or 项.文件名}{年份}"
                                f"  ·  候选 {len(项.候选们)}")
            条目.setToolTip(f"{项.路径}\n{项.说明}")
            self.列表.addItem(条目)
        self.列表.blockSignals(False)
        self._刷汇总()
        if self._项们:
            self.列表.setCurrentRow(max(0, min(int(起始行), len(self._项们) - 1)))
        else:
            self._换行(-1)
        return len(self._项们)

    def 条数(self) -> int:
        return len(self._项们)

    def 当前项(self) -> Optional[待确认项]:
        行 = self.列表.currentRow()
        return self._项们[行] if 0 <= 行 < len(self._项们) else None

    def 选中候选(self) -> Optional[匹配候选]:
        行 = self.候选表.currentRow()
        项 = self.当前项()
        if 项 is None or not (0 <= 行 < len(项.候选们)):
            return None
        return 项.候选们[行]

    def 取决定(self) -> Optional[待确认决定]:
        return self.决定

    # ---------------- 显示 ----------------

    def _换行(self, 行: int) -> None:
        项 = self.当前项()
        self.候选表.setRowCount(0)
        if 项 is None:
            self.路径标签.setText("队列是空的 🎉" if not self._项们
                              else "在左边选一条（也可以双击直接采纳）")
            self.说明标签.setText("")
            self.理由标签.setText("")
            self.采纳按钮.setEnabled(False)
            self.搜索按钮.setEnabled(False)
            self.忽略按钮.setEnabled(False)
            return
        self.路径标签.setText(f"📄 {项.路径}")
        self.说明标签.setText(f"自动匹配为什么没敢定：{项.说明 or '（没有说明）'}")
        self.候选表.setRowCount(len(项.候选们))
        for 序号, 候选 in enumerate(项.候选们):
            单元们 = (f"{候选.分数:.1f}", 候选.标题 or "（无标题）",
                    str(候选.年份 or ""), 候选.类型.value)
            for 列, 文本 in enumerate(单元们):
                单元 = QTableWidgetItem(文本)
                if 列 == 0:
                    单元.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
                if 候选.理由:
                    单元.setToolTip(候选.理由)
                self.候选表.setItem(序号, 列, 单元)
        最佳 = 项.最佳()
        if 项.候选们:
            self.候选表.setCurrentCell(0, 0)
            self.理由标签.setText("打分理由：" + (最佳.理由 if 最佳 and 最佳.理由
                                          else "（无）"))
        else:
            self.理由标签.setText("一个候选都没有：换个名字搜（🔍），"
                              "或先用「✍ 手动填写资料」写本地 NFO。")
        self.采纳按钮.setEnabled(bool(项.候选们))
        self.搜索按钮.setEnabled(True)
        self.忽略按钮.setEnabled(True)

    def _刷汇总(self) -> None:
        总数 = len(self._项们)
        self.汇总标签.setText(
            f"共 {总数} 条待确认" if 总数 else "没有待确认的条目（刮削队列是干净的）")
        self.忽略全部按钮.setEnabled(bool(总数))

    # ---------------- 动作 ----------------

    def 采纳(self) -> None:
        项, 候选 = self.当前项(), self.选中候选()
        if 项 is None or 候选 is None:
            self.理由标签.setText("先在右边选一个候选。")
            return
        self.决定 = 待确认决定(动作="采纳", 路径=项.路径, 标识=候选.来源标识,
                          类型=候选.类型, 标题=候选.标题 or 项.标题,
                          年份=候选.年份, 行号=self.列表.currentRow())
        self.accept()

    def 要搜索(self) -> None:
        项 = self.当前项()
        if 项 is None:
            return
        self.决定 = 待确认决定(动作="搜索", 路径=项.路径, 标题=项.标题,
                          年份=项.年份, 类型=项.类型, 行号=self.列表.currentRow())
        self.accept()

    def 忽略当前(self, 原因: str = "人工跳过") -> bool:
        """划掉当前这条并**留在窗口里**（清队列是连续动作）。"""
        项 = self.当前项()
        if 项 is None:
            return False
        行 = self.列表.currentRow()
        if self.服务 is not None:
            self.服务.忽略待确认(项.路径, 原因)
        else:
            self.库.记任务(项.路径, "跳过", None, 原因)
        self.刷新(起始行=行)
        return True

    def 忽略全部(self, 确认: bool = True) -> int:
        if not self._项们:
            return 0
        if 确认:
            答复 = QMessageBox.question(
                self, "忽略全部待确认",
                f"把 {len(self._项们)} 条都划成「跳过」？\n"
                "资料库不会被写入，但这些条目也不会再自动重试"
                "（下次扫描仍会重新搜索）。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if 答复 is not QMessageBox.StandardButton.Yes:
                return 0
        数 = 0
        for 项 in list(self._项们):
            if self.服务 is not None:
                self.服务.忽略待确认(项.路径, "人工全部跳过")
            else:
                self.库.记任务(项.路径, "跳过", None, "人工全部跳过")
            数 += 1
        self.刷新()
        return 数
