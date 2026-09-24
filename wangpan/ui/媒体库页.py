"""媒体库页：海报墙 + 详情页 + 待确认队列 + 手动匹配/手动资料 + 刮削流水线。

为什么单独成一页
================
这一块是 V2 自己长出来的能力（TMDB 刮削 / 海报墙 / 详情页），**两个主界面都要用**：

* V2 的"纯内核"主窗口（`启动.py --纯播放`）把它当一个标签页；
* 整合后的主界面（V1 那套顶栏 + 左栏 + 堆叠页）把它当"媒体库"那一页。

所以它必须是**自包含的一页**：自己持有资料库/图片缓存，自己起后台线程刮削，
对外只发信号（要播放 / 提示 / 日志），不反过来去摸主窗口的内部字段。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QVBoxLayout, QWidget

from ..scrape.模型 import 媒体类型

__all__ = ["媒体库页"]


class 媒体库页(QWidget):
    """一页装下：海报墙 / 详情页 / 待确认 / 手动匹配 / 手动资料。"""

    要播放 = Signal(str)
    提示 = Signal(str)
    日志 = Signal(str)

    def __init__(self, 父: Optional[QWidget] = None, *,
                 日志回调: Optional[Callable[[str], None]] = None,
                 提示回调: Optional[Callable[[str], None]] = None,
                 选网盘文件夹: Optional[Callable[[], Optional[dict]]] = None,
                 列网盘目录: Optional[Callable[[str, str], list]] = None) -> None:
        super().__init__(父)
        self._外部日志 = 日志回调
        self._外部提示 = 提示回调
        #: 网盘能力由界面层注入（wangpan 里不许 import v8_3，见 媒体库来源.py 的说明）
        self._选网盘文件夹 = 选网盘文件夹
        self._列网盘目录 = 列网盘目录
        #: 这一轮扫描里"网盘文件"的待刮削单元（刮削服务用它，不必碰本地磁盘）
        self._远端单元: dict = {}
        #: 刮削跑完如果留下"待确认"，自动把队列摆到人面前（测试可关掉）
        self.自动开队列 = True
        self._刮削线程: Optional[QThread] = None
        self._收尾过 = False

        self.资料库 = None
        self.图片缓存 = None
        self.海报墙 = None
        #: 多个媒体库文件夹（含网盘）的清单
        from .媒体库来源 import 来源清单 as _来源清单
        self.来源清单 = _来源清单()
        布局 = QVBoxLayout(self)
        布局.setContentsMargins(0, 0, 0, 0)
        try:
            from ..scrape.库 import 资料库
            from ..scrape.图片 import 图片缓存
            from .媒体库来源 import 来源清单
            from .海报墙页 import 海报墙页
            self.资料库 = 资料库()
            self.图片缓存 = 图片缓存()
            self.海报墙 = 海报墙页(self.资料库, self.图片缓存)
            布局.addWidget(self.海报墙)
            self.海报墙.要播放.connect(self.要播放)
            self.海报墙.要刮削.connect(self.刮削路径)
            self.海报墙.要手动匹配.connect(self.开手动匹配)
            self.海报墙.要处理待确认.connect(self.开待确认队列)
            self.海报墙.要管理文件夹.connect(self.管理文件夹)
            self.海报墙.状态变化.connect(self._提示)
        except Exception as 错:  # noqa: BLE001
            self.资料库 = None
            self.海报墙 = None
            self._写日志(f"[媒体库] 初始化失败（不影响播放）：{错}")

    # ------------------------------------------------------------------ 出口

    @property
    def 可用(self) -> bool:
        return self.资料库 is not None and self.海报墙 is not None

    def _写日志(self, 文本: str) -> None:
        self.日志.emit(str(文本))
        if self._外部日志 is not None:
            try:
                self._外部日志(str(文本))
            except Exception:  # noqa: BLE001
                pass

    def _提示(self, 文本: str) -> None:
        self.提示.emit(str(文本))
        if self._外部提示 is not None:
            try:
                self._外部提示(str(文本))
            except Exception:  # noqa: BLE001
                pass

    def 刷新(self) -> None:
        if self.海报墙 is not None:
            self.海报墙.刷新()

    # ------------------------------------------------------ 媒体库文件夹（多个）

    def 管理文件夹(self) -> None:
        """打开「媒体库文件夹」对话框（加/删文件夹，含网盘内的）。"""
        from .媒体库文件夹对话框 import 媒体库文件夹对话框
        框 = 媒体库文件夹对话框(
            self.来源清单, self,
            选网盘文件夹=self._选网盘文件夹,
            要扫描全部=self.扫描全部来源,
            日志=self._写日志)
        框.exec()

    def 扫描全部来源(self, 完成后: Optional[Callable[[], None]] = None) -> None:
        """把清单里的**所有**文件夹扫一遍：本地走 os.walk，网盘走递归列目录。"""
        from .媒体库来源 import 远端视频们
        全部 = self.来源清单.全部()
        if not 全部:
            self._提示("🎞 还没有媒体库文件夹：先点「📂 媒体库文件夹…」加一个")
            return
        本地目录们 = [x.路径 for x in 全部 if x.类型 == "本地"]
        远端单元们: list = []
        for 项 in 全部:
            if 项.类型 != "网盘":
                continue
            if not callable(self._列网盘目录):
                self._写日志(f"[媒体库] 没有接上网盘列目录，跳过 {项.路径}")
                continue
            self._写日志(f"[媒体库] 正在列网盘目录：{项.网盘}:{项.路径}")
            文件们 = 远端视频们(项.网盘, 项.路径, self._列网盘目录,
                          进度=self._写日志)
            self._写日志(f"[媒体库] {项.网盘}:{项.路径} 找到 {len(文件们)} 个视频")
            from ..scrape.扫描 import 造远端单元
            远端单元们 += 造远端单元(项.网盘, 项.路径, 文件们)
        if not 本地目录们 and not 远端单元们:
            self._提示("🎞 清单里的文件夹都没扫出内容（检查路径/网盘登录）")
            return
        self._远端单元 = {str(u.路径): u for u in 远端单元们}
        self._提示(f"🎞 开始扫描 {len(本地目录们)} 个本地文件夹 + "
                 f"{len(远端单元们)} 个网盘视频…"
                 "（匹配得到的直接进海报墙；**拿不准的会弹「待确认」队列**让你点一下）")
        self.刮削路径("", 完成后=完成后, 本地目录们=本地目录们,
                   远端单元们=远端单元们)

    # ------------------------------------------------------------------ 刮削

    def 建TMDB客户端(self):
        try:
            from ..scrape.tmdb import TMDB客户端, TMDB配置
            配置 = TMDB配置.从环境与配置()
            if not (配置.token or 配置.api_key):
                return None
            return TMDB客户端(配置)
        except Exception as 错:  # noqa: BLE001
            self._写日志(f"[刮削] 建 TMDB 客户端失败：{错}")
            return None

    def 刮削路径(self, 路径: str, 强制标识: str = "",
               强制类型=None, 完成后: Optional[Callable[[], None]] = None,
               本地目录们: Optional[list] = None,
               远端单元们: Optional[list] = None) -> None:
        """刮一个目录/文件：在**后台线程**里跑，界面只显示进度。

        为什么要线程 + 进度框：刮削要联网、要下图，几秒钟到几分钟都有可能；
        放在界面线程里会整个卡死（真机踩过"界面线程里干重活"的坑）。
        """
        from ..scrape.库 import 资料库
        from ..scrape.图片 import 图片缓存
        from ..scrape.服务 import 刮削服务, 刮削设置
        if self.资料库 is None:
            self._提示("🎞 媒体库没能初始化（资料库打不开）")
            return
        客户端 = self.建TMDB客户端()
        if 客户端 is None:
            QMessageBox.information(
                self, "还没有配置 TMDB",
                "刮削需要 TMDB 的 API Key（免费）：\n\n"
                "1. 到 https://www.themoviedb.org/settings/api 申请；\n"
                "2. 把 token 写进 数据/刮削.json（{\"tmdb\": {\"token\": \"…\"}}），\n"
                "   或设环境变量 V2_TMDB_TOKEN。\n\n"
                "没配置也能用：扫描 + 本地 NFO/图片仍然会入库。")
        日志回调 = self._写日志
        进度框 = QProgressDialog("正在扫描…", "取消", 0, 0, self)
        进度框.setWindowTitle("刮削中")
        进度框.setMinimumDuration(0)
        进度框.show()
        结果盒: dict = {}

        class 干活(QThread):
            def run(自己):
                try:
                    库 = 资料库()
                    缓存 = 图片缓存()
                    单元表 = {str(u.路径): u for u in (远端单元们 or [])}
                    服务 = 刮削服务(
                        库, 客户端, 缓存, 刮削设置(),
                        进度回调=lambda p: 结果盒.setdefault("进度", p.摘要()),
                        日志回调=日志回调,
                        远端单元=单元表)
                    if 本地目录们:
                        服务.扫库(list(本地目录们))
                    elif 路径:
                        服务.扫库([路径])
                    if 远端单元们:
                        服务.记远端单元(远端单元们)
                    if 强制标识:
                        结果盒["结果"] = 服务.刮路径(路径, 强制标识, 强制类型)
                    else:
                        结果盒["结果"] = 服务.续跑()
                    结果盒["待确认"] = 库.待确认数()
                    结果盒["统计"] = 库.统计().摘要()
                    服务.关闭()
                    库.关闭()
                except Exception as 错:  # noqa: BLE001
                    结果盒["错误"] = str(错)

        线程 = 干活(self)
        self._刮削线程 = 线程
        定时 = QTimer(self)
        定时.setInterval(400)
        定时.timeout.connect(lambda: 进度框.setLabelText(
            str(结果盒.get("进度") or "正在扫描…")))
        定时.start()
        进度框.canceled.connect(lambda: self._写日志("[刮削] 用户取消（本批跑完会停）"))

        def 收尾():
            定时.stop()
            进度框.close()
            self.刷新()
            结果 = 结果盒.get("结果")
            待确认数 = int(结果盒.get("待确认") or 0)
            self._提示("🎞 刮削完成：" + (结果.摘要() if 结果 else
                                              str(结果盒.get("错误") or "无结果"))
                             + f"｜{结果盒.get('统计', '')}"
                             + (f"｜⚠ {待确认数} 条待确认" if 待确认数 else ""))
            if 完成后 is not None:
                完成后()
            elif 待确认数 and self.自动开队列:
                self.开待确认队列()
        线程.finished.connect(收尾)
        线程.start()

    # ------------------------------------------------------------------ 待确认

    def 开待确认队列(self, 起始行: int = 0) -> None:
        """打开待确认队列；采纳后**接着开**，直到队列清空或用户关窗。

        为什么采纳完要重开一次：队列的正确用法是"连点" —— 看候选、点采纳、下一条。
        若每次都让用户自己再点一次工具条，清 20 条要点 40 次。
        """
        if self.资料库 is None:
            return
        from ..scrape.服务 import 刮削服务, 刮削设置
        from .待确认 import 待确认对话框
        服务 = 刮削服务(self.资料库, self.建TMDB客户端(), self.图片缓存, 刮削设置())
        try:
            框 = 待确认对话框(self.资料库, 服务, self, 起始行)
            if 框.条数() == 0 and not self.资料库.待确认():
                self._提示("✅ 没有待确认的条目")
                return
            框.exec()
            决定 = 框.取决定()
        finally:
            服务.关闭()
        if 决定 is None:
            self._提示("✅ 待确认队列：稍后再处理")
            return
        if 决定.动作 == "采纳":
            self.刮削路径(决定.路径, 强制标识=决定.标识, 强制类型=决定.类型,
                       完成后=lambda: self.开待确认队列(决定.行号))
        elif 决定.动作 == "搜索":
            self._搜索并刮(决定)

    def _搜索并刮(self, 决定) -> None:
        from .手动匹配 import 手动匹配对话框
        对话 = 手动匹配对话框(self.建TMDB客户端(), 决定.标题 or "",
                        决定.年份, 决定.类型 or 媒体类型.电影, self)
        if not 对话.exec():
            return
        选择 = 对话.取选择()
        if 选择 is None or not 选择.标识:
            return
        self.刮削路径(决定.路径, 强制标识=选择.标识, 强制类型=选择.类型,
                   完成后=lambda: self.开待确认队列(决定.行号))

    # ------------------------------------------------------------------ 手动匹配 / 手动资料

    def 开手动匹配(self, 媒体id: int) -> None:
        from .手动匹配 import 手动匹配对话框
        from .手动资料 import 手动资料对话框
        条目 = self.资料库.取媒体(int(媒体id)) if self.资料库 is not None else None
        类型 = 条目.类型 if 条目 is not None else None
        对话 = 手动匹配对话框(self.建TMDB客户端(), (条目.标题 if 条目 else "") or "",
                        (条目.年份 if 条目 else None), 类型 or 媒体类型.电影, self)
        要手动 = {"要": False}
        对话.要手动填写.connect(lambda: 要手动.update(要=True))
        对话.exec()
        if 要手动["要"]:
            self.开手动资料(媒体id)
            return
        选择 = 对话.取选择()
        if 选择 is None or not 选择.标识:
            return
        文件 = self._找媒体主文件(媒体id)
        if not 文件:
            self._提示("🎞 这条资料没有对应的本地文件，改不了")
            return
        self._写日志(f"[刮削] 手动指定 {选择.可读()}")
        self.刮削路径(str(文件), 强制标识=选择.标识, 强制类型=选择.类型)

    def 开手动资料(self, 媒体id: int) -> None:
        """手动填写/修改资料（**不需要网络**）。"""
        from .手动资料 import 手动资料对话框
        if self.资料库 is None:
            return
        对话 = 手动资料对话框(self.资料库, self.图片缓存,
                        int(媒体id) if 媒体id else None, self)
        if 对话.exec() and 对话.取条目() is not None:
            新id = int(媒体id) if 媒体id else None
            self.刷新()
            self._提示("🎞 资料已保存（手动填写）"
                             + (f"，媒体 id {新id}" if 新id else ""))

    def _找媒体主文件(self, 媒体id: int) -> str:
        try:
            文件们 = self.资料库.文件们(int(媒体id))
            for 行 in 文件们:
                if 行["是主文件"]:
                    return str(行["路径"])
            return str(文件们[0]["路径"]) if 文件们 else ""
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------------ 收尾

    def 关闭(self) -> None:
        """停掉后台线程、关掉库（**幂等**）。"""
        if self._收尾过:
            return
        self._收尾过 = True
        try:
            if self.海报墙 is not None:
                self.海报墙.关闭()
        except Exception:  # noqa: BLE001
            pass
        try:
            线 = self._刮削线程
            if 线 is not None and 线.isRunning():
                线.wait(15000)          # 刮削可能正在下图，给它跑完（不然库是半截的）
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.资料库 is not None:
                self.资料库.关闭()
        except Exception:  # noqa: BLE001
            pass
