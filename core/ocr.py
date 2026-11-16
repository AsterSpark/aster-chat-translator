# -*- coding: utf-8 -*-
"""OCR 兜底：图片直读失败时，先把文字识别出来（规格 §2 第 9 条）。

用 Windows 自带 OCR（winocr），不联网、不花钱。

语言选择（v0.1.2 起改成"两套都跑、按行择优"，原因见下）：
    同一张「中文客户端」聊天图（频道名中文、正文英文）：
        en-US      → 英文正文更准，但 [队伍] 认成 [ßAff]、[1. 交易] 认成 [1 .
        zh-Hans-CN → 中文频道名准，但英文正文容易被啃：think→th ink、
                     Maomaos→00m00s、gold→g01d
    两边各有各的坏法，所以 recognize_auto() 会把同一张图用两套语言包各认一遍，
    按行 y 坐标对齐后逐行挑更像样的那份（见 _pick_text）。
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


# 时间戳里常见的字母/数字混淆：19:so → 19:50、os:S9 → 05:59
_TS_LOOKALIKE = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1",
                               "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2",
                               "q": "9", "g": "9"})
# 小时位也允许是"长得像数字的字母"：游戏字体下 05:59 常被认成 os:S9 / oS:S9
_TS_FIX = re.compile(r"^([0-9A-Za-z]{1,2})\s*[:：]\s*([0-9A-Za-z]{2})(?=\s|\[|$)")


def _fix_timestamp(s):
    m = _TS_FIX.match(s or "")
    if not m:
        return s
    # 换算后必须全是数字才采纳，避免把 "hi:xx" 这种正文误改成时间
    hour = m.group(1).translate(_TS_LOOKALIKE)
    minute = m.group(2).translate(_TS_LOOKALIKE)
    if not (hour.isdigit() and minute.isdigit()):
        return s
    # 换算后必须是合法时间：正文 "go: 50 gold" 会被 g→9/o→0 拼成 90:50
    if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
        return s
    return hour + ":" + minute + s[m.end():]


def normalize_line(line):
    """把 OCR 的全角标点、多余空格修回客户端原样。"""
    s = line or ""
    for a, b in _FULLWIDTH.items():
        s = s.replace(a, b)
    s = _CJK_GAP.sub("", s)
    s = _BRACKET_GAP.sub(_fmt_bracket, s)
    # 时间戳 00:10 中间被拆开的空格 / 被认成 19:so 这种
    s = re.sub(r"^(\d{1,2})\s*:\s*(\d{2})", r"\1:\2", s)
    return _fix_timestamp(s).strip()


def _run(img, lang):
    return [t for t, _ in _run_boxed(img, lang)]


def _run_boxed(img, lang):
    """跑一次 OCR，返回 [(文本, 行中心 y)]（拿不到框时 y 为 None）。

    y 用来把两套语言包的结果按行对齐。
    """
    import winocr
    res = winocr.recognize_pil_sync(img, lang)
    out = []
    if isinstance(res, dict):
        for l in (res.get("lines") or []):
            text = l.get("text", "") if isinstance(l, dict) else str(l)
            ys = []
            for w in ((l.get("words") or []) if isinstance(l, dict) else []):
                r = (w or {}).get("bounding_rect") or {}
                if r:
                    ys.append(float(r.get("y", 0)) + float(r.get("height", 0)) / 2.0)
            out.append((text, (sum(ys) / len(ys)) if ys else None))
    else:
        for l in res.lines:
            text = getattr(l, "text", str(l))
            ys = []
            for w in getattr(l, "words", []) or []:
                r = getattr(w, "bounding_rect", None)
                if r is not None:
                    ys.append(float(r.y) + float(r.height) / 2.0)
            out.append((text, (sum(ys) / len(ys)) if ys else None))
    return out


_CJK_ANY = re.compile(r"[\u4e00-\u9fff]")
_DIGIT_IN_WORD = re.compile(r"(?<![0-9])[A-Za-z]*[0-9]+[A-Za-z]+|[A-Za-z]+[0-9]+[A-Za-z]*")
_TS = re.compile(r"^\s*\d{1,2}\s*[:：]\s*\d{2}")


def _cjk_ratio(s):
    if not s:
        return 0.0
    return len(_CJK_ANY.findall(s)) / max(1, len(s.strip()))


def _bad_tokens(text):
    """明显被 OCR 啃坏的 token 数：00m00s、S:4S、4 § 这类字母数字混排。

    正常聊天里字母数字混排很少（v0.8.9、+10、500g 这种要放过），所以只数
    「字母和数字黏在一起、又不是纯数字/纯时间戳」的 token。
    """
    n = 0
    for tok in (text or "").split():
        core = tok.strip("[](){}<>:：,.;!?\"'")
        if not core:
            continue
        if _TS.match(tok):
            continue
        if re.search(r"[0-9]", core) and re.search(r"[A-Za-z]", core):
            if not re.fullmatch(r"[vV]?\d+(?:[.,]\d+)*[a-zA-Z]?", core):   # v0.8.9 / 10m / 500g
                n += 1
    return n


_BRACKETED = re.compile(r"\[([^\[\]]*)\]")


def _split_prefix(line):
    """拆成（频道/说话人前缀, 正文）。前缀里通常是中文频道名，正文是英文。

    用"最后一个 ']' 后面跟冒号"的位置切：聊天行固定是
    `时间 [频道] [说话人]: 正文`，而 OCR 经常在冒号前多一个空格（"Moltenbrow] :"）。
    """
    s = line or ""
    i = s.rfind("]")
    while i >= 0:
        j = i + 1
        while j < len(s) and s[j] in " \t":
            j += 1
        if j < len(s) and s[j] in ":：":
            j += 1
            while j < len(s) and s[j] in " \t":
                j += 1
            return s[:j], s[j:]
        i = s.rfind("]", 0, i)
    return "", s


def _cjk_chars(s):
    return len(_CJK_ANY.findall(s or ""))


_TIME_HEAD = re.compile(r"^\s*(\d{1,2}:\d{2})(?=\s|\[|$)")


def _time_head(s):
    """行首是不是一个正常时间戳；是就返回 HH:MM，否则空串。"""
    m = _TIME_HEAD.match(s or "")
    return m.group(1) if m else ""


def _cjk_in_brackets(s):
    """方括号里的中文字数：[队伍] 是 2，[1] 是 0，[ 冂 [Lavabeard] 是 1。"""
    return sum(_cjk_chars(m.group(1)) for m in _BRACKETED.finditer(s or ""))


def _chat_line_count(items):
    return sum(1 for t, _ in items if _CHAT_LINE.match(t or ""))


def _splice(base, other, base_is_en):
    """把两份识别拼成一行：前缀（频道名）和正文（聊天内容）分别择优。

    实测两边各有各的强项：中文包能把 [队伍] 认对、英文包会把 [队伍] 认成 [ßAff]；
    英文包能把 "need gold for repair" 认对、中文包会认成 "need g01d for repair"。
    另外中文包认英文行时前缀会烂掉（19:49 [1] → 19:49 [ 冂 ），这种整行用英文包。
    """
    bp, bb = _split_prefix(base)
    op, ob = _split_prefix(other)

    if not bp or not op:
        # 没有频道/说话人前缀（折行、系统提示）：拆不出前缀就别拼，保持底稿
        return base.strip()

    if base_is_en:
        prefix = bp
        if op and _cjk_in_brackets(op) >= 2 > _cjk_in_brackets(bp):
            prefix = op                      # [队伍] 这种只有中文包认得对
        body = ob if _cjk_chars(bb) < 2 <= _cjk_chars(ob) else bb
    elif _cjk_in_brackets(bp) >= 2:          # 中文频道名可信，正文照样可以取英文包
        prefix = bp
        body = ob if (_cjk_chars(bb) < 2 and _cjk_chars(ob) < 2) else bb
    elif _cjk_chars(bb) < 2 and other.strip():
        return other.strip()                 # 正文是英文：整行用英文包更靠得住
    else:
        prefix, body = bp, bb

    # 英文包常把行首时间戳丢掉（"05:59 [1] [X]: y" → "[1] [X]: y"），
    # 中文包那份往往认得时间，就把它补回来，否则前端拆不出"时间+名字"那一行。
    if not _time_head(prefix):
        for cand in (op, bp, other, base):
            t = _time_head(cand)
            if t:
                prefix = t + " " + prefix.lstrip()
                break
    return (prefix + body).strip()


def _nearest_index(items, y, tol=8.0):
    """找 y 最接近的一行下标（超过 tol 就当没有）。"""
    best, best_d = None, tol
    if y is None:
        return None
    for j, (_, yb) in enumerate(items):
        if yb is None:
            continue
        d = abs(yb - y)
        if d <= best_d:
            best, best_d = j, d
    return best


def _merge_lines(base, other, base_is_en):
    """把两套识别合起来。

    只在**两边切行方式一致**（行数相同、每行 y 都挨得很近）时才逐行拼接 ——
    两套 OCR 把同一行切成不同的段时，硬拼会拼出"06:01 [1]" + 下一行的半截，
    反而更糟（实测）。切法不一致时就只用底稿，最多从另一套借个行首时间。
    """
    if not base:
        return list(other)
    if not other:
        return list(base)

    same_split = (len(base) == len(other)
                  and all(yb is not None and ya is not None and abs(ya - yb) <= 8.0
                          for (_, ya), (_, yb) in zip(base, other)))
    if same_split:
        return [(_splice(ta, tb, base_is_en), ya)
                for (ta, ya), (tb, _) in zip(base, other)]

    out = []
    for ta, ya in base:
        if not _time_head(ta):
            j = _nearest_index(other, ya)
            if j is not None:
                t = _time_head(other[j][0])
                if t:
                    ta = t + " " + ta.lstrip()
        out.append((ta, ya))

    # 英文包读不了中文任务链接（整段吞掉），中文包读得到但会把前缀认烂：
    # 这里只把中文包读到的"中文正文"补到英文行尾，前缀仍用英文包的。
    for k, (ta, ya) in enumerate(out):
        if ya is None:
            continue
        j = _nearest_index(other, ya)
        if j is None:
            continue
        tb = other[j][0]
        _, ob = _split_prefix(tb)
        if _cjk_chars(ta) >= 2 or _cjk_chars(ob) < 3:
            continue
        idx = ob.find("[")               # 只要方括号里的链接/物品名，别把 "LFG" 重复一遍
        if idx < 0:
            continue
        extra = normalize_line(ob[idx:]).strip()
        # 英文包已经读到同一段链接（[[32+] 之类）就别再补一遍，否则会翻出重复内容
        head = re.sub(r"\s+", "", extra)[:4]
        if head and head in re.sub(r"\s+", "", ta):
            continue
        if extra and extra not in ta:
            out[k] = (ta.rstrip() + " " + extra, ya)

    # 补漏：另一套认出、底稿整行没认出来的聊天行（比如中文包读到了任务链接那段）
    base_ys = [y for _, y in base if y is not None]
    for tb, yb in other:
        if yb is None or not (_CHAT_LINE.match(tb) or _HAS_SPEAKER.search(tb)):
            continue
        if _SYSTEM_LIKE.search(tb):
            continue
        if any(abs(yb - y) <= 8.0 for y in base_ys):
            continue
        out.append((tb, yb))
    out.sort(key=lambda it: it[1] if it[1] is not None else 0.0)
    return out


def _safe_run(img, lang):
    try:
        return _run_boxed(img, lang)
    except Exception as e:
        log(f"OCR 语言 {lang} 不可用（{type(e).__name__}: {e}）")
        return []


def recognize_auto(img, lang=None):
    """两套语言包都跑一遍，挑一套当底，再逐行拼接。

    底稿怎么选：先跑英文包（快），只要它认出 >=2 条带时间戳的聊天行，就认为画面
    是英文聊天（中文包这时候只会把英文啃成 "00m00s"）；否则拿中文包当底。
    然后按行 y 坐标对齐，频道名和正文分别取各自更准的那份（见 _splice）。
    """
    primary = (lang or DEFAULT_LANG).strip() or DEFAULT_LANG
    # 先各自归一化（全角标点、时间戳纠错），再对齐拼接 ——
    # 否则 "os:S9" 这种还没修的时间戳会让 _splice 认不出来、也就补不回行首时间。
    en = [(normalize_line(t), y) for t, y in _safe_run(img, FALLBACK_LANG)]
    zh = (en if primary == FALLBACK_LANG
          else [(normalize_line(t), y) for t, y in _safe_run(img, primary)])
    if not en and not zh:
        return []

    # 中文客户端：英文包会把汉字当成拉丁字母啃出 "09:39 09239 So lofiFish" 这种乱码，
    # 而且它"看起来也有时间戳"，会骗过下面的英文判定。所以先按汉字数量判断：
    # 中文结果里的汉字远多于英文结果 → 直接用中文那一份。
    # 判据用"汉字密度"而不是绝对数量：英文画面里中文包也会蹦出几十个错字，
    # 但密度只有 0.15 左右；真正的中文聊天密度在 0.45 上下（实测 0.45 vs 0.14/0.21）。
    zh_cjk, zh_density = _cjk_stats(zh)
    if zh_cjk >= 30 and zh_density >= 0.35:
        log(f"OCR：画面以中文为主（汉字 {zh_cjk}，密度 {zh_density:.2f}），只用中文结果")
        items = _stitch_by_y(zh)
        return [normalize_line(t) for t, _ in items]

    base_is_en = _chat_line_count(en) >= 2
    base, other = (en, zh) if base_is_en else (zh, en)
    items = _merge_lines(base, other, base_is_en)
    items = _stitch_by_y(items)          # 时间戳被单独认成一行时，按 y 缝回去
    return [normalize_line(t) for t, _ in items]


def _cjk_count(lines):
    text = "".join(t for t, _ in lines)
    return len(_CJK_ANY.findall(text))


def _cjk_stats(lines):
    """(汉字数, 汉字密度)：密度按去掉空白后的字符算，避开中文 OCR 的字间空格。"""
    text = "".join(t for t, _ in lines)
    compact = re.sub(r"\s+", "", text)
    cjk = len(_CJK_ANY.findall(compact))
    return cjk, (cjk / len(compact)) if compact else 0.0


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


_TERM_PUNCT = re.compile(r"[.!?。！？…]\s*$")


def _is_continuation(prev, cur):
    """cur 是不是上一条聊天行折行后的下一段。

    游戏聊天框宽度有限，长句会被折成两行：第二行没有时间戳/频道/说话人，
    以前直接按"不是聊天行"丢掉，于是模型拿到的是半句话（"what happened to"），
    翻出来自然莫名其妙。这里把折行接回去。
    """
    if not prev or not cur:
        return False
    if _CHAT_LINE.match(cur) or _HAS_SPEAKER.search(cur):
        return False          # 自己就是一条完整的聊天行
    if len(cur) > 60 or ("]" in cur and not cur.lstrip().startswith("[")):
        return False          # 太长或带方括号：更像系统消息/另一条内容
    if _TERM_PUNCT.search(prev):
        return False          # 上一句已经写完了，不是折行
    # 折行的第二段通常以小写/中文开头；但"贴任务/物品链接"的那一段是以 [[ 开头的
    # （"[Cfour Forever]: LFM [[32]+ 探寻来源]" 被 OCR 切成 "…LFM" + "[[32]+ …]"），
    # 这种也要接回去，否则别人发任务的那几行就只剩个光秃秃的 "LFM"。
    if cur.lstrip().startswith("["):
        return True
    if cur[0].isdigit() and "]" not in cur:
        return True                      # "21 priest" 这种被切出来的尾巴
    return bool(cur[0].islower() or _CJK_ANY.match(cur))


_SPEAKER_SEP = re.compile(r"\]\s*[:：]|说\s*[:：]")   # 英文 "]:" / 中文 "说："


def _is_junk_chat_line(s):
    """整行只有时间戳/方括号、没有正文（例如 OCR 把系统提示认成 "06:10 :"）。

    注意要用**第一个 "]:"** 来切正文：任务/物品链接本身就以 "]" 结尾
    （"[Cfour Forever]: LFM [[32]+ 探寻来源]"），用最后一个 "]" 切会把正文判空，
    于是贴任务的那几行全被当成残片丢掉（实测用户反馈"别人发任务会被忽略"）。
    """
    if not s:
        return True
    m = _SPEAKER_SEP.search(s)
    if not m:
        return True                      # 连"说话人]:"都没有，只是时间戳残片
    return not s[m.end():].strip()


_PREFIX_ONLY = re.compile(r"^\s*\d{1,2}[:：]\d{2}\s*(?:\[[^\]]{0,40}\]\s*)+$")
_TIME_ONLY = re.compile(r"^\s*\d{1,2}[:：]\d{2}\s*$")
_TIME_ONLY_LINE = _TIME_ONLY


def _stitch_by_y(items):
    """把「时间戳单独一行、正文另起一行」的两段按 y 坐标缝回一条聊天行。

    游戏聊天的时间戳是灰的、正文是彩色的，Windows OCR 有时会把它们当成两条
    文本行输出（同一行时间戳先出、正文后出），拼出来的顺序是乱的、还会漏行。
    这里按"y 几乎相同 + 下一段以 [ 开头"配对。
    """
    used = set()
    out = []
    for i, (t, y) in enumerate(items):
        if i in used or not _TIME_ONLY_LINE.match(t or ""):
            continue
        best, best_d = None, 6.0
        for j, (t2, y2) in enumerate(items):
            if j == i or j in used or y is None or y2 is None:
                continue
            if not (t2 or "").lstrip().startswith("["):
                continue
            d = abs(y2 - y)
            if d <= best_d:
                best, best_d = j, d
        if best is None:
            continue
        used.add(i)
        used.add(best)
        out.append((t.strip() + " " + items[best][0].strip(), y))
    for i, (t, y) in enumerate(items):
        if i not in used:
            out.append((t, y))
    out.sort(key=lambda it: (it[1] is None, it[1] or 0.0))
    return out


def _join_broken_prefix(lines):
    """OCR 有时把 "06:01 [1] [名字]: 正文" 切成两行：
    第一行只有 "06:01 [1]"（有时连方括号都没有，只剩 "06:01"），第二行才是正文。接回去。
    """
    out, i, n = [], 0, len(lines)
    while i < n:
        s = (lines[i] or "").strip()
        nxt = (lines[i + 1] or "").strip() if i + 1 < n else ""
        if s and nxt:
            if _PREFIX_ONLY.match(s) and not _CHAT_LINE.match(nxt):
                out.append(s + " " + nxt)
                i += 2
                continue
            # 只剩一个时间戳：只有下一行确实带说话人（有 "]"）时才接
            if _TIME_ONLY.match(s) and "]" in nxt:
                out.append(s + " " + nxt)
                i += 2
                continue
        out.append(s)
        i += 1
    return out


# 系统提示（变更频道 / 遭到攻击 / 插件公告）：不是玩家聊天，不翻也不占条数
_SYSTEM_LIKE = re.compile(
    r"(变更|變更|频道|頻道|已加入|离开|離開|遭到攻击|遭到攻擊|系统|系統|插件公告|Questie|OpenMap)")


def pick_chat_lines(lines, max_lines=12):
    """只保留像聊天行的内容：按时间戳/说话人切条，其余片段并回上一条。

    OCR 的痛点是"漏翻"：它会把一条聊天切成好几段（时间戳、正文、任务链接各出
    一行），以前只接小写/中文开头的片段，像 "LFG [131+1"、"21 priest" 这种就直接
    丢了。现在除了系统提示，所有属于上一行的片段都并回去。
    """
    chat = []
    for raw in _join_broken_prefix(lines):
        s = (raw or "").strip()
        if not s:
            continue
        # 系统消息过滤只对"没有说话人"的行生效：
        # 以前拿整行去匹配，正文里出现"频道/系统/离开"就把正常聊天整行吞掉。
        has_speaker = bool(_CHAT_LINE.match(s) or _SPEAKER_SEP.search(s))
        if _SYSTEM_LIKE.search(s) and not has_speaker:
            continue                       # 系统提示：不要（先判，别让它混进聊天行）
        # 中文客户端：系统/插件公告没有时间戳、也没有"某某说："，直接跳过
        if (not _CHAT_LINE.match(s) and _CJK_ANY.search(s)
                and "说" not in s and "：" not in s and ":" not in s):
            continue
        # 没有时间戳（客户端关掉了时间显示）时，靠"说话人："也能认出来
        if _CHAT_LINE.match(s) or _HAS_SPEAKER.search(s) or _SPEAKER_SEP.search(s):
            if _is_junk_chat_line(s):
                continue
            chat.append(s)
        elif chat and _is_continuation(chat[-1], s):
            chat[-1] = (chat[-1] + " " + s).strip()   # 折行/被切开的尾巴：接回去
    # 以前这里有个兜底：一行都没认出来时，把"长度 > 6"的行全塞进来。
    # 结果满屏系统消息（"暴雪游戏行为规范。"）会被当成聊天，本地词库就把它们
    # 逐词翻一遍显示出来。现在宁可返回空，让上层提示"没认出聊天内容"。
    chat = [l.strip() for l in chat if l.strip()]
    if max_lines and len(chat) > max_lines:
        chat = chat[-max_lines:]
    log(f"OCR 命中 {len(chat)} 行")
    return chat
