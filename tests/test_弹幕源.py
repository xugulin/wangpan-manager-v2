"""弹幕源测试：弹弹play 协议（签名/哈希/解析/缓存/并发/匹配）+ 本地文件（XML/JSON）。

怎么做到**不联网**
==================
:class:`弹弹play源` 的 HTTP 出口只有一个可注入的 ``传输`` 参数，这里的
:class:`假传输` 把它换掉：请求被记下来、响应按预置数据编出来。所有需要"网络"的
断言（URL 里的查询串、请求头、请求体、调用次数）都在假的请求记录上做 ——
真实接口一次都不打。

测什么、为什么这么测
====================
* 签名：**自己按官方公式手算一遍**再比对（不调被测函数），否则测试只是复述实现；
  还要钉住"Path 不含查询串"（这是最容易写错、且报错信息只有 401 的地方）；
* 前 16MB 哈希：拿 20MB 的文件验证"改尾部不变、改头部变"—— 直接钉住"只读前 16MB"；
* ``p`` 解析：把官方规则里"该丢的"逐条造出来（字段不足、时间坏、颜色坏、模式 2/3/6/7）；
* 缓存/并发：用**调用次数**说话（缓存命中 1 次、TTL 过期 2 次、8 线程并发仍 1 次）。
"""

from __future__ import annotations

import json
import threading
import urllib.parse
import time
import unittest
from base64 import b64encode
from hashlib import md5, sha256
from pathlib import Path

from tests.公用 import 临时目录

from wangpan.danmaku.模型 import 弹幕, 弹幕池, 弹幕模式, 来源标识
from wangpan.danmaku.源 import (
    本地源, 前16MB的MD5, 建默认源, 弹弹play源, 弹幕源错误, 找同名弹幕文件, 检查业务状态,
    生成签名头, 签名原文, 算签名, 鉴权错误说明, 素材信息, 应答, 导出为JSON, 官方基地址,
    哈希字节数, 解析B站XML, 解析响应, 解析条目, 解析我们的JSON, 读同名字幕弹幕, 读凭证,
    请求, 纯接口路径)

弹弹play日志名 = "wangpan.danmaku.源.弹弹play"


# ---------------------------------------------------------------------------
# 假的 HTTP 传输
# ---------------------------------------------------------------------------

class 假传输:
    """记录每一次请求，按预置数据回答（可以被多线程同时调用，所以加锁）。"""

    def __init__(self, 回答=None) -> None:
        self.请求们: list[请求] = []
        self.回答 = 回答
        self.锁 = threading.Lock()

    def __call__(self, 请: 请求) -> 应答:
        with self.锁:
            self.请求们.append(请)
            序号 = len(self.请求们)
        if isinstance(self.回答, 应答):
            return self.回答
        if callable(self.回答):
            体 = self.回答(请, 序号)
        else:
            体 = self.回答
        if isinstance(体, 应答):
            return 体
        return 应答(200, json.dumps(体, ensure_ascii=False))

    # ---- 断言辅助 ----
    @property
    def 次数(self) -> int:
        return len(self.请求们)

    def 最后一个网址(self) -> str:
        return self.请求们[-1].地址

    def 最后一个体(self) -> dict:
        return json.loads((self.请求们[-1].体 or b"{}").decode("utf-8"))


def 弹幕响应(*文本们) -> dict:
    """造一份 ``/api/v2/comment`` 形状的响应。"""
    return {"count": len(文本们),
            "comments": [{"cid": 1000 + 序, "p": f"{序 + 1}.50,1,16777215,用户{序}",
                          "m": 文本} for 序, 文本 in enumerate(文本们)]}


def 匹配响应(匹配们, isMatched: bool = True) -> dict:
    return {"success": True, "errorCode": 0, "errorMessage": "",
            "isMatched": isMatched, "matches": 匹配们}


def 造源(假: 假传输, 目录: Path, **改动) -> 弹弹play源:
    """造一个"带凭证、不读环境、缓存落在临时目录、不节流"的源（测试的默认姿势）。"""
    参数 = dict(app_id="测试AppId", app_secret="测试密钥", 传输=假, 读环境=False,
               配置路径=Path(目录) / "不存在的弹幕源.json", 缓存目录=Path(目录) / "缓存",
               最小请求间隔秒=0.0)
    参数.update(改动)
    return 弹弹play源(**参数)


# ---------------------------------------------------------------------------
# 签名
# ---------------------------------------------------------------------------

