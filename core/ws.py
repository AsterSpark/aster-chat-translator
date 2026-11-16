# -*- coding: utf-8 -*-
"""WebSocket 通道：前端 ↔ 后端双向。

职责：
- 后端 → 前端：推送弹窗数据（{"type": "show", payload: {...}}）
- 前端 → 后端：想说的话（{"type": "compose", id, intent, raw, translation}）
               后端跑完模型后回 {"type": "compose", id, ok, replies, error}

为什么用 websockets 库而不是自己写协议：
RFC 6455 的分帧、掩码、分片、心跳细节多，自己实现容易出隐蔽 bug；
这个库是纯 Python，约 200KB，打包无负担。
"""

import asyncio
import json
import os
import threading
import time

from websockets.asyncio.server import serve

from . import config as cfg_mod
from . import model
from .log import log
from .origins import ALLOWED as ALLOWED_ORIGINS, is_app_origin

CFG = cfg_mod.load()

# 保存设置后由 server 注入：callable(cfg) -> None（重绑热键）
SETTINGS_HOOK = None

PORT = 17651
HOST = "127.0.0.1"


def origin_of(ws):
    """取握手时的 Origin；拿不到就返回 None。"""
    try:
        return ws.request.headers.get("Origin")
    except Exception:
        return None


class Hub:
    def __init__(self):
        self.clients = set()
        self.loop = None
        self._server = None
        self.last_payload = None      # 新页面连上时补发最近一条
    # ---------- 连接管理 ----------
    async def _handler(self, ws):
        self.clients.add(ws)
        print(f"[ws] 页面已连接，当前 {len(self.clients)} 个", flush=True)
        try:
            if self.last_payload is not None:
                await ws.send(json.dumps({"type": "show", "payload": self.last_payload},
                                         ensure_ascii=False))
            async for raw in ws:
                await self._on_message(ws, raw)
        except Exception as e:
            print(f"[ws] 连接异常: {e}", flush=True)
        finally:
            self.clients.discard(ws)
            print(f"[ws] 页面断开，剩余 {len(self.clients)} 个", flush=True)

    async def _on_message(self, ws, raw):
        try:
            msg = json.loads(raw)
        except Exception:
            await ws.send(json.dumps({"type": "error", "error": "非法 JSON"}))
            return

        kind = msg.get("type")
        if kind == "compose":
            # 模型调用是阻塞的，扔线程池，别卡住事件循环
            asyncio.create_task(self._do_compose(ws, msg))
        elif kind == "settings_get":
            # 设置面板要回填星号，所以把 Key 一起发 —— 但只发给自己的页面。
            # WebSocket 不受 CORS 约束，任意网页都能连 127.0.0.1:17651；
            # 不判来源就等于把明文 Key 公开出去。
            cfg = cfg_mod.load()
            view = cfg_mod.public_view(cfg)
            if is_app_origin(origin_of(ws)):
                view["api"]["key_value"] = (cfg.get("api") or {}).get("key", "")
            await ws.send(json.dumps({"type": "settings", "cfg": view},
                                     ensure_ascii=False))
        elif kind == "usage_get":
            from . import usage
            await ws.send(json.dumps({"type": "usage", "usage": usage.snapshot()},
                                     ensure_ascii=False))
        elif kind == "settings_save":
            await self._do_save_settings(ws, msg)
        elif kind == "set_capture":
            await self._do_set_capture(ws, msg)
        elif kind == "ping":
            await ws.send(json.dumps({"type": "pong", "t": time.time()}))
        elif kind == "toast_ready":
            # 提示条窗口起来后主动要一次启动提示（比定时器可靠）
            await ws.send(json.dumps({"type": "notice",
                                      "text": "Aster 聊天翻译 · 仅支持魔兽世界聊天框", "ms": 1600},
                                     ensure_ascii=False))
        else:
            await ws.send(json.dumps({"type": "error",
                                      "error": f"未知消息类型 {kind}"}))

    async def _do_set_capture(self, ws, msg):
        """框选结果写进配置（物理像素）"""
        try:
            r = msg.get("rect") or {}
            left, top = int(r.get("left", 0)), int(r.get("top", 0))
            right, bottom = int(r.get("right", 0)), int(r.get("bottom", 0))
            if right - left < 40 or bottom - top < 20:
                await ws.send(json.dumps({"type": "capture_saved", "ok": False,
                                          "error": "框太小了"}, ensure_ascii=False))
                return
            cfg = cfg_mod.load()
            cfg["capture"] = {"left": left, "top": top, "right": right, "bottom": bottom}
            cfg_mod.save(cfg)
            global CFG
            CFG = cfg
            log(f"截图区域已更新：{left},{top} → {right},{bottom}")
            await ws.send(json.dumps({"type": "capture_saved", "ok": True,
                                      "rect": cfg["capture"]}, ensure_ascii=False))
            await self._broadcast({"type": "settings", "cfg": cfg_mod.public_view(cfg)})
        except Exception as e:
            log(f"保存截图区域失败: {e}")
            await ws.send(json.dumps({"type": "capture_saved", "ok": False,
                                      "error": str(e)[:100]}, ensure_ascii=False))

    async def _do_save_settings(self, ws, msg):
        """保存设置：写本工程 config.json + 重绑热键 + 通知所有页面"""
        try:
            data = msg.get("cfg") or {}
            cfg = cfg_mod.load()
            old_engine = str(cfg.get("engine") or "").lower()
            if "ui_font" in data:
                cfg["ui_font"] = str(data["ui_font"] or "")
            if "ui_scale" in data:
                cfg["ui_scale"] = max(0.8, min(1.5, float(data["ui_scale"])))
            if data.get("engine") in ("auto", "ai", "offline"):
                cfg["engine"] = data["engine"]
            if "require_game" in data:
                cfg["require_game"] = bool(data["require_game"])
            hk = data.get("hotkey") or {}
            cfg.setdefault("hotkey", {})
            for k in ("translate_key", "last_key"):
                if hk.get(k):
                    cfg["hotkey"][k] = str(hk[k])

            # API Key：留空表示不改；api_key_clear 才清除
            cfg.setdefault("api", {})
            if data.get("api_key_clear"):
                cfg["api"]["key"] = ""
                log("API Key 已清除")
            elif str(data.get("api_key") or "").strip():
                cfg["api"]["key"] = str(data["api_key"]).strip()
                log("API Key 已更新（长度 %d）" % len(cfg["api"]["key"]))

            path = cfg_mod.save(cfg)
            global CFG
            CFG = cfg
            log(f"设置已保存 -> {path}")

            if SETTINGS_HOOK:
                await asyncio.to_thread(SETTINGS_HOOK, cfg)

            await ws.send(json.dumps({"type": "settings_saved", "ok": True,
                                      "path": path}, ensure_ascii=False))
            await self._broadcast({"type": "settings",
                                   "cfg": cfg_mod.public_view(cfg)})

            # 翻译模式切了就在顶部提示条报一句（设置保存后才真正生效）
            new_engine = str(cfg.get("engine") or "").lower()
            if new_engine != old_engine:
                is_ai = new_engine == "ai"
                label = "AI 模型" if is_ai else "离线词库"
                HUB.push_notice(f"已切换 {label}", 1600, "ai" if is_ai else "offline")
        except Exception as e:
            log(f"保存设置失败: {e}")
            await ws.send(json.dumps({"type": "settings_saved", "ok": False,
                                      "error": str(e)[:120]}, ensure_ascii=False))

    async def _do_compose(self, ws, msg):
        mid = msg.get("id")
        intent = (msg.get("intent") or "").strip()
        raw = msg.get("raw") or ""
        zh = msg.get("translation") or ""

        if not intent:
            await ws.send(json.dumps({"type": "compose", "id": mid, "ok": False,
                                      "error": "没有输入内容",
                                      "replies": []}, ensure_ascii=False))
            return

        # 真实模型调用（WOWTR_STUB=1 时用占位，方便没 Key 也能调界面）
        try:
            if os.environ.get("WOWTR_STUB") == "1":
                replies = await asyncio.to_thread(_compose_stub, intent, raw, zh)
            else:
                replies = await asyncio.to_thread(model.compose, CFG, intent, raw, zh)
            payload = {"type": "compose", "id": mid, "ok": True,
                       "replies": replies, "error": None}
        except model.ModelError as e:
            payload = {"type": "compose", "id": mid, "ok": False,
                       "replies": [], "error": str(e)}
        except Exception as e:
            payload = {"type": "compose", "id": mid, "ok": False,
                       "replies": [], "error": str(e)[:120]}
        await ws.send(json.dumps(payload, ensure_ascii=False))

    # ---------- 对外 ----------
    async def _broadcast(self, data):
        if not self.clients:
            return
        text = json.dumps(data, ensure_ascii=False)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def broadcast_threadsafe(self, data):
        """从任意线程推一条消息（HTTP 线程会用到）"""
        if not self.loop:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(data), self.loop)

    def push_show(self, payload):
        self.broadcast_threadsafe({"type": "show", "payload": payload})

    def push_notice(self, text, ms=0, tone=""):
        """提示条文案：text 为空表示收起；ms>0 自动收起；tone 决定文字配色"""
        self.broadcast_threadsafe({"type": "notice", "text": text or "",
                                   "ms": int(ms), "tone": tone or ""})

    async def serve_forever(self):
        try:
            # origins：挡跨站 WebSocket 劫持（任意网页连本地 WS 读走 Key）。
            # 列表里带 None 表示"没有 Origin 的客户端也放行"，这样本机脚本/调试工具
            # 不受影响 —— 浏览器发起的连接一定会带 Origin，装不了这个空子。
            async with serve(self._handler, HOST, PORT,
                             max_size=2 * 1024 * 1024,
                             origins=[*ALLOWED_ORIGINS, None]) as srv:
                self._server = srv
                await srv.serve_forever()
        except OSError as e:
            print(f"[ws] 端口 {PORT} 被占用（可能已经有一个后端在跑）：{e}", flush=True)


HUB = Hub()


def _compose_stub(intent, raw, zh):
    """占位：模拟模型耗时并返回一条明显是占位的回复"""
    time.sleep(1.2)
    print(f"[ws] compose 占位（真实模型下一步接）: {intent[:20]!r}", flush=True)
    return [
        {"en": "[stub] Want to run it together?",
         "cn": "（占位回复）要不要一起打？"},
        {"en": "[stub] I'm free after this pull.",
         "cn": "（占位回复）拉完这波我就有空。"},
    ]


def start_in_thread():
    """在后台线程里跑 WS 事件循环，返回 Hub"""
    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        HUB.loop = loop
        try:
            loop.run_until_complete(HUB.serve_forever())
        except Exception as e:
            print(f"[ws] 启动失败: {e}", flush=True)

    threading.Thread(target=run, daemon=True, name="ws-server").start()
    # 等事件循环就绪
    for _ in range(50):
        if HUB.loop:
            return HUB
        time.sleep(0.05)
    return HUB


if __name__ == "__main__":
    start_in_thread()
    print(f"WS 监听 ws://{HOST}:{PORT}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("退出")
