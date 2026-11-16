# -*- coding: utf-8 -*-
"""日志：runtime.log，超 512KB 轮转成 runtime.1.log（规格 §10）。"""

import os
import sys
import time

ROOT = (os.environ.get("WOWTR_HOME") or (os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
LOG_PATH = os.path.join(ROOT, "runtime.log")
MAX_BYTES = 512 * 1024


def log(msg):
    line = time.strftime("[%H:%M:%S] ") + str(msg)
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > MAX_BYTES:
            old = os.path.join(ROOT, "runtime.1.log")
            try:
                if os.path.exists(old):
                    os.remove(old)
                os.replace(LOG_PATH, old)
            except Exception:
                pass
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass
