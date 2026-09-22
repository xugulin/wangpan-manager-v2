"""按配置建适配器注册表（M6）。

配置格式（``数据/网盘.json``，V2 自己的，与 V1 无关）::

    {
      "网盘": [
        {"标识": "本地", "类型": "本地", "名字": "本机文件夹", "根": "/home/x/媒体"},
        {"标识": "演示HTTP", "类型": "HTTP", "名字": "演示直链",
         "基地址": "http://127.0.0.1:8000/影片.mp4",
         "请求头": {"Referer": "https://pan.example.com/"}}
      ]
    }

为什么用配置而不是硬编码：V2 要能"不依赖任何其它项目"地扩展；
新增一种网盘 = 实现契约 + 写进配置，界面不用改。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .接口 import 注册表
from .http import HTTP适配器
from .本地 import 本地适配器

__all__ = ["默认配置路径", "读配置", "建注册表"]


def 默认配置路径() -> Path:
    return Path(__file__).resolve().parents[2] / "数据" / "网盘.json"


def 读配置(路径: Optional[Path] = None) -> list[dict]:
    路径 = Path(路径) if 路径 else 默认配置路径()
    try:
        if not 路径.is_file():
            return []
        数据 = json.loads(路径.read_text(encoding="utf-8"))
        return list(数据.get("网盘") or [])
    except Exception:  # noqa: BLE001 - 配置坏了不该让程序起不来
        return []


def 建注册表(配置们: Optional[list[dict]] = None, 路径: Optional[Path] = None) -> 注册表:
    表 = 注册表()
    for 项 in (配置们 if 配置们 is not None else 读配置(路径)):
        类型 = str(项.get("类型") or "本地")
        标识 = str(项.get("标识") or 项.get("名字") or 类型)
        try:
            if 类型 == "本地":
                表.注册(本地适配器(项.get("根") or "."), 标识)
            elif 类型 in ("光鸭", "光鸭云盘", "GUANGYA"):
                from .光鸭 import 光鸭适配器
                对象 = 光鸭适配器()
                对象.名字 = str(项.get("名字") or 对象.名字)
                表.注册(对象, 标识)
            elif 类型.upper() == "HTTP":
                对象 = HTTP适配器(项.get("基地址") or "", 项.get("请求头"),
                                项.get("索引地址") or "")
                对象.名字 = str(项.get("名字") or 对象.名字)
                表.注册(对象, 标识)
        except Exception:  # noqa: BLE001 - 单个网盘坏掉不影响其它
            continue
    return 表
