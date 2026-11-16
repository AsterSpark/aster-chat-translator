# -*- coding: utf-8 -*-
"""主链路：热键 → 检查 → 截屏 → 缓存 → 图片直读 → OCR 降级 → 词库兜底。

对应规格 §2 第 1–12 条。
"""

import hashlib
import os
import threading
import time

from . import cache, capture, glossary, last, local_engine, model, ocr, win32
from . import usage
from .log import log

_busy = threading.Lock()

MSG_TRANSLATING = "翻译中"


def _test_image():
    """测试钩子：WOWTR_TEST_IMAGE=图片路径 时用这张图代替截屏（避免把桌面发给模型）"""
    p = os.environ.get("WOWTR_TEST_IMAGE")
    if p and os.path.exists(p):
        from PIL import Image
        return Image.open(p).convert("RGB")
    return None


def _result(raw, zh, summary, replies, need_key=False):
    return {"prefix": "", "raw": raw, "translation": zh, "summary": summary,
            "replies": replies, "need_key": need_key}


def _valid_capture(c):
    """框选区域是否可用（首次安装是 None，用户还没框过）"""
    if not isinstance(c, dict):
        return False
    try:
        return (int(c["right"]) - int(c["left"]) >= 40
                and int(c["bottom"]) - int(c["top"]) >= 20)
    except Exception:
        return False


def translate(cfg, push, notify=None):
    """跑一次完整翻译。push(payload) 推结果；notify(text, ms) 推提示条。"""
    if not _busy.acquire(blocking=False):
        log("上一次还没跑完，忽略这次热键")
        return

    def _notice(text, ms=0):
        if notify:
            try:
                notify(text, ms)
            except Exception:
                pass

    # 提示条延迟 150ms 再弹「翻译中」：命中缓存时整个流程 <20ms，先弹再立刻
    # 收起会让屏幕顶部闪一帧（实测提示条只可见 16ms）。慢调用照样看得到。
    toast_timer = {"t": None}

    def _start_toast():
        t = threading.Timer(0.15, lambda: _notice(MSG_TRANSLATING))
        t.daemon = True
        toast_timer["t"] = t
        t.start()

    def _clear_notice():
        t = toast_timer["t"]
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass
            toast_timer["t"] = None
        _notice("", 1)

    try:
        t0 = time.time()

        # 每次按热键都重新读配置：这样「暂停」「框选区域」「游戏检测」改完立即生效
        from . import config as _cfg
        cfg = _cfg.load()

        # 0) 设置窗口开着就不翻译，省额度
        # 状态必须从 core/state.py 读：定义在 server.py 的话，`py -m core.server`
        # 启动时这里会 import 到另一份副本，守卫等于不存在（实测开窗照翻）。
        from . import state
        if state.settings_open():
            log("设置窗口开着，跳过翻译")
            _notice("设置打开中，暂不翻译", 1200)
            return

        # 0) 暂停开关
        if cfg.get("paused"):
            log("热键已暂停，这次忽略")
            _notice("热键已暂停", 1000)
            return

        # 1) 还没框选过区域：直接提示去设置里框，别去截一张 0×0 的图
        if not _valid_capture(cfg.get("capture")):
            log("还没有设置翻译区域，跳过")
            _notice("请在设置中框选聊天窗口区域", 2200)
            return

        # 2) 游戏状态
        if cfg.get("require_game", True) and os.environ.get("WOWTR_NO_GAMECHECK") != "1":
            gstate, msg = win32.game_state(cfg.get("game_process", "Wow.exe"),
                                           cfg.get("require_foreground", True))
            if gstate != "ok":
                log(f"跳过：{msg}")
                _notice(msg, 1300)
                return

        # 走到这里才是真翻一次。前面那些 "跳过"（暂停 / 无游戏 / 设置在开）
        # 不计入次数，否则托盘里的「本次 N 次」会被没翻译的按键灌水。
        try:
            usage.add_translate()      # 命中缓存也算一次：用户确实按了、也确实出了结果
        except Exception:
            pass

        # 提示条延迟 150ms 再弹「翻译中」：命中缓存时整个流程 <20ms，
        # 先弹再立刻收起会让屏幕顶部闪一帧（实测提示条只可见 16ms）。
        # 慢调用（AI 2–3 秒）照样看得到，不影响原有反馈。
        _start_toast()

        # 2) 截屏
        img = _test_image()
        if img is None:
            img = capture.grab(cfg)
        log(f"截图 {img.size}")

        png = capture.prepare_png(img)

        # 引擎要在查缓存之前定下来：缓存 key 必须带上引擎和模型，否则切了模式
        # 还会命中上一次模式的结果，顶栏 chip 也会被那条结果的 engine 改回去，
        # 界面显示的模式与实际配置不一致（实测：切离线后弹 AI 译文 + chip 翻回 AI）。
        engine = (cfg.get("engine") or "auto").lower()
        if engine == "auto":
            engine = "ai" if (cfg.get("api") or {}).get("key") else "offline"
        model_name = str((cfg.get("api") or {}).get("model") or "")
        ckey = hashlib.md5(
            png + b"\x00" + engine.encode("utf-8") + b"\x00" + model_name.encode("utf-8")
        ).hexdigest()

        # 3) 缓存
        cached = cache.get(ckey)
        if cached:
            log(f"缓存命中（{time.time() - t0:.1f}s），未消耗额度")
            _clear_notice()
            push(cached)
            return

        log(f"PNG {len(png) / 1024:.0f} KB")

        # 4) 图片直读（免费/离线模式直接跳过，省额度；engine 在上面已解析）

        result, vision_err = None, None
        if engine == "ai":
            try:
                r = model.vision(cfg, png)
                result = _result(r.get("raw", ""), r.get("translation", ""),
                                 r.get("summary", ""), r.get("replies", []))
                result["engine"] = "ai"
            except model.ModelError as e:
                vision_err = str(e)
                log(f"图片直读失败: {e}")

        # 5) OCR 降级（AI 模式下的备用；离线模式下的主路径）
        if not result:
            try:
                lines = ocr.recognize(img, (cfg.get("ocr") or {}).get("lang", "en-US"))
                chat = ocr.pick_chat_lines(lines, int(cfg.get("max_lines", 12)))
                chat_text = "\n".join(chat)
                if chat_text:
                    if engine == "ai":
                        try:
                            r = model.translate_text(cfg, chat_text)
                            result = _result(r["raw"], r["translation"], r["summary"],
                                             r["replies"])
                            result["engine"] = "ai"
                            log("OCR + 文本模型成功")
                        except model.ModelError as e:
                            log(f"文本模型失败: {e}")
                            vision_err = vision_err or str(e)
                    else:
                        # 离线：本地模型翻整句，没有模型就直接词库
                        zh_lines, engines = local_engine.translate_lines(chat)
                        if zh_lines and all(zh_lines):
                            result = _result(chat_text, "\n".join(zh_lines),
                                             "（离线模型翻译，可能不准）", [])
                            result["engine"] = ("local" if "model" in engines
                                               else "glossary")
                            log("OCR + 离线模型成功")
                        else:
                            log("离线模型不可用")
                else:
                    log("OCR 没识别出聊天行")
            except Exception as e:
                log(f"OCR 失败: {e}")

        # 6) 词库兜底
        if not result:
            try:
                lines = ocr.recognize(img, (cfg.get("ocr") or {}).get("lang", "en-US"))
                chat = ocr.pick_chat_lines(lines, int(cfg.get("max_lines", 12)))
            except Exception:
                chat = []
            out_lines, left = glossary.translate_chat_lines(chat)
            result = _result(
                "\n".join(chat) or "（未识别到聊天内容，检查截图区域设置）",
                "\n".join(out_lines) or "（无法识别）",
                ("未识别词：" + " ".join(left)) if left else "（本地词库翻译）",
                [],
                need_key=(engine == "ai"),
            )
            result["engine"] = "glossary"
            log("走词库兜底")

        # 7) 落盘 / 缓存 / 推送
        if not result.get("need_key"):
            cache.put(ckey, result)
            last.save(result)
        _clear_notice()
        log(f"完成 {time.time() - t0:.1f}s")
        push(result)
    except Exception as e:
        log(f"主链路出错: {e}")
        _clear_notice()
        push({"prefix": "", "raw": "", "translation": "", "summary": "",
              "replies": [], "error": str(e)[:150]})
    finally:
        _busy.release()