class 签名测试(unittest.TestCase):
    def test_签名原文就是四段直接拼接(self):
        """``AppId + Timestamp + Path + AppSecret``：**没有分隔符**，时间戳是十进制文本。

        为什么要单独钉这一条：官方文档只给了公式，拼接细节（有没有冒号、时间戳是不是
        补零、路径带不带查询串）任何一处写错都会得到 ``Invalid Signature``，
        而报错信息完全一样。这里用固定时间戳 + 假密钥把**原文**和**散列值**都钉死。
        """
        self.assertEqual(签名原文("app-id-123", 1700000000, "/api/v2/match", "secret-456"),
                         "app-id-1231700000000/api/v2/matchsecret-456")
        # 时间戳当 int 传、当字符串传，结果必须一致（不能拼出 "1700000000" 之外的东西）
        self.assertEqual(签名原文("id", "1700000000", "/x", "s"),  # type: ignore[arg-type]
                         "id1700000000/xs")

    def test_算签名是标准base64的sha256(self):
        """自己手算一遍 sha256 + base64 比对（不调被测函数），钉住"标准 base64"。"""
        原文 = "id1700000000/api/v2/match秘钥"
        期望 = b64encode(sha256(原文.encode("utf-8")).digest()).decode("ascii")
        得到 = 算签名("id", 1700000000, "/api/v2/match", "秘钥")
        self.assertEqual(得到, 期望)
        self.assertTrue(得到.endswith("="), "sha256 是 32 字节，base64 必然以 = 结尾")
        self.assertNotIn("-", 得到)
        self.assertNotIn("_", 得到)

    def test_中文密钥按utf8编码(self):
        """密钥是用户手输的，理论上可能是非 ASCII；官方示例统一 UTF-8，别用 latin-1。"""
        期望 = b64encode(sha256("id1700000000/路径密钥".encode("utf-8")).digest()).decode()
        self.assertEqual(算签名("id", 1700000000, "/路径", "密钥"), 期望)

    def test_签名就是官方那个公式(self):
        """``base64(sha256(AppId + Timestamp + Path + AppSecret))`` —— 手算一遍比对。"""
        时刻 = 1700000000
        头 = 生成签名头("app-id-123", "secret-456", "/api/v2/match", 时刻)
        原文 = f"app-id-123{时刻}/api/v2/matchsecret-456"
        期望 = b64encode(sha256(原文.encode("utf-8")).digest()).decode("ascii")
        self.assertEqual(头["X-Signature"], 期望)
        self.assertEqual(头["X-AppId"], "app-id-123")
        self.assertEqual(头["X-Timestamp"], "1700000000")

    def test_签名路径不含查询串(self):
        """``/api/v2/comment/123?withRelated=true`` 参与签名的只有 ``/api/v2/comment/123``。"""
        带查询 = 生成签名头("id", "秘", "/api/v2/comment/123?withRelated=true", 1700000000)
        不带查询 = 生成签名头("id", "秘", "/api/v2/comment/123", 1700000000)
        self.assertEqual(带查询["X-Signature"], 不带查询["X-Signature"])
        self.assertEqual(纯接口路径("/api/v2/comment/123?withRelated=true&chConvert=0"),
                         "/api/v2/comment/123")

    def test_签名路径去掉域名只留路径(self):
        """传整个 URL 进来也要算对（只取 path 部分）。"""
        整个 = 生成签名头("id", "秘", "https://api.dandanplay.net/api/v2/match", 1700000000)
        只有路径 = 生成签名头("id", "秘", "/api/v2/match", 1700000000)
        self.assertEqual(整个["X-Signature"], 只有路径["X-Signature"])
        self.assertEqual(纯接口路径("api/v2/match"), "/api/v2/match")

    def test_签名头里没有密钥(self):
        头 = 生成签名头("id", "超级机密的密钥", "/api/v2/match", 1700000000)
        self.assertEqual(sorted(头), ["X-AppId", "X-Signature", "X-Timestamp"])
        self.assertNotIn("超级机密的密钥", json.dumps(头, ensure_ascii=False))

    def test_不传时间戳就用当前时间(self):
        前 = int(time.time())
        头 = 生成签名头("id", "秘", "/api/v2/match")
        self.assertGreaterEqual(int(头["X-Timestamp"]), 前)

    def test_凭证从环境变量与配置文件读(self):
        with 临时目录() as 目录:
            配置 = Path(目录) / "弹幕源.json"
            配置.write_text(json.dumps({"dandanplay": {"appId": "文件里的ID",
                                                    "appSecret": "文件里的密钥"}},
                                     ensure_ascii=False), encoding="utf-8")
            self.assertEqual(读凭证(配置, 环境={})[:2], ("文件里的ID", "文件里的密钥"))
            # 环境变量优先（哪怕配置文件里有）
            标识, 密钥, 来源 = 读凭证(配置, 环境={"V2_DANDANPLAY_APP_ID": "环境ID",
                                             "V2_DANDANPLAY_APP_SECRET": "环境密钥"})
            self.assertEqual((标识, 密钥), ("环境ID", "环境密钥"))
            self.assertIn("环境变量", 来源)
            self.assertEqual(读凭证(Path(目录) / "没有.json", 环境={})[:2], ("", ""))

    def test_坏配置文件不崩(self):
        with 临时目录() as 目录:
            配置 = Path(目录) / "弹幕源.json"
            配置.write_text("{这不是 JSON", encoding="utf-8")
            with self.assertLogs("wangpan.danmaku.源.弹弹play", level="WARNING"):
                self.assertEqual(读凭证(配置, 环境={})[:2], ("", ""))


class 鉴权错误说明测试(unittest.TestCase):
    """服务端 ``X-Error-Message`` 是**分类**，每类修法不同，必须翻对。

    官方错误表（``https://doc.dandanplay.com/open/`` §6）：``Missing Authentication
    Headers`` 缺头、``Invalid Timestamp`` 时钟、``Invalid Signature`` 签名不匹配、
    ``Invalid AppId`` **签名模式下 = AppId 或 AppSecret 无效**、``Invalid AppSecret``
    凭证模式专用。翻错的后果是让人去改错的地方（真机踩过：只知道 403，不知道去核对密钥）。
    """

    def test_每种官方错误的说明都指对了地方(self):
        例 = {
            "Missing Authentication Headers": ("鉴权", "appId/appSecret"),
            "Invalid Timestamp": ("时间", "Unix 秒"),
            "Invalid AppId": ("AppSecret", "审核"),
            "Invalid Signature": ("sha256", "查询串"),
            "Invalid AppSecret": ("轮换", "appSecret"),
        }
        for 原文, (关键词甲, 关键词乙) in 例.items():
            说明 = 鉴权错误说明(原文, 有凭证=True)
            self.assertIn(关键词甲, 说明, 原文)
            self.assertIn(关键词乙, 说明, 原文)

    def test_服务端原文必须原样带上(self):
        """不能把官方原文改写掉 —— 以后排查、以及给官方提工单都要靠它。"""
        for 原文 in ("Invalid AppId", "Missing Authentication Headers"):
            self.assertIn(f"服务端原文：{原文}", 鉴权错误说明(原文))

    def test_没配凭证时说清楚是没配(self):
        说明 = 鉴权错误说明("Invalid AppId", 有凭证=False)
        self.assertIn("没有", 说明)
        self.assertIn("Invalid AppId", 说明)

    def test_不认识的错误原样返回(self):
        """没见过的值不加戏（宁可少说，也别编一个错的修法）。"""
        self.assertEqual(鉴权错误说明("Something New From Server"), "Something New From Server")
        self.assertEqual(鉴权错误说明(""), "")

    def test_说明里绝不出现密钥(self):
        """secret 只可能出现在"原文"里，而原文来自服务端响应头，不含我们的密钥。"""
        说明 = 鉴权错误说明("Invalid AppId")
        self.assertNotIn("AppSecret=", 说明)


