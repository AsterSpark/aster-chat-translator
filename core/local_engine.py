# -*- coding: utf-8 -*-
"""离线翻译引擎（本地 opus-mt / m2m100，CTranslate2 int8，纯 CPU，不联网）

流程只有一条：
    原文 → 剥掉聊天外壳 → 术语占位保护 → 模型翻译 → 还原术语 → 拼回外壳

为什么要占位：模型是英→中单向的，直接喂中文会崩（输出 ⁇ *），
所以只能用 [[1]] 这种占位符。实测模型基本会原样保留。

模型目录（按顺序挑第一个通过自检的）：
    models/m2m100-en-zh   CT2 版 m2m100 418M（473MB，质量更好 —— 但目前只有
                          转换坏的公开版本，见下方注意）
    models/argos-en-zh    Argos 包 / opus-mt-en-zh（77M，82MB，当前实际在用）

注意：**加载时会做一次自检**（翻一句 "hello"），输出退化成复读机的直接跳过。
踩过的坑：gn64/M2M100_418M_CTranslate2（hf-mirror 上唯一的 m2m100 CT2 版）
能加载能出字，但 'hello' 翻成 'hello hello hello…'、'thank you' 翻成二十个
「感謝」，float32 也一样 —— 是转换坏了，不是参数问题。等有可靠的转换版本，
把目录放进来就会自动优先用它。
"""

import os
import re
import threading

from .log import log

_HOME = os.environ.get("WOWTR_HOME") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))

# 优先级：有可用的 m2m100 就用它，否则退回 opus-mt（Argos）。
# m2m100 必须在加载时通过自检才算"可用" —— 见过被转换坏的模型能加载、能出字，
# 但输出是 "hello hello hello…"/"感謝感謝感謝…" 这种退化重复，不加自检会直接
# 把乱码端给用户（实测 gn64/M2M100_418M_CTranslate2 就是这样）。
_MODEL_CANDIDATES = ["m2m100-en-zh", "nllb-200-en-zh", "argos-en-zh", "opus-mt-en-zh"]


def _is_nllb(d):
    """NLLB：带 HF 的 tokenizer.json（m2m100 的 CT2 转换版不带这个文件）。

    注意 NLLB 的包里**同时**有 sentencepiece.bpe.model，所以不能拿 spm 判断，
    否则会被误认成 m2m100、用错语言标记。
    """
    return (os.path.isfile(os.path.join(d, "model.bin"))
            and os.path.isfile(os.path.join(d, "tokenizer.json")))


def _is_m2m100(d):
    """m2m100：单份 SentencePiece + 共享词表，没有 source/target 两套 spm。

    注意 `sentencepiece.bpe.model` 是判断依据之一 —— ctranslate2 的转换器
    **不会输出分词器**，从 HF 转完模型后必须把 `sentencepiece.bpe.model` 和
    `vocab.json` 一起拷进输出目录，否则这里判不出来、模型加载不了。
    """
    return (os.path.isfile(os.path.join(d, "model.bin"))
            and os.path.isfile(os.path.join(d, "sentencepiece.bpe.model")))


def _is_marian(d):
    return (os.path.isfile(os.path.join(d, "model.bin"))
            and (os.path.isfile(os.path.join(d, "sentencepiece.model"))
                 or os.path.isfile(os.path.join(d, "source.spm"))))


def _candidate_dirs():
    for name in _MODEL_CANDIDATES:
        d = os.path.join(_HOME, "models", name)
        if _is_nllb(d):
            yield d, "nllb"
        elif _is_m2m100(d):
            yield d, "m2m100"
        elif _is_marian(d):
            yield d, "marian"


def _spm_paths(d):
    """opus-mt 时代的布局（双 spm），保留兼容用。"""
    src = os.path.join(d, "source.spm")
    tgt = os.path.join(d, "target.spm")
    shared = os.path.join(d, "sentencepiece.model")
    if not os.path.isfile(src) and os.path.isfile(shared):
        src = shared
    if not os.path.isfile(tgt) and os.path.isfile(shared):
        tgt = shared
    return src, tgt


_lock = threading.Lock()
_translator = None
_src_sp = None
_tgt_sp = None
_shared_sp = None       # m2m100 用一份 SentencePiece
_tok = None             # NLLB 用 HF tokenizer
_failed = False
_kind = None            # 实际加载成功的模型类型
_dir = None             # 实际加载成功的目录

