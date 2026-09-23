"""TMDB 客户端的测试（**全部离线**：注入假传输，一次网络都不发）。

钉住的是这些容易写错的地方：
* 请求路径/查询参数/鉴权头（Bearer 与 api_key 两种）；
* 语言回退："只在字段为空时补一次"，且补完就停；
* ``include_image_language=zh,null``（是 ``zh`` **不是** ``zh-CN``，还必须带 ``null``）；
* 429 退避重试（听 ``Retry-After``，没有就指数退避，最多 3 次）；
* 缓存命中 + TTL 过期 + **180 天硬过期**（注入"现在时间"，不真的等）；
* 分级：空串过滤、``type`` 优先级、**TMDB 没有 CN** 时的国家回退；
* 剧集阵容两种结构（``aggregate_credits`` 的 ``roles[]`` 与 ``credits`` 的 ``character``）都要能解析；
* token/api_key **绝不出现在日志与异常文本里**。
"""

from __future__ import annotations

import json
import logging
import os
import unittest
from pathlib import Path
from unittest import mock

from tests.公用 import 临时目录

from wangpan.scrape.模型 import 媒体类型
from wangpan.scrape.tmdb import (TMDB客户端, TMDB配置, 接口基地址, 最长缓存秒, 请求失败,
                              署名, 默认缓存目录)


# ============================ 测试用假传输 ============================


class 假传输:
    """假的 HTTP 传输：把每次调用记下来，应答由测试函数决定。"""

    def __init__(self, 处理):
        self.处理 = 处理
        self.调用: list[dict] = []

    def __call__(self, 方法: str, 地址: str, 参数: dict, 头: dict) -> dict:
        本次 = {"方法": 方法, "地址": 地址, "路径": 地址[len(接口基地址):] if 地址.startswith(接口基地址) else 地址,
              "参数": dict(参数), "头": dict(头)}
        self.调用.append(本次)
        return self.处理(本次, len(self.调用))

    @property
    def 次数(self) -> int:
        return len(self.调用)

    def 语言们(self) -> list[str]:
        return [x["参数"].get("language") for x in self.调用]

    def 路径们(self) -> list[str]:
        return [x["路径"] for x in self.调用]


def 固定(应答: dict):
    """不管请求什么都回同一份数据。"""
    return lambda 本次, 次: 应答


# ============================ 测试数据 ============================


def 电影数据(语言: str = "zh-CN") -> dict:
    中文 = 语言.startswith("zh")
    return {
        "id": 438631,
        "title": "沙丘" if 中文 else "Dune",
        "original_title": "Dune",
        "release_date": "2021-09-15",
        "overview": "保罗·厄崔迪前往沙丘。" if 中文 else "Paul Atreides travels to Arrakis.",
        "runtime": 155,
        "vote_average": 7.8,
        "vote_count": 12000,
        "popularity": 321.5,
        "genres": [{"name": "科幻"}, {"name": "冒险"}],
        "status": "Released",
        "original_language": "en",
        "production_companies": [{"name": "Legendary Pictures"}],
        "imdb_id": "tt1160419",
        "release_dates": {"results": [
            {"iso_3166_1": "US", "release_dates": [
                {"certification": "", "type": 3},          # 空串必须过滤
                {"certification": "PG-13", "type": 3},     # 影院（优先级最高）
                {"certification": "R", "type": 5},         # 实物：不该被选中
            ]},
            {"iso_3166_1": "HK", "release_dates": [{"certification": "IIA", "type": 3}]},
            {"iso_3166_1": "TW", "release_dates": [{"certification": "保護級", "type": 3}]},
            {"iso_3166_1": "DE", "release_dates": [{"certification": "12", "type": 3}]},
            {"iso_3166_1": "FR", "release_dates": [{"certification": "  ", "type": 3}]},
        ]},
        "images": {"posters": [
            {"file_path": "/zh.jpg", "iso_639_1": "zh", "vote_average": 5.0, "vote_count": 2,
             "width": 1000, "height": 1500},
            {"file_path": "/en.jpg", "iso_639_1": "en", "vote_average": 9.9, "vote_count": 99},
            {"file_path": "/none.jpg", "iso_639_1": None, "vote_average": 7.0, "vote_count": 5},
        ], "backdrops": [{"file_path": "/bd.jpg", "iso_639_1": "", "vote_average": 3.0}],
            "logos": [{"file_path": "/logo.png", "iso_639_1": "zh", "vote_average": 1.0}]},
        "credits": {"cast": [
            {"id": 2, "name": "演员乙", "order": 1, "character": "角色乙", "profile_path": "/b.jpg"},
            {"id": 1, "name": "演员甲", "order": 0, "character": "角色甲"},
        ], "crew": [
            {"id": 9, "name": "导演兼制片", "job": "Producer"},
            {"id": 9, "name": "导演兼制片", "job": "Director"},
            {"id": 10, "name": "编剧", "job": "Screenplay"},
        ]},
        "videos": {"results": [{"site": "YouTube", "type": "Trailer", "key": "abc"}]},
        "external_ids": {"imdb_id": "tt1160419"},
    }


