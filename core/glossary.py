# -*- coding: utf-8 -*-
"""本地词库翻译（免费模式的第一层，也是 AI 全挂时的最后兜底）。

两层：
  1. 句式模板 —— 把高频句子结构整段换掉，读起来像人话
  2. 术语替换 —— 剩下的词按最长优先换成中文

词表来自 core/terms_data.py（1124 条，BabelChat + wow-translate 合并，均 MIT）。
这里只放少量「更口语」的偏好覆盖，改动请写进 OVERRIDES。
"""

import re

from .terms_data import TERMS as _UPSTREAM

# 上游词按「正式译名」给，聊天里更习惯短的说法，这里覆盖
OVERRIDES = {
    "lfm": "缺人",
    "lfg": "求组",
    "lf1m": "缺1人",
    "inv": "组我",
    "pst": "私聊我",
    "cod": "到付",
    "boe": "装绑",
    "bis": "毕业装",
    # 模型容易翻错/不翻的游戏名词，补上
    "trainer": "训练师",
    "naxx": "纳克萨玛斯",
    "gg": "打得好",
    "ty": "谢谢",
    "tank": "坦克",
    "healer": "治疗",
    "heal": "治疗",
    "dps": "输出",
    "aoe": "群攻",
    "oom": "没蓝了",
    "wipe": "团灭",
    "brez": "战复",
    "enchant": "附魔",
    "enchants": "附魔",
    "cool": "酷",
    "each": "每个",
    "lfg": "求组",
    "sigil": "徽记",
    "dog": "兄弟",
    "mate": "兄弟",
}

# 聊天里的高频虚词/代词，上游词表没有，但翻出来能顺不少
COMMON = {
    "the": "", "a": "", "an": "", "for": "去", "to": "", "of": "",
    "i": "我", "im": "我", "i'm": "我", "me": "我", "my": "我的",
    "you": "你", "u": "你", "your": "你的", "we": "我们", "they": "他们",
    "is": "是", "are": "是", "am": "是", "was": "是", "be": "是",
    "can": "能", "could": "能", "will": "会", "would": "会",
    "want": "想", "wanna": "想", "need": "需要", "have": "有", "has": "有",
    "in": "在", "on": "在", "at": "在", "with": "和", "and": "和",
    "all": "各位", "everyone": "各位", "guys": "各位", "someone": "有人",
    "now": "现在", "here": "这里", "there": "那里", "this": "这个",
    "that": "那个", "it": "它", "just": "就", "still": "还",
    "good": "好", "great": "很好", "nice": "不错", "bad": "糟",
    "yes": "是", "no": "不", "ok": "好", "okay": "好", "sure": "当然",
    "up": "", "next": "下一个", "again": "再来一次", "much": "多少",
}

# 聊天里最常见的普通词（术语表没有的），补上能显著减少"半英半中"
EXTRA_COMMON = {
    "think": "觉得", "thought": "想", "know": "知道", "knew": "知道",
    "most": "大多数", "more": "更多", "less": "更少", "much": "多少",
    "many": "很多", "lot": "很多", "lots": "很多", "bit": "一点", "little": "一点",
    "game": "游戏", "games": "游戏", "play": "玩", "played": "玩了",
    "playing": "在玩", "players": "玩家", "player": "玩家",
    "pretty": "挺", "fun": "好玩", "funny": "搞笑", "when": "当",
    "dont": "别", "don't": "别", "doesnt": "不", "doesn't": "不",
    "didnt": "没", "didn't": "没", "cant": "不能", "can't": "不能",
    "wont": "不会", "won't": "不会", "im": "我", "ive": "我",
    "go": "去", "goes": "去", "going": "要", "went": "去了", "gone": "走了",
    "about": "关于", "part": "部分", "lol": "哈哈", "tbh": "说实话",
    "imo": "我觉得", "actually": "其实", "really": "真的", "even": "甚至",
    "things": "东西", "thing": "东西", "stuff": "东西",
    "people": "人", "anyone": "有人", "somebody": "有人", "nobody": "没人",
    "server": "服务器", "realm": "服务器", "lvl": "等级",
    "get": "拿", "got": "拿到", "give": "给", "take": "拿",
    "make": "做", "made": "做了", "use": "用", "used": "用了",
    "work": "工作", "works": "有效", "come": "来", "coming": "来",
    "kill": "杀", "killed": "杀了", "die": "死", "died": "死了", "dead": "死了",
    "sell": "卖", "buy": "买", "try": "试", "help": "帮忙", "wait": "等",
    "start": "开始", "stop": "停", "finish": "完成", "done": "完成",
    "sorry": "抱歉", "please": "请", "pls": "请", "plz": "请",
    "maybe": "也许", "right": "对", "yeah": "对", "yep": "对", "nope": "不",
    "love": "喜欢", "like": "喜欢", "hate": "讨厌", "better": "更好",
    "best": "最好", "worse": "更差", "worst": "最差",
    "always": "总是", "never": "从不", "sometimes": "有时",
    "today": "今天", "tomorrow": "明天", "yesterday": "昨天",
    "night": "晚上", "morning": "早上", "day": "天", "week": "周",
    "hour": "小时", "minute": "分钟", "mins": "分钟",
    "first aid": "急救", "firstaid": "急救",
    "tyvm": "谢谢", "goat": "最强", "ggs": "打得好",
    # 上游词表里 whisper 的数据坏了（会输出"密密密密密密"）
    "whisper": "密语", "whisper me": "密我",
}

TERMS = dict(_UPSTREAM)
TERMS.update(OVERRIDES)
TERMS.update(COMMON)
TERMS.update(EXTRA_COMMON)

