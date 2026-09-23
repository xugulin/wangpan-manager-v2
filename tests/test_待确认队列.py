"""待确认队列（库迁移 + 候选持久化 + 服务行为 + 界面）测试，**不联网**。

为什么这一屏必须有测试：
* 队列的正确性全靠"任务表里存了什么" —— 候选丢了，界面就只剩一个光秃秃的路径，
  用户还是没法决定；所以候选的**存取往返**要被钉住；
* "拿不准就不入库"是这一版的核心约定（旧行为会往海报墙塞一张文件名当标题的空卡，
  而且多条没有 TMDB 身份的条目会互相顶掉），必须有一个测试盯着；
* 老库是**已经装在用户机器上的**，迁移错了 = 资料库打不开。这里造一个"老结构"的库
  真跑一次迁移，检查数据（含子表）一行不少。
"""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from tests.公用 import 临时目录

from PySide6.QtWidgets import QApplication, QDialog

from wangpan.scrape.库 import 资料库, 查询条件
from wangpan.scrape.模型 import (图片, 图片类型, 媒体条目, 媒体类型, 匹配候选, 集, 季)
from wangpan.scrape.服务 import 刮削服务, 刮削设置

应用 = QApplication.instance() or QApplication([])


# ---------------- 假 TMDB（同名片子多，注定"拿不准"）----------------

class 含糊TMDB:
    """候选一堆、分数咬得很紧的假客户端 —— 专门用来制造"需要确认"。"""

    def __init__(self) -> None:
        self.取的详情: list[str] = []

    def 可用(self) -> bool:
        return True

    def 配置信息(self) -> dict:
        return {"images": {"secure_base_url": "https://image.tmdb.org/t/p/"}}

    def 图片地址(self, 远端路径: str, 尺寸: str = "w500") -> str:
        return f"https://image.tmdb.org/t/p/{尺寸}{远端路径}"

    def 搜电影(self, 标题: str, 年份=None) -> list[匹配候选]:
        return [匹配候选(来源标识="101", 标题=标题, 原名="Alpha", 年份=年份,
                       类型=媒体类型.电影, 热度=120, 海报远端="/a.jpg"),
                匹配候选(来源标识="202", 标题=标题 + " 2", 原名="Beta", 年份=年份,
                       类型=媒体类型.电影, 热度=118, 海报远端="/b.jpg"),
                匹配候选(来源标识="303", 标题=标题, 原名="Gamma", 年份=年份,
                       类型=媒体类型.电影, 热度=117, 海报远端="/c.jpg")]

    def 搜剧集(self, 标题: str, 年份=None) -> list[匹配候选]:
        return self.搜电影(标题, 年份)

    def 取电影(self, id: str) -> 媒体条目:
        self.取的详情.append(str(id))
        条目 = 媒体条目(类型=媒体类型.电影, 标题=f"取到的{id}", 原名="Picked",
                    年份=2024, 评分=7.5, 简介="人工确认后取回的详情")
        条目.外部ID = {"tmdb": str(id)}
        条目.海报 = 图片(图片类型.海报, f"/{id}.jpg", None, 500, 750)
        return 条目

    def 取剧集(self, id: str) -> 媒体条目:
        条目 = self.取电影(id)
        条目.类型 = 媒体类型.剧集
        条目.季们 = [季(季号=1, 集数=1, 集们=[集(季号=1, 集号=1, 标题="第一集")])]
        return 条目

    def 取季(self, 剧id: str, 季号: int) -> 季:
        return 季(季号=季号)

    def 取分级(self, 类型, id: str):
        return []

    def 取图片(self, 类型, id: str, 种类: str = ""):
        return []

    def 下载图片(self, 远端路径: str, 落点: Path, 尺寸: str = "w500"):
        return None

    def 关闭(self) -> None:
        return


# ---------------- 老库迁移 ----------------

