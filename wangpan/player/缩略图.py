"""缩略图/进度预览（M5 剩余项）：给定时间点，独立解一帧出来。

为什么单独开一路解码（而不是用播放器现成的帧）：
* 播放器正忙着播当前时间点，**没有**目标时间点的帧；
* 硬要播放器去 seek 会把正在看的画面打断（用户只是把鼠标停在进度条上而已）。

所以这里用"自己的 demux + 自己的解码器"，seek 到目标点解一帧、缩到指定宽度，
解完就关。代价是一次 seek（本地几乎瞬间；网络源会发一次 Range 请求）。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QImage

from ..ffmpeg import 绑定 as B
from .解码 import 视频解码器
from .解封装 import 输入

__all__ = ["缩略图器"]


class 缩略图器:
    """按需出图（用完自己收尾；同一实例可复用，避免反复打开输入）。"""

    def __init__(self, 地址: str, 请求头: Optional[dict] = None,
                 宽: int = 320, 日志回调=None) -> None:
        self.地址 = 地址
        self.请求头 = dict(请求头 or {})
        self.宽 = max(64, int(宽))
        self._日志 = 日志回调 or (lambda _t: None)
        self._输入: Optional[输入] = None
        self._解码器: Optional[视频解码器] = None

    # ---------------- 内部 ----------------

    def _准备(self) -> bool:
        if self._输入 is not None and self._解码器 is not None:
            return True
        try:
            选项 = None
            from . import 网络
            if 网络.是网络地址(self.地址):
                选项 = 网络.网络选项(self.地址, self.请求头, 允许重连=False)
            self._输入 = 输入.打开(self.地址, 选项)
            if self._输入.视频流 is None:
                self.关闭()
                return False
            self._解码器 = 视频解码器(self._输入.绑定, self._输入.视频流)
            宽 = self.宽
            高 = max(2, int(self._输入.视频流.高 * 宽 / max(1, self._输入.视频流.宽)))
            self._解码器.设置输出尺寸(宽, 高)
            self._解码器.打开()
            return True
        except Exception as 错:  # noqa: BLE001
            self._日志(f"[缩略图] 打不开：{错}")
            self.关闭()
            return False

    def 关闭(self) -> None:
        for 部件 in (self._解码器, self._输入):
            try:
                if 部件 is not None:
                    部件.关() if hasattr(部件, "关") else 部件.关闭()
            except Exception:  # noqa: BLE001
                pass
        self._解码器 = None
        self._输入 = None

    # ---------------- 对外 ----------------

    def 取图(self, 秒: float) -> Optional[QImage]:
        """取目标时间点的缩略图（失败返回 None，绝不影响正在播的画面）。"""
        if not self._准备() or self._输入 is None or self._解码器 is None:
            return None
        try:
            # 跳到目标点**之前**一点，然后往前解到目标点之后（关键帧回退）
            起点 = max(0.0, float(秒) - 0.2)
            self._输入.跳转(起点)
            self._解码器.冲刷()
            截止 = float(秒) + 1.5
            最好 = None
            for _ in range(240):
                包 = self._输入.读包()
                if 包 is None:
                    break
                if 包["流序号"] != self._输入.视频流.序号:
                    self._输入.释放包()
                    continue
                新包 = self._输入.复制包()
                self._输入.释放包()
                try:
                    self._解码器.送包(新包)
                finally:
                    self._输入.释放新包(新包)
                while self._解码器.收帧() >= 0:
                    帧 = self._解码器.取帧()
                    if 帧 is None:
                        continue
                    if 帧.时间秒 < 0 or 帧.时间秒 >= 秒:
                        return 帧.转QImage()
                    最好 = 帧                     # 记着最后一张，防止目标点没有帧
            return 最好.转QImage() if 最好 is not None else None
        except Exception as 错:  # noqa: BLE001
            self._日志(f"[缩略图] 解码失败：{错}")
            self.关闭()
            return None
