"""TMDB 客户端走**真 HTTP** 的测试（环回口上的假 TMDB，不碰外网）。

为什么单测里也要有真 HTTP 这一层
================================
`test_tmdb客户端.py` 用的是"注入假传输"，覆盖的是**解析与缓存逻辑**；
而下面这些环节是注入式测试**碰不到**的：
* `urllib` 真的发出去、状态码真的被判、`Content-Length`/连接复用真的没问题；
* 鉴权到底带没带（假 TMDB 没收到 Bearer 就回 401 —— 这是可验证的事实）；
* 图片 CDN 的基地址来自 `/configuration`，再拼 `尺寸 + 远端路径` 真的能下到图。

真实 TMDB 的 API 域名在部分网络下到不了（见 docs/研究），
所以"这条链路随时能验"必须有离线手段 —— 这就是 tests/假TMDB服务.py 的用途。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.公用 import 临时目录
from tests.假TMDB服务 import 默认候选, 假代理服务, 假TMDB服务

from PySide6.QtWidgets import QApplication

from wangpan.scrape.图片 import 图片缓存
from wangpan.scrape.库 import 资料库
from wangpan.scrape.模型 import 媒体类型
from wangpan.scrape.服务 import 刮削服务, 刮削设置
from wangpan.scrape.tmdb import (TMDB客户端, TMDB配置, _清理密钥, 请求失败)


def _清过(配置: TMDB配置) -> tuple[str, str]:
    """用配置加载时同一套清理逻辑（配置对象本身不做清理，读文件时才清）。"""
    return _清理密钥(配置.token), _清理密钥(配置.api_key)

应用 = QApplication.instance() or QApplication([])


class 真HTTP测试(unittest.TestCase):
    """客户端 ↔ HTTP 服务端（真 socket、真 JSON、真鉴权）。"""

    @classmethod
    def setUpClass(cls):
        cls.临时 = 临时目录()
        cls.根 = Path(cls.临时.name)
        cls.服务 = 假TMDB服务(cls.根 / "图床")
        cls.服务.造图()
        cls.地址 = cls.服务.启动()

    @classmethod
    def tearDownClass(cls):
        cls.服务.停止()
        cls.临时.cleanup()

    def _客户端(self, **覆盖):
        配置 = TMDB配置(token="TESTTOKEN-should-never-leak", language="zh-CN",
                     缓存目录=self.根 / f"tmdb缓存_{self.id().split('.')[-1]}",
                     接口基地址=self.服务.接口基地址, 超时秒=5.0)
        for 键, 值 in 覆盖.items():
            setattr(配置, 键, 值)
        return TMDB客户端(配置)

    # ---------------- 基础链路 ----------------

    def test_配置信息与图片基地址来自真实响应(self):
        客户端 = self._客户端()
        self.assertEqual(客户端.配置信息()["images"]["secure_base_url"],
                        f"{self.服务.地址}/t/p/")
        self.assertTrue(客户端.图片基地址().endswith("/t/p/"))
        self.assertEqual(客户端.图片地址("/p101.jpg", "w500"),
                        f"{self.服务.地址}/t/p/w500/p101.jpg")

    def test_搜索解析(self):
        客户端 = self._客户端()
        候选们 = 客户端.搜电影("沙丘", 2021)
        self.assertEqual(len(候选们), len(默认候选))
        self.assertEqual(候选们[0].来源标识, "101")
        self.assertEqual(候选们[0].标题, "沙丘")
        self.assertEqual(候选们[0].年份, 2021)
        self.assertEqual(候选们[0].类型, 媒体类型.电影)
        self.assertEqual(候选们[0].海报远端, "/p101.jpg")
        剧候选 = 客户端.搜剧集("沙丘")
        self.assertEqual(剧候选[0].类型, 媒体类型.剧集)
        self.assertTrue(any("search/tv" in x for x in self.服务.记录))

    def test_电影详情_分级_演职员_图片(self):
        客户端 = self._客户端()
        条目 = 客户端.取电影("101")
        self.assertEqual(条目.标题, "沙丘")
        self.assertEqual(条目.外部ID["tmdb"], "101")
        self.assertEqual(条目.外部ID["imdb"], "tt0000101")
        self.assertEqual(条目.时长分钟, 155)
        self.assertIn("科幻", 条目.标签)
        self.assertTrue(条目.海报 is not None and 条目.海报.远端路径)
        self.assertTrue(条目.背景 is not None)
        分级文本 = [g.可读() for g in 条目.分级们]
        self.assertIn("US:PG-13", 分级文本)
        self.assertIn("HK:IIA", 分级文本)
        self.assertEqual(条目.导演()[0].人物.名字, "丹尼斯·维伦纽瓦")
        self.assertGreaterEqual(len(条目.演员()), 2)
        # 取分级/取图片复用详情缓存：不该多打请求
        之前 = self.服务.请求数
        客户端.取分级(媒体类型.电影, "101")
        客户端.取图片(媒体类型.电影, "101", "poster")
        self.assertEqual(self.服务.请求数, 之前, "详情已在缓存里，不该再请求")

    def test_剧集与季(self):
        客户端 = self._客户端()
        剧 = 客户端.取剧集("202")
        self.assertEqual(剧.类型, 媒体类型.剧集)
        self.assertEqual(剧.外部ID.get("tvdb"), "tv202")
        self.assertEqual(len(剧.季们), 1)
        self.assertEqual(剧.季们[0].季号, 1)
        季 = 客户端.取季("202", 1)
        self.assertEqual(len(季.集们), 3)
        self.assertEqual(季.集们[0].标题, "第 1 集")

    def test_没凭据就被服务端拒(self):
        客户端 = self._客户端(token="", api_key="")
        self.assertFalse(客户端.可用())
        with self.assertRaises(请求失败) as 上下文:
            客户端.搜电影("沙丘")
        self.assertEqual(上下文.exception.状态码, 401)

    def test_错误信息里不出现令牌(self):
        客户端 = self._客户端(token="SECRET-abc-123")
        # 404 端点照样要脱敏（真机上错误信息会被打进日志/状态栏）
        with self.assertRaises(Exception) as 上下文:
            客户端._调用("/不存在的端点", {})
        self.assertNotIn("SECRET-abc-123", str(上下文.exception))

    def test_中文凭据给的是人话而不是latin1报错(self):
        """真机踩点：从网页复制 token 常带上中文引号/全角空格，底层那句
        "'latin-1' codec can't encode characters" 没人看得懂。"""
        客户端 = self._客户端(token="真实令牌")
        self.assertIn("非 ASCII", 客户端.配置.密钥问题())
        with self.assertRaises(请求失败) as 上下文:
            客户端.搜电影("沙丘")
        文本 = str(上下文.exception)
        self.assertIn("非 ASCII", 文本)
        self.assertNotIn("latin-1", 文本)
        # 从配置里读的时候顺手把中文引号/全角空格清掉（清完就正常了）
        配置 = TMDB配置(token="\u201cabc123\u201d", api_key="\u3000key456\u3000")
        配置.token, 配置.api_key = _清过(配置)
        self.assertEqual((配置.token, 配置.api_key), ("abc123", "key456"))

    def test_拼图URL不额外发请求(self):
        """设计取舍：拼 URL 绝不为了拿基地址多发一次请求（没有就直接用官方默认）。"""
        客户端 = self._客户端()
        之前 = self.服务.请求数
        self.assertEqual(客户端.图片地址("/p101.jpg", "w500"),
                        "https://image.tmdb.org/t/p/w500/p101.jpg")
        self.assertEqual(self.服务.请求数, 之前, "拼 URL 不该产生请求")

    def test_下载图片到本地(self):
        客户端 = self._客户端()
        客户端.配置信息()                     # 真机流程就是这样：先取基地址，再下图
        落点 = self.根 / "下的图.jpg"
        结果 = 客户端.下载图片("/p101.jpg", 落点, "w500")
        self.assertIsNotNone(结果, "图片 CDN 链路要能真下到文件")
        self.assertTrue(落点.is_file() and 落点.stat().st_size > 0)
        from PySide6.QtGui import QImage
        self.assertFalse(QImage(str(落点)).isNull(), "下来的必须是能解码的图")


class 端到端队列测试(unittest.TestCase):
    """扫目录 → 拿不准进队列 → 人工采纳 → 入库下图（全程真 HTTP）。"""

    @classmethod
    def setUpClass(cls):
        cls.临时 = 临时目录()
        cls.根 = Path(cls.临时.name)
        cls.服务 = 假TMDB服务(cls.根 / "图床")
        cls.服务.造图()
        cls.服务.启动()
        cls.媒体 = cls.根 / "媒体"
        (cls.媒体 / "未知片名 (2021)").mkdir(parents=True, exist_ok=True)
        # 这个名字**故意谁都对不上**（"未知片名"）：最佳候选只有 40 多分 → 进待确认队列。
        # 注意别用"沙丘 (2021)"：那种"片名+年份都对得上"的会直接采纳
        #（见 匹配打分.自动决策 的"精确一致"例外，那是真机测真 API 之后加的规则）。
        (cls.媒体 / "未知片名 (2021)" / "未知片名 (2021) 1080p.mkv").write_bytes(
            b"x" * (2 * 1024 * 1024))

    @classmethod
    def tearDownClass(cls):
        cls.服务.停止()
        cls.临时.cleanup()

    def _造(self):
        库 = 资料库(self.根 / f"库_{self.id().split('.')[-1]}.db")
        self.addCleanup(库.关闭)
        缓存 = 图片缓存(self.根 / f"图缓存_{self.id().split('.')[-1]}")
        配置 = TMDB配置(token="REALHTTP-token", 缓存目录=self.根 / "tmdb缓存",
                     接口基地址=self.服务.接口基地址, 超时秒=5.0)
        客户端 = TMDB客户端(配置)
        设置 = 刮削设置(最小文件字节=1024, 本批上限=5, 抓季集详情=False)
        服务 = 刮削服务(库, 客户端, 缓存, 设置)
        self.addCleanup(服务.关闭)
        return 库, 缓存, 客户端, 服务

    def test_片名年份都对得上就直接采纳(self):
        """真机规则：候选里有"片名一模一样 + 年份对得上"的那一部 → 不用问人。"""
        媒体 = self.根 / "直接采纳"
        (媒体 / "沙丘 (2021)").mkdir(parents=True, exist_ok=True)
        (媒体 / "沙丘 (2021)" / "沙丘 (2021) 2160p.mkv").write_bytes(
            b"y" * (2 * 1024 * 1024))
        库, 缓存, 客户端, 服务 = self._造()
        服务.扫库([媒体])
        结果 = 服务.续跑()
        self.assertEqual(结果.成功, 1, f"该直接采纳：{结果.摘要()}")
        self.assertEqual(结果.需要确认, 0)
        self.assertEqual(库.待确认数(), 0)
        行 = 库.列表()[0]
        self.assertEqual(库.取媒体(int(行["id"])).外部ID["tmdb"], "101")

    def test_含糊条目进队列再采纳(self):
        库, 缓存, 客户端, 服务 = self._造()
        服务.扫库([self.媒体])
        结果 = 服务.续跑()
        self.assertEqual(结果.需要确认, 1, f"三部同名片应该拿不准：{结果.摘要()}")
        # 用户真机两次反馈"扫了媒体库什么都不显示"，所以改成：**底稿先进海报墙**，
        # 同时仍然进待确认队列（两者都要）。刮到之后会用同一条记录补全。
        self.assertEqual(库.计数(), 1, "扫描到的条目要立刻出现在海报墙上（用户要求）")
        项 = 库.待确认()[0]
        self.assertEqual(库.待确认数(), 1, "该进确认队列的还是要进队列")
        self.assertGreaterEqual(len(项.候选们), 2)
        # 人选"沙丘 2021"（202 号）
        选中 = next(c for c in 项.候选们 if c.来源标识 == "202")
        结果2 = 服务.采纳候选(项.路径, 选中)
        self.assertEqual(结果2.失败, 0, f"采纳不该失败：{结果2.错误们}")
        self.assertEqual(库.待确认数(), 0)
        行们 = 库.列表()
        self.assertEqual(len(行们), 1)
        条目 = 库.取媒体(int(行们[0]["id"]))
        self.assertEqual(条目.外部ID["tmdb"], "202")
        self.assertEqual(条目.标题, "未知片名",
                         "本地文件名解析出的标题优先（不该被在线覆盖）")
        self.assertEqual(条目.原名, 默认候选[1]["original_title"],
                         "取回来的必须是选中那一部的详情（202）")
        self.assertEqual(条目.时长分钟, 155)
        # 海报/背景是真的从"CDN"（同一个服务器的 /t/p/）下下来的
        self.assertIsNotNone(条目.海报)
        self.assertTrue(条目.海报.可用(), f"海报该落到本地：{条目.海报.本地路径}")
        self.assertTrue(条目.背景.可用())
        self.assertTrue(条目.演员()[0].人物.头像.可用(), "演员头像也要下下来")
        self.assertTrue(any(库.文件们(int(行们[0]["id"]))))
        self.assertIn("成功", [r["状态"] for r in 库.取任务()])


if __name__ == "__main__":
    unittest.main()


class 代理测试(unittest.TestCase):
    """代理链路（SOCKS5 握手 + CONNECT 隧道）——用环回假代理真跑一遍。

    真机背景：这台机器直连到不了 api.themoviedb.org（DNS 被解析到无关地址），
    而本机 v2rayN 的 SOCKS5 口是通的。所以"能不能走代理"直接决定这个模块在
    真实网络下可用不可用；而单测里**不能依赖**用户机器上真有个代理，
    于是 `tests/假TMDB服务.假代理服务` 顶替它（真握手、真转发）。
    """

    @classmethod
    def setUpClass(cls):
        cls.临时 = 临时目录()
        cls.根 = Path(cls.临时.name)
        cls.服务 = 假TMDB服务(cls.根 / "图床")
        cls.服务.造图()
        cls.服务.启动()
        cls.SOCKS = 假代理服务("socks5")
        cls.SOCKS.启动()
        cls.HTTP代理 = 假代理服务("http")
        cls.HTTP代理.启动()

    @classmethod
    def tearDownClass(cls):
        for 服务 in (cls.SOCKS, cls.HTTP代理, cls.服务):
            服务.停止()
        cls.临时.cleanup()

    def _客户端(self, 代理: str):
        配置 = TMDB配置(token="PROXY-token-1", 缓存目录=self.根 / f"代理缓存_{代理[:6]}",
                     接口基地址=self.服务.接口基地址, 超时秒=5.0, 代理=代理)
        return TMDB客户端(配置)

    def test_socks5代理能取到数据(self):
        客户端 = self._客户端(f"socks5://127.0.0.1:{self.SOCKS.端口}")
        候选们 = 客户端.搜电影("沙丘", 2021)
        self.assertEqual(候选们[0].来源标识, "101")
        self.assertTrue(self.SOCKS.记录, "假 SOCKS5 应该收到一次 CONNECT")
        主机, 端口 = self.SOCKS.记录[-1]
        self.assertEqual((主机, 端口), ("127.0.0.1", self.服务.端口))

    def test_http代理的CONNECT隧道也能用(self):
        客户端 = self._客户端(f"http://127.0.0.1:{self.HTTP代理.端口}")
        条目 = 客户端.取电影("202")
        self.assertEqual(条目.外部ID.get("tmdb"), "202")
        self.assertTrue(self.HTTP代理.记录)

    def test_代理挂了给的是人话(self):
        客户端 = self._客户端("socks5://127.0.0.1:9")      # 没人听的端口
        with self.assertRaises(请求失败) as 上下文:
            客户端.搜电影("沙丘")
        文本 = str(上下文.exception)
        self.assertIn("代理", 文本)
        self.assertNotIn("PROXY-token-1", 文本)

    def test_代理配置解析(self):
        from wangpan.scrape.代理 import 代理错误, 解析代理
        self.assertIsNone(解析代理(""))
        self.assertEqual(解析代理("socks5://127.0.0.1:10808").可读(),
                        "socks5://127.0.0.1:10808")
        self.assertEqual(解析代理("127.0.0.1:1080").类型, "socks5")   # 只给 host:port
        self.assertTrue(解析代理("socks5h://a:1").代理解析域名)
        带认证 = 解析代理("socks5://user:p%40ss@127.0.0.1:1080")
        self.assertEqual((带认证.用户名, 带认证.密码), ("user", "p@ss"))
        self.assertNotIn("p@ss", 带认证.可读(), "可读形式不能回显密码")
        with self.assertRaises(代理错误):
            解析代理("ftp://127.0.0.1:21")

    def test_配置摘要会报代理但不含密码(self):
        客户端 = self._客户端("socks5://user:secret@127.0.0.1:1080")
        摘要 = str(客户端.配置摘要())
        self.assertIn("socks5://127.0.0.1:1080", 摘要)
        self.assertIn("带认证：user", 摘要)
        self.assertNotIn("secret", 摘要)