# ---------------------------------------------------------------------------
# 前 16MB 的 MD5
# ---------------------------------------------------------------------------

class 哈希测试(unittest.TestCase):
    #: 每块 1MB，头部 16 块 = 正好 16MB
    块 = 1024 * 1024

    @classmethod
    def setUpClass(cls) -> None:
        cls._临时 = 临时目录()
        cls.目录 = Path(cls._临时.__enter__())
        #: 20MB：前 16MB 全是 0x11，后 4MB 全是 0x22
        cls.大文件 = cls.目录 / "20MB.bin"
        with open(cls.大文件, "wb") as 文件:
            文件.write(bytes([0x11]) * (cls.块 * 16))
            文件.write(bytes([0x22]) * (cls.块 * 4))
        #: 3MB 的小文件（不足 16MB → 读全部）
        cls.小文件 = cls.目录 / "3MB.bin"
        cls.小内容 = bytes([0x33]) * (cls.块 * 3)
        cls.小文件.write_bytes(cls.小内容)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._临时.__exit__(None, None, None)

    def test_常量就是16MB(self):
        self.assertEqual(哈希字节数, 16 * 1024 * 1024)

    def test_等于只对前16MB求MD5(self):
        期望 = md5(bytes([0x11]) * (self.块 * 16)).hexdigest()
        self.assertEqual(前16MB的MD5(self.大文件), 期望)

    def test_改尾部不影响结果(self):
        with 临时目录() as 目录:
            副本 = Path(目录) / "改尾.bin"
            副本.write_bytes(self.大文件.read_bytes())
            with open(副本, "r+b") as 文件:            # 尾部（第 17MB 之后）改掉
                文件.seek(16 * self.块 + 100)
                文件.write(b"\xff" * 4096)
            self.assertEqual(前16MB的MD5(副本), 前16MB的MD5(self.大文件))

    def test_改头部会改变结果(self):
        with 临时目录() as 目录:
            副本 = Path(目录) / "改头.bin"
            副本.write_bytes(self.大文件.read_bytes())
            with open(副本, "r+b") as 文件:            # 头部第一个字节改掉
                文件.seek(0)
                文件.write(b"\x99")
            self.assertNotEqual(前16MB的MD5(副本), 前16MB的MD5(self.大文件))

    def test_不足16MB就读全部(self):
        self.assertEqual(前16MB的MD5(self.小文件), md5(self.小内容).hexdigest())


# ---------------------------------------------------------------------------
# p 字段与 comment 响应
# ---------------------------------------------------------------------------

class 条目解析测试(unittest.TestCase):
    def test_三种模式(self):
        滚动 = 解析条目(1, "12.34,1,16777215,用户甲", "滚动")
        底部 = 解析条目(2, "13.00,4,16777215,用户乙", "底部")
        顶部 = 解析条目(3, "14.99,5,16777215,用户丙", "顶部")
        self.assertEqual([滚动.模式, 底部.模式, 顶部.模式],
                         [弹幕模式.滚动, 弹幕模式.底部, 弹幕模式.顶部])

    def test_时间与颜色与发送者(self):
        条 = 解析条目(7788, "12.34,1,16777215,张三", "你好")
        self.assertEqual(条.毫秒, 12340)          # 秒 → 毫秒，四舍五入
        self.assertEqual(条.秒, 12.34)
        self.assertEqual(条.文本, "你好")
        self.assertEqual(条.发送者, "张三")
        self.assertEqual(条.标识, "7788")
        self.assertEqual(条.来源, 来源标识.弹弹play)

    def test_颜色16711680是红色(self):
        """``R*65536+G*256+B`` = 16711680 → 0xFF0000（这是官方的算法，不是 RGB 反转）。"""
        条 = 解析条目(1, "1.00,1,16711680,甲", "红")
        self.assertEqual(条.颜色, 0xFF0000)

    def test_字段不足四条一律丢弃(self):
        for p in ("12.34,1,16777215", "12.34,1", "12.34", "", "12.34,1,16777215"):
            self.assertIsNone(解析条目(1, p, "文本"), f"{p!r} 应该被丢掉")

    def test_时间非法丢弃(self):
        for 时间 in ("abc", "", "nan", "inf", "1e5"):
            self.assertIsNone(解析条目(1, f"{时间},1,16777215,甲", "文本"), 时间)

    def test_颜色非法丢弃(self):
        for 颜色 in ("abc", "-1", "16777216", "0xFFFFFF"):
            self.assertIsNone(解析条目(1, f"1.00,1,{颜色},甲", "文本"), 颜色)

    def test_模式不是145的一律丢弃(self):
        for 模式 in (0, 2, 3, 6, 7, 8, 9, "abc"):
            self.assertIsNone(解析条目(1, f"1.00,{模式},16777215,甲", "文本"), 模式)
        # 而 1/4/5 要能过
        for 模式 in (1, 4, 5):
            self.assertIsNotNone(解析条目(1, f"1.00,{模式},16777215,甲", "文本"), 模式)

    def test_解析响应结构与丢弃(self):
        响应 = {"count": 4, "comments": [
            {"cid": 1, "p": "1.00,1,16777215,甲", "m": "好的"},
            {"cid": 2, "p": "2.00,7,16777215,乙", "m": "高级弹幕（丢）"},
            {"cid": 3, "p": "坏的", "m": "坏条目（丢）"},
            "不是字典（丢）",
        ]}
        池 = 解析响应(响应)
        self.assertEqual(len(池), 1)
        self.assertEqual(池.条目们[0].标识, "1")
        self.assertIn("弹弹play", 池.来源说明)

    def test_响应里success为假要报错(self):
        """⚠️ 业务错误藏在 HTTP 200 里 —— 不看 success 就会当成"这集没弹幕"。"""
        with self.assertRaises(弹幕源错误):
            解析响应({"success": False, "errorCode": 400, "errorMessage": "参数不合法"})

    def test_errorCode非0要报错(self):
        with self.assertRaises(弹幕源错误):
            检查业务状态({"success": True, "errorCode": 1, "errorMessage": "签名错误"})
        # 只有 count/comments 的响应（没有 success/errorCode）不该报错
        检查业务状态({"count": 0, "comments": []})
        with self.assertRaises(弹幕源错误):
            检查业务状态([1, 2, 3])