def show_last(push, notify=None):
    """小键盘 - ：把上一次结果重新推出来"""
    ts, result = last.load()
    if not result:
        log("还没有翻译记录")
        if notify:
            notify("还没有翻译记录", 1300)
        return
    result = dict(result)
    result["prefix"] = f"上一次翻译 · {ts}"
    log(f"回看上一次（{ts}）")
    push(result)


def start_hotkeys(cfg, push, notify=None):
    """按配置装两个热键：翻译 / 看上一次"""
    if not cfg.get("keyboard_hook", True):
        log("配置里关闭了键盘钩子")
        return []

    hooks = []
    hk_cfg = cfg.get("hotkey") or {}

    tk = str(hk_cfg.get("translate_key") or "num_add").lower()
    vk_t = win32.VK_MAP.get(tk)
    if vk_t:
        def _on_translate(_vk):
            log("热键触发（翻译）")
            threading.Thread(target=translate, args=(cfg, push, notify),
                             daemon=True).start()
        h = win32.Hotkey(vk_t, _on_translate)
        if h.start():
            hooks.append(h)
    else:
        log(f"看不懂的翻译热键名: {tk}")

    lk = str(hk_cfg.get("last_key") or "num_sub").lower()
    vk_l = win32.VK_MAP.get(lk)
    if vk_l:
        def _on_last(_vk):
            log("热键触发（看上一次）")
            show_last(push, notify)
        h2 = win32.Hotkey(vk_l, _on_last)
        if h2.start():
            hooks.append(h2)
    return hooks