def 剧集数据(阵容结构: str = "aggregate", 语言: str = "zh-CN") -> dict:
    数据 = {
        "id": 1399,
        "name": "剧名" if 语言.startswith("zh") else "Show Name",
        "original_name": "Show Name",
        "first_air_date": "2011-04-17",
        "overview": "简介" if 语言.startswith("zh") else "Overview",
        "episode_run_time": [57],
        "vote_average": 8.4,
        "vote_count": 21000,
        "popularity": 500.0,
        "genres": [{"name": "剧情"}],
        "status": "Ended",
        "original_language": "en",
        "production_companies": [{"name": "HBO"}],
        "number_of_seasons": 8,
        "number_of_episodes": 73,
        "seasons": [
            # 官方坑：name 会随语言变（"Staffel 1"），季号只能看 season_number
            {"season_number": 2, "name": "Staffel 2", "episode_count": 10,
             "air_date": "2012-04-01", "poster_path": "/s2.jpg", "overview": "第二季"},
            {"season_number": 1, "name": "Staffel 1", "episode_count": 10,
             "air_date": "2011-04-17", "poster_path": "/s1.jpg", "overview": "第一季"},
            {"season_number": 0, "name": "Specials", "episode_count": 3, "air_date": "",
             "poster_path": "", "overview": ""},
        ],
        "content_ratings": {"results": [
            {"iso_3166_1": "US", "rating": "TV-MA"},
            {"iso_3166_1": "HK", "rating": "M"},
            {"iso_3166_1": "DE", "rating": "16"},
            {"iso_3166_1": "FR", "rating": ""},          # 空串过滤
        ]},
        "images": {"posters": [{"file_path": "/tv-zh.jpg", "iso_639_1": "zh",
                                "vote_average": 4.0, "vote_count": 1}],
                   "backdrops": [{"file_path": "/tv-bd.jpg", "iso_639_1": None,
                                  "vote_average": 6.0}]},
        "external_ids": {"tvdb_id": 121361},              # 剧集**没有** imdb_id
    }
    if 阵容结构 == "aggregate":
        数据["aggregate_credits"] = {
            "cast": [
                {"id": 11, "name": "全剧演员乙", "order": 1, "total_episode_count": 60,
                 "roles": [{"character": "角色乙", "episode_count": 60}]},
                {"id": 12, "name": "全剧演员甲", "order": 0, "total_episode_count": 67,
                 "roles": [{"character": "角色甲", "episode_count": 67}]},
            ],
            "crew": [
                {"id": 21, "name": "制片人", "total_episode_count": 73,
                 "jobs": [{"job": "Executive Producer", "episode_count": 73}]},
                {"id": 22, "name": "导演甲", "total_episode_count": 10,
                 "jobs": [{"job": "Director", "episode_count": 10}]},
            ],
        }
    else:
        数据["credits"] = {
            "cast": [
                {"id": 11, "name": "最新季演员乙", "order": 1, "character": "角色乙"},
                {"id": 12, "name": "最新季演员甲", "order": 0, "character": "角色甲"},
            ],
            "crew": [{"id": 22, "name": "导演甲", "job": "Director"},
                     {"id": 23, "name": "作曲甲", "job": "Original Music Composer"}],
        }
    return 数据


def 季数据() -> dict:
    return {
        "id": 3624, "season_number": 1, "name": "第 1 季", "overview": "第一季简介",
        "air_date": "2011-04-17",
        "images": {"posters": [{"file_path": "/season1.jpg", "iso_639_1": "zh",
                                "vote_average": 2.0, "vote_count": 1}]},
        "episodes": [
            {"id": 63056, "episode_number": 1, "name": "凛冬将至", "overview": "第一集",
             "air_date": "2011-04-17", "runtime": 62, "vote_average": 8.0,
             "still_path": "/e1.jpg", "still_width": 1920, "still_height": 1080},
            {"id": 63057, "episode_number": 2, "name": " Kingsroad ", "overview": "",
             "air_date": "2011-04-24", "runtime": 56, "vote_average": 7.5,
             "still_path": "", "still_width": 0, "still_height": 0},
        ],
    }


def 人物数据() -> dict:
    return {"id": 287, "name": "布拉德·皮特", "also_known_as": ["Brad Pitt", "William Pitt"],
            "profile_path": "/p.jpg",
            "images": {"profiles": [{"file_path": "/p2.jpg", "iso_639_1": None,
                                     "vote_average": 5.5, "vote_count": 3}]}}


# ============================ 测试基类 ============================


class 客户端测试(unittest.TestCase):
    def setUp(self):
        self._临时 = 临时目录()
        self.根 = Path(self._临时.__enter__())
        # 挂个空 handler：退避重试会打 WARNING，测试输出里不想看到它们
        # （assertLogs 会自己挂捕获 handler，不受影响）
        self._空handler = logging.NullHandler()
        logging.getLogger("wangpan.scrape.tmdb").addHandler(self._空handler)

    def tearDown(self):
        logging.getLogger("wangpan.scrape.tmdb").removeHandler(self._空handler)
        self._临时.__exit__(None, None, None)

    def 建(self, 处理, 下载=None, 现在=None, 睡眠=None, **配置项):
        配置项.setdefault("token", "TEST-Bearer-token-123")
        配置项.setdefault("缓存目录", self.根 / "缓存")
        传输 = 假传输(处理)
        客户端 = TMDB客户端(TMDB配置(**配置项), 请求=传输, 下载=下载, 现在=现在, 睡眠=睡眠)
        return 客户端, 传输

    def 缓存文件们(self):
        return sorted((self.根 / "缓存").rglob("*.json"))


# ============================ 配置 ============================


