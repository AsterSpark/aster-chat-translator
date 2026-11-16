# -*- coding: utf-8 -*-
"""进程级共享状态：只放需要被多个模块读写的少量标志。

为什么要单独一个模块（别挪回 server.py）：
    用 `py -m core.server` 启动时，server.py 会以 `__main__` 名字加载。
    如果状态变量定义在 server.py 里，pipeline 里 `from .server import SETTINGS_OPEN`
    会**再加载一份** core.server，两边拿到的是两个不同的 dict —— 实测后果是
    「设置窗开着就不翻译」这条守卫永远读不到 True，设置窗开着也照翻、照花钱。
    放进这个只被 `import`、不会被 `-m` 执行的模块后，全进程只有一份。

同理，以后任何跨模块共享的可变状态都放这里，不要放 server.py。
"""

# 用 dict 而不是 bool：老代码（和工具脚本）里是 SETTINGS_OPEN["v"] 的写法，
# 保留这个形状，调用方不需要跟着改。
SETTINGS_OPEN = {"v": False}


def set_settings_open(open_: bool) -> bool:
    """设置窗口打开/关闭时由 HTTP 回调设置，返回当前值。"""
    SETTINGS_OPEN["v"] = bool(open_)
    return SETTINGS_OPEN["v"]


def settings_open() -> bool:
    return bool(SETTINGS_OPEN["v"])
