"""播放记录（M5）：记住"看到哪儿了"、最近播放、以及播放列表。

为什么单独一个模块：这几件事都要**落盘**（用户关掉程序再打开，位置不能丢），
而落盘的东西必须能容忍"文件坏了/被手改了/没权限" —— 一条都不能让播放本身失败。
所以这里的原则是：**读写都吞异常**，读不出来就当没有记录。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__all__ = ["播放记录", "记录本", "默认记录路径"]


def 默认记录路径() -> Path:
    """记录文件位置（项目内 数据/ 下；与 V1 无关，V2 自己的目录）。"""
    return Path(__file__).resolve().parents[2] / "数据" / "播放记录.json"


@dataclass
class 播放记录:
    """一个媒体文件的播放记录。"""

    标识: str                      # 用"地址/路径"当键（本地路径或直链去掉签名后的形式）
    位置秒: float = 0.0
    时长秒: float = 0.0
    名字: str = ""
    更新时刻: float = field(default_factory=time.time)
    播放次数: int = 0
    看完: bool = False

    @property
    def 进度比(self) -> float:
        if self.时长秒 <= 0:
            return 0.0
        return max(0.0, min(1.0, self.位置秒 / self.时长秒))

    def 可用位置(self, 最小秒: float = 5.0, 尾部留白秒: float = 10.0) -> float:
        """该从哪儿接着播：太靠前（开头）或太靠后（等于看完了）都不续播。"""
        if self.看完 or self.位置秒 < 最小秒:
            return 0.0
        if self.时长秒 > 0 and self.位置秒 > self.时长秒 - 尾部留白秒:
            return 0.0
        return self.位置秒


class 记录本:
    """播放记录的读写（JSON 文件；坏了就当空的，不抛异常）。"""

    def __init__(self, 路径: Optional[Path] = None, 上限: int = 500) -> None:
        self.路径 = Path(路径) if 路径 else 默认记录路径()
        self.上限 = max(10, int(上限))
        self._数据: dict[str, 播放记录] = {}
        self.读()

    # ---------------- 读写 ----------------

    def 读(self) -> int:
        self._数据 = {}
        try:
            if not self.路径.is_file():
                return 0
            原文 = json.loads(self.路径.read_text(encoding="utf-8"))
            for 项 in (原文.get("记录") or []):
                记录 = 播放记录(标识=str(项.get("标识") or ""),
                            位置秒=float(项.get("位置秒") or 0.0),
                            时长秒=float(项.get("时长秒") or 0.0),
                            名字=str(项.get("名字") or ""),
                            更新时刻=float(项.get("更新时刻") or 0.0),
                            播放次数=int(项.get("播放次数") or 0),
                            看完=bool(项.get("看完")))
                if 记录.标识:
                    self._数据[记录.标识] = 记录
        except Exception:  # noqa: BLE001 - 记录坏了不能影响播放
            self._数据 = {}
        return len(self._数据)

    def 存(self) -> bool:
        """写盘（先写临时文件再替换：写一半断电也不会留下半个坏文件）。"""
        try:
            self.路径.parent.mkdir(parents=True, exist_ok=True)
            项们 = sorted(self._数据.values(), key=lambda r: r.更新时刻, reverse=True)
            项们 = 项们[: self.上限]
            文本 = json.dumps({"版本": 1, "记录": [
                {"标识": r.标识, "位置秒": round(r.位置秒, 3),
                 "时长秒": round(r.时长秒, 3), "名字": r.名字,
                 "更新时刻": r.更新时刻, "播放次数": r.播放次数,
                 "看完": r.看完} for r in 项们]},
                ensure_ascii=False, indent=1)
            临时 = self.路径.with_suffix(".tmp")
            临时.write_text(文本, encoding="utf-8")
            os.replace(临时, self.路径)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---------------- 查询 / 更新 ----------------

    @staticmethod
    def 标识(地址: str) -> str:
        """记录的键：本地路径用绝对路径；网络地址去掉查询串（签名每次都变）。"""
        文本 = str(地址 or "")
        if "://" in 文本:
            return 文本.split("?", 1)[0]
        try:
            return str(Path(文本).expanduser().resolve())
        except Exception:  # noqa: BLE001
            return 文本

    def 取(self, 地址: str) -> Optional[播放记录]:
        return self._数据.get(self.标识(地址))

    def 记位置(self, 地址: str, 位置秒: float, 时长秒: float = 0.0,
             名字: str = "") -> 播放记录:
        键 = self.标识(地址)
        记录 = self._数据.get(键) or 播放记录(标识=键)
        记录.位置秒 = max(0.0, float(位置秒 or 0.0))
        if 时长秒:
            记录.时长秒 = float(时长秒)
        if 名字:
            记录.名字 = 名字
        # ⚠️ 时间戳要**严格递增**：Windows 上 time.time() 精度约 15ms，
        #    连续记两条会拿到同一个值，"最近播放"的排序就变成随机的（真机 CI 抓到）。
        现在 = time.time()
        for 已有 in self._数据.values():
            if 已有.标识 != 键 and 已有.更新时刻 >= 现在:
                现在 = 已有.更新时刻 + 1e-4
        记录.更新时刻 = 现在
        self._数据[键] = 记录
        return 记录

    def 记开始(self, 地址: str, 时长秒: float = 0.0, 名字: str = "") -> 播放记录:
        记录 = self.记位置(地址, 0.0, 时长秒, 名字)
        记录.播放次数 += 1
        记录.看完 = False
        return 记录

    def 记看完(self, 地址: str) -> None:
        记录 = self.取(地址)
        if 记录 is not None:
            记录.看完 = True
            记录.位置秒 = 记录.时长秒
            记录.更新时刻 = time.time()

    def 最近(self, 条数: int = 20) -> list[播放记录]:
        项们 = sorted(self._数据.values(), key=lambda r: r.更新时刻, reverse=True)
        return 项们[: max(1, int(条数))]

    def 续播位置(self, 地址: str) -> float:
        记录 = self.取(地址)
        return 记录.可用位置() if 记录 is not None else 0.0

    def 清空(self) -> None:
        self._数据 = {}
        self.存()