#: 迁移前的老结构（**带** UNIQUE(来源, tmdb_id)，且没有 唯一键 列）
老结构 = """
CREATE TABLE 媒体 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    类型 TEXT NOT NULL DEFAULT 'unknown',
    标题 TEXT NOT NULL DEFAULT '',
    原名 TEXT NOT NULL DEFAULT '',
    年份 INTEGER,
    简介 TEXT NOT NULL DEFAULT '',
    时长分钟 INTEGER NOT NULL DEFAULT 0,
    评分 REAL NOT NULL DEFAULT 0,
    评分票数 INTEGER NOT NULL DEFAULT 0,
    影评人评分 REAL NOT NULL DEFAULT 0,
    状态 TEXT NOT NULL DEFAULT '',
    原始语言 TEXT NOT NULL DEFAULT '',
    来源 TEXT NOT NULL DEFAULT 'tmdb',
    tmdb_id TEXT NOT NULL DEFAULT '',
    imdb_id TEXT NOT NULL DEFAULT '',
    tvdb_id TEXT NOT NULL DEFAULT '',
    海报 TEXT NOT NULL DEFAULT '',
    背景 TEXT NOT NULL DEFAULT '',
    标志 TEXT NOT NULL DEFAULT '',
    总季数 INTEGER NOT NULL DEFAULT 0,
    总集数 INTEGER NOT NULL DEFAULT 0,
    制片公司 TEXT NOT NULL DEFAULT '',
    刮削时间 REAL NOT NULL DEFAULT 0,
    更新时间 REAL NOT NULL DEFAULT 0,
    UNIQUE(来源, tmdb_id)
);
CREATE TABLE 文件 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    媒体id INTEGER NOT NULL REFERENCES 媒体(id) ON DELETE CASCADE,
    路径 TEXT NOT NULL UNIQUE, 大小 INTEGER DEFAULT 0, 修改时间 REAL DEFAULT 0,
    版本 TEXT DEFAULT '', 段号 INTEGER DEFAULT 0, 是主文件 INTEGER DEFAULT 1
);
CREATE TABLE 季 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    媒体id INTEGER NOT NULL REFERENCES 媒体(id) ON DELETE CASCADE,
    季号 INTEGER NOT NULL, 标题 TEXT DEFAULT '', 简介 TEXT DEFAULT '',
    集数 INTEGER DEFAULT 0, 播出日期 TEXT DEFAULT '', 海报 TEXT DEFAULT '',
    UNIQUE(媒体id, 季号)
);
CREATE TABLE 刮削任务 (
    路径 TEXT PRIMARY KEY, 状态 TEXT NOT NULL DEFAULT '待处理', 媒体id INTEGER,
    尝试次数 INTEGER NOT NULL DEFAULT 0, 说明 TEXT NOT NULL DEFAULT '',
    更新时间 REAL NOT NULL DEFAULT 0
);
"""