class 配置测试(客户端测试):
    def test_可用_没配置就是假(self):
        self.assertFalse(TMDB配置().可用())
        self.assertFalse(TMDB客户端(TMDB配置(), 请求=固定({})).可用())

    def test_从环境变量读_Bearer优先(self):
        环境 = {"V2_TMDB_TOKEN": "环境令牌", "V2_TMDB_API_KEY": "环境键"}
        with mock.patch.dict(os.environ, 环境, clear=False):
            配置 = TMDB配置.从环境与配置(self.根 / "没有这个文件.json")
        self.assertEqual(配置.token, "环境令牌")
        self.assertEqual(配置.api_key, "环境键")
        self.assertEqual(配置.鉴权方式(), "Bearer")

    def test_从配置文件读(self):
        路径 = self.根 / "刮削.json"
        路径.write_text('{"tmdb": {"token": "文件令牌", "language": "ja-JP",'
                     ' "imageLanguage": "ja", "cacheTtlSeconds": 3600}}', encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=True):
            配置 = TMDB配置.从环境与配置(路径)
        self.assertEqual(配置.token, "文件令牌")
        self.assertEqual(配置.language, "ja-JP")
        self.assertEqual(配置.image_language, "ja")
        self.assertEqual(配置.缓存TTL秒, 3600)

    def test_环境变量优先于文件(self):
        路径 = self.根 / "刮削.json"
        路径.write_text('{"tmdb": {"token": "文件令牌"}}', encoding="utf-8")
        with mock.patch.dict(os.environ, {"V2_TMDB_TOKEN": "环境令牌"}, clear=True):
            配置 = TMDB配置.从环境与配置(路径)
        self.assertEqual(配置.token, "环境令牌")

    def test_配置文件坏了不影响启动(self):
        路径 = self.根 / "刮削.json"
        路径.write_text("{ 这不是 JSON", encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=True):
            配置 = TMDB配置.从环境与配置(路径)
        self.assertEqual(配置.language, "zh-CN")

    def test_默认缓存目录在数据目录下(self):
        self.assertEqual(默认缓存目录().name, "tmdb")
        self.assertEqual(默认缓存目录().parent.name, "缓存")

    def test_配置摘要不含密钥(self):
        客户端, _ = self.建(固定(电影数据()), token="", api_key="TOPSECRET-key-1")
        摘要 = str(客户端.配置摘要())
        self.assertNotIn("TOPSECRET-key-1", 摘要)
        self.assertIn("api_key", 摘要)          # 只说用哪种鉴权

    def test_复制粘贴带进来的杂质会被清掉(self):
        """真机踩点：从网页复制 token 常带上中文引号、尖括号、全角/零宽空格。"""
        路径 = self.根 / "刮削.json"
        路径.write_text(json.dumps({"tmdb": {
            "token": "\u201cabc.DEF-123\u201d", "apiKey": "\u3000key.<456>\u200b"}},
            ensure_ascii=False), encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=True):
            配置 = TMDB配置.从环境与配置(路径)
        self.assertEqual(配置.token, "abc.DEF-123")
        self.assertEqual(配置.api_key, "key.456")
        self.assertEqual(配置.密钥问题(), "")

    def test_中文凭据给一句人话(self):
        """HTTP 头只吃 latin-1；不提前拦就会抛出 'latin-1' codec 那种没人看得懂的话。"""
        配置 = TMDB配置(token="真实令牌")
        self.assertIn("非 ASCII", 配置.密钥问题())
        self.assertEqual(TMDB配置(token="ok-token").密钥问题(), "")
        self.assertEqual(TMDB配置().密钥问题(), "")

    def test_署名文案是官方要求的那句(self):
        self.assertEqual(署名, "This product uses the TMDB API but is not endorsed or "
                          "certified by TMDB.")


# ============================ 请求与鉴权 ============================


