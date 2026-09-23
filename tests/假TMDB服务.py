"""跑在环回地址上的"假 TMDB"：**真 HTTP、真 urllib、真鉴权头**。

为什么需要它（而不是注入一个 Python 假对象）
==========================================
`TMDB客户端` 的默认传输是 `urllib`（`_默认请求`），而注入式假客户端会把这一层
整个绕过去。于是"JSON 解析对不对""鉴权头带没带""HTTP 状态码怎么处理""图片 CDN
的 URL 怎么拼"这些**真机上最容易坏的地方一个都没被覆盖**。

这一份 stub 顶替的是**网络对面**：
* 只实现我们真的会用到的那几个端点，返回**跟官方同形状**的 JSON；
* 鉴权必须带（``Authorization: Bearer …`` 或 ``api_key`` 查询参数），否则回 401
  —— 这样"客户端到底有没有把凭据发出去"是可验证的；
* 图片走同一个服务器的 ``/t/p/<尺寸><文件名>``，于是海报/背景/头像链路
  （``/configuration`` → 基地址 → 下载 → 落盘）也是真跑的。

真机验证（`工具/真机_待确认队列.py`）和单测都用它；
真实 TMDB API 在部分网络下到不了（见研究文档），但**这条链路必须随时能验**。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

__all__ = ["假TMDB服务", "默认候选"]


#: 默认造出来的候选（**故意含糊**：三部同名片、热度咬得很紧 → 自动匹配不敢定）
默认候选 = (
    {"id": 101, "title": "沙丘", "original_title": "Dune", "date": "2021-09-15",
     "popularity": 900.5, "vote_average": 8.1, "overview": "厄拉科斯：香料与沙虫。"},
    {"id": 202, "title": "沙丘 2021", "original_title": "Dune: Part One",
     "date": "2021-10-22", "popularity": 898.2, "vote_average": 8.0,
     "overview": "同一年的另一部《沙丘》—— 用来制造「拿不准」。"},
    {"id": 303, "title": "沙丘之子", "original_title": "Children of Dune",
     "date": "2003-03-16", "popularity": 120.0, "vote_average": 6.9,
     "overview": "迷你剧，片名很像。"},
)


def _图片文件(图片目录: Path, 名字: str) -> Optional[bytes]:
    路径 = 图片目录 / 名字
    return 路径.read_bytes() if 路径.is_file() else None


class _处理(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # 保持连接：一次刮削要发十几个请求
    #: 由 :class:`假TMDB服务` 在启动前注入（每个服务一份，互不干扰）
    服务: "假TMDB服务" = None            # type: ignore[assignment]

    # ---- 基础 ----
    def log_message(self, 格式, *参数):        # noqa: A003 - 静音（测试输出要干净）
        self.服务.记录.append(self.path)

    def _发JSON(self, 数据: dict, 码: int = 200) -> None:
        体 = json.dumps(数据, ensure_ascii=False).encode("utf-8")
        self.send_response(码)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(体)))
        self.end_headers()
        self.wfile.write(体)

    def _发字节(self, 体: bytes, 类型: str = "image/jpeg", 码: int = 200) -> None:
        self.send_response(码)
        self.send_header("Content-Type", 类型)
        self.send_header("Content-Length", str(len(体)))
        self.end_headers()
        self.wfile.write(体)

    def do_GET(self) -> None:                 # noqa: N802 - BaseHTTPRequestHandler 约定
        try:
            self._路由()
        except BrokenPipeError:               # 客户端提前断开（测试里很常见）
            pass

    # ---- 路由 ----
    def _路由(self) -> None:
        解析 = urlparse(self.path)
        路径 = unquote(解析.path)
        查询 = {k: v[0] for k, v in parse_qs(解析.query).items()}
        self.服务.请求数 += 1
        # 图片 CDN（不带鉴权，跟官方一致）
        if 路径.startswith("/t/p/"):
            名字 = 路径[len("/t/p/"):]
            尺寸, _, 文件 = 名字.partition("/")
            体 = _图片文件(self.服务.图片目录, f"{尺寸}/{文件}") or \
                _图片文件(self.服务.图片目录, 文件)
            if 体 is None:
                self._发JSON({"status_message": "图不存在"}, 404)
                return
            self._发字节(体)
            return
        if not self._有凭据(查询):
            self._发JSON({"status_code": 7,
                        "status_message": "Invalid API key: 需要 Authorization 或 api_key"}, 401)
            return
        if 路径 == "/3/configuration":
            self._发JSON({"images": {"secure_base_url": self.服务.地址 + "/t/p/",
                                 "base_url": self.服务.地址 + "/t/p/",
                                 "poster_sizes": ["w185", "w342", "w500"],
                                 "backdrop_sizes": ["w780", "w1280"],
                                 "profile_sizes": ["w185", "h632"],
                                 "still_sizes": ["w300"]}})
            return
        if 路径 in ("/3/search/movie", "/3/search/tv"):
            是剧 = 路径.endswith("/tv")
            self._发JSON({"page": 1, "total_results": len(self.服务.候选),
                       "results": [self._搜索条(x, 是剧) for x in self.服务.候选]})
            return
        段 = [x for x in 路径.split("/") if x]
        if len(段) >= 3 and 段[1] == "movie" and 段[2].isdigit():
            self._发JSON(self._电影详情(int(段[2])))
            return
        if len(段) >= 3 and 段[1] == "tv" and 段[2].isdigit():
            if len(段) >= 5 and 段[3] == "season" and 段[4].isdigit():
                self._发JSON(self._季详情(int(段[2]), int(段[4])))
                return
            self._发JSON(self._剧详情(int(段[2])))
            return
        if len(段) >= 3 and 段[1] == "person" and 段[2].isdigit():
            self._发JSON(self._人物详情(int(段[2])))
            return
        self._发JSON({"status_message": f"没实现的路径 {路径}"}, 404)

    def _有凭据(self, 查询: dict) -> bool:
        头 = self.headers.get("Authorization", "")
        return bool(查询.get("api_key")) or 头.startswith("Bearer ")

    # ---- 数据 ----
    def _搜索条(self, 候选: dict, 是剧: bool) -> dict:
        公共 = {"id": 候选["id"], "popularity": 候选["popularity"],
              "vote_average": 候选["vote_average"], "vote_count": 1200,
              "original_language": "en", "overview": 候选["overview"],
              "poster_path": f"/p{候选['id']}.jpg"}
        if 是剧:
            return {**公共, "name": 候选["title"], "original_name": 候选["original_title"],
                    "first_air_date": 候选["date"]}
        return {**公共, "title": 候选["title"], "original_title": 候选["original_title"],
                "release_date": 候选["date"]}

    def _图组(self, 编号: int) -> dict:
        return {"posters": [
            {"file_path": f"/p{编号}.jpg", "iso_639_1": "zh", "vote_average": 8.0,
             "vote_count": 20, "width": 500, "height": 750, "aspect_ratio": 0.667},
            {"file_path": f"/p{编号}_en.jpg", "iso_639_1": "en", "vote_average": 5.0,
             "vote_count": 10, "width": 500, "height": 750}],
            "backdrops": [{"file_path": f"/b{编号}.jpg", "iso_639_1": "", "vote_average": 7.0,
                           "vote_count": 8, "width": 1280, "height": 720}],
            "logos": []}

    def _演职员(self, 编号: int) -> dict:
        return {"cast": [
            {"id": 5001, "name": "提莫西·查拉梅", "original_name": "Timothée Chalamet",
             "character": "Paul", "order": 0, "profile_path": "/a5001.jpg"},
            {"id": 5002, "name": "丽贝卡·弗格森", "original_name": "Rebecca Ferguson",
             "character": "Jessica", "order": 1, "profile_path": "/a5002.jpg"}],
            "crew": [{"id": 6001, "name": "丹尼斯·维伦纽瓦", "job": "Director",
                      "department": "Directing", "profile_path": "/a6001.jpg"}]}

    def _电影详情(self, 编号: int) -> dict:
        候选 = next((x for x in self.服务.候选 if x["id"] == 编号), 默认候选[0])
        return {"id": 编号, "title": 候选["title"], "original_title": 候选["original_title"],
                "release_date": 候选["date"], "overview": 候选["overview"],
                "runtime": 155, "vote_average": 候选["vote_average"], "vote_count": 9800,
                "status": "Released", "original_language": "en",
                "imdb_id": f"tt{编号:07d}",
                "genres": [{"id": 878, "name": "科幻"}, {"id": 12, "name": "冒险"}],
                "production_companies": [{"id": 1, "name": "传奇影业"}],
                "images": self._图组(编号), "credits": self._演职员(编号),
                "release_dates": {"results": [
                    {"iso_3166_1": "US", "release_dates": [{"certification": "PG-13"}]},
                    {"iso_3166_1": "HK", "release_dates": [{"certification": "IIA"}]}]}}

    def _剧详情(self, 编号: int) -> dict:
        候选 = next((x for x in self.服务.候选 if x["id"] == 编号), 默认候选[0])
        return {"id": 编号, "name": 候选["title"], "original_name": 候选["original_title"],
                "first_air_date": 候选["date"], "overview": 候选["overview"],
                "episode_run_time": [48], "vote_average": 8.4, "vote_count": 5000,
                "status": "Returning Series", "original_language": "en",
                "external_ids": {"tvdb_id": f"tv{编号}"},
                "genres": [{"id": 10765, "name": "科幻"}],
                "seasons": [{"season_number": 1, "name": "第 1 季", "overview": "",
                             "episode_count": 3, "air_date": "2021-01-01",
                             "poster_path": f"/p{编号}_s1.jpg"}],
                "images": self._图组(编号), "credits": self._演职员(编号),
                "content_ratings": {"results": [
                    {"iso_3166_1": "US", "rating": "TV-14"},
                    {"iso_3166_1": "HK", "rating": "M"}]}}

    def _季详情(self, 剧编号: int, 季号: int) -> dict:
        return {"season_number": 季号, "name": f"第 {季号} 季", "overview": "季度简介",
                "air_date": "2021-01-01", "episodes": [
                    {"episode_number": i, "name": f"第 {i} 集", "overview": f"第 {i} 集简介",
                     "air_date": f"2021-01-{i:02d}", "runtime": 48, "vote_average": 8.0,
                     "still_path": f"/s{剧编号}_{i}.jpg"} for i in (1, 2, 3)],
                "images": {"posters": [{"file_path": f"/p{剧编号}_s{季号}.jpg",
                                      "iso_639_1": "zh", "vote_average": 7.0,
                                      "vote_count": 5, "width": 342, "height": 513}]}}

    def _人物详情(self, 编号: int) -> dict:
        return {"id": 编号, "name": "某演员", "original_name": "Someone",
                "biography": "简介", "profile_path": f"/a{编号}.jpg",
                "images": {"profiles": [{"file_path": f"/a{编号}.jpg", "iso_639_1": "",
                                       "vote_average": 6.0, "vote_count": 3,
                                       "width": 185, "height": 278}]}}


class 假TMDB服务:
    """环回口上的假 TMDB（线程里跑；``with`` 用也行）。"""

    def __init__(self, 图片目录: Path, 候选: Optional[list[dict]] = None) -> None:
        self.图片目录 = Path(图片目录)
        self.候选 = [dict(x) for x in (候选 or 默认候选)]
        self.记录: list[str] = []
        self.请求数 = 0
        self._服务器: Optional[ThreadingHTTPServer] = None
        self._线程: Optional[threading.Thread] = None
        self.端口 = 0
        self.地址 = ""

    # ---- 生命周期 ----
    def 启动(self) -> str:
        if self._服务器 is not None:
            return self.地址
        服务 = self

        class 带服务(_处理):
            pass

        带服务.服务 = 服务            # 在类体里写 `服务 = 服务` 会取不到闭包，所以注入
        self._服务器 = ThreadingHTTPServer(("127.0.0.1", 0), 带服务)   # 0 = 系统给空闲口
        self._服务器.daemon_threads = True
        self._线程 = threading.Thread(target=self._服务器.serve_forever,
                                 name="假TMDB", daemon=True)
        self._线程.start()
        self.端口 = int(self._服务器.server_address[1])
        self.地址 = f"http://127.0.0.1:{self.端口}"
        return self.地址

    def 停止(self) -> None:
        if self._服务器 is None:
            return
        self._服务器.shutdown()
        self._服务器.server_close()
        if self._线程 is not None:
            self._线程.join(timeout=3.0)
        self._服务器, self._线程 = None, None

    @property
    def 接口基地址(self) -> str:
        return f"{self.地址}/3"

    def __enter__(self) -> "假TMDB服务":
        self.启动()
        return self

    def __exit__(self, *_异常) -> None:
        self.停止()

    # ---- 造图（真 JPEG，落盘后由图片缓存/Qt 读）----
    def 造图(self) -> None:
        from PySide6.QtGui import QColor, QImage
        色表 = {"p101.jpg": (150, 90, 40), "p202.jpg": (40, 90, 150),
              "p303.jpg": (90, 150, 40), "p101_en.jpg": (60, 60, 60),
              "p202_en.jpg": (70, 70, 70), "p303_en.jpg": (80, 80, 80),
              "b101.jpg": (20, 30, 50), "b202.jpg": (30, 20, 50),
              "b303.jpg": (50, 30, 20), "p101_s1.jpg": (120, 80, 40),
              "p202_s1.jpg": (40, 80, 120), "p303_s1.jpg": (80, 120, 40),
              "a5001.jpg": (200, 160, 120), "a5002.jpg": (180, 140, 200),
              "a6001.jpg": (120, 160, 200), "s101_1.jpg": (100, 100, 160)}
        for 名, 色 in 色表.items():
            宽, 高 = (500, 750) if 名.startswith("p") or 名.startswith("a") else (1280, 720)
            if 名.startswith("s"):
                宽, 高 = 300, 169
            路径 = self.图片目录 / "w500" / 名
            路径.parent.mkdir(parents=True, exist_ok=True)
            图 = QImage(宽, 高, QImage.Format.Format_RGB32)
            图.fill(QColor(*色))
            图.save(str(路径))
            for 尺寸 in ("w185", "w342", "w1280", "w300", "h632"):
                (self.图片目录 / 尺寸).mkdir(parents=True, exist_ok=True)
                (self.图片目录 / 尺寸 / 名).write_bytes(路径.read_bytes())