# ---------------------------------------------------------------------------
# 请求、鉴权、缓存、并发
# ---------------------------------------------------------------------------

class 取弹幕测试(unittest.TestCase):
    def test_请求头与查询串(self):
        with 临时目录() as 目录:
            假 = 假传输(弹幕响应("一", "二"))
            源 = 造源(假, Path(目录))
            池 = 源.取弹幕("123")
            self.assertEqual(len(池), 2)
            网址 = 假.最后一个网址()
            self.assertTrue(网址.startswith(官方基地址 + "/api/v2/comment/123?"), 网址)
            self.assertIn("withRelated=true", 网址)
            self.assertIn("chConvert=0", 网址)
            头 = 假.请求们[-1].头
            self.assertEqual(头["X-AppId"], "测试AppId")
            self.assertIn("X-Signature", 头)
            self.assertNotIn("测试密钥", json.dumps(头, ensure_ascii=False))

    def test_403错误要带上原文并给出可照做的说明(self):
        """真机实测的那种响应：HTTP 403 + 空体 + ``X-Error-Message: Invalid AppId``。

        光抛 "HTTP 403" 没人知道该干什么；必须①带上服务端原文②说清"AppId 或 AppSecret
        无效"（官方语义）③指到 DevCenter 去核对。**不做任何自动重试** —— 凭据错了
        重试一万次还是 403，只会白白打人家的接口。
        """
        with 临时目录() as 目录:
            假 = 假传输(应答(403, "", {"X-Error-Message": "Invalid AppId"}))
            源 = 造源(假, Path(目录))
            with self.assertRaises(弹幕源错误) as 捕获:
                源.取弹幕("9")
            说明 = str(捕获.exception)
            self.assertIn("Invalid AppId", 说明)
            self.assertIn("AppSecret", 说明)
            self.assertIn("dev.dandanplay.com", 说明)
            self.assertEqual(假.次数, 1, "凭据类错误不许自动重试")
            self.assertNotIn("测试密钥", 说明)

    def test_403缺头与时间戳错误的说明不同(self):
        """同是 403，``Missing Authentication Headers`` 和 ``Invalid Timestamp``
        要给出**不一样**的修法（前者说没配凭证、后者说校准时钟）。"""
        with 临时目录() as 目录:
            缺头 = 造源(假传输(应答(403, "", {"X-Error-Message": "Missing Authentication Headers"})),
                       Path(目录))
            with self.assertRaises(弹幕源错误) as 甲:
                缺头.取弹幕("9")
            时钟 = 造源(假传输(应答(403, "", {"X-Error-Message": "Invalid Timestamp"})),
                       Path(Path(目录) / "c2"))
            with self.assertRaises(弹幕源错误) as 乙:
                时钟.取弹幕("9")
            self.assertIn("时间", str(乙.exception))
            self.assertNotEqual(str(甲.exception), str(乙.exception))

    def test_403没给X_Error_Message时退回通用报错(self):
        """没有分类信息就别硬编：老老实实报"HTTP 403 + 路径"。"""
        with 临时目录() as 目录:
            源 = 造源(假传输(应答(403, "被网关挡了")), Path(目录))
            with self.assertRaises(弹幕源错误) as 捕获:
                源.取弹幕("9")
            说明 = str(捕获.exception)
            self.assertIn("403", 说明)
            self.assertIn("/api/v2/comment/9", 说明)

    def test_chConvert可配(self):
        with 临时目录() as 目录:
            假 = 假传输(弹幕响应("一"))
            源 = 造源(假, Path(目录), chConvert=2)
            源.取弹幕("9")
            self.assertIn("chConvert=2", 假.最后一个网址())

    def test_HTTP200但success为假要抛错(self):
        with 临时目录() as 目录:
            假 = 假传输({"success": False, "errorCode": 401, "errorMessage": "签名验证失败"})
            源 = 造源(假, Path(目录))
            with self.assertRaises(弹幕源错误) as 捕获:
                源.取弹幕("9")
            self.assertIn("401", str(捕获.exception))
            self.assertIn("签名验证失败", str(捕获.exception))

    def test_非200状态码要抛错(self):
        with 临时目录() as 目录:
            假 = 假传输(应答(500, "服务器炸了"))
            源 = 造源(假, Path(目录))
            with self.assertRaises(弹幕源错误):
                源.取弹幕("9")
            假2 = 假传输(应答(200, "<html>不是 JSON</html>"))
            源2 = 造源(假2, Path(Path(目录) / "c2"))
            with self.assertRaises(弹幕源错误):
                源2.取弹幕("9")

    def test_episodeId必须是整数(self):
        with 临时目录() as 目录:
            假 = 假传输(弹幕响应("一"))
            源 = 造源(假, Path(目录))
            with self.assertRaises(弹幕源错误):
                源.取弹幕("1?withRelated=false")
            with self.assertRaises(弹幕源错误):
                源.取弹幕("")
            self.assertEqual(假.次数, 0, "坏标识不该发出请求")

    def test_缓存命中不发第二次请求(self):
        with 临时目录() as 目录:
            假 = 假传输(弹幕响应("一", "二"))
            源 = 造源(假, Path(目录))
            self.assertEqual(len(源.取弹幕("55")), 2)
            self.assertEqual(len(源.取弹幕("55")), 2)
            self.assertEqual(假.次数, 1, "第二次应该命中缓存，不再请求")
            self.assertEqual(源.缓存命中次数, 1)

    def test_缓存TTL过期后重取(self):
        with 临时目录() as 目录:
            现在 = [1000.0]
            假 = 假传输(lambda 请, 序: 弹幕响应(f"第{序}次"))
            源 = 造源(假, Path(目录), 时钟=lambda: 现在[0], 缓存TTL秒=3600)
            源.取弹幕("55")
            源.取弹幕("55")
            self.assertEqual(假.次数, 1)
            现在[0] += 3600 + 1                       # 时间往前走，缓存过期
            池 = 源.取弹幕("55")
            self.assertEqual(假.次数, 2)
            self.assertEqual(池.条目们[0].文本, "第2次")

    def test_关掉缓存每次都取(self):
        with 临时目录() as 目录:
            假 = 假传输(弹幕响应("一"))
            源 = 造源(假, Path(目录), 缓存TTL秒=0)
            源.取弹幕("55")
            源.取弹幕("55")
            self.assertEqual(假.次数, 2)

    def test_并发取同一集只发一次请求(self):
        """界面里两处同时要同一集很常见 —— 不去重就是把对方接口打成两倍流量。"""
        with 临时目录() as 目录:
            def 慢回答(请, 序):
                time.sleep(0.25)
                return 弹幕响应("一", "二", "三")

            假 = 假传输(慢回答)
            # 缓存也关掉：这次只考验"在途合并"
            源 = 造源(假, Path(目录), 缓存TTL秒=0)
            结果: list[弹幕池] = []
            线程们 = [threading.Thread(target=lambda: 结果.append(源.取弹幕("777")))
                     for _ in range(8)]
            for 线 in 线程们:
                线.start()
            for 线 in 线程们:
                线.join(15)
            self.assertEqual(len(结果), 8, "每个线程都应该拿到结果")
            self.assertTrue(all(len(池) == 3 for 池 in 结果))
            self.assertEqual(假.次数, 1, "8 个线程只该发 1 次请求")

    def test_缓存坏了当没有(self):
        with 临时目录() as 目录:
            假 = 假传输(弹幕响应("一"))
            源 = 造源(假, Path(目录))
            源.取弹幕("55")
            缓存文件 = next((Path(目录) / "缓存").glob("*.json"))
            缓存文件.write_text("{坏掉的缓存", encoding="utf-8")
            with self.assertLogs(弹弹play日志名, level="WARNING"):
                self.assertEqual(len(源.取弹幕("55")), 1)
            self.assertEqual(假.次数, 2, "缓存坏了要重新取，而不是报错")


