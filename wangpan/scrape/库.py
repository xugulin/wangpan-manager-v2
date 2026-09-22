"""刮削资料库（SQLite，从零设计）。

为什么用 SQLite 而不是 JSON：
* 海报墙要**分页 + 排序 + 过滤**（几万条也要秒开），JSON 每次全量读写在真实库上撑不住；
* 刮削是**可中断的批处理**（扫到一半关掉、下次接着扫），要有"任务表"记状态；
* 资料之间是多对多（作品↔人物、作品↔标签），关系表比嵌套 JSON 好维护。

设计要点
========
* **文件与资料分开**：``文件`` 表记真实路径（一部电影可以有 1080p/4K 多个版本），
  ``媒体`` 表记"这部作品"，两者用 ``媒体id`` 关联 —— 删除文件不该删资料；
* **图片只记路径**：图片本体在 :mod:`~wangpan.scrape.图片` 的缓存目录里，
  库里存"本地路径 + 远端路径"，换缓存目录不用迁移数据库；
* **进度单独一张表**（按文件路径），与播放器解耦：海报墙的"继续观看"直接查它；
* 所有写操作**一个事务**，失败回滚；开 WAL 让"边刮边看"不互相阻塞。
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .模型 import (人物, 人物工种, 图片, 图片类型, 媒体条目, 媒体类型, 参演, 分级,
                季, 集)

__all__ = ["资料库", "库统计", "查询条件"]

表结构 = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS 媒体 (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    类型          TEXT NOT NULL DEFAULT 'unknown',
    标题          TEXT NOT NULL DEFAULT '',
    原名          TEXT NOT NULL DEFAULT '',
    年份          INTEGER,
    简介          TEXT NOT NULL DEFAULT '',
    时长分钟      INTEGER NOT NULL DEFAULT 0,
    评分          REAL NOT NULL DEFAULT 0,       -- 0–10
    评分票数      INTEGER NOT NULL DEFAULT 0,
    影评人评分    REAL NOT NULL DEFAULT 0,       -- 0–100（量纲与评分不同！）
    状态          TEXT NOT NULL DEFAULT '',
    原始语言      TEXT NOT NULL DEFAULT '',
    来源          TEXT NOT NULL DEFAULT 'tmdb',
    tmdb_id       TEXT NOT NULL DEFAULT '',
    imdb_id       TEXT NOT NULL DEFAULT '',
    tvdb_id       TEXT NOT NULL DEFAULT '',
    海报          TEXT NOT NULL DEFAULT '',
    背景          TEXT NOT NULL DEFAULT '',
    标志          TEXT NOT NULL DEFAULT '',
    总季数        INTEGER NOT NULL DEFAULT 0,
    总集数        INTEGER NOT NULL DEFAULT 0,
    制片公司      TEXT NOT NULL DEFAULT '',
    刮削时间      REAL NOT NULL DEFAULT 0,
    更新时间      REAL NOT NULL DEFAULT 0,
    UNIQUE(来源, tmdb_id)
);
CREATE INDEX IF NOT EXISTS 媒体_标题 ON 媒体(标题);
CREATE INDEX IF NOT EXISTS 媒体_年份 ON 媒体(年份);
CREATE INDEX IF NOT EXISTS 媒体_类型 ON 媒体(类型);
CREATE INDEX IF NOT EXISTS 媒体_评分 ON 媒体(评分);
CREATE INDEX IF NOT EXISTS 媒体_刮削时间 ON 媒体(刮削时间);

CREATE TABLE IF NOT EXISTS 文件 (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    媒体id  INTEGER NOT NULL REFERENCES 媒体(id) ON DELETE CASCADE,
    路径    TEXT NOT NULL UNIQUE,
    大小    INTEGER NOT NULL DEFAULT 0,
    修改时间 REAL NOT NULL DEFAULT 0,
    版本    TEXT NOT NULL DEFAULT '',
    段号    INTEGER NOT NULL DEFAULT 0,
    是主文件 INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS 文件_媒体 ON 文件(媒体id);

CREATE TABLE IF NOT EXISTS 季 (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    媒体id  INTEGER NOT NULL REFERENCES 媒体(id) ON DELETE CASCADE,
    季号    INTEGER NOT NULL,
    标题    TEXT NOT NULL DEFAULT '',
    简介    TEXT NOT NULL DEFAULT '',
    集数    INTEGER NOT NULL DEFAULT 0,
    播出日期 TEXT NOT NULL DEFAULT '',
    海报    TEXT NOT NULL DEFAULT '',
    UNIQUE(媒体id, 季号)
);

CREATE TABLE IF NOT EXISTS 集 (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    媒体id  INTEGER NOT NULL REFERENCES 媒体(id) ON DELETE CASCADE,
    季号    INTEGER NOT NULL,
    集号    INTEGER NOT NULL,
    集号到  INTEGER NOT NULL DEFAULT 0,
    标题    TEXT NOT NULL DEFAULT '',
    简介    TEXT NOT NULL DEFAULT '',
    播出日期 TEXT NOT NULL DEFAULT '',
    时长分钟 INTEGER NOT NULL DEFAULT 0,
    评分    REAL NOT NULL DEFAULT 0,
    剧照    TEXT NOT NULL DEFAULT '',
    文件路径 TEXT NOT NULL DEFAULT '',
    外部ID  TEXT NOT NULL DEFAULT '',
    UNIQUE(媒体id, 季号, 集号)
);
CREATE INDEX IF NOT EXISTS 集_文件 ON 集(文件路径);

CREATE TABLE IF NOT EXISTS 人物 (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    名字    TEXT NOT NULL,
    原名    TEXT NOT NULL DEFAULT '',
    头像    TEXT NOT NULL DEFAULT '',
    来源    TEXT NOT NULL DEFAULT 'tmdb',
    tmdb_id TEXT NOT NULL DEFAULT '',
    UNIQUE(来源, tmdb_id, 名字)
);

CREATE TABLE IF NOT EXISTS 媒体人物 (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    媒体id  INTEGER NOT NULL REFERENCES 媒体(id) ON DELETE CASCADE,
    人物id  INTEGER NOT NULL REFERENCES 人物(id) ON DELETE CASCADE,
    工种    TEXT NOT NULL DEFAULT 'actor',
    角色    TEXT NOT NULL DEFAULT '',
    排序    INTEGER NOT NULL DEFAULT 999,
    集数    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(媒体id, 人物id, 工种, 角色)
);
CREATE INDEX IF NOT EXISTS 媒体人物_媒体 ON 媒体人物(媒体id);

CREATE TABLE IF NOT EXISTS 图片 (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    媒体id  INTEGER NOT NULL REFERENCES 媒体(id) ON DELETE CASCADE,
    类型    TEXT NOT NULL,
    远端路径 TEXT NOT NULL DEFAULT '',
    本地路径 TEXT NOT NULL DEFAULT '',
    宽      INTEGER NOT NULL DEFAULT 0,
    高      INTEGER NOT NULL DEFAULT 0,
    语言    TEXT NOT NULL DEFAULT '',
    评分    REAL NOT NULL DEFAULT 0,
    票数    INTEGER NOT NULL DEFAULT 0,
    来源    TEXT NOT NULL DEFAULT 'tmdb'
);
CREATE INDEX IF NOT EXISTS 图片_媒体 ON 图片(媒体id, 类型);

CREATE TABLE IF NOT EXISTS 分级 (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    媒体id  INTEGER NOT NULL REFERENCES 媒体(id) ON DELETE CASCADE,
    国家    TEXT NOT NULL DEFAULT '',
    值      TEXT NOT NULL DEFAULT '',
    来源    TEXT NOT NULL DEFAULT 'tmdb',
    UNIQUE(媒体id, 国家, 来源)
);

CREATE TABLE IF NOT EXISTS 标签 (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    媒体id  INTEGER NOT NULL REFERENCES 媒体(id) ON DELETE CASCADE,
    名称    TEXT NOT NULL,
    UNIQUE(媒体id, 名称)
);

CREATE TABLE IF NOT EXISTS 播放进度 (
    路径     TEXT PRIMARY KEY,
    媒体id   INTEGER,
    位置秒   REAL NOT NULL DEFAULT 0,
    时长秒   REAL NOT NULL DEFAULT 0,
    看完     INTEGER NOT NULL DEFAULT 0,
    更新时间 REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS 刮削任务 (
    路径     TEXT PRIMARY KEY,
    状态     TEXT NOT NULL DEFAULT '待处理',
    媒体id   INTEGER,
    尝试次数 INTEGER NOT NULL DEFAULT 0,
    说明     TEXT NOT NULL DEFAULT '',
    更新时间 REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS 刮削任务_状态 ON 刮削任务(状态);
"""


