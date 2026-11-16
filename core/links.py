# -*- coding: utf-8 -*-
"""按行修链接：把 AI 译文里的 `[[32+] 名称]` 替换回原文的样子"""
import re

_LINK = re.compile(r"\[\[\s*\d+\s*\+\s*\]\s*[^\[\]]{0,40}\]")
_LEVEL_HEAD = re.compile(r"\[\[\s*(\d+)\s*\+\s*\]")
_SPACE_BEFORE = re.compile(r"(?<=[^\s\[\]])(\[\[)")   # ] 后面紧跟的第二个链接不加空格


def _tokens(line):
    return _LINK.findall(line or "")


def _replace_token(tr, tok, lv):
    """把译文里 `[[lv+] …` 这一段换成原文 token；链接没闭合也要能修。"""
    hit = re.search(r"\[\[\s*" + re.escape(lv) + r"\s*\+\s*\]", tr)
    if not hit:
        # 译文里连等级标记都没有：直接补在行尾
        sep = "" if tr.rstrip().endswith("]") else " "
        return tr.rstrip() + sep + tok
    rest = tr[hit.end():]
    j = rest.find("]")
    k = rest.find("[[")
    if j >= 0 and (k < 0 or j < k):
        end = hit.end() + j
        return tr[:hit.start()] + tok + tr[end + 1:]
    # 这条链接没有闭合（下一个 [[ 之前没有 ]）：整段残缺链接都用原文 token 替掉
    stop = hit.end() + (k if k >= 0 else len(rest))
    return tr[:hit.start()] + tok + tr[stop:]


def repair_line(raw, tr):
    tr = (tr or "").rstrip()
    if not tr:
        return tr
    for tok in _tokens(raw):
        m = _LEVEL_HEAD.match(tok)
        if m:
            tr = _replace_token(tr, tok, m.group(1))
    return _SPACE_BEFORE.sub(r" \1", tr)


def repair(raw, translation):
    raw_lines = (raw or "").split("\n")
    tr_lines = (translation or "").split("\n")
    if not raw_lines or not tr_lines:
        return translation or ""
    for i in range(min(len(raw_lines), len(tr_lines))):
        tr_lines[i] = repair_line(raw_lines[i], tr_lines[i])
    return "\n".join(tr_lines)
