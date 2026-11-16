# -*- coding: utf-8 -*-
"""配置读写。

- 缺字段按默认值补齐，不因为配置少一个键就报错；
- 保存时写程序目录下的 config.json。
"""

import json
import os
import sys

DEFAULTS = {
    "api": {
        "key": "",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/chat/completions",
    },
    "hotkey": {"translate_key": "num_add", "last_key": "num_sub"},
    # 首次安装没有截图区域：必须让用户自己去框选，不要去截一张 0×0 的图
    "capture": None,
    "ocr": {"lang": "zh-Hans-CN"},
    "max_lines": 8,
    # 翻译模式：ai = 视觉模型；local = 本地词库（逐词对照，不联网、不要 Key）
    "engine": "ai",
    # 自定义价目（元/百万 tokens），覆盖 usage.py 里的内置表；空 = 用内置
    "pricing": {},
    "ui_font": "Noto Sans SC Medium",
    "ui_scale": 1.0,
    "keyboard_hook": True,
    "require_game": True,
    "game_process": "Wow.exe",
    "require_foreground": True,
    "paused": False,
}

if os.environ.get("WOWTR_HOME"):
    # 外壳会把它设成程序根目录（core\ 的上一级）
    PROJECT_ROOT = os.environ["WOWTR_HOME"]
elif getattr(sys, "frozen", False):
    # 打包后：配置放在 exe 旁边，而不是临时解包目录
    PROJECT_ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_CFG = os.path.join(PROJECT_ROOT, "config.json")


def _deep_fill(cfg, defaults):
    """按默认值补全缺失键（不覆盖已有值）"""
    for k, v in defaults.items():
        if k not in cfg:
            cfg[k] = json.loads(json.dumps(v))
        elif isinstance(v, dict) and isinstance(cfg[k], dict):
            _deep_fill(cfg[k], v)
    return cfg


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def which_path():
    """返回实际读取的配置路径（不存在返回 None）"""
    env = os.environ.get("WOWTR_CONFIG")
    for p in (env, LOCAL_CFG):
        if p and os.path.exists(p):
            return p
    return None


def load():
    """读配置：环境变量 WOWTR_CONFIG 优先，其次程序目录；都不存在时用默认值"""
    path = which_path()
    if path:
        try:
            cfg = _read(path)
        except Exception as e:
            print(f"[config] 读取失败 {path}: {e}，改用默认配置")
            cfg = {}
    else:
        cfg = {}
    cfg = _deep_fill(cfg, DEFAULTS)
    # v0.1.2：默认条数定为 8（6 太少、12 太多）。旧配置里是 6 或 12 的跟着换过来；
    # 用户显式改过的其它值保持不动。
    try:
        if int(cfg.get("max_lines") or 0) in (6, 12):
            cfg["max_lines"] = DEFAULTS["max_lines"]
    except (TypeError, ValueError):
        pass
    # v0.1.1 的 engine 有 offline / ai 两种；v0.1.2 把 offline 改叫 local
    eng = str(cfg.get("engine") or "ai").strip().lower()
    if eng == "offline":
        eng = "local"
    if eng not in ("ai", "local"):
        eng = "ai"
    cfg["engine"] = eng
    return cfg


def save(cfg, path=None):
    """保存到程序目录下的 config.json（先写临时文件再替换，避免读到半截 JSON）"""
    path = path or LOCAL_CFG
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def public_view(cfg):
    """给前端看的配置：抹掉 API Key，只留「有没有」和一个打码提示。

    前端永远拿不到明文 Key —— 界面里只需要知道「配没配」。
    """
    c = json.loads(json.dumps(cfg or {}))
    api = c.get("api") or {}
    key = str(api.pop("key", "") or "").strip()
    api["key_set"] = bool(key)
    if key:
        api["key_hint"] = (key[:3] + "…" + key[-4:]) if len(key) >= 10 else "已配置"
    else:
        api["key_hint"] = ""
    c["api"] = api
    return c


if __name__ == "__main__":
    p = which_path()
    c = load()
    print("配置来源:", p or "（默认值）")
    print("热键:", c["hotkey"]["translate_key"], "/", c["hotkey"]["last_key"])
    print("截图区域:", c["capture"])
    print("模型:", c["api"]["model"], " 有Key:", bool(c["api"]["key"].strip()))
