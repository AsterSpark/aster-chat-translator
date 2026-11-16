# -*- coding: utf-8 -*-
"""打包入口：PyInstaller 用它生成 AsterCore.exe（后端 sidecar）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.server import serve  # noqa: E402

if __name__ == "__main__":
    serve(demo="--demo" in sys.argv)