class 库迁移测试(unittest.TestCase):
    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.库路径 = Path(self.临时.name) / "老库.db"
        self._造老库()

    def _造老库(self) -> None:
        连接 = sqlite3.connect(str(self.库路径))
        with 连接:
            连接.executescript(老结构)
            连接.execute("INSERT INTO 媒体 (id, 类型, 标题, tmdb_id, 评分) "
                       "VALUES (1,'movie','有身份的片子','438631',8.1)")
            连接.execute("INSERT INTO 媒体 (id, 类型, 标题, tmdb_id) "
                       "VALUES (2,'movie','没身份的片子','')")
            连接.execute("INSERT INTO 文件 (媒体id, 路径, 是主文件) "
                       "VALUES (1,'/老/有身份.mkv',1),(2,'/老/没身份.mkv',1)")
            连接.execute("INSERT INTO 季 (媒体id, 季号, 标题) VALUES (1,1,'第 1 季')")
            连接.execute("INSERT INTO 刮削任务 (路径, 状态, 说明) "
                       "VALUES ('/老/没身份.mkv','需要确认','老库里就有的待确认')")
        连接.close()

    def test_迁移后老数据一行不少(self):
        库 = 资料库(self.库路径)
        self.addCleanup(库.关闭)
        行们 = 库.列表(查询条件(条数=50))
        self.assertEqual(len(行们), 2, "两部老片都要在")
        一 = 库.取媒体(1)
        self.assertEqual(一.标题, "有身份的片子")
        self.assertEqual(一.外部ID.get("tmdb"), "438631")   # id 原样搬，子表指针不断
        self.assertEqual(len(库.文件们(1)), 1)
        季行 = 库.一个("SELECT 标题 FROM 季 WHERE 媒体id=1")
        self.assertEqual(季行["标题"], "第 1 季", "外键子表不能被重建带走")
        self.assertEqual(库.取任务("需要确认")[0]["说明"], "老库里就有的待确认")

    def test_迁移后不同身份的本地条目各占一行(self):
        """修的就是这个：以前第二条没有 tmdb id 的条目会**顶着**第一条。"""
        库 = 资料库(self.库路径)
        self.addCleanup(库.关闭)
        甲 = 媒体条目(类型=媒体类型.电影, 标题="本地甲", 年份=2001,
                   文件路径=Path("/本地/甲.mkv"))
        乙 = 媒体条目(类型=媒体类型.电影, 标题="本地乙", 年份=2002,
                   文件路径=Path("/本地/乙.mkv"))
        甲id, 乙id = 库.存条目(甲), 库.存条目(乙)
        self.assertNotEqual(甲id, 乙id, "两条没有 TMDB 身份的本地条目不能合成一行")
        self.assertEqual(库.取媒体(甲id).标题, "本地甲")
        self.assertEqual(库.取媒体(乙id).标题, "本地乙")
        # 同一个文件再存一次 → 还是同一行（不是每次都新建）
        self.assertEqual(库.存条目(甲), 甲id)

    def test_迁移后同一个TMDB身份仍然只有一行(self):
        库 = 资料库(self.库路径)
        self.addCleanup(库.关闭)
        一 = 媒体条目(类型=媒体类型.电影, 标题="沙丘", 外部ID={"tmdb": "438631"})
        二 = 媒体条目(类型=媒体类型.电影, 标题="沙丘（再次刮削）",
                   外部ID={"tmdb": "438631"})
        self.assertEqual(库.存条目(一), 库.存条目(二))
        self.assertEqual(库.取媒体(1).标题, "沙丘（再次刮削）")
        self.assertEqual(库.计数(查询条件()), 2, "老库 2 条，不该因为重刮多出来")

    def test_本地条目后来拿到身份会更新同一行(self):
        库 = 资料库(self.库路径)
        self.addCleanup(库.关闭)
        条目 = 媒体条目(类型=媒体类型.电影, 标题="先本地", 文件路径=Path("/x/片.mkv"))
        id1 = 库.存条目(条目)
        条目.外部ID["tmdb"] = "999"
        条目.标题 = "刮到真名了"
        self.assertEqual(库.存条目(条目), id1, "本地条目刮到身份后要更新原行")
        self.assertEqual(库.取媒体(id1).标题, "刮到真名了")
        self.assertEqual(库.取媒体(id1).外部ID.get("tmdb"), "999")


