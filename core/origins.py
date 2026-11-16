# -*- coding: utf-8 -*-
"""本地接口的来源白名单（HTTP 与 WebSocket 共用）。

为什么两边都要判：
    - HTTP：CORS 只挡"读响应"，挡不住"把请求发出去"，所以 POST 还得自己校验 Origin
    - WebSocket：**根本不受 CORS 约束**，任意网页都能连 ws://127.0.0.1:17651，
      不判来源就等于把设置（含明文 API Key）公开给任何一个打开的网页

页面打包后跑在 http://tauri.localhost；调试时也可能直接开 http://127.0.0.1:17650。
"""

import os

_PORT = os.environ.get("WOWTR_PORT", "17650")

ALLOWED = {
    "http://tauri.localhost",
    "https://tauri.localhost",
    f"http://127.0.0.1:{_PORT}",
    f"http://localhost:{_PORT}",
    "http://127.0.0.1:17650",
    "http://localhost:17650",
}


def is_app_origin(origin):
    """是不是我们自己的页面（无 Origin 的本地脚本不算，返回 False）。"""
    return bool(origin) and origin in ALLOWED
