# -*- coding: utf-8 -*-
"""主链路：热键 → 检查 → 截屏 → 缓存 → 图片直读（失败则 OCR + 文本模型）。

对应规格 §2 第 1–12 条。
"""

import hashlib
import os
import re
import threading
import time

from . import cache, capture, glossary, last, links, miss_log, model, ocr, win32
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


def _ask_settings_for_capture():
    """没框选区域时把设置窗拉起来，并让设置页高亮「重新框选」那一行。

    以前只在提示条说一句「请在设置中框选」，用户还得自己去找托盘菜单 ——
    首次使用的人根本不知道该点哪儿。
    """
    try:
        from .ws import HUB
        HUB.broadcast_threadsafe({"type": "need_capture"})
    except Exception as e:
        log(f"引导打开设置失败: {e}")


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

        # 0) 暂停开关
        if cfg.get("paused"):
            log("热键已暂停，这次忽略")
            _notice("热键已暂停", 1000)
            return

        # 1) 还没框选过区域：直接提示去设置里框，别去截一张 0×0 的图
        if not _valid_capture(cfg.get("capture")):
            log("还没有设置翻译区域：打开设置引导框选")
            _notice("还没框选聊天区域，已打开设置", 2200)
            _ask_settings_for_capture()
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

        # 模式：ai（视觉模型）/ local（本地词库）
        engine = str(cfg.get("engine") or "ai").lower()
        if engine not in ("ai", "local"):
            engine = "ai"

        # 缓存 key = 图片 + 模型名 + 模式。必须带模式：
        # 否则同一张图 AI 翻过之后，切到本地词库按热键会直接拿回 AI 的结果（反过来也会串）。
        model_name = str((cfg.get("api") or {}).get("model") or "")
        ocr_lang = str((cfg.get("ocr") or {}).get("lang") or "")
        max_lines = str(int(cfg.get("max_lines", 8)))
        ckey = hashlib.md5(png + b"\x00" + model_name.encode("utf-8")
                           + b"\x00" + engine.encode("ascii")
                           + b"\x00" + ocr_lang.encode("utf-8")
                           + b"\x00" + max_lines.encode("ascii")).hexdigest()

        # 3) 缓存
        cached = cache.get(ckey)
        if cached:
            log(f"缓存命中（{time.time() - t0:.1f}s），未消耗额度")
            _clear_notice()
            push(cached)
            return

        log(f"PNG {len(png) / 1024:.0f} KB")

        # 3.5) 本地词库模式：截屏 → OCR → 词库，全程不联网、不要 Key
        if engine == "local":
            payload = _local_payload(cfg, img)
            if not payload:
                log("本地词库：没认出聊天内容")
                _clear_notice()
                _notice("没认出聊天内容，检查框选区域", 2000)
                return
            cache.put(ckey, payload)
            last.save(payload)
            _clear_notice()
            log(f"本地词库完成 {time.time() - t0:.1f}s（未调用模型）")
            push(payload)
            return

        # 4) 图片直读：把截图交给视觉模型（AI 模式唯一的主路径）
        has_key = bool(str((cfg.get("api") or {}).get("key") or "").strip())
        result, vision_err = None, None
        via_vision = False      # 只有"视觉直读"的 raw 才是可信原文（OCR 那份可能更花）
        if not has_key:
            log("没配置 API Key，跳过图片直读")
            vision_err = "还没有配置 API Key"
        else:
            try:
                r = model.vision(cfg, png,
                                 max_lines=int(cfg.get("max_lines", 8)))
                result = _result(r.get("raw", ""), r.get("translation", ""),
                                 r.get("summary", ""), r.get("replies", []))
                result["engine"] = "ai"
                result["mode"] = "ai"
                via_vision = True
            except model.ModelError as e:
                vision_err = str(e)
                log(f"图片直读失败: {e}")

        # 5) 退路：视觉接口不行时，先 OCR 出文字再让文本模型翻（同样走 API）
        if not result and has_key:
            try:
                lines = ocr.recognize_auto(img, (cfg.get("ocr") or {}).get("lang", "en-US"))
                chat = ocr.pick_chat_lines(lines, int(cfg.get("max_lines", 8)))
                chat_text = "\n".join(chat)
                if chat_text:
                    try:
                        r = model.translate_text(cfg, chat_text)
                        result = _result(r["raw"], r["translation"], r["summary"],
                                         r["replies"])
                        result["engine"] = "ai"
                        result["mode"] = "ai"
                        log("OCR + 文本模型成功")
                    except model.ModelError as e:
                        log(f"文本模型失败: {e}")
                        vision_err = vision_err or str(e)
                else:
                    log("OCR 没识别出聊天行")
            except Exception as e:
                log(f"OCR 失败: {e}")

        # 5.5) 链接修复：AI 是看图重新打字，`[[32+] 名称]` 偶尔会少括号/黏在字上，
        # 按原文行把链接 token 替换回去（只动标点和空格，不改其它文字）
        if result and via_vision:
            result["translation"] = links.repair(result.get("raw"),
                                                 result.get("translation"))

        # 到这一步有结果才算"这次翻成功"；下面的失败分支不能进缓存
        ok = result is not None

        # 6) 到这一步还没结果：如实报错（没 Key / 接口失败 / 没认出聊天）
        if not result:
            if not has_key:
                summary = "还没有配置 API Key：在设置里填上 Key 才能翻译"
                # 不想自己切模式，但得让他知道还有一条不花钱的路
                _notice("也可以切到「本地词库」，但只能看大概意思", 3200)
            elif vision_err:
                summary = f"翻译失败：{vision_err}"
            else:
                summary = "没认出聊天内容，检查截图区域"
            result = _result(
                "（没有取到聊天内容）" if not has_key else "—",
                "—",
                summary,
                [],
                need_key=not has_key,
            )
            result["engine"] = "ai"
            log("翻译没成功：" + summary)

        # 7) 落盘 / 缓存 / 推送
        # 只有真正翻成功才缓存：以前"接口超时/没 Key"的失败结果也会缓存 5 分钟，
        # 同一张截图再按一次热键，直接拿回那条"翻译失败"，用户以为程序卡住了。
        if ok and not result.get("need_key"):
            cache.put(ckey, result)
            last.save(result)

        # 翻译途中用户切了模式：结果还是旧模式的，别推上去把弹窗/标签拉回去。
        # 仍然写进"上一次"，用户主动回看时能看到它。
        now_engine = str(_cfg.load().get("engine") or "ai").lower()
        _clear_notice()
        log(f"完成 {time.time() - t0:.1f}s")
        if now_engine != engine:
            log(f"翻译途中模式已切到 {now_engine}，这次 {engine} 的结果不再弹窗")
            return
        push(result)
    except Exception as e:
        log(f"主链路出错: {e}")
        _clear_notice()
        push({"prefix": "", "raw": "", "translation": "", "summary": "",
              "replies": [], "error": str(e)[:150]})
    finally:
        _busy.release()