class 候选持久化测试(unittest.TestCase):
    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.库 = 资料库(Path(self.临时.name) / "库.db")
        self.addCleanup(self.库.关闭)

    def test_候选与本地信息能存能取(self):
        候选们 = [匹配候选(来源标识="101", 标题="甲", 年份=2020, 类型=媒体类型.电影,
                       分数=71.5, 理由="片名像、年份差 1"),
                匹配候选(来源标识="202", 标题="乙", 年份=2021, 类型=媒体类型.剧集,
                       分数=70.0)]
        self.库.记任务("/m/含糊.mkv", "需要确认", None, "分数太接近", 候选=候选们,
                    标题="含糊", 年份=2021, 类型=媒体类型.电影)
        项们 = self.库.待确认()
        self.assertEqual(len(项们), 1)
        项 = 项们[0]
        self.assertEqual(项.路径, "/m/含糊.mkv")
        self.assertEqual(项.标题, "含糊")
        self.assertEqual(项.年份, 2021)
        self.assertEqual(项.类型, 媒体类型.电影)
        self.assertEqual(len(项.候选们), 2)
        self.assertEqual(项.候选们[0].来源标识, "101")
        self.assertAlmostEqual(项.候选们[0].分数, 71.5, delta=0.01)
        self.assertEqual(项.候选们[1].类型, 媒体类型.剧集)
        self.assertEqual(项.最佳().来源标识, "101")
        self.assertIn("候选 2 个", 项.一句话())
        self.assertEqual(self.库.待确认数(), 1)

    def test_脏候选不炸界面(self):
        self.库.记任务("/m/坏.mkv", "需要确认", None, "手写坏数据",
                    候选=[匹配候选(来源标识="1", 标题="好")])
        self.库.全部("UPDATE 刮削任务 SET 候选='{不是 JSON' WHERE 路径='/m/坏.mkv'")
        项 = self.库.待确认()[0]
        self.assertEqual(项.候选们, [], "坏 JSON 要当成没有候选，而不是抛异常")

    def test_成功后候选被清掉(self):
        self.库.记任务("/m/甲.mkv", "需要确认", None, "拿不准",
                    候选=[匹配候选(来源标识="1")], 标题="甲")
        self.库.记任务("/m/甲.mkv", "成功", 7, "入库了")
        行 = self.库.取任务("成功")[0]
        self.assertEqual(行["候选"], "", "已经入库的条目不该留着过期候选")
        self.assertEqual(self.库.待确认数(), 0)

    def test_跳过与删除(self):
        self.库.记任务("/m/甲.mkv", "需要确认", None, "拿不准")
        self.assertEqual(self.库.待确认数(), 1)
        self.库.记任务("/m/甲.mkv", "跳过", None, "人工跳过")
        self.assertEqual(self.库.待确认数(), 0)
        self.assertEqual(self.库.统计().跳过, 1)
        self.assertIn("跳过 1", self.库.统计().摘要())
        self.assertEqual(self.库.删任务("/m/甲.mkv"), 1)
        self.assertEqual(self.库.删任务("/m/甲.mkv"), 0)
        self.assertEqual(self.库.取任务("跳过"), [])


# ---------------- 服务行为（拿不准就不入库）----------------

