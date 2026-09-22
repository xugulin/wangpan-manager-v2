"""网盘适配器（M6）：V2 自己的契约与实现（本地 / HTTP；真实网盘按同一契约扩展）。"""

from .接口 import 条目, 直链, 不支持, 适配器, 注册表  # noqa: F401
from .本地 import 本地适配器                            # noqa: F401
from .http import HTTP适配器                            # noqa: F401
from .工厂 import 建注册表                              # noqa: F401
from .光鸭 import 光鸭适配器, 光鸭令牌                   # noqa: F401

__all__ = ["条目", "直链", "不支持", "适配器", "注册表",
           "本地适配器", "HTTP适配器", "建注册表", "光鸭适配器", "光鸭令牌"]