# ---------------------------------------------------------------- 本地词库（逐词对照）
# 只是"逐词对照"，不是翻译：不联网、不花钱、不需要 Key。
# 09:39 [Fish Box]: 正文 / 09:31 托马斯·米勒说：正文 —— 两种客户端格式都支持
_LINE_SPLIT = re.compile(r"^(\d{1,2}:\d{2}[^:：]*?[:：])\s*(.*)$")

SUMMARY_LOCAL = "本地词库逐词对照，不是完整翻译"


def glossary_result(chat_lines, collect=None):
    """把 OCR 出来的聊天行逐行过词库，返回可直接推给前端的 payload。

    collect：传一个 list 进来，没收录的词会被追加进去（调用方负责写 miss_log）。
    """
    raw, zh_lines, left = [], [], set()
    for line in chat_lines or []:
        raw.append(line)
        m = _LINE_SPLIT.match(line)
        head, body = (m.group(1), m.group(2)) if m else ("", line)
        zh = glossary.local_translate(body, strip_channel=False) if body else ""
        if zh and head:
            zh_lines.append(head + " " + zh)
        else:
            zh_lines.append(zh or head.strip())
        for w in glossary.leftover_english(zh):
            left.add(w.lower())

    if collect is not None:
        collect.extend(sorted(left))

    return {
        "prefix": "本地词库",
        "raw": "\n".join(raw),
        "translation": "\n".join(zh_lines),
        "summary": SUMMARY_LOCAL,
        "replies": [],
        "need_key": False,
        "mode": "local",
    }


_URL_FRAG = re.compile(r"(?:https?://|www\.)\S+|[A-Za-z0-9-]+\.[A-Za-z0-9-]+")


def _clean_miss(words, raw_text):
    """缺词日志只留"真的值得补"的词：去掉网址碎片和疑似玩家名"""
    url_words = set()
    for u in _URL_FRAG.findall(raw_text or ""):
        for w in re.findall(r"[A-Za-z]{2,}", u):
            url_words.add(w.lower())
    # 正文里首字母大写的词多半是玩家/地名，不记
    proper = {w.lower() for w in re.findall(r"(?<![A-Za-z])[A-Z][a-z]{2,}", raw_text or "")}
    out, seen = [], set()
    for w in words:
        lw = w.lower()
        if lw in url_words or lw in proper or lw in seen:
            continue
        seen.add(lw)
        out.append(lw)
    return out


def _local_payload(cfg, img):
    """截好的图 → OCR → 词库；没认出聊天返回 None"""
    lines = ocr.recognize_auto(img, (cfg.get("ocr") or {}).get("lang", "zh-Hans-CN"))
    chat = ocr.pick_chat_lines(lines, int(cfg.get("max_lines", 8)))
    if not chat:
        return None
    miss = []
    payload = glossary_result(chat, collect=miss)
    miss = _clean_miss(miss, payload.get("raw", ""))
    payload["prefix"] = "本地词库 · " + time.strftime("%H:%M:%S")
    if miss:
        try:
            miss_log.add(miss)
            # 立刻刷盘：不然要攒够 20 个词才写，用户按一次根本看不到日志
            miss_log.flush()
        except Exception:
            pass
    return payload


def show_last(push, notify=None):
    """小键盘 - ：把上一次结果重新推出来"""
    ts, result = last.load()
    if not result:
        log("还没有翻译记录")
        # 首次使用：多半连聊天区域都还没框，顺手把设置打开引导一下
        try:
            from . import config as _cfg
            if not _valid_capture(_cfg.load().get("capture")):
                _ask_settings_for_capture()
                if notify:
                    notify("还没框选聊天区域，已打开设置", 2200)
                return
        except Exception:
            pass
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
