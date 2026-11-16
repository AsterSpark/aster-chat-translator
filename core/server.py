# -*- coding: utf-8 -*-
"""本地服务：静态页面（HTTP）+ 双向消息（WebSocket）。

端口分配：
- 17650 HTTP：托管 app 目录，方便浏览器打开调试；也留了 POST /show 方便命令行推数据
- 17651 WS ：前后端双向，弹窗数据推送 + 「我想说」请求

只监听 127.0.0.1，不对外。
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as cfg_mod
from . import state
from . import ws
from .log import log
from .origins import ALLOWED as ALLOWED_ORIGINS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT, "app")
HTTP_PORT = int(os.environ.get("WOWTR_PORT", "17650"))

DEMO = {
    "prefix": "上一次翻译 · 2026-11-14 08:09:51",
    "raw": "08:08 [1] [Immaculate Magistrate]: SW has a druid trainer right? just IF does not\n"
           "08:08 [1] [Chef Donnie]: correct\n"
           "08:08 [1] [Naiturh Face]: he is over by the park, next to the AH",
    "translation": "08:08 Immaculate Magistrate: 暴风城有德鲁伊训练师吧？只有铁炉堡没有\n"
                   "08:08 Chef Donnie: 没错\n"
                   "08:08 Naiturh Face: 德鲁伊训练师在公园那边",
    "summary": "综合频道闲聊，几个玩家在聊德鲁伊训练师位置，不用理会。",
    "replies": [],
    "need_key": False,
}


def push(payload):
    """推一条弹窗数据给所有已连接页面"""
    ws.HUB.last_payload = payload
    ws.HUB.push_show(payload)


def notify(text, ms=0):
    """提示条（翻译中 / 游戏未运行 / 还没有翻译记录…）"""
    ws.HUB.push_notice(text, ms)


def push_settings(cfg):
    """配置变了就广播（前端据此刷新界面）"""
    ws.HUB.broadcast_threadsafe({"type": "settings",
                                 "cfg": cfg_mod.public_view(cfg)})


CAPTURE_PREVIEW = os.path.join(cfg_mod.PROJECT_ROOT, "capture_preview.png")


def _capture_test():
    """截一张当前区域，存成 png 供设置面板预览"""
    from . import capture
    try:
        img = capture.grab(cfg_mod.load())
        img.save(CAPTURE_PREVIEW)
        log(f"测试截图 {img.size} -> capture_preview.png")
    except Exception as e:
        log(f"测试截图失败: {e}")


class CoreHTTP(ThreadingHTTPServer):
    # Windows 下 allow_reuse_address=True 会让两个进程同时绑同一端口（会被静默抢端口），
    # 这里关掉，第二个实例会明确报"端口被占用"。
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    server_version = "WoWTrCore/0.1"

    def log_message(self, fmt, *args):
        if args and str(args[1]) >= "400":
            sys.stderr.write("[http] %s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # 打包后页面在 tauri.localhost，调本地接口是跨域，只对自己的页面放行
        origin = self.headers.get("Origin") if self.headers else None
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Vary", "Origin")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin") if self.headers else None
        if origin and origin not in ALLOWED_ORIGINS:
            self._send(403, b"forbidden origin")
            return
        self._send(204, b"")

    def _serve_file(self, rel):
        path = os.path.normpath(os.path.join(APP_DIR, rel))
        if not path.startswith(APP_DIR) or not os.path.isfile(path):
            self._send(404, b"not found")
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        # 去掉查询串，避免把 ?v=2 当成文件名
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._serve_file("index.html")
            return
        # 注意：只能精确匹配 /logs，别把 /logs.html、/logs.css 也吃掉
        if self.path in ("/logs", "/logs?") or self.path.startswith("/logs?"):
            from .log import LOG_PATH
            try:
                with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()[-500:]
                body = "".join(lines).encode("utf-8")
            except Exception:
                body = "（还没有日志）\n".encode("utf-8")
            self._send(200, body, "text/plain; charset=utf-8")
            return
        if path == "/logpath":
            from .log import LOG_PATH
            body = json.dumps({"path": LOG_PATH}, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if self.path.startswith("/capture_preview.png"):
            if os.path.isfile(CAPTURE_PREVIEW):
                with open(CAPTURE_PREVIEW, "rb") as f:
                    self._send(200, f.read(), "image/png")
            else:
                self._send(404, b"no preview")
            return
        self._serve_file(path.lstrip("/"))

    def do_POST(self):
        # 跨站请求直接拒：CORS 只挡"读响应"，挡不住"把请求发出去"，
        # 所以任何外部 Origin 的 POST 一律 403（本机脚本不带 Origin，不受影响）。
        origin = self.headers.get("Origin") if self.headers else None
        if origin and origin not in ALLOWED_ORIGINS:
            self._send(403, b"forbidden origin")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.path == "/demo":
            push(DEMO)
            self._send(200, b'{"ok":true}', "application/json; charset=utf-8")
            return
        if self.path == "/show":
            try:
                payload = json.loads(raw.decode("utf-8", "replace"))
            except Exception as e:
                self._send(400, json.dumps({"error": str(e)}).encode())
                return
            push(payload)
            self._send(200, b'{"ok":true}', "application/json; charset=utf-8")
            return
        if self.path == "/translate":
            # 自检接口：用 WOWTR_TEST_IMAGE / WOWTR_NO_GAMECHECK 可在无游戏环境跑一次主链路
            import threading
            from . import pipeline
            threading.Thread(target=pipeline.translate,
                             args=(cfg_mod.load(), push, notify), daemon=True).start()
            self._send(200, b'{"ok":true}', "application/json; charset=utf-8")
            return
        if self.path == "/show_last":
            import threading
            from . import pipeline
            threading.Thread(target=pipeline.show_last,
                             args=(push, notify), daemon=True).start()
            self._send(200, b'{"ok":true}', "application/json; charset=utf-8")
            return
        if self.path.startswith("/window_settings"):
            open_q = self.path.split("open=", 1)[-1] if "open=" in self.path else "0"
            now = state.set_settings_open(open_q.startswith("1"))
            log("设置窗口 " + ("打开" if now else "关闭"))
            self._send(200, b'{"ok":true}', "application/json; charset=utf-8")
            return
        if self.path.startswith("/debug"):
            from urllib.parse import unquote
            msg = unquote(self.path.split("msg=", 1)[-1]) if "msg=" in self.path else ""
            if msg:
                log("[ui] " + msg)
            self._send(200, b'{"ok":true}', "application/json; charset=utf-8")
            return
        if self.path == "/toggle_pause":
            cfg = cfg_mod.load()
            cfg["paused"] = not bool(cfg.get("paused"))
            cfg_mod.save(cfg)
            log("热键已" + ("暂停" if cfg["paused"] else "恢复"))
            push_settings(cfg)
            body = json.dumps({"ok": True, "paused": cfg["paused"]},
                              ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if self.path == "/capture_test":
            # 截一张当前配置区域的图，给设置面板预览
            import threading
            threading.Thread(target=_capture_test, daemon=True).start()
            self._send(200, b'{"ok":true}', "application/json; charset=utf-8")
            return
        self._send(404, b"not found")


def _watch_parent():
    """外壳被强杀时，后端别变孤儿进程：父进程没了就自己退"""
    pid = os.environ.get("WOWTR_PARENT_PID")
    if not pid:
        return
    import ctypes
    import threading
    import time as _t

    kernel32 = ctypes.windll.kernel32
    SYNCHRONIZE = 0x00100000
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
    if not handle:
        return

    def loop():
        kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0xFFFFFFFF)
        print("[core] 外壳已退出，后端跟着退出", flush=True)
        os._exit(0)

    threading.Thread(target=loop, daemon=True, name="parent-watch").start()


def serve(port=HTTP_PORT, demo=False):
    # 命令行开关（不用设环境变量，方便试跑）
    #   --no-gamecheck        跳过「Wow.exe 在前台」检查
    #   --test-image 路径      用一张图代替截屏
    argv = sys.argv[1:]
    if "--no-gamecheck" in argv:
        os.environ["WOWTR_NO_GAMECHECK"] = "1"
        print("[core] 已跳过游戏检测（--no-gamecheck）")
    if "--test-image" in argv:
        i = argv.index("--test-image")
        if i + 1 < len(argv):
            os.environ["WOWTR_TEST_IMAGE"] = argv[i + 1]
            print(f"[core] 用测试图代替截屏：{argv[i + 1]}")

    cfg = cfg_mod.load()
    ws.start_in_thread()
    try:
        httpd = CoreHTTP(("127.0.0.1", port), Handler)
    except OSError as e:
        print(f"[core] 端口 {port} 被占用（可能已经有一个后端在跑）：{e}")
        print("[core] 先把旧的关掉（Ctrl+C），再重启本程序。")
        return
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    print(f"[core] 配置来源: {cfg_mod.which_path() or '默认值'}")
    print(f"[core] 页面 {url}")
    print(f"[core] WS   ws://127.0.0.1:{ws.PORT}")

    # 主链路：热键 → 截屏 → 图片直读 → 推送
    hooks = []

    def rebind(new_cfg):
        """设置保存后重绑热键"""
        from . import pipeline
        for h in hooks:
            try:
                h.stop()
            except Exception:
                pass
        hooks.clear()
        hooks.extend(pipeline.start_hotkeys(new_cfg, push, notify))
        print(f"[core] 热键已重绑：{new_cfg['hotkey']['translate_key']} / "
              f"{new_cfg['hotkey']['last_key']}", flush=True)

    ws.SETTINGS_HOOK = rebind

    if "--no-hotkey" not in sys.argv:
        from . import pipeline
        hooks.extend(pipeline.start_hotkeys(cfg, push, notify))
        if hooks:
            print(f"[core] 热键已就绪：{cfg['hotkey']['translate_key']} / "
                  f"{cfg['hotkey']['last_key']}")

    if demo:
        import threading
        threading.Timer(1.5, lambda: push(DEMO)).start()
    _watch_parent()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[core] 退出")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    serve(demo="--demo" in sys.argv)
