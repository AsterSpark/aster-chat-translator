# -*- coding: utf-8 -*-
"""打包入口：PyInstaller 用它生成 AsterCore.exe（后端 sidecar）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _register_dll_dirs():
    """冻结后把各包的 *.libs 目录注册进 DLL 搜索路径。

    sentencepiece 的扩展模块依赖 vcomp140.dll，PyInstaller 会把它放在
    `sentencepiece.libs\\` 下，但不会自动注册这个目录 —— 结果一 import 就报
      DLL load failed while importing _sentencepiece: 找不到指定的模块
    然后悄悄降级成词库兜底，离线模型等于没装。开发环境里不会被发现，
    只有打包版才炸。
    """
    if not getattr(sys, "frozen", False):
        return
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    try:
        names = os.listdir(base)
    except OSError:
        return
    for name in names:
        if not name.endswith(".libs"):
            continue
        try:
            os.add_dll_directory(os.path.join(base, name))
        except (OSError, AttributeError):
            pass


_register_dll_dirs()

from core.server import serve  # noqa: E402

if __name__ == "__main__":
    serve(demo="--demo" in sys.argv)
