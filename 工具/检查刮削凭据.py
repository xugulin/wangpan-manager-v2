#!/usr/bin/env python3
"""凭据与连通性自检（刮削 / 弹幕）：一眼看出"缺什么、哪儿不通、怎么办"。

用法：
    运行环境/venv/bin/python 工具/检查刮削凭据.py

它会依次检查：
1. **凭据**：TMDB（Bearer 或 v3 Key）、弹弹play（AppId + AppSecret）读到了没有（**只报有没有，不打印值**）；
2. **代理**：配了 ``V2_TMDB_PROXY`` / ``数据/刮削.json`` 的 ``proxy`` 没有、连不连得上
   （本机 v2rayN 的 SOCKS5 口是 10808；**直连不通但代理通**就是这种情况）；
3. **TMDB API**：直连与经代理各试一次（"直连不通、代理通"要如实报出来）；
4. **TMDB 图片 CDN**：能不能下图（这个和 API 是两个域名，经常一个通一个不通）；
5. **弹弹play**：带签名请求一次公开接口，看是不是 403（说明需要 AppId）。

这条自检很重要：实测遇到过"图片 CDN 通、API 不通"（DNS 被劫持到无关 IP），
只看报错会觉得是 Key 写错了；后来这台机器**直连两个域名都不通、走本机 SOCKS5 代理才通**，
所以现在两个路径都测、都报。
"""

from __future__ import annotations

import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

项目根 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(项目根))
from wangpan.控制台 import 修控制台          # noqa: E402

修控制台()

结论: list[str] = []


def 记(文本: str, 好: bool | None = None) -> None:
    标 = "✅" if 好 is True else ("❌" if 好 is False else "•")
    print(f"{标} {文本}", flush=True)
    结论.append(f"{标} {文本}")


def 查DNS(域名: str) -> list[str]:
    try:
        v4 = [x[4][0] for x in socket.getaddrinfo(域名, 443, socket.AF_INET)]
        return sorted(set(v4))
    except Exception:  # noqa: BLE001
        return []


def 查HTTP(地址: str, 超时: float = 12.0, 头: dict | None = None) -> tuple[int, str]:
    try:
        请求 = urllib.request.Request(地址, headers=头 or {"User-Agent": "wangpan-v2/2.0"})
        with urllib.request.urlopen(请求, timeout=超时) as 回应:
            return int(getattr(回应, "status", 200) or 200), ""
    except urllib.error.HTTPError as 错:
        return int(错.code), str(错.headers.get("X-Error-Message") or "")
    except Exception as 错:  # noqa: BLE001
        return 0, str(错)