class 服务队列测试(unittest.TestCase):
    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)
        self.媒体 = self.根 / "媒体"
        self.媒体.mkdir(parents=True, exist_ok=True)
        self.文件 = self.媒体 / "含糊的名字 2021.mkv"
        self.文件.write_bytes(b"x" * (2 * 1024 * 1024))
        self.库 = 资料库(self.根 / "库.db")
        self.addCleanup(self.库.关闭)
        self.客户端 = 含糊TMDB()
        self.日志: list[str] = []
        self.服务 = 刮削服务(self.库, self.客户端, None,
                       刮削设置(最小文件字节=1024, 下载图片=False),
                       日志回调=self.日志.append)
        self.addCleanup(self.服务.关闭)

    def test_拿不准只进队列不进资料库(self):
        结果 = self.服务.刮路径(self.文件)
        self.assertEqual(结果.需要确认, 1, f"应该拿不准：{self.日志}")
        self.assertEqual(self.库.计数(查询条件()), 0,
                     "没有 TMDB 身份的条目不该出现在海报墙（会是一张空卡）")
        self.assertEqual(self.库.待确认数(), 1)
        项 = self.库.待确认()[0]
        self.assertEqual(项.路径, str(self.文件))
        self.assertGreaterEqual(len(项.候选们), 2, "候选要一起存下来给界面用")
        self.assertTrue(项.说明, "要写清为什么没敢自动定")
        self.assertEqual(self.库.取任务("需要确认")[0]["媒体id"], None,
                         "队列条目不指向任何媒体行")

    def test_采纳候选后入库并挂上文件(self):
        服务 = 刮削服务(self.库, self.客户端, None,
                    刮削设置(最小文件字节=1024, 下载图片=False))
        self.addCleanup(服务.关闭)
        服务.刮路径(self.文件)
        项 = self.库.待确认()[0]
        结果 = 服务.采纳候选(项.路径, 项.候选们[1])
        self.assertEqual(结果.失败, 0, f"采纳不该失败：{结果.错误们}")
        self.assertEqual(结果.成功, 1)
        self.assertEqual(self.客户端.取的详情[-1], "202", "要按人选的那个 id 取详情")
        self.assertEqual(self.库.待确认数(), 0, "确认过的条目要离开队列")
        行们 = self.库.列表(查询条件())
        self.assertEqual(len(行们), 1)
        媒体id = int(行们[0]["id"])
        self.assertEqual(self.库.取媒体(媒体id).外部ID.get("tmdb"), "202")
        路径们 = [x["路径"] for x in self.库.文件们(媒体id)]
        self.assertIn(str(self.文件), 路径们, "本地文件要挂上去")
        self.assertEqual([r["状态"] for r in self.库.取任务()], ["成功"])

    def test_忽略后不再出现在队列(self):
        self.服务.刮路径(self.文件)
        self.assertEqual(self.库.待确认数(), 1)
        self.服务.忽略待确认(self.文件, "人工跳过")
        self.assertEqual(self.库.待确认数(), 0)
        self.assertEqual(self.库.取任务("跳过")[0]["路径"], str(self.文件))
        # 续跑只挑"待处理/失败"，跳过的不会被自动拾回来
        self.assertEqual(self.服务.续跑().需要确认, 0)
        self.assertEqual(self.库.统计().跳过, 1)

    def test_重搜会刷新候选并留在队列(self):
        服务 = 刮削服务(self.库, 含糊TMDB(), None,
                    刮削设置(最小文件字节=1024, 下载图片=False))
        self.addCleanup(服务.关闭)
        服务.刮路径(self.文件)
        旧候选数 = len(self.库.待确认()[0].候选们)
        服务.重搜待确认(self.文件)
        self.assertEqual(len(self.库.待确认()[0].候选们), 旧候选数)
        self.assertEqual(self.库.待确认数(), 1, "重搜后仍在队列里等人点")


# ---------------- 界面 ----------------