class 请求测试(客户端测试):
    def test_Bearer鉴权头(self):
        客户端, 传输 = self.建(固定(电影数据()))
        客户端.取电影("438631")
        本次 = 传输.调用[0]
        self.assertEqual(本次["头"]["Authorization"], "Bearer TEST-Bearer-token-123")
        self.assertNotIn("api_key", 本次["参数"])
        self.assertEqual(本次["方法"], "GET")

    def test_api_key走查询参数(self):
        客户端, 传输 = self.建(固定(电影数据()), token="", api_key="KEY-123")
        客户端.取电影("438631")
        本次 = 传输.调用[0]
        self.assertEqual(本次["参数"]["api_key"], "KEY-123")
        self.assertNotIn("Authorization", 本次["头"])

    def test_搜电影_路径与参数(self):
        客户端, 传输 = self.建(固定({"results": []}))
        客户端.搜电影("沙丘", 2021)
        本次 = 传输.调用[0]
        self.assertEqual(本次["路径"], "/search/movie")
        self.assertEqual(本次["参数"]["query"], "沙丘")
        self.assertEqual(本次["参数"]["year"], "2021")
        self.assertEqual(本次["参数"]["language"], "zh-CN")
        self.assertEqual(本次["参数"]["include_adult"], "false")

    def test_搜剧集_用first_air_date_year(self):
        客户端, 传输 = self.建(固定({"results": []}))
        客户端.搜剧集("Show Name", 2011)
        本次 = 传输.调用[0]
        self.assertEqual(本次["路径"], "/search/tv")
        self.assertEqual(本次["参数"]["first_air_date_year"], "2011")
        self.assertNotIn("year", 本次["参数"])

    def test_没有年份时不传年份参数(self):
        客户端, 传输 = self.建(固定({"results": []}))
        客户端.搜电影("沙丘")
        self.assertNotIn("year", 传输.调用[0]["参数"])

    def test_空标题不发请求(self):
        客户端, 传输 = self.建(固定({"results": []}))
        self.assertEqual(客户端.搜电影("   "), [])
        self.assertEqual(传输.次数, 0)

    def test_取电影_附加数据齐了(self):
        客户端, 传输 = self.建(固定(电影数据()))
        客户端.取电影("438631")
        本次 = 传输.调用[0]
        self.assertEqual(本次["路径"], "/movie/438631")
        附加 = 本次["参数"]["append_to_response"]
        for 片段 in ("credits", "release_dates", "images", "videos", "external_ids"):
            self.assertIn(片段, 附加)

    def test_取剧集_附加数据齐了(self):
        客户端, 传输 = self.建(固定(剧集数据()))
        客户端.取剧集("1399")
        附加 = 传输.调用[0]["参数"]["append_to_response"]
        for 片段 in ("aggregate_credits", "content_ratings", "images", "external_ids", "videos"):
            self.assertIn(片段, 附加)

    def test_图片语言参数是zh不是zhCN(self):
        """官方明确：图片不支持区域变体，而且必须带 null（无语言的通用图占大多数）。"""
        客户端, 传输 = self.建(固定(电影数据()))
        客户端.取电影("438631")
        值 = 传输.调用[0]["参数"]["include_image_language"]
        self.assertEqual(值, "zh,null")
        self.assertNotIn("zh-CN", 值)
        self.assertIn("null", 值)

    def test_图片语言参数_配置成区域码时自动降级(self):
        客户端, _ = self.建(固定(电影数据()), image_language="zh-CN")
        self.assertEqual(客户端.图片语言参数(), "zh,null")
        客户端2, _ = self.建(固定(电影数据()), image_language="zh,null")
        self.assertEqual(客户端2.图片语言参数(), "zh,null")

    def test_配置信息_不带语言参数(self):
        客户端, 传输 = self.建(固定({"images": {"secure_base_url": "https://img.example/t/p/"}}))
        客户端.配置信息()
        self.assertEqual(传输.调用[0]["路径"], "/configuration")
        self.assertNotIn("language", 传输.调用[0]["参数"])


# ============================ 语言回退 ============================


class 语言回退测试(客户端测试):
    def _处理(self, 首选空: bool = True):
        def 处理(本次, 次):
            语言 = 本次["参数"].get("language")
            if 语言 == "en-US":
                return 电影数据("en-US")
            if 首选空:
                return {"id": 438631, "title": "", "overview": "", "original_language": "en"}
            return 电影数据("zh-CN")
        return 处理

    def test_字段为空才补一次(self):
        客户端, 传输 = self.建(self._处理())
        条目 = 客户端.取电影("438631")
        self.assertEqual(传输.次数, 2)                    # 只补一次，不是每次都请求两次
        self.assertEqual(传输.语言们(), ["zh-CN", "en-US"])
        self.assertEqual(条目.标题, "Dune")               # 空的标题被 en-US 补上
        self.assertIn("Arrakis", 条目.简介)

    def test_字段齐了就不补(self):
        客户端, 传输 = self.建(self._处理(首选空=False))
        条目 = 客户端.取电影("438631")
        self.assertEqual(传输.次数, 1)
        self.assertEqual(条目.标题, "沙丘")

    def test_非空字段不被覆盖(self):
        def 处理(本次, 次):
            if 本次["参数"].get("language") == "en-US":
                return 电影数据("en-US")
            return {"id": 1, "title": "中文标题", "overview": "", "original_language": "en"}
        客户端, _ = self.建(处理)
        条目 = 客户端.取电影("1")
        self.assertEqual(条目.标题, "中文标题")           # 中文标题保住
        self.assertIn("Arrakis", 条目.简介)               # 空的简介用英文补

    def test_回退结果也进缓存(self):
        客户端, 传输 = self.建(self._处理())
        客户端.取电影("438631")
        次数 = 传输.次数
        客户端.取电影("438631")
        self.assertEqual(传输.次数, 次数)                 # 两种语言的缓存都在，第二次 0 请求
        self.assertEqual(传输.次数, 2)

    def test_原始语言也要能兜底(self):
        def 处理(本次, 次):
            语言 = 本次["参数"].get("language")
            if 语言 == "ja":
                return {"id": 5, "title": "日本語タイトル", "overview": "日本語", "original_language": "ja"}
            return {"id": 5, "title": "", "overview": "", "original_language": "ja"}
        客户端, 传输 = self.建(处理)
        条目 = 客户端.取电影("5")
        self.assertEqual(传输.语言们(), ["zh-CN", "en-US", "ja"])
        self.assertEqual(条目.标题, "日本語タイトル")

    def test_搜索结果缺标题时也补(self):
        客户端, 传输 = self.建(lambda 本次, 次: {"results": [
            {"id": 1, "title": "Dune" if 本次["参数"]["language"] == "en-US" else "",
             "original_title": "", "release_date": "2021-09-15", "overview": "",
             "popularity": 10.0, "vote_average": 8.0, "vote_count": 100}]})
        候选们 = 客户端.搜电影("Dune", 2021)
        self.assertEqual(传输.次数, 2)
        self.assertEqual(候选们[0].标题, "Dune")

    def test_搜索没结果就不再请求(self):
        客户端, 传输 = self.建(固定({"results": []}))
        客户端.搜电影("不存在的片子")
        self.assertEqual(传输.次数, 1)