# 注意：不能用 \b —— 中文字符在 Python 里也算 \w，"个tank" 的 \b 不成立，
# 会导致紧挨中文的术语替换不上。改用「前后不是英文字母」判断。
_TERM_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(sorted((re.escape(k) for k in TERMS if k),
                                        key=len, reverse=True)) + r")(?![A-Za-z])",
    re.IGNORECASE,
)

# 频道前缀：[1. Trade] / [Party] / [公会] 之类
_CHANNEL_RE = re.compile(r"^\s*\[\s*\d+\.\s*[^\]]{1,20}\]\s*|^\s*\[[^\]]{1,12}\]\s*")

# 句式模板：先整句改写，再交给术语替换
_PHRASES = [
    # 组队喊话
    (r"\blfm\s+(\d+)\s*(?:more\s+)?(tank|tanks|healer|healers|heal|dps)\b", r"缺\1个\2"),
    (r"\blf(?:g)?(\d)m\b(?:\s+(tank|healer|heal|dps))?", r"缺\1人"),
    (r"\blfm\b", "缺人"),
    (r"\blfg\b", "求组"),
    # 交易
    (r"\bwts\b", "出售"),
    (r"\bwtb\b", "收购"),
    (r"\bwtt\b", "交换"),
    # 找人
    (r"\bany\s?one\s+(?:up\s+for|want\s+to\s+do|wanna\s+do|for)\s+(.+?)\s*\?", r"有人去\1吗？"),
    (r"\bany\s?one\s+(?:up\s+for|want\s+to\s+do|wanna\s+do|for)\s+(.+)$", r"有人去\1吗"),
    (r"\bany\s?one\s+interested\s+in\s+(.+?)\s*\?", r"有人对\1感兴趣吗？"),
    # 请求
    (r"\b(?:can|could)\s+(?:i|you)\s+(?:get|have)\s+(?:an?\s+)?(?:inv|invite)\b\??", "能组我一下吗？"),
    (r"\binv(?:ite)?\s+me\b", "组我"),
    (r"\binv\b", "组我"),
    # 「动词 + me」要整段换：先换 pst 再换 me 会拼出「私聊我我」
    (r"\bpst\s+me\b", "密我"),
    (r"\bcod\s+me\b", "货到付款给我"),
    (r"\bpst\b", "私聊我"),
    (r"\bneed\s+(?:a\s+)?(tank|healer|heal|dps)\b", r"需要\1"),
    (r"\bstill\s+need\s+(?:a\s+)?(.+?)\?*\s*$", r"还缺\1吗"),
    # 礼貌/状态
    (r"\bty\b|\bthx\b|\bthanks\b", "谢谢"),
    (r"\bnp\b|\byw\b", "不客气"),
    (r"\bbrb\b", "马上回来"),
    (r"\b(\d+)\s*min\b", r"\1 分钟"),
    (r"\bafk\b", "暂离"),
    (r"\bomw\b", "马上到"),
    (r"\bgg\b", "打得好"),
    (r"\bgl\b|\bglhf\b", "好运"),
    (r"\byou\s+too\b", "你也是"),
    (r"\bgood\s+luck\b", "好运"),
    (r"\bhow\s+much\b", "多少钱"),
    (r"\bhow\s+many\b", "多少个"),
    (r"\bwhere\s+(?:is|are)\b", "在哪"),
    (r"\bwhat\s+do\s+you\s+mean\b|\bwdym\b", "什么意思"),
    (r"\bany\s+healer\b", "有治疗吗"),
    (r"\bany\s+tank\b", "有坦克吗"),
]

_PHRASE_RULES = [(re.compile(p, re.IGNORECASE), r) for p, r in _PHRASES]


_URL_RE = re.compile(r"(?:https?://|www\.)[^\s，。；、]+|"
                    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/[^\s，。；、]*")


def local_translate(text, strip_channel=True):
    """把一句英文尽量变成中文：去频道前缀 → 套句式模板 → 替换术语

    strip_channel=False 用于"正文已经拆出来"的场景：正文开头的 [First Aid]
    是技能名，不是频道，删掉就漏翻了。
    """
    s = (text or "").strip()
    if not s:
        return ""
    if strip_channel:
        s = _CHANNEL_RE.sub("", s).strip()

    # 网址原样保留：逐词替换会把 .../help/article/... 里的 help 翻成"帮忙"
    urls = []

    def _keep_url(m):
        urls.append(m.group(0))
        return "\u0000%d\u0000" % (len(urls) - 1)

    s = _URL_RE.sub(_keep_url, s)

    for rx, rep in _PHRASE_RULES:
        s = rx.sub(rep, s)
    for rx, rep in _PHRASE_RULES:      # 第二轮，处理模板替换后新露出的词
        if rx.search(s):
            s = rx.sub(rep, s)

    def _rep(m):
        return TERMS.get(m.group(1).lower(), m.group(1))

    s = _TERM_RE.sub(_rep, s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = re.sub(r"^[，、\s]+|[，、\s]+$", "", s)
    # 中文之间不留空格
    s = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])", "", s)
    s = re.sub(r"[ \t]+(?=[，。？！、：；])", "", s)

    for i, u in enumerate(urls):
        s = s.replace("\u0000%d\u0000" % i, u)
    return s


def leftover_english(text):
    """没被翻译掉、仍然像英文单词的片段"""
    words = re.findall(r"[A-Za-z][A-Za-z']{2,}", text or "")
    return [w for w in words if w.lower() not in TERMS]


def translate_chat_lines(lines):
    """逐行翻译，返回 (中文行列表, 未识别词列表)"""
    out, left = [], set()
    for line in lines or []:
        zh = local_translate(line)
        out.append(zh)
        for w in leftover_english(zh):
            left.add(w.lower())
    return out, sorted(left)[:12]
