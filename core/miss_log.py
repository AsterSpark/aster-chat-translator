# -*- coding: utf-8 -*-
"""未收录词的维护日志（给「本地词库」模式用）

只记：词、出现次数、最后一次时间。不记整行聊天，避免把玩家名/私聊内容写进去。
文件：程序目录 glossary_miss.log（一行一个 JSON），超过 200 KB 轮转成 .1（只留一份）。
写盘时机：累计 20 个新词、或进程退出。这样按热键时不会被磁盘 IO 拖慢。
"""

import atexit
import json
import os
import threading
import time

from . import config as cfg_mod

PATH = os.path.join(cfg_mod.PROJECT_ROOT, "glossary_miss.log")
MAX_BYTES = 200 * 1024
FLUSH_AT = 20

_LOCK = threading.Lock()
_BUF = {}
_registered = False


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _rotate_if_needed():
    try:
        if os.path.exists(PATH) and os.path.getsize(PATH) > MAX_BYTES:
            old = PATH + ".1"
            if os.path.exists(old):
                os.remove(old)
            os.replace(PATH, old)
    except Exception:
        pass


def _flush_locked():
    global _BUF
    if not _BUF:
        return
    _rotate_if_needed()
    try:
        with open(PATH, "a", encoding="utf-8") as f:
            for w, n in _BUF.items():
                f.write(json.dumps({"w": w, "n": n, "t": _now()},
                                   ensure_ascii=False) + "\n")
    except Exception:
        return          # 写不进去就算了，别影响翻译
    _BUF = {}


def add(words):
    """记一批没收录的词（重复的累加次数）"""
    global _registered
    if not words:
        return
    with _LOCK:
        for w in words:
            w = (w or "").strip().lower()
            if not w:
                continue
            _BUF[w] = _BUF.get(w, 0) + 1
        if len(_BUF) >= FLUSH_AT:
            _flush_locked()
    if not _registered:
        _registered = True
        try:
            atexit.register(flush)
        except Exception:
            pass


def flush():
    with _LOCK:
        _flush_locked()


def summary(path=None):
    """给维护脚本用：返回 {词: 次数}（把日志里多条同词记录加起来）"""
    path = path or PATH
    total = {}
    for p in (path + ".1", path):
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    w = str(rec.get("w") or "").strip().lower()
                    if w:
                        total[w] = total.get(w, 0) + int(rec.get("n") or 1)
        except Exception:
            continue
    return total