# NLLB-200 的语言标记（不是 __zh__，别混）
NLLB_SRC = "eng_Latn"
NLLB_TGT = "zho_Hans"

_PLACE = re.compile(r"\[\[(\d+)\]\]")


def available():
    """有没有任何可用的模型目录（实际能不能用由 _load 的自检决定）。"""
    return any(True for _ in _candidate_dirs())


def _degenerate(text):
    """输出是不是退化重复（"需要需要需要…" / "hello hello hello…"）。

    被转换坏的模型能加载、也能出字，但会陷进复读循环。宁可判定它不可用，
    也不要把它端到用户面前（实测 gn64/M2M100_418M_CTranslate2 就是这样：
    连 'hello' 都翻成 'hello hello hello…'）。
    """
    s = (text or "").strip()
    if not s or len(s) > 200:
        return True
    for n in (2, 3, 4):
        for i in range(0, max(0, len(s) - n * 3)):
            frag = s[i:i + n]
            if frag.strip() and s.count(frag) > 4:
                return True
    return False


def _probe_m2m(tr, sp):
    toks = [_m2m_tokens(sp, "hello")]
    r = tr.translate_batch(toks, beam_size=2, max_decoding_length=16,
                           target_prefix=[["__zh__"]])
    hyp = list(r[0].hypotheses[0]) if r[0].hypotheses else []
    if hyp and hyp[0] == "__zh__":
        hyp = hyp[1:]
    return sp.decode(hyp).replace("\u2581", " ").strip()


def _m2m_tokens(sp, text):
    """m2m100 的输入：__en__ 打头、**</s> 收尾**。

    少了收尾的 </s>，模型会直接退化成复读机（hello → hello hello hello…），
    很容易被误判成"模型坏了"——我在 NLLB 和 m2m100 上各踩了一次。
    """
    return ["__en__"] + sp.encode(text, out_type=str) + ["</s>"]


def _nllb_decode(tk, toks):
    """NLLB：去掉目标语言标记，token 串换回 id 再交给 tokenizer 解码。"""
    toks = list(toks)
    if toks and toks[0] == NLLB_TGT:
        toks = toks[1:]
    ids = [i for i in (tk.token_to_id(t) for t in toks) if i is not None]
    return tk.decode(ids).strip()


def _nllb_encode(tk, text):
    """NLLB 的源句**必须以 </s> 结尾**。

    少了这个 EOS，600M 的模型会直接退化成复读机（hello → hello hello hello…），
    很容易被误判成"模型坏了"。另外这个 tokenizer 会在句子后面多塞一个 <unk>，
    要去掉（实测 hello → ['▁hello', '</s>', '<unk>']）。
    """
    return [t for t in tk.encode(text).tokens if t not in ("<s>", "<unk>")]


def _probe_nllb(tr, tk):
    toks = [[NLLB_SRC] + _nllb_encode(tk, "hello")]
    r = tr.translate_batch(toks, beam_size=2, max_decoding_length=16,
                           target_prefix=[[NLLB_TGT]])
    return _nllb_decode(tk, r[0].hypotheses[0])


def _probe_marian(tr, src_sp, tgt_sp):
    toks = [src_sp.encode("hello", out_type=str)]
    r = tr.translate_batch(toks, beam_size=2, max_decoding_length=16)
    return tgt_sp.decode(r[0].hypotheses[0]).replace("\u2581", " ").strip()


def _load():
    global _translator, _src_sp, _tgt_sp, _shared_sp, _tok, _failed, _kind, _dir
    if _translator is not None:
        return True
    if _failed:
        return False

    import ctranslate2
    import sentencepiece as spm

    for d, kind in _candidate_dirs():
        name = os.path.basename(d)
        try:
            tr = ctranslate2.Translator(d, device="cpu", compute_type="int8")
            if kind == "nllb":
                from tokenizers import Tokenizer
                tk = Tokenizer.from_file(os.path.join(d, "tokenizer.json"))
                probe = _probe_nllb(tr, tk)
                if _degenerate(probe):
                    log(f"离线模型自检失败（{name}）：输出退化 {probe[:40]!r}，跳过")
                    continue
                _tok, _kind = tk, kind
            elif kind == "m2m100":
                sp = spm.SentencePieceProcessor(
                    model_file=os.path.join(d, "sentencepiece.bpe.model"))
                probe = _probe_m2m(tr, sp)
                if _degenerate(probe):
                    log(f"离线模型自检失败（{name}）：输出退化 {probe[:40]!r}，跳过")
                    continue
                _shared_sp, _kind = sp, kind
            else:
                src_p, tgt_p = _spm_paths(d)
                s1 = spm.SentencePieceProcessor(model_file=src_p)
                s2 = spm.SentencePieceProcessor(model_file=tgt_p)
                probe = _probe_marian(tr, s1, s2)
                if _degenerate(probe):
                    log(f"离线模型自检失败（{name}）：输出退化 {probe[:40]!r}，跳过")
                    continue
                _src_sp, _tgt_sp, _kind = s1, s2, kind
            _translator, _dir = tr, d
            log(f"离线模型已加载：{name}（{kind}，自检输出 {probe[:12]!r}）")
            return True
        except Exception as e:
            log(f"离线模型加载失败（{name}）：{e}")

    _failed = True
    log("没有可用的离线模型（全部自检失败或不存在）")
    return False