# ---------------------------------------------------------------------------
# match / search
# ---------------------------------------------------------------------------

class 匹配接口测试(unittest.TestCase):
    def test_请求体字段正确(self):
        with 临时目录() as 目录:
            视频 = Path(目录) / "影片 第01话.mkv"
            视频.write_bytes(b"x" * 4096)
            假 = 假传输(匹配响应([{"episodeId": 101, "animeId": 5, "animeTitle": "某番",
                                "episodeTitle": "第1话", "type": "tvseries", "shift": 0.0}]))
            源 = 造源(假, Path(目录))
            候选 = 源.匹配(素材信息(路径=视频, 时长秒=1440.6))
            体 = 假.最后一个体()
            self.assertEqual(假.请求们[-1].方法, "POST")
            self.assertTrue(假.最后一个网址().endswith("/api/v2/match"))
            self.assertEqual(体["fileName"], "影片 第01话")     # 不含目录与扩展名
            self.assertEqual(体["fileSize"], 4096)
            self.assertEqual(体["videoDuration"], 1441)         # 秒，四舍五入
            self.assertEqual(体["matchMode"], "hashAndFileName")
            self.assertEqual(体["fileHash"], 前16MB的MD5(视频))
            self.assertEqual(len(候选), 1)
            self.assertEqual((候选[0].标识, 候选[0].标题, 候选[0].集号),
                             ("101", "某番", "1"))

    def test_多个候选都解析出来(self):
        with 临时目录() as 目录:
            假 = 假传输(匹配响应([
                {"episodeId": 1, "animeTitle": "甲番", "episodeTitle": "第1话",
                 "type": "tvseries", "shift": -1.5},
                {"episodeId": 2, "animeTitle": "乙番", "episodeTitle": "第2话", "type": "tvseries"},
                {"animeTitle": "没有 episodeId 的（丢）"}]))
            源 = 造源(假, Path(Path(目录) / "c"))
            候选 = 源.匹配(素材信息(文件名="某番 第1话.mkv"))
            self.assertEqual([候.标识 for 候 in 候选], ["1", "2"])
            self.assertEqual(候选[0].偏移秒, -1.5)
            self.assertEqual(候选[1].集号, "2")

    def test_isMatched为假返回空(self):
        with 临时目录() as 目录:
            假 = 假传输(匹配响应([{"episodeId": 1, "animeTitle": "甲番"}], isMatched=False))
            源 = 造源(假, Path(目录))
            self.assertEqual(源.匹配(素材信息(文件名="不知道什么.mkv")), [])

    def test_没有文件时退回按文件名匹配(self):
        with 临时目录() as 目录:
            假 = 假传输(匹配响应([{"episodeId": 1, "animeTitle": "甲番"}]))
            源 = 造源(假, Path(目录))
            源.匹配(素材信息(文件名="甲番 第1话.mkv"))
            体 = 假.最后一个体()
            self.assertEqual(体["fileHash"], "")
            self.assertEqual(体["matchMode"], "fileNameOnly")

    def test_手动搜索(self):
        """搜索是 ``GET /api/v2/search/episodes``（查询参数），不是 ``POST /api/v2/search``。

        为什么改：``POST /api/v2/search`` 在官方 v2 里**根本没有这个路由**（真机实测
        404；假传输的单测看不出来，因为它不校验路由）。官方 Swagger 里只有
        ``GET /api/v2/search/episodes``（可按 anime / tmdbId 查）与
        ``GET /api/v2/search/anime``。
        """
        with 临时目录() as 目录:
            假 = 假传输({"success": True, "errorCode": 0, "animes": [
                {"animeId": 5, "animeTitle": "某番", "type": "tvseries",
                 "episodes": [{"episodeId": 11, "episodeTitle": "第1话"},
                              {"episodeId": 12, "episodeTitle": "第2话"}]}]})
            源 = 造源(假, Path(目录))
            全部 = 源.搜索("某番")
            self.assertEqual([候.标识 for 候 in 全部], ["11", "12"])
            第二集 = 源.搜索("某番", 集号=2)
            self.assertEqual([候.标识 for 候 in 第二集], ["12"])
            self.assertEqual(假.请求们[-1].方法, "GET")
            查询 = dict(urllib.parse.parse_qsl(
                urllib.parse.urlsplit(假.最后一个网址()).query))
            self.assertEqual(查询["anime"], "某番")
            self.assertEqual(查询["episode"], "2")
            self.assertEqual(查询["v2"], "true",
                             "官方 2026-07-13 起 v2=true 切新版搜索引擎（签名不含查询串）")
            self.assertTrue(假.最后一个网址().startswith(
                "https://api.dandanplay.net/api/v2/search/episodes?"))

    def test_按TMDBid搜索(self):
        """资料库给的 TMDB id 可以**精确查**（tmdbIdType：0=剧、1=电影）。"""
        with 临时目录() as 目录:
            假 = 假传输({"success": True, "errorCode": 0, "animes": []})
            源 = 造源(假, Path(目录))
            源.搜索("", 集号=7, 标识="223911")
            查询 = dict(urllib.parse.parse_qsl(
                urllib.parse.urlsplit(假.最后一个网址()).query))
            self.assertEqual(查询["tmdbId"], "223911")
            self.assertEqual(查询["tmdbIdType"], "0")
            self.assertEqual(查询["episode"], "7")
            源.搜索("某电影", 标识="438631", 电影=True)
            查询 = dict(urllib.parse.parse_qsl(
                urllib.parse.urlsplit(假.最后一个网址()).query))
            self.assertEqual(查询["tmdbIdType"], "1", "电影要给 1")
            self.assertEqual(查询["anime"], "某电影")