# ============================ 限流 ============================


class 限流测试(客户端测试):
    def test_429前两次失败第三次成功(self):
        睡眠: list[float] = []

        def 处理(本次, 次):
            if 次 <= 2:
                raise 请求失败(429, "Too Many Requests")
            return 电影数据()
        客户端, 传输 = self.建(处理, 睡眠=睡眠.append)
        条目 = 客户端.取电影("438631")
        self.assertEqual(传输.次数, 3)
        self.assertEqual(睡眠, [1.0, 2.0])                # 指数退避（没有 Retry-After）
        self.assertEqual(条目.标题, "沙丘")

    def test_听Retry_After(self):
        睡眠: list[float] = []

        def 处理(本次, 次):
            if 次 == 1:
                raise 请求失败(429, "限流", "2")
            return 电影数据()
        客户端, 传输 = self.建(处理, 睡眠=睡眠.append)
        客户端.取电影("438631")
        self.assertEqual(睡眠, [2.0])
        self.assertEqual(传输.次数, 2)

    def test_重试上限三次_再失败就抛(self):
        睡眠: list[float] = []

        def 处理(本次, 次):
            raise 请求失败(429, "一直限流")
        客户端, 传输 = self.建(处理, 睡眠=睡眠.append)
        with self.assertRaises(请求失败) as 上下文:
            客户端.取电影("438631")
        self.assertEqual(传输.次数, 4)                    # 首次 + 3 次重试
        self.assertEqual(len(睡眠), 3)
        self.assertEqual(上下文.exception.状态码, 429)

    def test_普通错误不重试(self):
        客户端, 传输 = self.建(lambda 本次, 次: (_ for _ in ()).throw(RuntimeError("炸了")))
        with self.assertRaises(请求失败):
            客户端.取电影("438631")
        self.assertEqual(传输.次数, 1)

    def test_带code属性的429也认(self):
        """注入的传输可能抛 HTTPError 这类带 code 的异常，也要能识别成限流。"""
        class 带码异常(Exception):
            code = 429
        睡眠: list[float] = []

        def 处理(本次, 次):
            if 次 == 1:
                raise 带码异常("429 或者别的")
            return 电影数据()
        客户端, 传输 = self.建(处理, 睡眠=睡眠.append)
        客户端.取电影("438631")
        self.assertEqual(传输.次数, 2)


# ============================ 缓存 ============================


class 缓存测试(客户端测试):
    def test_第二次不请求(self):
        客户端, 传输 = self.建(固定(电影数据()))
        客户端.取电影("438631")
        客户端.取电影("438631")
        self.assertEqual(传输.次数, 1)
        self.assertTrue(self.缓存文件们(), "缓存应该落盘了")

    def test_缓存按类型与语言分层(self):
        客户端, _ = self.建(固定(电影数据()))
        客户端.取电影("438631")
        相对 = [str(p.relative_to(self.根 / "缓存")) for p in self.缓存文件们()]
        self.assertIn(str(Path("官方") / "movie" / "zh-CN" / "438631.json"), 相对)

    def test_换接口基地址不共用缓存(self):
        """缓存按端点分区：否则"环回假服务/代理"缓存下来的数据会被当成 TMDB 的真数据。"""
        官方, 传输1 = self.建(固定(电影数据()))
        官方.取电影("438631")
        self.assertEqual(传输1.次数, 1)
        代理, 传输2 = self.建(固定(电影数据()), 接口基地址="http://127.0.0.1:9/3")
        代理.取电影("438631")
        self.assertEqual(传输2.次数, 1, "换了端点必须重新取，不能吃上一个端点的缓存")
        相对 = sorted(str(p.relative_to(self.根 / "缓存")) for p in self.缓存文件们())
        self.assertTrue(any(x.startswith("官方") for x in 相对))
        self.assertTrue(any(x.startswith("端点_") for x in 相对))

    def test_缓存坏了当没有(self):
        客户端, 传输 = self.建(固定(电影数据()))
        客户端.取电影("438631")
        传输.调用.clear()
        for 文件 in self.缓存文件们():
            文件.write_text("{半截 JSON", encoding="utf-8")
        客户端.取电影("438631")
        self.assertEqual(传输.次数, 1)

    def test_TTL过期就重新请求(self):
        现在 = [1_700_000_000.0]
        客户端, 传输 = self.建(固定(电影数据()), 缓存TTL秒=7 * 86400,
                          现在=lambda: 现在[0])
        客户端.取电影("438631")
        现在[0] += 6 * 86400
        客户端.取电影("438631")
        self.assertEqual(传输.次数, 1)                    # TTL 内命中
        现在[0] += 2 * 86400
        客户端.取电影("438631")
        self.assertEqual(传输.次数, 2)                    # 过了 8 天：过期

    def test_180天硬过期(self):
        """官方合规：不得缓存超过 6 个月。哪怕把 TTL 配成 400 天，180 天后也必须重取。"""
        现在 = [1_700_000_000.0]
        客户端, 传输 = self.建(固定(电影数据()), 缓存TTL秒=400 * 86400,
                          现在=lambda: 现在[0])
        客户端.取电影("438631")
        现在[0] += 179 * 86400
        客户端.取电影("438631")
        self.assertEqual(传输.次数, 1)
        现在[0] += 2 * 86400                              # 第 181 天
        客户端.取电影("438631")
        self.assertEqual(传输.次数, 2)

    def test_缓存记录里有抓取时间(self):
        import json
        现在 = [1_700_000_000.0]
        客户端, 传输 = self.建(固定(电影数据()), 现在=lambda: 现在[0])
        客户端.取电影("438631")
        记录 = json.loads(self.缓存文件们()[0].read_text(encoding="utf-8"))
        self.assertEqual(记录["抓取时间"], 1_700_000_000.0)
        self.assertEqual(记录["数据"]["id"], 438631)

    def test_TTL设0就是不缓存(self):
        客户端, 传输 = self.建(固定(电影数据()), 缓存TTL秒=0)
        客户端.取电影("438631")
        客户端.取电影("438631")
        self.assertEqual(传输.次数, 2)
        self.assertEqual(self.缓存文件们(), [])

    def test_取分级复用详情缓存(self):
        """分级就在详情里：先取详情再取分级，一次网络都不该多发。"""
        客户端, 传输 = self.建(固定(电影数据()))
        客户端.取电影("438631")
        客户端.取分级(媒体类型.电影, "438631")
        self.assertEqual(传输.次数, 1)

    def test_最长缓存秒是180天(self):
        self.assertEqual(最长缓存秒, 180 * 86400)


