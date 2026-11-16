# -*- coding: utf-8 -*-
"""上一次翻译结果（规格 §2 第 12 条 + 小键盘 - 回看）。"""

import json
import os
import sys
import time

from .log import log

ROOT = (os.environ.get("WOWTR_HOME") or (os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PATH = os.path.join(ROOT, "last_result.json")


def save(result):
    try:
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "result": result},
                      f, ensure_ascii=False, indent=1)
    except Exception as e:
        log(f"保存上一次结果失败: {e}")


def load():
    """返回 (时间戳, result) 或 ("", None)"""
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("ts", ""), data.get("result") or None
    except Exception:
        return "", None
