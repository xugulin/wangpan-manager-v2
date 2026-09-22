"""光鸭适配器：离线单测（注入假传输层）+ 可选的真机联调。

离线部分**不联网**：用一个假的"请求"函数喂响应，验证解析与路径解析逻辑。
真机部分要 ``V2_测试光鸭=1`` 且有令牌才跑（CI/别人机器上自动跳过）。
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from tests.公用 import 临时目录

from wangpan.pan.光鸭 import 默认令牌路径, 光鸭令牌, 光鸭适配器
from wangpan.pan.接口 import 不支持


class 假传输:
    """按"路径 → 响应"喂数据，同时记录请求，方便断言参数对不对。"""

    def __init__(self, 响应们: dict, 记录: list | None = None) -> None:
        self.响应们 = 响应们
        self.记录 = 记录 if 记录 is not None else []

    def __call__(self, 方法: str, 地址: str, 体=None, 头=None) -> dict:
        路径 = 地址.split("guangyapan.com", 1)[-1]
        体数据 = json.loads((体 or b"{}").decode("utf-8"))
        self.记录.append((方法, 路径, 体数据, dict(头 or {})))
        for 键, 值 in self.响应们.items():
            if 路径.startswith(键):
                return 值(体数据) if callable(值) else 值
        return {"code": 0, "data": {"list": []}}


def 造适配器(响应们: dict, 记录: list | None = None, 令牌: str = "假令牌") -> 光鸭适配器:
    仓 = 光鸭令牌(Path("/nonexistent/不该读到的令牌.json"))
    仓.写(令牌, "假刷新令牌", 7200.0)
    return 光鸭适配器(令牌=仓, 请求=假传输(响应们, 记录), 日志回调=lambda _t: None)


class 光鸭离线测试(unittest.TestCase):
    def test_列目录用对了主机与请求体(self):
        记录: list = []
        适配器 = 造适配器({"/userres/v1/file/get_file_list": {
            "code": 0, "data": {"list": [
                {"fileId": "1", "fileName": "影片.mp4", "fileSize": 1234,
                 "resType": 1, "fileType": 4, "ext": "mp4", "utime": 1700000000000},
                {"fileId": "2", "fileName": "目录A", "resType": 2, "dirType": 1},
            ]}}}, 记录)
        条目们 = 适配器.列出("/")
        self.assertEqual([x.名字 for x in 条目们], ["影片.mp4", "目录A"])
        self.assertFalse(条目们[0].是目录)
        self.assertTrue(条目们[1].是目录, "resType=2 才是目录")
        self.assertEqual(条目们[0].大小, 1234)
        self.assertTrue(条目们[0].可播放())
        # 请求打到了 api.guangyapan.com（不是网页 www）
        方法, 路径, 体, 头 = 记录[0]
        self.assertEqual(方法, "POST")
        self.assertEqual(路径, "/userres/v1/file/get_file_list")
        self.assertEqual(体["parentId"], "")
        self.assertIn("clientId", 体, "业务请求要带 clientId")
        self.assertTrue(str(头.get("authorization", "")).startswith("Bearer "),
                        "必须带授权头")
        self.assertGreater(条目们[0].修改时间, 1_600_000_000, "毫秒时间戳要换算成秒")

    def test_路径解析成fileId(self):
        def 列(体):
            父 = 体.get("parentId") or ""
            if 父 == "":
                return {"code": 0, "data": {"list": [
                    {"fileId": "10", "fileName": "电影", "resType": 2}]}}
            if 父 == "10":
                return {"code": 0, "data": {"list": [
                    {"fileId": "20", "fileName": "2024", "resType": 2}]}}
            return {"code": 0, "data": {"list": []}}
        适配器 = 造适配器({"/userres/v1/file/get_file_list": 列})
        self.assertEqual(适配器._解析父目录("/电影/2024"), "20")
        self.assertEqual(适配器._解析父目录("/"), "")
        with self.assertRaises(不支持):
            适配器._解析父目录("/不存在的目录")

    def test_取直链用signedURL字段(self):
        记录: list = []
        适配器 = 造适配器({
            "/userres/v1/file/get_file_list": {"code": 0, "data": {"list": [
                {"fileId": "99", "fileName": "片子.mp4", "fileSize": 999,
                 "resType": 1}]}},
            "/userres/v1/get_res_download_url": {"code": 0, "data": {
                # ⚠️ 实测字段名就是 signedURL（写成 downloadUrl 会拿不到）
                "signedURL": "https://cdn.example.com/a?sign=x",
                "expiresIn": 600}},
        }, 记录)
        直链 = 适配器.取直链("/片子.mp4")
        self.assertTrue(直链.地址.startswith("https://cdn.example.com/"))
        self.assertEqual(直链.大小, 999)
        self.assertEqual(直链.有效秒, 600)
        体 = [r for r in 记录 if r[1].endswith("get_res_download_url")][0][2]
        self.assertEqual(体["fileId"], "99", "取直链要用 fileId")

    def test_接口报错要抛出来而不是给空列表(self):
        适配器 = 造适配器({"/userres/v1/file/get_file_list": {
            "code": 401, "msg": "未登录"}})
        with self.assertRaises(不支持) as 上:
            适配器.列出("/")
        self.assertIn("未登录", str(上.exception))

    def test_未登录时明说(self):
        仓 = 光鸭令牌(Path("/nonexistent/x.json"))
        适配器 = 光鸭适配器(令牌=仓)
        with self.assertRaises(不支持) as 上:
            适配器.列出("/")
        self.assertIn("未登录", str(上.exception))
        self.assertEqual(适配器.能力, {"列出", "直链", "下载"})
        self.assertTrue(适配器.支持分段下载, "光鸭直链支持 Range，应该走多段下载")


class 令牌测试(unittest.TestCase):
    def setUp(self):
        self.临时 = 临时目录()
        self.addCleanup(self.临时.cleanup)
        self.路径 = Path(self.临时.name) / "令牌_光鸭.json"

    def test_存取(self):
        仓 = 光鸭令牌(self.路径)
        self.assertFalse(仓.有效())
        仓.写("访问A", "刷新A", 7200.0, "用户1")
        self.assertTrue(self.路径.is_file())
        新仓 = 光鸭令牌(self.路径)
        self.assertEqual(新仓.访问令牌, "访问A")
        self.assertEqual(新仓.刷新令牌, "刷新A")
        self.assertEqual(新仓.用户标识, "用户1")
        self.assertTrue(新仓.有效())
        self.assertEqual(oct(self.路径.stat().st_mode)[-3:], "600",
                         "令牌是凭据，权限要收紧")

    def test_快过期会去刷新(self):
        仓 = 光鸭令牌(self.路径)
        仓.写("旧访问", "刷新B", 10.0)          # 10 秒就过期（小于提前刷新余量）
        self.assertFalse(仓.有效(), "快过期就不该算有效")
        调用: list = []

        def 请求(方法, 地址, 体=None, 头=None):
            调用.append((方法, 地址, json.loads((体 or b"{}").decode())))
            return {"access_token": "新访问", "refresh_token": "刷新C", "expires_in": 7200}
        self.assertTrue(仓.刷新(请求))
        self.assertEqual(仓.访问令牌, "新访问")
        self.assertEqual(仓.刷新令牌, "刷新C")
        方法, 地址, 体 = 调用[0]
        self.assertEqual(方法, "POST")
        self.assertIn("account.guangyapan.com/v1/auth/token", 地址)
        self.assertEqual(体["grant_type"], "refresh_token")
        self.assertEqual(体["refresh_token"], "刷新B")
        self.assertIn("client_id", 体)

    def test_坏文件不影响启动(self):
        self.路径.write_text("{坏 JSON", encoding="utf-8")
        仓 = 光鸭令牌(self.路径)
        self.assertEqual(仓.访问令牌, "")
        self.assertFalse(仓.有效())


@unittest.skipUnless(os.environ.get("V2_测试光鸭") == "1" and 默认令牌路径().is_file(),
                     "要 V2_测试光鸭=1 且本机有光鸭令牌才跑（真机联调）")
class 光鸭联调测试(unittest.TestCase):
    """真机联调：列真实目录 + 取真实直链 + Range 取一段字节。"""

    def test_真机能列出且直链可取字节(self):
        import urllib.request
        适配器 = 光鸭适配器()
        self.assertTrue(适配器.登录状态()["已登录"], "令牌应该可用")
        条目们 = 适配器.列出("/")
        self.assertGreater(len(条目们), 0, "根目录不该是空的")
        文件 = [x for x in 条目们 if not x.是目录 and x.大小 > 10000]
        if not 文件:
            self.skipTest("根目录没有够大的文件")
        直链 = 适配器.取直链(文件[0].路径)
        self.assertTrue(直链.地址.startswith("http"))
        请求 = urllib.request.Request(直链.地址,
                                    headers={**直链.请求头, "Range": "bytes=0-1023"})
        with urllib.request.urlopen(请求, timeout=30) as 应答:
            数据 = 应答.read()
        self.assertEqual(len(数据), 1024)
        self.assertIn(应答.status, (200, 206))


if __name__ == "__main__":
    unittest.main()