# ============================ 密钥不外泄 ============================


class 密钥不外泄测试(客户端测试):
    def test_异常里没有token(self):
        密钥 = "TOPSECRET-Bearer-9f8e7d"

        def 处理(本次, 次):
            raise RuntimeError(f"底层库把请求打出来了：Authorization: Bearer {密钥}")
        客户端, 传输 = self.建(处理, token=密钥)
        with self.assertRaises(请求失败) as 上下文:
            客户端.取电影("438631")
        self.assertNotIn(密钥, str(上下文.exception))
        self.assertIn("***", str(上下文.exception))

    def test_异常里没有api_key(self):
        密钥 = "TOPSECRET-apikey-123"
        客户端, 传输 = self.建(lambda 本次, 次: (_ for _ in ()).throw(
            RuntimeError(f"GET https://api.themoviedb.org/3/movie/1?api_key={密钥}")),
            token="", api_key=密钥)
        with self.assertRaises(请求失败) as 上下文:
            客户端.取电影("1")
        文本 = str(上下文.exception)
        self.assertNotIn(密钥, 文本)
        self.assertIn("api_key=***", 文本)

    def test_日志里没有token(self):
        密钥 = "TOPSECRET-Bearer-abc123"

        def 处理(本次, 次):
            if 次 == 1:
                raise 请求失败(429, "限流")
            return 电影数据()
        客户端, 传输 = self.建(处理, token=密钥, 睡眠=lambda 秒: None)
        with self.assertLogs("wangpan.scrape.tmdb", level="DEBUG") as 日志:
            客户端.取电影("438631")
        文本 = "\n".join(日志.output)
        self.assertNotIn(密钥, 文本)
        self.assertIn("429", 文本)

    def test_网络错误消息里没有完整URL(self):
        def 处理(本次, 次):
            raise RuntimeError(f"连接失败：{本次['地址']}?api_key=秘密")
        客户端, 传输 = self.建(处理, token="", api_key="SECRET")
        with self.assertRaises(请求失败) as 上下文:
            客户端.取电影("1")
        self.assertNotIn("秘密", str(上下文.exception))


# ============================ 详情字段映射 ============================


class 电影详情测试(客户端测试):
    def test_字段映射(self):
        客户端, _ = self.建(固定(电影数据()))
        条目 = 客户端.取电影("438631")
        self.assertEqual(条目.类型, 媒体类型.电影)
        self.assertEqual(条目.标题, "沙丘")
        self.assertEqual(条目.原名, "Dune")
        self.assertEqual(条目.年份, 2021)
        self.assertEqual(条目.时长分钟, 155)              # 官方单位就是分钟
        self.assertEqual(条目.评分, 7.8)                  # 0–10
        self.assertEqual(条目.评分票数, 12000)
        self.assertEqual(条目.标签, ["科幻", "冒险"])
        self.assertEqual(条目.状态, "Released")
        self.assertEqual(条目.原始语言, "en")
        self.assertEqual(条目.制片公司, ["Legendary Pictures"])

    def test_外部ID_电影有imdb(self):
        客户端, _ = self.建(固定(电影数据()))
        条目 = 客户端.取电影("438631")
        self.assertEqual(条目.外部ID["imdb"], "tt1160419")
        self.assertEqual(条目.外部ID["tmdb"], "438631")

    def test_热度不当评分(self):
        客户端, _ = self.建(固定(电影数据()))
        条目 = 客户端.取电影("438631")
        self.assertEqual(条目.评分, 7.8)
        self.assertNotEqual(条目.评分, 321.5)             # popularity 不是评分

    def test_图片选择_中文优先(self):
        客户端, _ = self.建(固定(电影数据()))
        条目 = 客户端.取电影("438631")
        self.assertEqual(条目.海报.远端路径, "/zh.jpg")   # 中文海报虽然评分低也优先
        self.assertEqual(条目.海报.语言, "zh")
        self.assertEqual(条目.背景.远端路径, "/bd.jpg")
        self.assertEqual(条目.标志.远端路径, "/logo.png")
        self.assertEqual(条目.海报.宽, 1000)

    def test_演员按order排序(self):
        客户端, _ = self.建(固定(电影数据()))
        条目 = 客户端.取电影("438631")
        演员 = 条目.演员()
        self.assertEqual([x.人物.名字 for x in 演员], ["演员甲", "演员乙"])   # order 0 在前
        self.assertEqual(演员[0].角色, "角色甲")
        self.assertEqual(演员[0].排序, 0)

    def test_同一个人多职务取优先级高的工种(self):
        客户端, _ = self.建(固定(电影数据()))
        条目 = 客户端.取电影("438631")
        self.assertEqual([x.人物.名字 for x in 条目.导演()], ["导演兼制片"])
        编剧 = [x for x in 条目.参演们 if x.人物.名字 == "编剧"]
        self.assertEqual(编剧[0].工种.value, "writer")


class 剧集详情测试(客户端测试):
    def test_字段映射(self):
        客户端, _ = self.建(固定(剧集数据()))
        条目 = 客户端.取剧集("1399")
        self.assertEqual(条目.类型, 媒体类型.剧集)
        self.assertEqual(条目.标题, "剧名")
        self.assertEqual(条目.原名, "Show Name")
        self.assertEqual(条目.年份, 2011)
        self.assertEqual(条目.时长分钟, 57)               # 单集时长
        self.assertEqual(条目.状态, "Ended")

    def test_外部ID_剧集只有tvdb(self):
        """官方事实：剧集详情里没有 imdb_id，只有 tvdb_id。"""
        客户端, _ = self.建(固定(剧集数据()))
        条目 = 客户端.取剧集("1399")
        self.assertEqual(条目.外部ID["tvdb"], "121361")
        self.assertNotIn("imdb", 条目.外部ID)

    def test_季号只看season_number(self):
        """`seasons[].name` 会随语言变（Staffel 1），季号必须用 season_number。"""
        客户端, _ = self.建(固定(剧集数据()))
        条目 = 客户端.取剧集("1399")
        self.assertEqual([s.季号 for s in 条目.季们], [0, 1, 2])
        self.assertEqual([s.集数 for s in 条目.季们], [3, 10, 10])
        self.assertEqual(条目.季们[1].标题, "Staffel 1")

    def test_aggregate_credits结构(self):
        客户端, _ = self.建(固定(剧集数据("aggregate")))
        条目 = 客户端.取剧集("1399")
        演员 = 条目.演员()
        self.assertEqual([x.人物.名字 for x in 演员], ["全剧演员甲", "全剧演员乙"])
        self.assertEqual(演员[0].角色, "角色甲")          # roles[0].character
        self.assertEqual(演员[0].集数, 67)                # total_episode_count
        self.assertEqual([x.人物.名字 for x in 条目.导演()], ["导演甲"])
        制片 = [x for x in 条目.参演们 if x.工种.value == "producer"]
        self.assertEqual(制片[0].人物.名字, "制片人")

    def test_credits结构也能解析(self):
        客户端, _ = self.建(固定(剧集数据("credits")))
        条目 = 客户端.取剧集("1399")
        演员 = 条目.演员()
        self.assertEqual([x.人物.名字 for x in 演员], ["最新季演员甲", "最新季演员乙"])
        self.assertEqual(演员[0].角色, "角色甲")          # character 字段
        self.assertEqual([x.人物.名字 for x in 条目.导演()], ["导演甲"])
        作曲 = [x for x in 条目.参演们 if x.工种.value == "composer"]
        self.assertEqual(作曲[0].人物.名字, "作曲甲")

    def test_aggregate优先于credits(self):
        """剧集全剧阵容要用 aggregate_credits（credits 只有最新一季）。"""
        数据 = 剧集数据("aggregate")
        数据["credits"] = 剧集数据("credits")["credits"]
        客户端, _ = self.建(固定(数据))
        条目 = 客户端.取剧集("1399")
        self.assertIn("全剧演员甲", [x.人物.名字 for x in 条目.演员()])
        self.assertNotIn("最新季演员甲", [x.人物.名字 for x in 条目.演员()])

    def test_取季_集列表(self):
        客户端, 传输 = self.建(固定(季数据()))
        季 = 客户端.取季("1399", 1)
        self.assertEqual(传输.调用[0]["路径"], "/tv/1399/season/1")
        self.assertEqual(季.季号, 1)
        self.assertEqual(季.标题, "第 1 季")
        self.assertEqual(季.集数, 2)
        self.assertEqual([e.编号 for e in 季.集们], ["S01E01", "S01E02"])
        self.assertEqual(季.集们[0].标题, "凛冬将至")
        self.assertEqual(季.集们[0].时长分钟, 62)
        self.assertEqual(季.集们[0].剧照.远端路径, "/e1.jpg")
        self.assertEqual(季.集们[1].剧照, None)
        self.assertEqual(季.海报.远端路径, "/season1.jpg")

    def test_取人物_头像(self):
        客户端, 传输 = self.建(固定(人物数据()))
        人物 = 客户端.取人物("287")
        self.assertEqual(传输.调用[0]["路径"], "/person/287")
        self.assertEqual(人物.标识, "287")
        self.assertEqual(人物.名字, "布拉德·皮特")
        self.assertEqual(人物.原名, "Brad Pitt")          # 用 also_known_as 兜底
        self.assertEqual(人物.头像.远端路径, "/p2.jpg")


# ============================ 分级 ============================