def main() -> int:
    记(f"代理环境变量：{ {k: v for k, v in os.environ.items() if 'proxy' in k.lower()} or '（没有）'}")

    # ---- 1) TMDB 凭据 ----
    try:
        from wangpan.scrape.tmdb import TMDB客户端, TMDB配置
        配置 = TMDB配置.从环境与配置()
        有凭据 = bool(配置.token or 配置.api_key)
        记(f"TMDB 凭据：Bearer {'有' if 配置.token else '无'}｜v3 Key {'有' if 配置.api_key else '无'}"
          f"｜语言 {配置.language}｜图片语言 {配置.image_language}", 有凭据)
        if not 有凭据:
            记("  怎么办：到 https://www.themoviedb.org/settings/api 申请，写进 "
              "数据/刮削.json（{\"tmdb\":{\"token\":\"…\"}}）或设 V2_TMDB_TOKEN")
    except Exception as 错:  # noqa: BLE001
        记(f"TMDB 凭据读取失败：{错}", False)

    # ---- 2) TMDB API 可达性 ----
    ip们 = 查DNS("api.themoviedb.org")
    记(f"api.themoviedb.org 解析到：{ip们 or '（解析失败）'}")
    码, 详情 = 查HTTP("https://api.themoviedb.org/3/configuration?api_key=probe")
    直连通 = 码 in (200, 401, 404)      # 401/404 也算通（说明连上了，只是参数不对）
    记(f"TMDB API 直连：HTTP {码 or '连接失败'}"
      + (f"（{详情}）" if 详情 else ""), 直连通)

    # ---- 3) 代理（真机实测：直连两个域名都不通，走本机 SOCKS5 就通）----
    经代理通 = False
    代理对象 = None
    try:
        from wangpan.scrape.代理 import 代理错误, 解析代理, 经代理取
        代理文本 = (配置.代理 if "配置" in dir() else "") or ""
        代理对象 = 解析代理(代理文本) if 代理文本 else None
        if 代理对象 is None:
            记(f"TMDB 代理：没配（V2_TMDB_PROXY / 数据/刮削.json 的 proxy；"
              f"本机 v2rayN 的 SOCKS5 口通常是 socks5://127.0.0.1:10808）")
        else:
            记(f"TMDB 代理：{代理对象.可读()}")
            try:
                应答 = 经代理取("https://api.themoviedb.org/3/configuration?api_key=probe",
                            超时=12.0, 代理=代理对象)
                经代理通 = 应答.状态码 in (200, 401, 404)
                记(f"TMDB API 经代理：HTTP {应答.状态码}", 经代理通)
            except 代理错误 as 错:
                记(f"TMDB API 经代理失败：{错}", False)
                if not 直连通:
                    记("  怎么办：代理也不通时，检查代理程序是否在跑、端口对不对"
                      "（v2rayN 默认 SOCKS 口 10808）")
    except Exception as 错:  # noqa: BLE001
        记(f"代理检查异常：{错}", False)
    if not 直连通 and not 经代理通:
        记("  怎么办：这台机器连不上 TMDB 的 **API 域名**（常见原因：需要代理，"
          "或 DNS 被劫持到无关 IP）。两种配法：\n"
          "      V2_TMDB_PROXY=socks5://127.0.0.1:10808   ← 本客户端自己实现的 SOCKS5（推荐）\n"
          "      export https_proxy=http://127.0.0.1:7890  ← 标准环境变量（HTTP 代理）\n"
          "    （图片 CDN 与 API 是两个域名，可能一个通一个不通）")

    # ---- 4) 图片 CDN（直连 + 代理各试一次）----
    码图, _ = 查HTTP("https://image.tmdb.org/t/p/w92/1E5baAaEse26fej7uHcjOgEE2t2.jpg")
    记(f"TMDB 图片 CDN 直连：HTTP {码图 or '连接失败'}", 码图 == 200)
    if 码图 != 200 and 代理对象 is not None:
        try:
            from wangpan.scrape.代理 import 经代理取
            应答 = 经代理取("https://image.tmdb.org/t/p/w92/1E5baAaEse26fej7uHcjOgEE2t2.jpg",
                        超时=12.0, 代理=代理对象)
            记(f"TMDB 图片 CDN 经代理：HTTP {应答.状态码}（{len(应答.体)} 字节）",
              应答.状态码 == 200)
        except Exception as 错:  # noqa: BLE001
            记(f"TMDB 图片 CDN 经代理失败：{str(错)[:120]}", False)
    elif 码图 != 200:
        记("  怎么办：图片 CDN 不通时海报墙只有占位色块，但资料仍能入库")

    # ---- 4) 弹弹play ----
    try:
        from wangpan.danmaku.源 import 建默认源
        from wangpan.danmaku.源.接口 import 素材信息
        源们 = [x for x in 建默认源() if getattr(x, "能匹配", lambda: False)()]
        源 = 源们[0] if 源们 else None
        if 源 is None:
            记("弹弹play 源没建起来", False)
        else:
            有签 = getattr(源, "能签名", lambda: False)()
            记(f"弹弹play 凭据：AppId {'有' if getattr(源, 'app_id', '') else '无'}"
              f"｜AppSecret {'有' if getattr(源, 'app_secret', '') else '无'}"
              f"｜能签名 {有签}", 有签)
            if not 有签:
                记("  怎么办：AppId 在 https://dev.dandanplay.com 的【应用管理】页；"
                  "写进 数据/弹幕源.json（{\"dandanplay\":{\"appId\":\"…\",\"appSecret\":\"…\"}}）"
                  "或设 V2_DANDANPLAY_APP_ID / V2_DANDANPLAY_APP_SECRET")
            try:
                结果 = 源.匹配(素材信息(文件名="葬送的芙莉莲 第01话.mp4", 时长秒=1440))
                记(f"弹弹play 匹配自检：拿到 {len(结果)} 个候选"
                  + (f"（第一个：{结果[0].标题}）" if 结果 else "（空）"), bool(结果))
            except Exception as 错:  # noqa: BLE001
                记(f"弹弹play 匹配自检失败：{str(错)[:160]}", False)
    except Exception as 错:  # noqa: BLE001
        记(f"弹弹play 检查异常：{错}", False)

    报告 = 项目根 / "数据" / "凭据自检.txt"
    报告.parent.mkdir(parents=True, exist_ok=True)
    报告.write_text("\n".join(结论) + "\n", encoding="utf-8")
    print(f"\n（已写入 {报告}）", flush=True)
    失败 = sum(1 for x in 结论 if x.startswith("❌"))
    return 0 if 失败 == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