class 待确认界面测试(unittest.TestCase):
    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)
        self.媒体 = self.根 / "媒体"
        self.媒体.mkdir(parents=True, exist_ok=True)
        self.文件们 = []
        for 序号 in (1, 2, 3):
            路径 = self.媒体 / f"含糊 {序号}.mkv"
            路径.write_bytes(b"x" * (2 * 1024 * 1024))
            self.文件们.append(路径)
        self.库 = 资料库(self.根 / "库.db")
        self.addCleanup(self.库.关闭)
        self.客户端 = 含糊TMDB()
        self.服务 = 刮削服务(self.库, self.客户端, None,
                       刮削设置(最小文件字节=1024, 下载图片=False))
        self.addCleanup(self.服务.关闭)
        for 路径 in self.文件们:
            self.服务.刮路径(路径)

    def _框(self, 起始行: int = 0):
        from wangpan.ui.待确认 import 待确认对话框
        框 = 待确认对话框(self.库, self.服务, None, 起始行)
        self.addCleanup(框.deleteLater)
        return 框

    def test_列队列与候选(self):
        框 = self._框()
        self.assertEqual(框.条数(), 3)
        self.assertEqual(框.列表.count(), 3)
        self.assertIn("候选", 框.列表.item(0).text())
        self.assertEqual(框.候选表.rowCount(), 3, "三条候选都要列出来")
        float(框.候选表.item(0, 0).text())          # 分数列要是数字
        self.assertTrue(框.候选表.item(0, 1).text(), "候选标题不能是空的")
        self.assertTrue(框.采纳按钮.isEnabled())
        self.assertIn("共 3 条待确认", 框.汇总标签.text())
        self.assertIn(框.当前项().路径, 框.路径标签.text())

    def test_采纳返回决定(self):
        框 = self._框()
        框.候选表.setCurrentCell(1, 0)          # 选第二个候选
        当前 = 框.当前项().路径
        框.采纳()
        self.assertEqual(框.result(), QDialog.DialogCode.Accepted)
        决定 = 框.取决定()
        self.assertIsNotNone(决定)
        self.assertEqual(决定.动作, "采纳")
        self.assertEqual(决定.标识, "202")
        self.assertEqual(决定.类型, 媒体类型.电影)
        self.assertEqual(决定.路径, 框.当前项().路径)

    def test_忽略当前会跳下一条(self):
        框 = self._框()
        第一 = 框.当前项().路径
        self.assertTrue(框.忽略当前())
        self.assertEqual(框.条数(), 2)
        self.assertEqual(self.库.待确认数(), 2)
        self.assertEqual(self.库.取任务("跳过")[0]["路径"], 第一)
        self.assertNotEqual(框.当前项().路径, 第一, "忽略完要自动跳到下一条")

    def test_忽略全部(self):
        框 = self._框()
        self.assertEqual(框.忽略全部(确认=False), 3)
        self.assertEqual(框.条数(), 0)
        self.assertEqual(self.库.待确认数(), 0)
        self.assertIn("没有待确认", 框.汇总标签.text())
        self.assertFalse(框.采纳按钮.isEnabled())
        self.assertFalse(框.忽略按钮.isEnabled())

    def test_队列空了不崩(self):
        框 = self._框()
        框.忽略全部(确认=False)
        框.采纳()                     # 没有当前项：应当安静地什么都不做
        框.要搜索()
        self.assertIsNone(框.取决定())
        self.assertIsNone(框.当前项())

    def test_换个名字再搜返回搜索决定(self):
        框 = self._框()
        框.要搜索()
        决定 = 框.取决定()
        self.assertEqual(决定.动作, "搜索")
        self.assertEqual(决定.路径, 框.当前项().路径)
        self.assertTrue(决定.标题, "搜索决定要带上标题（手动匹配的初始关键词）")


class 海报墙按钮测试(unittest.TestCase):
    """工具条上的「✅ 待确认 N」要跟着队列长度走（不然用户不知道有活要干）。"""

    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.根 = Path(self.临时.name)
        self.库 = 资料库(self.根 / "库.db")
        self.addCleanup(self.库.关闭)
        from wangpan.scrape.图片 import 图片缓存
        self.缓存 = 图片缓存(self.根 / "图")

    def _墙(self):
        from wangpan.ui.海报墙页 import 海报墙页
        墙 = 海报墙页(self.库, self.缓存)
        self.addCleanup(墙.关闭)
        return 墙

    def test_按钮显示队列条数并能发信号(self):
        墙 = self._墙()
        self.assertEqual(墙.待确认数, 0)
        self.assertEqual(墙.待确认按钮.text(), "✅ 待确认")
        点击 = []
        墙.要处理待确认.connect(lambda: 点击.append(True))
        self.库.记任务("/m/含糊.mkv", "需要确认", None, "拿不准",
                    候选=[匹配候选(来源标识="1", 标题="甲")], 标题="含糊")
        墙.刷新()
        self.assertEqual(墙.待确认数, 1)
        self.assertIn("1", 墙.待确认按钮.text())
        self.assertIn("1 条刮削拿不准", 墙.待确认按钮.toolTip())
        墙.待确认按钮.click()
        self.assertEqual(点击, [True], "点按钮要通知主窗口去开队列")

    def test_队列清空后按钮回到零(self):
        墙 = self._墙()
        self.库.记任务("/m/含糊.mkv", "需要确认", None, "拿不准")
        墙.刷新()
        self.assertEqual(墙.待确认数, 1)
        self.库.记任务("/m/含糊.mkv", "跳过", None, "人工跳过")
        墙.刷新()
        self.assertEqual(墙.待确认数, 0)
        self.assertEqual(墙.待确认按钮.text(), "✅ 待确认")


if __name__ == "__main__":
    unittest.main()