# ---------------------------------------------------------------------------
# 本地文件
# ---------------------------------------------------------------------------

B站样例XML = """<?xml version="1.0" encoding="UTF-8"?>
<i>
  <chatserver>chat.bilibili.com</chatserver>
  <d p="1.50,1,25,16777215,1500000000,0,用户甲,1001">第一条</d>
  <d p="2.25,4,25,16711680,1500000001,0,用户乙,1002">底部红色</d>
  <d p="3.00,5,18,255,1500000002,0,用户丙,1003">顶部蓝色</d>
  <d p="4.00,7,25,16777215,1500000003,0,用户丁,1004">高级弹幕（丢）</d>
  <d p="5.00,6,25,16777215,1500000004,0,用户戊,1005">逆向弹幕（丢）</d>
  <d p="6.00,1,25,abc,1500000005,0,用户己,1006">颜色坏（丢）</d>
  <d p="坏,1,25,16777215,1500000006,0,用户庚,1007">时间坏（丢）</d>
  <d p="7.00">只有时间</d>
  <d p="8.00,1">只有时间与模式</d>
  <d p="9.00,1,25">时间模式字号</d>
  <d p="10.00,1,25,16777215,0,0,用户辛,1008">转义 &amp;lt; 与 &amp;amp; 与 &lt;标签&gt;</d>
</i>
"""