@dataclass
class 库统计:
    媒体数: int = 0
    电影数: int = 0
    剧集数: int = 0
    季数: int = 0
    集数: int = 0
    人物数: int = 0
    图片数: int = 0
    文件数: int = 0
    待处理: int = 0
    需要确认: int = 0
    失败: int = 0

    def 摘要(self) -> str:
        return (f"作品 {self.媒体数}（电影 {self.电影数}｜剧集 {self.剧集数}）"
                f"｜季 {self.季数}｜集 {self.集数}｜人物 {self.人物数}"
                f"｜图片 {self.图片数}｜文件 {self.文件数}"
                f"｜待处理 {self.待处理}｜待确认 {self.需要确认}｜失败 {self.失败}")


@dataclass
class 查询条件:
    """海报墙的分页/排序/过滤条件。"""

    关键词: str = ""
    类型: Optional[媒体类型] = None
    年份从: int = 0
    年份到: int = 0
    最低评分: float = 0.0
    标签: str = ""
    排序: str = "更新时间"
    倒序: bool = True
    偏移: int = 0
    条数: int = 60

    def 排序子句(self) -> str:
        列 = {"标题": "标题", "年份": "年份", "评分": "评分",
             "更新时间": "更新时间", "刮削时间": "刮削时间"}.get(self.排序, "更新时间")
        方向 = "DESC" if self.倒序 else "ASC"
        if 列 == "标题":
            return f"ORDER BY 标题 COLLATE NOCASE {方向}, id"
        return f"ORDER BY {列} {方向}, id"


