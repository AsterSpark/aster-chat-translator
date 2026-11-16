# -*- coding: utf-8 -*-
"""OCR 兜底：图片直读失败时，先把文字识别出来（规格 §2 第 9 条）。

用 Windows 自带 OCR（winocr），不联网、不花钱。

语言选择（实测数据，见下）：
    同一张「中文客户端」聊天图（频道名中文、正文英文）：
        en-US      → 正文完美，但 [队伍] 认成 [ßAff]、[1. 交易] 认成 [1 .
        zh-Hans-CN → 频道名和正文都认对，代价是全角冒号 00：10、中文之间多空格
    所以默认用 zh-Hans-CN，再把全角标点和中文间空格归一化回客户端原样。
    没装中文语言包时自动退回 en-US，不会因为语言包缺失就整个 OCR 失败。
"""

import re

from .log import log

DEFAULT_LANG = "zh-Hans-CN"
FALLBACK_LANG = "en-US"

# 全角 → 半角（只处理会破坏后续解析的几种）
_FULLWIDTH = {
    "：": ":", "。": ".", "，": ",", "［": "[", "］": "]",
    "　": " ", "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
}

# 中文字符之间被 OCR 插进去的空格： [ 队 伍 ] → [队伍]
_CJK_GAP = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
# 方括号贴着中文时空格也去掉：[ 队伍 ] → [队伍]
_BRACKET_GAP = re.compile(r"\[\s*([\[\]\u4e00-\u9fff0-9.\s]*?)\s*\]")


def _fmt_bracket(m):
    inner = re.sub(r"\s*\.\s*", ".", m.group(1).strip())
    return "[" + inner + "]"


def normalize_line(line):
    """把 OCR 的全角标点、多余空格修回客户端原样。"""
    s = line or ""
    for a, b in _FULLWIDTH.items():
        s = s.replace(a, b)
    s = _CJK_GAP.sub("", s)
    s = _BRACKET_GAP.sub(_fmt_bracket, s)
    # 时间戳 00:10 中间被拆开的空格
    s = re.sub(r"^(\d{1,2})\s*:\s*(\d{2})", r"\1:\2", s)
    return s.strip()


def _run(img, lang):
    import winocr
    res = winocr.recognize_pil_sync(img, lang)
    if isinstance(res, dict):
        return [l.get("text", "") if isinstance(l, dict) else str(l)
                for l in (res.get("lines") or [])]
    return [l.text for l in res.lines]


def recognize(img, lang=None):
    """返回识别到的文本行（已归一化）。语言包缺失时自动退回 en-US。"""
    lang = (lang or DEFAULT_LANG).strip() or DEFAULT_LANG
    try:
        lines = _run(img, lang)
    except Exception as e:
        if lang != FALLBACK_LANG:
            log(f"OCR 语言 {lang} 不可用（{type(e).__name__}），退回 {FALLBACK_LANG}")
            try:
                lines = _run(img, FALLBACK_LANG)
            except Exception as e2:
                log(f"OCR 失败: {e2}")
                return []
        else:
            log(f"OCR 失败: {e}")
            return []
    return [normalize_line(x) for x in lines]


_CHAT_LINE = re.compile(r"^\s*\d{1,2}[:：]\d{2}")
_HAS_SPEAKER = re.compile(r"\]\s*\[?[^\]]{2,20}\]?\s*[:：]")


SYSTEM_RE = re.compile(
    r"(系统|系統|System|You have learned|You receive|You've earned|"
    r"Congratulations|Your .* has|joined the channel|left the channel|"
    r"已加入频道|离开了频道|你获得了|恭喜|成就达成|奖励|"
    r"|Hachievement|Received item|Loot|经验值|荣誉点数)",
    re.I,
)


def pick_chat_lines(lines, max_lines=12):
    """只保留像聊天行的内容：先按时间戳/说话人筛，筛不出就取长一点的短句"""
    chat = [l for l in lines if _CHAT_LINE.match(l) or _HAS_SPEAKER.search(l)]
    if not chat:
        chat = [l for l in lines if len(l.strip()) > 6]
    chat = [l.strip() for l in chat if l.strip()]
    if max_lines and len(chat) > max_lines:
        chat = chat[-max_lines:]
    log(f"OCR 命中 {len(chat)} 行")
    return chat