class 本地解析测试(unittest.TestCase):
    def test_B站XML三种模式与字段(self):
        条目们 = 解析B站XML(B站样例XML)
        self.assertEqual(条目们[0].毫秒, 1500)
        self.assertEqual(条目们[0].模式, 弹幕模式.滚动)
        self.assertEqual(条目们[0].颜色, 0xFFFFFF)
        self.assertEqual(条目们[0].发送者, "用户甲")
        self.assertEqual(条目们[0].标识, "1001")
        self.assertEqual(条目们[0].池, "0")
        self.assertEqual(条目们[0].来源, 来源标识.本地)
        self.assertEqual(条目们[1].模式, 弹幕模式.底部)
        self.assertEqual(条目们[1].颜色, 0xFF0000)
        self.assertEqual(条目们[2].模式, 弹幕模式.顶部)
        self.assertEqual(条目们[2].颜色, 0x0000FF)
        self.assertEqual(条目们[2].字号, 18)

    def test_B站XML该丢的都丢了(self):
        文本们 = [条.文本 for 条 in 解析B站XML(B站样例XML)]
        self.assertEqual(文本们, ["第一条", "底部红色", "顶部蓝色", "只有时间",
                                "只有时间与模式", "时间模式字号",
                                "转义 &lt; 与 &amp; 与 <标签>"])

    def test_B站XML字段不足时尽力解析(self):
        """字段不足 8 个时：时间必须有，模式缺了按滚动、颜色缺了按白色、字号按 25。"""
        条目们 = 解析B站XML(B站样例XML)[3:6]
        self.assertEqual([(条.毫秒, 条.模式, 条.颜色, 条.字号) for 条 in 条目们],
                         [(7000, 弹幕模式.滚动, 0xFFFFFF, 25),
                          (8000, 弹幕模式.滚动, 0xFFFFFF, 25),
                          (9000, 弹幕模式.滚动, 0xFFFFFF, 25)])
        self.assertEqual([条.发送者 for 条 in 条目们], ["", "", ""])

    def test_B站XML反转义是单趟的(self):
        """``&amp;lt;`` 应该还原成字面量 ``&lt;``（先换 &amp; 就会变成 ``<``）。"""
        条目们 = 解析B站XML(B站样例XML)
        末尾 = 条目们[-1]
        self.assertEqual(末尾.文本, "转义 &lt; 与 &amp; 与 <标签>")

    def test_B站XML不用xml模块也能扛住畸形写法(self):
        """自闭合（无文本）、单引号属性、被别的标签夹着、空文本 —— 都不能把解析搞崩。

        （不认 CDATA 是手写正则的已知取舍，见模块 docstring；B 站导出的文件里没有。）
        """
        畸形 = ('<i><d p="1,1,25,16777215" />'
              "<d p='2,5,25,16777215,0,0,人,2'>单引号属性</d>"
              "<foo><d p=\"4,1,25,16777215,0,0,人,4\">夹在别的标签里也认</d></foo>"
              "<d p=\"5,1,25,16777215,0,0,人,5\"></d></i>")
        条目们 = 解析B站XML(畸形)
        self.assertEqual([条.文本 for 条 in 条目们],
                         ["单引号属性", "夹在别的标签里也认"])
        self.assertEqual(条目们[0].模式, 弹幕模式.顶部)
        self.assertEqual(条目们[0].毫秒, 2000)

    def test_我们的JSON解析与导出往返(self):
        池 = 弹幕池([
            弹幕(1500, "第一条", 弹幕模式.滚动, 0xFFFFFF, 25, "用户甲", 来源标识.本地, "1"),
            弹幕(2250, "底部", 弹幕模式.底部, 0xFF0000, 18, "用户乙", 来源标识.本地, "2"),
            弹幕(3000, "顶部", 弹幕模式.顶部, 0x0000FF, 36, "用户丙", 来源标识.本地, "3"),
        ])
        文本 = 导出为JSON(池)
        项 = json.loads(文本)
        self.assertEqual(sorted(项[0]), sorted(["毫秒", "文本", "模式", "颜色", "发送者", "字号"]))
        回来 = 弹幕池(解析我们的JSON(文本))
        摘要 = lambda 条: (条.毫秒, 条.文本, 条.模式, 条.颜色, 条.发送者, 条.字号)
        self.assertEqual([摘要(条) for 条 in 回来], [摘要(条) for 条 in 池])
        self.assertEqual(回来.统计()["总数"], 3)

    def test_导出带元信息能保住来源(self):
        条 = 弹幕(1000, "来自网络", 弹幕模式.滚动, 0xFFFFFF, 25, "甲", 来源标识.弹弹play, "99")
        文本 = 导出为JSON(弹幕池([条]), 带元信息=True)
        self.assertIn("来源", json.loads(文本)[0])
        回来 = 解析我们的JSON(文本)[0]
        self.assertEqual((回来.来源, 回来.标识), (来源标识.弹弹play, "99"))

    def test_我们的JSON容错(self):
        文本 = json.dumps([
            {"毫秒": 1000, "文本": "正常", "模式": "顶部", "颜色": 16711680},
            {"毫秒": 2000, "文本": "模式写数字", "模式": 4},
            {"秒": 3.5, "文本": "只写了秒"},
            {"文本": "没有时间（丢）"},
            "不是字典（丢）",
        ], ensure_ascii=False)
        条目们 = 解析我们的JSON(文本)
        self.assertEqual([(条.毫秒, 条.模式) for 条 in 条目们],
                         [(1000, 弹幕模式.顶部), (2000, 弹幕模式.底部), (3500, 弹幕模式.滚动)])
        self.assertEqual(条目们[0].颜色, 0xFF0000)

    def test_坏JSON要抛错(self):
        with self.assertRaises(弹幕源错误):
            解析我们的JSON("{不是数组")
        with self.assertRaises(弹幕源错误):
            解析我们的JSON('{"不是": "数组"}')

    def test_找同名弹幕文件(self):
        with 临时目录() as 目录:
            根 = Path(目录)
            视频 = 根 / "影片.mp4"
            视频.write_bytes("假装是视频".encode())
            (根 / "影片.xml").write_text("<i></i>", encoding="utf-8")
            (根 / "影片.json").write_text("[]", encoding="utf-8")
            (根 / "影片.danmaku.json").write_text("[]", encoding="utf-8")
            for 无关 in ("别的.xml", "影片.srt", "影片.danmaku.txt", "影片 预告.xml"):
                (根 / 无关).write_text("x", encoding="utf-8")
            找到 = 找同名弹幕文件(视频)
            self.assertEqual([路径.name for 路径 in 找到],
                             ["影片.xml", "影片.json", "影片.danmaku.json"])
            # 没有同名文件时返回空（而不是乱认一个）
            self.assertEqual(找同名弹幕文件(根 / "没这个.mp4"), [])