class 资料库:
    """SQLite 资料库（同一个连接多线程用；sqlite3 自带串行化，写操作很短）。"""

    def __init__(self, 路径: Optional[Path] = None) -> None:
        if 路径 is None:
            路径 = Path(__file__).resolve().parents[2] / "数据" / "资料库.db"
        self.路径 = Path(路径)
        self.路径.parent.mkdir(parents=True, exist_ok=True)
        self._连接 = sqlite3.connect(str(self.路径), timeout=15.0,
                                 check_same_thread=False)
        self._连接.row_factory = sqlite3.Row
        self._连接.executescript(表结构)
        self._连接.commit()

    # ---------------- 基础设施 ----------------

    def 关闭(self) -> None:
        try:
            self._连接.commit()
        except Exception:  # noqa: BLE001
            pass
        self._连接.close()

    def 全部(self, sql: str, 参数: Sequence = ()) -> list[sqlite3.Row]:
        return list(self._连接.execute(sql, 参数))

    def 一个(self, sql: str, 参数: Sequence = ()) -> Optional[sqlite3.Row]:
        return self._连接.execute(sql, 参数).fetchone()

    # ---------------- 写入 ----------------

    def 存条目(self, 条目: 媒体条目) -> int:
        """把一个 :class:`媒体条目` 落库（存在就更新，含季/集/人物/图片/分级/标签）。"""
        现在 = time.time()
        主键 = 条目.外部ID.get("tmdb") or ""
        行 = self.一个("SELECT id FROM 媒体 WHERE 来源=? AND tmdb_id=?", (条目.来源, 主键))
        with self._连接:
            if 行:
                媒体id = int(行["id"])
                self._更新媒体(媒体id, 条目, 现在)
            else:
                游标 = self._连接.execute(
                    "INSERT INTO 媒体 (类型, 来源, tmdb_id, 更新时间, 刮削时间) "
                    "VALUES (?,?,?,?,?)",
                    (条目.类型.value, 条目.来源, 主键, 现在, 现在))
                媒体id = int(游标.lastrowid or 0)
                self._更新媒体(媒体id, 条目, 现在)
            self._写季集(媒体id, 条目)
            self._写人物(媒体id, 条目)
            self._写图片(媒体id, 条目)
            self._写分级与标签(媒体id, 条目)
            self._写文件(媒体id, 条目)
        return 媒体id

    def _更新媒体(self, 媒体id: int, 条目: 媒体条目, 现在: float) -> None:
        self._连接.execute(
            """UPDATE 媒体 SET 类型=?, 标题=?, 原名=?, 年份=?, 简介=?, 时长分钟=?,
               评分=?, 评分票数=?, 影评人评分=?, 状态=?, 原始语言=?,
               imdb_id=?, tvdb_id=?, 海报=?, 背景=?, 标志=?,
               总季数=?, 总集数=?, 制片公司=?, 更新时间=?, 刮削时间=?
               WHERE id=?""",
            (条目.类型.value, 条目.标题, 条目.原名, 条目.年份, 条目.简介,
             条目.时长分钟, 条目.评分, 条目.评分票数, 条目.影评人评分, 条目.状态,
             条目.原始语言, 条目.外部ID.get("imdb", ""), 条目.外部ID.get("tvdb", ""),
             self._图片相对(条目.海报), self._图片相对(条目.背景),
             self._图片相对(条目.标志), len(条目.季们), 条目.汇总集数(),
             json.dumps(条目.制片公司, ensure_ascii=False), 现在, 现在, 媒体id))

    @staticmethod
    def _图片相对(图: Optional[图片]) -> str:
        if 图 is None or 图.本地路径 is None:
            return ""
        return str(图.本地路径)

    def _写季集(self, 媒体id: int, 条目: 媒体条目) -> None:
        for 季对象 in 条目.季们:
            self._连接.execute(
                """INSERT INTO 季 (媒体id, 季号, 标题, 简介, 集数, 播出日期, 海报)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(媒体id, 季号) DO UPDATE SET
                     标题=excluded.标题, 简介=excluded.简介, 集数=excluded.集数,
                     播出日期=excluded.播出日期, 海报=excluded.海报""",
                (媒体id, 季对象.季号, 季对象.标题, 季对象.简介, len(季对象.集们),
                 季对象.播出日期, self._图片相对(季对象.海报)))
            for 集对象 in 季对象.集们:
                self._连接.execute(
                    """INSERT INTO 集 (媒体id, 季号, 集号, 集号到, 标题, 简介, 播出日期,
                        时长分钟, 评分, 剧照, 文件路径, 外部ID)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(媒体id, 季号, 集号) DO UPDATE SET
                         集号到=excluded.集号到, 标题=excluded.标题, 简介=excluded.简介,
                         播出日期=excluded.播出日期, 时长分钟=excluded.时长分钟,
                         评分=excluded.评分, 剧照=excluded.剧照,
                         文件路径=CASE WHEN excluded.文件路径<>'' THEN excluded.文件路径
                                     ELSE 集.文件路径 END,
                         外部ID=excluded.外部ID""",
                    (媒体id, 季对象.季号, 集对象.集号, 集对象.集号到, 集对象.标题,
                     集对象.简介, 集对象.播出日期, 集对象.时长分钟, 集对象.评分,
                     self._图片相对(集对象.剧照), str(集对象.文件路径 or ""),
                     集对象.外部ID))

    def _写人物(self, 媒体id: int, 条目: 媒体条目) -> None:
        self._连接.execute("DELETE FROM 媒体人物 WHERE 媒体id=?", (媒体id,))
        for 关系 in 条目.参演们:
            人 = 关系.人物
            tmdb_id = 人.标识 or ""
            行 = (self.一个("SELECT id FROM 人物 WHERE 来源=? AND tmdb_id=?",
                          (人.来源, tmdb_id)) if tmdb_id else None)
            if 行:
                人物id = int(行["id"])
                self._连接.execute("UPDATE 人物 SET 名字=?, 原名=?, 头像=? WHERE id=?",
                                (人.名字, 人.原名, self._图片相对(人.头像), 人物id))
            else:
                游标 = self._连接.execute(
                    "INSERT INTO 人物 (名字, 原名, 头像, 来源, tmdb_id) VALUES (?,?,?,?,?)",
                    (人.名字, 人.原名, self._图片相对(人.头像), 人.来源, tmdb_id))
                人物id = int(游标.lastrowid or 0)
            self._连接.execute(
                """INSERT OR REPLACE INTO 媒体人物 (媒体id, 人物id, 工种, 角色, 排序, 集数)
                   VALUES (?,?,?,?,?,?)""",
                (媒体id, 人物id, 关系.工种.value, 关系.角色, 关系.排序, 关系.集数))

    def _写图片(self, 媒体id: int, 条目: 媒体条目) -> None:
        self._连接.execute("DELETE FROM 图片 WHERE 媒体id=?", (媒体id,))
        全部: list[图片] = list(条目.图片们)
        for 主图, 类型 in ((条目.海报, 图片类型.海报), (条目.背景, 图片类型.背景),
                        (条目.标志, 图片类型.标志)):
            if 主图 is not None and 主图 not in 全部:
                全部.append(主图)
        for 图 in 全部:
            if not 图.远端路径 and not 图.本地路径:
                continue
            self._连接.execute(
                """INSERT INTO 图片 (媒体id, 类型, 远端路径, 本地路径, 宽, 高,
                    语言, 评分, 票数, 来源) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (媒体id, 图.类型.value, 图.远端路径, self._图片相对(图), 图.宽, 图.高,
                 图.语言, 图.评分, 图.票数, 图.来源))

    def _写分级与标签(self, 媒体id: int, 条目: 媒体条目) -> None:
        self._连接.execute("DELETE FROM 分级 WHERE 媒体id=?", (媒体id,))
        for 项 in 条目.分级们:
            if not 项.值:
                continue
            self._连接.execute(
                "INSERT OR REPLACE INTO 分级 (媒体id, 国家, 值, 来源) VALUES (?,?,?,?)",
                (媒体id, 项.国家, 项.值, 项.来源))
        self._连接.execute("DELETE FROM 标签 WHERE 媒体id=?", (媒体id,))
        for 名 in 条目.标签:
            if 名:
                self._连接.execute("INSERT OR IGNORE INTO 标签 (媒体id, 名称) VALUES (?,?)",
                                (媒体id, 名))

    def _写文件(self, 媒体id: int, 条目: 媒体条目) -> None:
        路径们: list[tuple[Path, str, int, int]] = []
        if 条目.文件路径 is not None:
            路径们.append((条目.文件路径, "", 0, 1))
        for 季对象 in 条目.季们:
            for 集对象 in 季对象.集们:
                if 集对象.文件路径 is not None:
                    路径们.append((集对象.文件路径, "", 0, 0))
        for 路径, 版本, 段号, 主文件 in 路径们:
            文本 = str(路径)
            try:
                状态 = 路径.stat()
                大小, 改动 = 状态.st_size, 状态.st_mtime
            except OSError:
                大小, 改动 = 0, 0.0
            self._连接.execute(
                """INSERT INTO 文件 (媒体id, 路径, 大小, 修改时间, 版本, 段号, 是主文件)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(路径) DO UPDATE SET
                     媒体id=excluded.媒体id, 大小=excluded.大小,
                     修改时间=excluded.修改时间, 版本=excluded.版本,
                     段号=excluded.段号, 是主文件=excluded.是主文件""",
                (媒体id, 文本, 大小, 改动, 版本, 段号, 主文件))

    # ---------------- 查询（给海报墙） ----------------

    def 列表(self, 条件: Optional[查询条件] = None) -> list[sqlite3.Row]:
        条件 = 条件 or 查询条件()
        子句, 参数 = self._条件转SQL(条件)
        sql = (f"SELECT id, 类型, 标题, 原名, 年份, 评分, 影评人评分, 海报, 背景, "
               f"总季数, 总集数, 时长分钟, 简介 FROM 媒体 {子句} {条件.排序子句()} "
               f"LIMIT ? OFFSET ?")
        参数 = list(参数) + [max(1, int(条件.条数)), max(0, int(条件.偏移))]
        return self.全部(sql, 参数)

    def 计数(self, 条件: Optional[查询条件] = None) -> int:
        条件 = 条件 or 查询条件()
        子句, 参数 = self._条件转SQL(条件)
        行 = self.一个(f"SELECT COUNT(*) AS n FROM 媒体 {子句}", 参数)
        return int(行["n"]) if 行 else 0

    def _条件转SQL(self, 条件: 查询条件) -> tuple[str, list]:
        条件们: list[str] = []
        参数: list = []
        if 条件.关键词:
            条件们.append("(标题 LIKE ? OR 原名 LIKE ?)")
            词 = f"%{条件.关键词}%"
            参数 += [词, 词]
        if 条件.类型 is not None:
            条件们.append("类型 = ?")
            参数.append(条件.类型.value)
        if 条件.年份从:
            条件们.append("年份 >= ?")
            参数.append(int(条件.年份从))
        if 条件.年份到:
            条件们.append("年份 <= ?")
            参数.append(int(条件.年份到))
        if 条件.最低评分:
            条件们.append("评分 >= ?")
            参数.append(float(条件.最低评分))
        if 条件.标签:
            条件们.append("id IN (SELECT 媒体id FROM 标签 WHERE 名称 = ?)")
            参数.append(条件.标签)
        return (("WHERE " + " AND ".join(条件们)) if 条件们 else ""), 参数

    def 取媒体(self, 媒体id: int) -> Optional[媒体条目]:
        """按 id 取完整资料（含季/集/人物/图片/分级/标签）。"""
        行 = self.一个("SELECT * FROM 媒体 WHERE id=?", (媒体id,))
        if not 行:
            return None
        try:
            类型 = 媒体类型(行["类型"])
        except ValueError:
            类型 = 媒体类型.未知
        条目 = 媒体条目(
            类型=类型, 标题=行["标题"], 原名=行["原名"], 年份=行["年份"],
            简介=行["简介"], 时长分钟=行["时长分钟"], 评分=行["评分"],
            评分票数=行["评分票数"], 影评人评分=行["影评人评分"], 状态=行["状态"],
            原始语言=行["原始语言"], 来源=行["来源"])
        条目.外部ID = {"tmdb": 行["tmdb_id"], "imdb": 行["imdb_id"],
                    "tvdb": 行["tvdb_id"]}
        条目.制片公司 = json.loads(行["制片公司"] or "[]")
        条目.海报 = self._图从行(行["海报"], 图片类型.海报)
        条目.背景 = self._图从行(行["背景"], 图片类型.背景)
        条目.标志 = self._图从行(行["标志"], 图片类型.标志)
        for 季行 in self.全部("SELECT * FROM 季 WHERE 媒体id=? ORDER BY 季号", (媒体id,)):
            季对象 = 季(季号=季行["季号"], 标题=季行["标题"], 简介=季行["简介"],
                      集数=季行["集数"], 播出日期=季行["播出日期"],
                      海报=self._图从行(季行["海报"], 图片类型.海报))
            for 集行 in self.全部(
                    "SELECT * FROM 集 WHERE 媒体id=? AND 季号=? ORDER BY 集号",
                    (媒体id, 季行["季号"])):
                季对象.集们.append(集(
                    季号=集行["季号"], 集号=集行["集号"], 集号到=集行["集号到"],
                    标题=集行["标题"], 简介=集行["简介"], 播出日期=集行["播出日期"],
                    时长分钟=集行["时长分钟"], 评分=集行["评分"],
                    剧照=self._图从行(集行["剧照"], 图片类型.剧照),
                    文件路径=Path(集行["文件路径"]) if 集行["文件路径"] else None,
                    外部ID=集行["外部ID"]))
            条目.季们.append(季对象)
        for 关系行 in self.全部(
                """SELECT m.*, p.名字 AS 人名字, p.原名 AS 人原名, p.头像 AS 人头像,
                          p.tmdb_id AS 人tmdb, p.来源 AS 人来源
                   FROM 媒体人物 m JOIN 人物 p ON p.id = m.人物id
                   WHERE m.媒体id=? ORDER BY m.排序""", (媒体id,)):
            人 = 人物(标识=关系行["人tmdb"], 名字=关系行["人名字"],
                    原名=关系行["人原名"],
                    头像=self._图从行(关系行["人头像"], 图片类型.人物),
                    来源=关系行["人来源"])
            try:
                工种 = 人物工种(关系行["工种"])
            except ValueError:
                工种 = 人物工种.其它
            条目.参演们.append(参演(人物=人, 工种=工种, 角色=关系行["角色"],
                                排序=关系行["排序"], 集数=关系行["集数"]))
        for 图行 in self.全部("SELECT * FROM 图片 WHERE 媒体id=?", (媒体id,)):
            try:
                类型 = 图片类型(图行["类型"])
            except ValueError:
                continue
            条目.图片们.append(图片(
                类型=类型, 远端路径=图行["远端路径"],
                本地路径=Path(图行["本地路径"]) if 图行["本地路径"] else None,
                宽=图行["宽"], 高=图行["高"], 语言=图行["语言"], 评分=图行["评分"],
                票数=图行["票数"], 来源=图行["来源"]))
        for 级行 in self.全部("SELECT * FROM 分级 WHERE 媒体id=?", (媒体id,)):
            条目.分级们.append(分级(国家=级行["国家"], 值=级行["值"],
                                来源=级行["来源"]))
        条目.标签 = [行["名称"] for 行 in
                  self.全部("SELECT 名称 FROM 标签 WHERE 媒体id=?", (媒体id,))]
        return 条目

    @staticmethod
    def _图从行(相对: str, 类型: 图片类型) -> Optional[图片]:
        if not 相对:
            return None
        return 图片(类型=类型, 本地路径=Path(相对))

    def 按路径找媒体(self, 路径: str | Path) -> Optional[int]:
        文本 = str(路径)
        行 = self.一个("SELECT 媒体id FROM 文件 WHERE 路径=?", (文本,))
        if 行:
            return int(行["媒体id"])
        行 = self.一个("SELECT 媒体id FROM 集 WHERE 文件路径=?", (文本,))
        return int(行["媒体id"]) if 行 else None

    def 文件们(self, 媒体id: int) -> list[sqlite3.Row]:
        return self.全部("SELECT * FROM 文件 WHERE 媒体id=? ORDER BY 是主文件 DESC, id",
                       (媒体id,))

    def 删除媒体(self, 媒体id: int) -> bool:
        with self._连接:
            self._连接.execute("DELETE FROM 媒体 WHERE id=?", (媒体id,))
        return True

    def 清空(self) -> None:
        with self._连接:
            for 表 in ("媒体", "文件", "季", "集", "人物", "媒体人物", "图片",
                      "分级", "标签", "播放进度", "刮削任务"):
                self._连接.execute(f"DELETE FROM {表}")

    # ---------------- 播放进度 ----------------

    def 记进度(self, 路径: str | Path, 位置秒: float, 时长秒: float = 0.0,
             媒体id: Optional[int] = None, 看完: bool = False) -> None:
        with self._连接:
            self._连接.execute(
                """INSERT INTO 播放进度 (路径, 媒体id, 位置秒, 时长秒, 看完, 更新时间)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(路径) DO UPDATE SET
                     位置秒=excluded.位置秒,
                     时长秒=CASE WHEN excluded.时长秒>0 THEN excluded.时长秒
                              ELSE 播放进度.时长秒 END,
                     看完=excluded.看完, 更新时间=excluded.更新时间,
                     媒体id=COALESCE(excluded.媒体id, 播放进度.媒体id)""",
                (str(路径), 媒体id, float(位置秒), float(时长秒),
                 1 if 看完 else 0, time.time()))

    def 取进度(self, 路径: str | Path) -> Optional[sqlite3.Row]:
        return self.一个("SELECT * FROM 播放进度 WHERE 路径=?", (str(路径),))

    def 继续观看(self, 条数: int = 20) -> list[sqlite3.Row]:
        """海报墙"继续观看"：有进度、没看完、离结尾还有一段的，按最近看排序。"""
        return self.全部(
            """SELECT p.*, m.标题, m.海报, m.类型, m.年份, m.评分
               FROM 播放进度 p LEFT JOIN 媒体 m ON m.id = p.媒体id
               WHERE p.看完 = 0 AND p.位置秒 > 5
                 AND (p.时长秒 = 0 OR p.位置秒 < p.时长秒 - 10)
               ORDER BY p.更新时间 DESC LIMIT ?""", (max(1, int(条数)),))

    # ---------------- 刮削任务 ----------------

    def 记任务(self, 路径: str | Path, 状态: str, 媒体id: Optional[int] = None,
             说明: str = "") -> None:
        with self._连接:
            self._连接.execute(
                """INSERT INTO 刮削任务 (路径, 状态, 媒体id, 尝试次数, 说明, 更新时间)
                   VALUES (?,?,?,0,?,?)
                   ON CONFLICT(路径) DO UPDATE SET
                     状态=excluded.状态,
                     媒体id=COALESCE(excluded.媒体id, 刮削任务.媒体id),
                     说明=excluded.说明, 更新时间=excluded.更新时间,
                     尝试次数=刮削任务.尝试次数
                        + CASE WHEN excluded.状态='失败' THEN 1 ELSE 0 END""",
                (str(路径), 状态, 媒体id, 说明, time.time()))

    def 取任务(self, 状态: str = "", 条数: int = 200) -> list[sqlite3.Row]:
        if 状态:
            return self.全部(
                "SELECT * FROM 刮削任务 WHERE 状态=? ORDER BY 更新时间 DESC LIMIT ?",
                (状态, 条数))
        return self.全部("SELECT * FROM 刮削任务 ORDER BY 更新时间 DESC LIMIT ?", (条数,))

    # ---------------- 统计 ----------------

    def 统计(self) -> 库统计:
        def 数(sql: str) -> int:
            行 = self.一个(sql)
            return int(行[0]) if 行 else 0
        return 库统计(
            媒体数=数("SELECT COUNT(*) FROM 媒体"),
            电影数=数("SELECT COUNT(*) FROM 媒体 WHERE 类型='movie'"),
            剧集数=数("SELECT COUNT(*) FROM 媒体 WHERE 类型='tv'"),
            季数=数("SELECT COUNT(*) FROM 季"),
            集数=数("SELECT COUNT(*) FROM 集"),
            人物数=数("SELECT COUNT(*) FROM 人物"),
            图片数=数("SELECT COUNT(*) FROM 图片"),
            文件数=数("SELECT COUNT(*) FROM 文件"),
            待处理=数("SELECT COUNT(*) FROM 刮削任务 WHERE 状态='待处理'"),
            需要确认=数("SELECT COUNT(*) FROM 刮削任务 WHERE 状态='需要确认'"),
            失败=数("SELECT COUNT(*) FROM 刮削任务 WHERE 状态='失败'"))