class 分级测试(客户端测试):
    def test_电影_过滤空串并按type优先级(self):
        客户端, _ = self.建(固定(电影数据()))
        分级们 = 客户端.取分级(媒体类型.电影, "438631")
        表 = {g.国家: g.值 for g in 分级们}
        self.assertEqual(表["US"], "PG-13")               # type 3 影院 > type 5 实物；空串被过滤
        self.assertNotIn("FR", 表)                        # 只有空白串的国家直接不要

    def test_电影_没有CN时回退HK_TW_US(self):
        客户端, _ = self.建(固定(电影数据()))
        分级们 = 客户端.取分级(媒体类型.电影, "438631")
        国家们 = [g.国家 for g in 分级们]
        self.assertNotIn("CN", 国家们)                    # TMDB 就没有 CN
        self.assertEqual(国家们[:3], ["HK", "TW", "US"])
        条目 = 客户端.取电影("438631")
        self.assertEqual(条目.取分级().可读(), "HK:IIA")  # 模型的取分级按 CN→HK→TW→US 回退

    def test_电影_没有任何分级就是空列表(self):
        数据 = 电影数据()
        数据["release_dates"] = {"results": [{"iso_3166_1": "US", "release_dates": [
            {"certification": "", "type": 3}]}]}
        客户端, _ = self.建(固定(数据))
        self.assertEqual(客户端.取分级(媒体类型.电影, "438631"), [])

    def test_剧集_用content_ratings(self):
        客户端, _ = self.建(固定(剧集数据()))
        分级们 = 客户端.取分级(媒体类型.剧集, "1399")
        表 = {g.国家: g.值 for g in 分级们}
        self.assertEqual(表["US"], "TV-MA")
        self.assertEqual(表["HK"], "M")
        self.assertNotIn("FR", 表)
        self.assertEqual([g.国家 for g in 分级们][:2], ["HK", "US"])

    def test_剧集详情里也带分级(self):
        客户端, _ = self.建(固定(剧集数据()))
        条目 = 客户端.取剧集("1399")
        self.assertEqual(条目.取分级().可读(), "HK:M")


# ============================ 图片 ============================


class 图片测试(客户端测试):
    #: 假的图片字节（bytes 字面量里不能写中文，所以单独放一个常量）
    图片字节 = b"\x89PNG-fake-image"

    def test_图片地址_默认基地址(self):
        客户端, _ = self.建(固定(电影数据()))
        self.assertEqual(客户端.图片地址("/abc.jpg", "w500"),
                     "https://image.tmdb.org/t/p/w500/abc.jpg")
        self.assertEqual(客户端.图片地址("abc.jpg"), "https://image.tmdb.org/t/p/w500/abc.jpg")
        self.assertEqual(客户端.图片地址("", "w500"), "")

    def test_图片地址_用configuration里的基地址(self):
        客户端, _ = self.建(固定({"images": {"secure_base_url": "https://img.example.com/t/p/"}}))
        客户端.配置信息()
        self.assertEqual(客户端.图片地址("/x.png", "w185"),
                     "https://img.example.com/t/p/w185/x.png")

    def test_图片地址_完整URL原样返回(self):
        客户端, _ = self.建(固定(电影数据()))
        self.assertEqual(客户端.图片地址("https://other.example/a.jpg"), "https://other.example/a.jpg")

    def test_图片尺寸表来自configuration(self):
        客户端, _ = self.建(固定({"images": {"secure_base_url": "https://img.example.com/t/p/",
                                       "poster_sizes": ["w92", "w500", "original"]}}))
        信息 = 客户端.配置信息()
        self.assertIn("w500", 信息["images"]["poster_sizes"])

    def test_取图片_按语言排序(self):
        客户端, _ = self.建(固定(电影数据()))
        图片们 = 客户端.取图片(媒体类型.电影, "438631", "poster")
        self.assertEqual([x.远端路径 for x in 图片们], ["/zh.jpg", "/none.jpg", "/en.jpg"])

    def test_取图片_全部种类(self):
        客户端, _ = self.建(固定(电影数据()))
        图片们 = 客户端.取图片(媒体类型.电影, "438631")
        self.assertEqual(len(图片们), 5)

    def test_下载图片(self):
        下载过: list[str] = []
        客户端, _ = self.建(固定({"images": {"secure_base_url": "https://img.example.com/t/p/"}}),
                        下载=lambda 地址: (下载过.append(地址), self.图片字节)[1])
        客户端.配置信息()
        落点 = self.根 / "图" / "海报.jpg"
        self.assertEqual(客户端.下载图片("/p.jpg", 落点, "w500"), 落点)
        self.assertEqual(下载过, ["https://img.example.com/t/p/w500/p.jpg"])
        self.assertEqual(落点.read_bytes(), self.图片字节)

    def test_下载图片_已存在就不重复下(self):
        下载过: list[str] = []
        客户端, _ = self.建(固定(电影数据()), 下载=lambda 地址: 下载过.append(地址) or self.图片字节)
        落点 = self.根 / "已有.jpg"
        落点.write_bytes(b"old-image")
        self.assertEqual(客户端.下载图片("/a.jpg", 落点), 落点)
        self.assertEqual(下载过, [])

    def test_下载图片_失败不抛异常(self):
        def 下载(地址):
            raise RuntimeError("网络断了")
        客户端, _ = self.建(固定(电影数据()), 下载=下载)
        落点 = self.根 / "下不下来.jpg"
        with self.assertLogs("wangpan.scrape.tmdb", level="WARNING"):
            self.assertIsNone(客户端.下载图片("/a.jpg", 落点))
        self.assertFalse(落点.exists())


if __name__ == "__main__":
    unittest.main()
