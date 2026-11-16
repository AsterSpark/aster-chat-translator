# -*- coding: utf-8 -*-
"""本次运行的用量统计：调用次数、token 明细、按内置价表估算花费。

口径：进程启动 → 退出算一次会话，重启归零。
价格：**只有 DeepSeek 收录了单价**（美元 / 百万 token，高峰为北京时间
工作日 09:00-12:00、14:00-18:00）。换成其它接口时只统计 token，不显示金额。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

_CN = timezone(timedelta(hours=8))
_LOCK = threading.Lock()

# 单价表：只收录**确认过价格**的模型，键是模型名里的片段（小写匹配）。
# 没收录的接口一律不猜价 —— 否则会拿 DeepSeek 的单价去乘别家的用量，
# 给出一个看着精确、其实错误的数字。美元 / 百万 token。
PRICE_BY_MODEL = {
    "deepseek": {
        "hit":  {"peak": 0.006, "off": 0.003},
        "miss": {"peak": 0.300, "off": 0.150},
        "out":  {"peak": 1.200, "off": 0.600},
    },
}
USD_CNY = 7.1


def price_table_for(model: str):
    """按模型名找单价表；找不到返回 None（= 只统计 token，不算钱）。"""
    m = (model or "").lower()
    for key, table in PRICE_BY_MODEL.items():
        if key in m:
            return table
    return None


_STATS = {
    "translates": 0,   # 用户发起的翻译次数（含命中缓存）
    "calls": 0,        # 成功调用模型的次数
    "prompt": 0,       # 输入 tokens
    "hit": 0,          # 输入里缓存命中的部分
    "miss": 0,         # 输入里缓存未命中的部分
    "completion": 0,   # 输出 tokens
    "usd": 0.0,        # 累计花费（美元，只含已收录单价的部分）
    "unpriced": 0,     # 没有内置单价的调用次数
}
_MODEL = ""
_STARTED = datetime.now(_CN)


def is_peak(now: datetime | None = None) -> bool:
    """是否处于高峰计费时段（北京时间，工作日）。"""
    now = now or datetime.now(_CN)
    if now.weekday() >= 5:          # 周六 / 周日
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60 <= minutes < 12 * 60) or (14 * 60 <= minutes < 18 * 60)


def add_translate() -> None:
    """用户按了一次翻译（不管后面是不是命中缓存）。"""
    with _LOCK:
        _STATS["translates"] += 1


def record(resp, model: str = "") -> None:
    """记录一次模型调用的用量。resp 是接口返回的 JSON。

    计价只对**内置了单价的模型**生效（目前只有 DeepSeek）。换成本地没收录的
    接口时，只累计 token、不猜钱 —— 否则会拿 DeepSeek 的价去乘别家的用量，
    给用户一个看着精确、其实错误的 ¥ 数字。
    """
    global _MODEL

    usage = (resp or {}).get("usage") or {}
    if not usage:
        return

    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)

    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    hit = int(hit) if hit is not None else 0
    miss = int(miss) if miss is not None else max(0, prompt - hit)

    name = str((resp or {}).get("model") or model or "")
    table = price_table_for(name)
    cost = 0.0
    if table:
        band = "peak" if is_peak() else "off"
        cost = (hit * table["hit"][band]
                + miss * table["miss"][band]
                + completion * table["out"][band]) / 1_000_000

    with _LOCK:
        _STATS["calls"] += 1
        _STATS["prompt"] += prompt
        _STATS["hit"] += hit
        _STATS["miss"] += miss
        _STATS["completion"] += completion
        _STATS["usd"] += cost
        if not table:
            _STATS["unpriced"] += 1
        if name:
            _MODEL = name


def snapshot() -> dict:
    """给前端看的快照。"""
    with _LOCK:
        s = dict(_STATS)
        model = _MODEL

    cny = s["usd"] * USD_CNY
    if cny and cny < 0.01:
        cny = round(cny, 4)
    else:
        cny = round(cny, 2)

    peak = is_peak()
    return {
        "translates": s["translates"],
        "calls": s["calls"],
        "prompt": s["prompt"],
        "hit": s["hit"],
        "miss": s["miss"],
        "completion": s["completion"],
        "total": s["prompt"] + s["completion"],
        "usd": round(s["usd"], 6),
        "cny": cny,
        "model": model or "",
        # 有没有调用是"没内置单价"的：前端别在这种情况显示 ¥
        "unpriced": s["unpriced"],
        "priced": s["unpriced"] == 0,
        "peak": peak,
        "usd_cny": USD_CNY,
        "started_at": _STARTED.strftime("%H:%M"),
    }