def translate(lines, beam_size=2):
    """纯模型翻译（英→中），不做术语处理。传英文列表，返回中文列表。

    m2m100 用语言标记定位方向：源句前加 __en__，强制目标前缀 __zh__，
    解码后把 __zh__ 这个 token 去掉。
    """
    if not lines:
        return []
    if not _load():
        return [None] * len(lines)

    with _lock:
        try:
            if _kind == "nllb":
                tokens = [[NLLB_SRC] + _nllb_encode(_tok, x) for x in lines]
                results = _translator.translate_batch(
                    tokens, beam_size=beam_size, max_batch_size=16,
                    max_decoding_length=256,
                    target_prefix=[[NLLB_TGT]] * len(lines),
                )
                return [_nllb_decode(_tok, r.hypotheses[0] if r.hypotheses else [])
                        for r in results]

            if _kind == "m2m100":
                tokens = [_m2m_tokens(_shared_sp, x) for x in lines]
                results = _translator.translate_batch(
                    tokens, beam_size=beam_size, max_batch_size=16,
                    max_decoding_length=256,
                    target_prefix=[["__zh__"]] * len(lines),
                )
                out = []
                for r in results:
                    hyp = list(r.hypotheses[0]) if r.hypotheses else []
                    if hyp and hyp[0] == "__zh__":
                        hyp = hyp[1:]
                    zh = _shared_sp.decode(hyp)
                    out.append(zh.replace("\u2581", " ").strip())
                return out

            tokens = [_src_sp.encode(x, out_type=str) for x in lines]
            results = _translator.translate_batch(
                tokens, beam_size=beam_size, max_batch_size=16,
                max_decoding_length=256,
            )
            out = []
            for r in results:
                hyp = r.hypotheses[0] if r.hypotheses else []
                zh = _tgt_sp.decode(hyp)
                out.append(zh.replace("\u2581", " ").strip())
            return out
        except Exception as e:
            log(f"离线翻译出错: {e}")
            return [None] * len(lines)


# ---------------------------------------------------------------- 术语保护

def _protect_terms():
    """要保护的词：够长、够专有（副本名、地名、玩法词），虚词和短缩写不保护。

    试过把 guild/trainer/boss 这类短名词也强制保护，结果更糟：占位符 [[1]]
    会打断句子结构，模型把 is running / tonight 直接留在英文里。所以仍然
    只保护「够长」的专有名词，短名词交给模型自己翻。
    """
    from . import glossary

    table = {}
    for k, v in glossary.TERMS.items():
        if k in glossary.COMMON:      # 虚词交给模型
            continue
        # 只保护专有名词（副本/地名/多词短语）；tank、thanks 这类模型自己能翻
        if " " not in k and len(k) < 8:
            continue
        table[k] = v
    return table


_PROTECT_CACHE = None


def _protect(text):
    """把长术语换成 [[1]]，返回 (保护后的文本, {编号: 中文})"""
    global _PROTECT_CACHE
    if _PROTECT_CACHE is None:
        terms = _protect_terms()
        if terms:
            rx = re.compile(
                r"(?<![A-Za-z])(" + "|".join(
                    sorted((re.escape(k) for k in terms), key=len, reverse=True)
                ) + r")(?![A-Za-z])",
                re.IGNORECASE,
            )
        else:
            rx = None
        _PROTECT_CACHE = (rx, terms)

    rx, terms = _PROTECT_CACHE
    if rx is None:
        return text, {}

    mapping = {}

    def _rep(m):
        word = m.group(1).lower()
        if word not in terms:
            return m.group(1)
        idx = len(mapping) + 1
        mapping[idx] = terms[word]
        return f"[[{idx}]]"

    return rx.sub(_rep, text), mapping


