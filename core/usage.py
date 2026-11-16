# -*- coding: utf-8 -*-
"""本次运行的用量统计：调用次数、token 明细、按内置价表估算花费。

口径：进程启动 → 退出算一次会话，重启归零。
价格：内置 DeepSeek 的单价（元 / 百万 token），可用 config.json 的
"pricing" 覆盖。换成其它接口时只统计 token，不显示金额。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

_CN = timezone(timedelta(hours=8))
_LOCK = threading.Lock()

# 单价表：只收录**确认过价格**的模型（人民币 / 百万 tokens）。
# 换成本地没收录的接口时，只累计 token、不猜钱 —— 否则会拿 DeepSeek 的价去乘
# 别家的用量，给出一个看着精确、其实错误的数字。
# 价格会变：可以用 config.json 的 "pricing" 覆盖，格式：
#   "pricing": {"deepseek-chat": {"hit": 0.025, "miss": 3.0, "out": 6.0}}
PRICE_CNY = {
    # 2026 年的常见费率（输入命中 / 输入未命中 / 输出），单位：元/百万 tokens
    "deepseek-chat": {"hit": 0.025, "miss": 3.0, "out": 6.0},
    "deepseek-reasoner": {"hit": 0.025, "miss": 3.0, "out": 12.0},
    # 兜底：名字里带 deepseek 的其它模型
    "deepseek": {"hit": 0.025, "miss": 3.0, "out": 6.0},
}

_OVERRIDE: dict = {}


def set_prices(table):
    """由 server 在启动/保存设置时灌入 config.json 里的自定义价目（可空）。"""
    global _OVERRIDE
    _OVERRIDE = table if isinstance(table, dict) else {}


def price_table_for(model: str):
    """按模型名找单价表；找不到返回 None（= 只统计 token，不算钱）。"""
    m = (model or "").lower()
    for key, table in _OVERRIDE.items():
        if key.lower() in m and isinstance(table, dict):
            return table
    for key in sorted(PRICE_CNY, key=len, reverse=True):
        if key in m:
            return PRICE_CNY[key]
    return None


_STATS = {
    "translates": 0,   # 用户发起的翻译次数（含命中缓存）
    "calls": 0,        # 成功调用模型的次数
    "prompt": 0,       # 输入 tokens
    "hit": 0,          # 输入里缓存命中的部分
    "miss": 0,         # 输入里缓存未命中的部分
    "completion": 0,   # 输出 tokens
    "cny": 0.0,        # 累计花费（元，只含已收录单价的部分）
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
    """记录一次模型调用的用量。

    计价只对**有价目的模型**生效。没收录的接口一律不猜价，只累计 token。
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
        cost = (hit * float(table.get("hit", 0))
                + miss * float(table.get("miss", 0))
                + completion * float(table.get("out", 0))) / 1_000_000

    with _LOCK:
        _STATS["calls"] += 1
        _STATS["prompt"] += prompt
        _STATS["hit"] += hit
        _STATS["miss"] += miss
        _STATS["completion"] += completion
        _STATS["cny"] += cost
        if not table:
            _STATS["unpriced"] += 1
        if name:
            _MODEL = name


def snapshot() -> dict:
    """给前端看的快照（金额单位：元）。"""
    with _LOCK:
        st = dict(_STATS)
        model = _MODEL

    cny = round(st["cny"], 4) if st["cny"] and st["cny"] < 0.01 else round(st["cny"], 2)
    return {
        "translates": st["translates"],
        "calls": st["calls"],
        "prompt": st["prompt"],
        "hit": st["hit"],
        "miss": st["miss"],
        "completion": st["completion"],
        "total": st["prompt"] + st["completion"],
        "cny": cny,
        "model": model or "",
        # 有没有调用是"没内置单价"的：前端别在这种情况显示 ¥
        "unpriced": st["unpriced"],
        "priced": st["unpriced"] == 0,
    }
