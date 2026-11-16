# -*- coding: utf-8 -*-
"""结果缓存：同一张截图 5 分钟内不重复扣费（规格 §2 第 6 条）。"""

import time

TTL = 300          # 5 分钟
MAX_ITEMS = 20

_CACHE = {}


def get(key):
    item = _CACHE.get(key)
    if item and (time.time() - item[0]) < TTL:
        return item[1]
    if item:
        _CACHE.pop(key, None)
    return None


def put(key, value):
    _CACHE[key] = (time.time(), value)
    if len(_CACHE) > MAX_ITEMS:
        # 先丢最早的一半，简单粗暴但不影响正确性
        for k in list(_CACHE)[: MAX_ITEMS // 2]:
            _CACHE.pop(k, None)


def clear():
    _CACHE.clear()
