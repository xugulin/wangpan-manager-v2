"""逐集弹幕记忆：记住"这个文件对应哪一集弹幕"，下次自动装好、不用再点。

为什么值得单独做
================
弹幕匹配有两类麻烦：**自动匹配选错**（番剧正片/番外/续作标题极像）与
**每次打开都要重新选**。人只要纠正过一次，就应该记住 —— 所以这里存三样东西：
文件路径（或直链去掉查询串）、源的 episodeId、以及**是谁定的**（自动/用户）。
用户定过的条目**不再被自动匹配覆盖**（这是关键：自动逻辑再准也不该推翻人的选择）。

存储用 JSON（体量小：一集一条，几千集也就几百 KB），坏了当空的，绝不影响播放。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

__all__ = ["弹幕记忆", "记忆条目", "默认记忆路径"]


def 默认记忆路径() -> Path:
    return Path(__file__).resolve().parents[2] / "数据" / "弹幕记忆.json"


@dataclass
class 记忆条目:
    """一个文件/地址 ↔ 一集弹幕的对应关系。"""

    键: str = ""                   # 文件路径 或 去掉查询串的直链
    源: str = ""                   # "animeko" / "dandanplay" / "local"
    标识: str = ""                 # episodeId
    标题: str = ""
    集标题: str = ""
    集号: str = ""
    用户选定: bool = False         # True = 用户亲手选的，自动匹配不许覆盖
    更新时间: float = field(default_factory=time.time)
    命中次数: int = 0

    def 可读(self) -> str:
        谁定的 = "用户选定" if self.用户选定 else "自动匹配"
        return (f"{self.标题 or '?'} {self.集标题 or ''}"
                f"｜{self.源}:{self.标识}｜{谁定的}")


class 弹幕记忆:
    """逐集弹幕记忆的读写（**吞异常**：记忆坏了只当没有，不能让播放失败）。"""

    def __init__(self, 路径: Optional[Path] = None) -> None:
        self.路径 = Path(路径) if 路径 else 默认记忆路径()
        self._表: dict[str, 记忆条目] = {}
        self.读()

    # ---------------- 键 ----------------

    @staticmethod
    def 键(地址或路径: str | Path) -> str:
        """本地文件用绝对路径；网络地址去掉查询串（签名每次都变）。"""
        文本 = str(地址或路径 or "")
        if "://" in 文本:
            return 文本.split("?", 1)[0]
        try:
            return str(Path(文本).expanduser().resolve())
        except Exception:  # noqa: BLE001
            return 文本

    # ---------------- 读写 ----------------

    def 读(self) -> int:
        self._表 = {}
        try:
            if not self.路径.is_file():
                return 0
            原文 = json.loads(self.路径.read_text(encoding="utf-8"))
            for 项 in (原文.get("记忆") or []):
                条 = 记忆条目(**{k: v for k, v in 项.items()
                              if k in 记忆条目.__dataclass_fields__})
                if 条.键:
                    self._表[条.键] = 条
        except Exception:  # noqa: BLE001
            self._表 = {}
        return len(self._表)

    def 存(self) -> bool:
        try:
            self.路径.parent.mkdir(parents=True, exist_ok=True)
            项们 = sorted(self._表.values(), key=lambda x: x.更新时间, reverse=True)[:5000]
            文本 = json.dumps({"版本": 1, "记忆": [asdict(x) for x in 项们]},
                           ensure_ascii=False, indent=1)
            临时 = self.路径.with_suffix(".tmp")
            临时.write_text(文本, encoding="utf-8")
            os.replace(临时, self.路径)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---------------- 查询/更新 ----------------

    def 取(self, 地址或路径: str | Path) -> Optional[记忆条目]:
        return self._表.get(self.键(地址或路径))

    def 记(self, 地址或路径: str | Path, 源: str, 标识: str, 标题: str = "",
          集标题: str = "", 集号: str = "", 用户选定: bool = False) -> 记忆条目:
        """记下一条对应关系；**用户选定的会覆盖自动的，反之不覆盖**。"""
        键 = self.键(地址或路径)
        已有 = self._表.get(键)
        if 已有 is not None and 已有.用户选定 and not 用户选定:
            # 人定过的，自动匹配不许改（哪怕这次"看起来更准"）
            已有.命中次数 += 1
            return 已有
        条 = 记忆条目(键=键, 源=str(源), 标识=str(标识), 标题=标题, 集标题=集标题,
                    集号=str(集号), 用户选定=bool(用户选定),
                    命中次数=(已有.命中次数 + 1) if 已有 else 0)
        self._表[键] = 条
        return 条

    def 记命中(self, 地址或路径: str | Path) -> None:
        条 = self.取(地址或路径)
        if 条 is not None:
            条.命中次数 += 1

    def 忘掉(self, 地址或路径: str | Path) -> bool:
        return self._表.pop(self.键(地址或路径), None) is not None

    def 清空(self) -> None:
        self._表 = {}
        self.存()

    def 全部(self) -> list[记忆条目]:
        return sorted(self._表.values(), key=lambda x: x.更新时间, reverse=True)

    def 统计(self) -> dict:
        条们 = list(self._表.values())
        return {"总数": len(条们),
                "用户选定": sum(1 for x in 条们 if x.用户选定),
                "按源": {源: sum(1 for x in 条们 if x.源 == 源)
                      for 源 in {x.源 for x in 条们}}}