class 本地源测试(unittest.TestCase):
    def test_不支持按文件匹配(self):
        源 = 本地源()
        self.assertFalse(源.能匹配())
        self.assertEqual(源.匹配(素材信息(文件名="影片.mkv")), [])
        self.assertEqual(源.标识, "local")

    def test_自动发现并合并去重(self):
        with 临时目录() as 目录:
            根 = Path(目录)
            视频 = 根 / "影片.mkv"
            视频.write_bytes("假装是视频".encode())
            (根 / "影片.xml").write_text(
                '<i><d p="1.00,1,25,16777215,0,0,甲,1">同一条</d>'
                '<d p="2.00,5,25,16777215,0,0,乙,2">只在XML里</d></i>', encoding="utf-8")
            (根 / "影片.danmaku.json").write_text(json.dumps([
                {"毫秒": 1000, "文本": "同一条", "模式": "滚动"},
                {"毫秒": 3000, "文本": "只在JSON里", "模式": "底部"},
            ], ensure_ascii=False), encoding="utf-8")
            源 = 本地源()
            池 = 源.取弹幕(str(视频))
            self.assertEqual(sorted(条.文本 for 条 in 池),
                             ["只在JSON里", "只在XML里", "同一条"])
            self.assertTrue(all(条.来源 is 来源标识.本地 for 条 in 池))
            self.assertIn("本地", 池.来源说明)

    def test_直接给弹幕文件也行(self):
        with 临时目录() as 目录:
            文件 = Path(目录) / "影片.xml"
            文件.write_text('<i><d p="1.00,1,25,16777215,0,0,甲,1">就一条</d></i>',
                          encoding="utf-8")
            池 = 本地源().取弹幕(str(文件))
            self.assertEqual(len(池), 1)

    def test_读同名字幕弹幕入口(self):
        """播放侧只用这一个入口：给视频路径 → 池（找不到给 None，不抛异常）。"""
        with 临时目录() as 目录:
            根 = Path(目录)
            视频 = 根 / "影片.mp4"
            视频.write_bytes("假装是视频".encode())
            self.assertIsNone(读同名字幕弹幕(视频), "旁边没有弹幕文件时给 None")
            self.assertIsNone(读同名字幕弹幕(None))
            self.assertIsNone(读同名字幕弹幕(根 / "没这个.mp4"))
            (根 / "影片.xml").write_text(
                '<i><d p="1.00,1,25,16777215,0,0,甲,1">同名弹幕</d></i>', encoding="utf-8")
            池 = 读同名字幕弹幕(视频)
            self.assertEqual([条.文本 for 条 in 池], ["同名弹幕"])

    def test_找不到文件要报错(self):
        with 临时目录() as 目录:
            with self.assertRaises(弹幕源错误):
                本地源().取弹幕(str(Path(目录) / "没有这个.mp4"))
            with self.assertRaises(弹幕源错误):
                本地源().取弹幕("")

    def test_GB18030的XML也能读(self):
        with 临时目录() as 目录:
            文件 = Path(目录) / "老工具导出的.xml"
            文件.write_bytes('<i><d p="1.00,1,25,16777215,0,0,甲,1">简体中文弹幕</d></i>'
                          .encode("gb18030"))
            池 = 本地源().取弹幕(str(文件))
            self.assertEqual(池.条目们[0].文本, "简体中文弹幕")

    def test_本地源有缓存但改了文件会重读(self):
        with 临时目录() as 目录:
            文件 = Path(目录) / "影片.xml"
            文件.write_text('<i><d p="1.00,1,25,16777215,0,0,甲,1">一</d></i>', encoding="utf-8")
            源 = 本地源()
            self.assertEqual(len(源.取弹幕(str(文件))), 1)
            self.assertEqual(len(源.取弹幕(str(文件))), 1)
            self.assertEqual(源.读取次数, 1, "第二次应该走缓存")
            文件.write_text('<i><d p="1.00,1,25,16777215,0,0,甲,1">一</d>'
                          '<d p="2.00,1,25,16777215,0,0,乙,2">二</d></i>', encoding="utf-8")
            self.assertEqual(len(源.取弹幕(str(文件))), 2, "文件变了要重新读")
            self.assertEqual(源.读取次数, 2)


# ---------------------------------------------------------------------------
# 工厂与"没有凭证也要能用"
# ---------------------------------------------------------------------------

class 建默认源测试(unittest.TestCase):
    """默认源链：**Animeko（公开，无需凭据）→ 弹弹play（要 AppId）→ 本地文件**。

    顺序不是随便排的：弹弹play 现在连 /match 都要求 AppId 签名（真机实测 403），
    而个人开发者拿 AppId 要审核 → 把"不需要凭据就能用"的源放最前，
    没配任何 key 的用户也能拿到在线弹幕。
    """

    def test_默认三个源且顺序对(self):
        with 临时目录() as 目录:
            源们 = 建默认源({"传输": 假传输(弹幕响应("一")), "读环境": False,
                           "缓存目录": Path(目录) / "缓存",
                           "配置路径": Path(目录) / "没有.json"})
            self.assertEqual([源.标识 for 源 in 源们],
                             ["animeko", "dandanplay", "local"])
            self.assertEqual([源.能匹配() for 源 in 源们], [True, True, False])

    def test_可以只要网络源(self):
        with 临时目录() as 目录:
            源们 = 建默认源({"本地": False, "传输": 假传输(弹幕响应("一")),
                           "读环境": False, "缓存目录": Path(目录) / "缓存",
                           "配置路径": Path(目录) / "没有.json"})
            self.assertEqual([源.标识 for 源 in 源们], ["animeko", "dandanplay"])

    def test_不认识的配置项只警告不报错(self):
        with 临时目录() as 目录:
            with self.assertLogs("wangpan.danmaku.源", level="WARNING"):
                源们 = 建默认源({"传输": 假传输(弹幕响应("一")), "读环境": False,
                               "缓存目录": Path(目录) / "缓存",
                               "配置路径": Path(目录) / "没有.json",
                               "这个是错的键": 1})
            self.assertEqual(len(源们), 3)

    def test_没有凭证就发不带鉴权的请求并写进日志(self):
        with 临时目录() as 目录:
            假 = 假传输(弹幕响应("一"))
            with self.assertLogs(弹弹play日志名, level="INFO") as 捕获:
                源 = 弹弹play源(传输=假, 读环境=False,
                             配置路径=Path(目录) / "没有.json",
                             缓存目录=Path(目录) / "缓存", 最小请求间隔秒=0.0)
            日志文本 = "\n".join(记录.getMessage() for 记录 in 捕获.records)
            self.assertIn("公开", 日志文本)
            self.assertFalse(源.能签名())
            源.取弹幕("1")
            头 = 假.请求们[-1].头
            self.assertNotIn("X-AppId", 头, "没有凭证就不该带鉴权头")
            self.assertNotIn("X-Signature", 头)

    def test_密钥绝不进日志(self):
        秘密 = "这是绝密-不要出现在日志里"
        with 临时目录() as 目录:
            with self.assertLogs(弹弹play日志名, level="INFO") as 捕获:
                源 = 弹弹play源("测试AppId", 秘密, 传输=假传输(弹幕响应("一")),
                             读环境=False, 配置路径=Path(目录) / "没有.json",
                             缓存目录=Path(目录) / "缓存")
            文本 = "\n".join(记录.getMessage() for 记录 in 捕获.records)
            self.assertNotIn(秘密, 文本)
            self.assertTrue(源.能签名())
            self.assertNotIn(秘密, json.dumps(源.诊断(), ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