def _restore(text, mapping):
    if not text:
        return text

    def _rep(m):
        return mapping.get(int(m.group(1)), m.group(0))

    return _PLACE.sub(_rep, text)


def _placeholder_intact(text, mapping):
    """占位符有没有被模型弄坏（多出括号、被拆开都算坏）"""
    if not mapping:
        return "[[" not in (text or "")
    found = [int(x) for x in _PLACE.findall(text or "")]
    if sorted(found) != sorted(mapping.keys()):
        return False
    return "[[" not in _PLACE.sub("", text)


# ---------------------------------------------------------------- 对外主入口

# 聊天行的「外壳」：时间戳 + 频道标签 + 说话人，例如
#     00:10 [队伍] [Halduron]: need 31397 gold
# 这一段必须原样保留 —— 模型是英→中的，把中文频道名喂进去它只会吐乱码
# （实测输出过 "[ ⁇ [Halduron]"），而且它也不该"翻译"时间戳和人名。
_PREFIX_TS = re.compile(
    r"^(\s*\d{1,2}:\d{2}\s*(?:\[[^\]]{1,20}\]\s*)+[:：]\s*)(.*)$", re.S)
_PREFIX_TAG = re.compile(
    r"^((?:\[[^\]]{1,20}\]\s*)+[:：]\s*)(.*)$", re.S)


def split_prefixed(line):
    """拆成 (外壳, 正文)。外壳原样保留，只翻正文。"""
    for rx in (_PREFIX_TS, _PREFIX_TAG):
        m = rx.match(line or "")
        if m:
            return m.group(1), m.group(2)
    return "", line or ""


def _cjk_ratio(s):
    if not s:
        return 0.0
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return cjk / len(s)


def _latin_ratio(s):
    """非空白字符里拉丁字母占比：用来判断"这句到底翻成中文没有"。"""
    chars = [ch for ch in (s or "") if not ch.isspace()]
    if not chars:
        return 0.0
    latin = sum(1 for ch in chars if ("a" <= ch <= "z") or ("A" <= ch <= "Z"))
    return latin / len(chars)


def translate_lines(lines):
    """一条路：剥外壳 → 保护术语 → 模型 → 还原 → 拼回外壳。坏了的行退回词库。

    返回 (中文行列表, 每行来源列表: "model" / "rule" / "keep")
    """
    from . import glossary

    lines = [x or "" for x in (lines or [])]
    if not lines:
        return [], []

    slang = re.compile(
        r"\b(lfm|lfg|lf\d+m|wts|wtb|wtt|pst|inv|invite|cod|bis|boe|ty|gg|np|brb|afk|omw|thx)\b",
        re.I)

    prefixes, bodies = [], []
    for line in lines:
        pre, body = split_prefixed(line)
        prefixes.append(pre)
        bodies.append(body)

    protected, maps = [], []
    for body in bodies:
        p, m = _protect(body)
        protected.append(p)
        maps.append(m)

    outs = translate(protected)

    texts, sources = [], []
    for i, body in enumerate(bodies):
        pre = prefixes[i]
        # 正文本来就是中文：照抄，别让英→中模型去"翻"它（规则 6）
        if body.strip() and _cjk_ratio(body) >= 0.25:
            texts.append(pre + body)
            sources.append("keep")
            continue
        model_text = outs[i] if i < len(outs) else None
        model_ok = bool(model_text) and _placeholder_intact(model_text, maps[i])

        # 聊天缩写、或很短的句子，词库比模型准。
        # 试过"谁的拉丁字母少就用谁"，结果 cod me → 「鳕鱼我」、brb 被吃掉、
        # 物品名被翻成「蓝蓝蓝宝石」——模型在短句/专名上并不可靠，所以这里仍以词库为准。
        short_or_slang = bool(slang.search(body)) or len(body.split()) <= 4
        if short_or_slang:
            texts.append(pre + glossary.local_translate(body))
            sources.append("rule")
            continue
        if model_ok:
            texts.append(pre + _restore(model_text, maps[i]))
            sources.append("model")
        else:
            texts.append(pre + glossary.local_translate(body))
            sources.append("rule")
    return texts, sources


def warm_up():
    """后台预热，避免第一次按热键卡一下"""
    def run():
        _load()
        translate(["looking for more"])
    threading.Thread(target=run, daemon=True, name="local-warmup").start()
