# -*- coding: utf-8 -*-
"""模型调用：把中文意图写成英文玩家能看懂的口语（「我想说」）。

只走用户自填的 API Key（OpenAI 兼容接口）。
DSH 账号通道（读 ~/.dsh/.credentials.yaml）留到后面单独接，不混在这一层。
"""

import json
import os
import urllib.error
import urllib.request

from .log import log
from . import usage


def _log_usage(tag, resp):
    """把接口返回的 usage 记进日志：花了多少输入/输出、缓存命中多少。

    只看这行就能判断这次调用贵在哪，不用猜。
    """
    u = (resp or {}).get("usage") or {}
    if not u:
        log(f"{tag} usage：接口没返回用量")
        return
    try:
        usage.record(resp)
    except Exception as e:      # 记账失败绝不影响翻译
        log(f"{tag} 用量记账失败: {e}")
    hit = u.get("prompt_cache_hit_tokens")
    miss = u.get("prompt_cache_miss_tokens")
    log(f"{tag} usage：输入 {u.get('prompt_tokens', '?')} tokens"
        f"（缓存命中 {hit if hit is not None else 'n/a'}"
        f" / 未命中 {miss if miss is not None else 'n/a'}）"
        f"，输出 {u.get('completion_tokens', '?')}，合计 {u.get('total_tokens', '?')}")

COMPOSE_SYS = """把玩家想说的简短中文写成《魔兽世界》英文玩家日常口语，直接可复制发出。
输入：一段游戏聊天作为语境 + 一句中文意思。输出 2-3 条英文。
规则：
0 信息完整优先：人名、时间、数量、条件、请求一项都不能省；宁可长，不许只翻一半
1 尽量 ≤14 个单词，信息多可到 20 个词；全小写，除句末不加标点，不加表情
2 用英文玩家日常口语，可用 lfm / inv / pst / ty / np 这类缩写
3 三条要有区别：最直接 / 更客气 / 更简短
4 不要逐字翻译，写成母语者会说的话
5 每条配一句中文说明意思和场合
6 en 里只放要发出去的那句话：不要加引号、emoji、中文、也不要加"你可以说："这类前缀
7 中文里出现的人名、副本名、物品名保留原样，不要硬翻
8 语境里已经有人说过类似的话，也要写成玩家自己要发的那句，不要照搬原文
注意：不要假设或提及玩家的种族、职业、阵营、等级、性别；不要提到翻译过程或本工具。
只输出 JSON，不要解释：{"replies":[{"en":"...","cn":"..."}]}"""


VISION_SYS = """你是《魔兽世界》英文聊天翻译器，输入一张聊天截图，输出 JSON：
{"raw":"逐行英文原文","translation":"逐行中文","summary":"一句话判断"}
规则：
1 一行对一行、顺序不变；图上有几条聊天消息就输出几条，不要自行删减（行数裁剪由程序负责）
2 时间戳照抄原样（08:20 就写 08:20），不要省略、不要改成别的格式
3 同一条消息在客户端里被换行折成两行的，合并成一条：后一行通常没有时间戳、也没有说话人，像 "and a healer..." 这种续行要接到上一条末尾，别拆成两条
4 raw 只照抄：不改写、不补全、不续写、不编造图上没出现的行
5 频道名必须逐字照抄，一个字都不许改：图上写 [队伍] 就写 [队伍]，不许写成 [公会]/[团队]/[小队]；图上写 [1. 交易] 就写 [1. 交易]，不许写成 [1. 综合]。频道名只有这几种可能：[队伍] [团队] [公会] [小队] [1. 综合] [1. 交易] [1. 本地防务] [密语]，但**以图上实际写的为准**，看不清就保留你看到的字形，不要替换成"更合理"的频道
6 原文本身就是中文的行，译文照抄中文，不要反译成英文
7 物品链接、价格、数量照原样保留数字（5k 就是 5k，不要自己加"金"、不要换算）；`[[32+] 名称]` 这类任务/物品链接必须**逐字照抄**：两个左方括号、等级数字、加号、右方括号、链接里的中文都不许改、不许翻译、不许少写一个括号；链接与前后文字之间保留一个空格
8 译文用简体中文口语，人名和副本名保留原文或通用译名；缩写按此展开：wts=出售 wtb=求购 cod=货到付款 pst=密我 inv=拉我 lfm=招人 brb=马上回来 ty=谢谢 np=不客气 need=缺；称呼类口语按**意思**译，不要字面：dog / man / bro / dude / mate 都是「兄弟 / 哥们」（绝不译成「狗」）
9 系统通知、获得物品、成就、频道公告一律忽略，不进 raw
10 summary 一句话、最多 20 字：先说大概在聊什么，再给要不要理会的判断（可回 / 不用回 / 可回可不回都行，拿不准就写可回可不回）；优先看队伍和私聊，交易频道喊话一般不用回。不写人名、不提职业种族、不复述原文
11 图里没有可读聊天（全黑、或只有系统消息）时：raw 和 translation 都输出空字符串，summary 写「没有聊天内容」，不要编造任何行
12 只输出 JSON，不要 markdown 代码块，不要解释
"""



SYSTEM_RE = None


def _drop_system(raw, translation, limit=8):
    """去掉系统/通知类行，并只保留最后 limit 行"""
    import re as _re
    global SYSTEM_RE
    if SYSTEM_RE is None:
        SYSTEM_RE = _re.compile(
            r"(系统|系統|System|You have learned|You receive|You've earned|"
            r"Congratulations|joined the channel|left the channel|"
            r"已加入频道|离开了频道|你获得了|恭喜|成就|奖励|Received item)",
            _re.I)
    if not raw:
        return raw, translation
    r_lines = raw.split("\n")
    t_lines = (translation or "").split("\n")
    keep_r, keep_t = [], []
    for i, line in enumerate(r_lines):
        if SYSTEM_RE.search(line):
            continue
        keep_r.append(line)
        if i < len(t_lines):
            keep_t.append(t_lines[i])
    return "\n".join(keep_r[-limit:]), "\n".join(keep_t[-limit:])


class ModelError(RuntimeError):
    """带用户可读信息的错误"""


def _user_text(intent, raw, zh):
    return (
        "【聊天语境·原文】\n" + (raw or "（无）") +
        "\n\n【聊天语境·中文】\n" + (zh or "（无）") +
        "\n\n【我想表达】\n" + intent
    )


def _strip_fence(s):
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _content_of(resp):
    """取 choices[0].message.content，兼容"字符串"和"分段数组"两种格式。

    OpenAI 新版和部分兼容层会返回 [{"type":"text","text":"..."}] 这种数组，
    直接 json.loads 会炸在"[{"上。
    """
    try:
        c = resp["choices"][0]["message"]["content"]
    except Exception:
        raise ModelError("接口返回格式异常（没有 choices[0].message.content）")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for p in c:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
            elif isinstance(p, str):
                parts.append(p)
        return "".join(parts)
    return str(c)


def _post_chat(url, body, headers, timeout):
    """POST 一次；接口不认 response_format 时去掉它重试一次。

    不少 OpenAI 兼容网关（国产模型、自建代理、部分 Azure 部署）不支持
    response_format，带上就 400。系统提示里本来就要求"只输出 JSON"，
    去掉照样能解析，所以这里退一步而不是直接报错。
    """
    def send(b):
        return _post_json(url, json.dumps(b, ensure_ascii=False).encode("utf-8"),
                          headers, timeout)

    try:
        return send(body)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        if e.code == 400 and "response_format" in detail and "response_format" in body:
            log(f"接口不支持 response_format，去掉重试：{detail[:60]}")
            body2 = dict(body)
            body2.pop("response_format", None)
            return send(body2)
        raise


def _post_json(url, data, headers, timeout, attempts=2):
    """POST 一次；网络抖动/超时时重试一次（接口限流错误不重试）"""
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError:
            raise                      # 4xx/5xx 不重试，交给上层分类
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if i + 1 < attempts:
                log(f"请求失败，重试一次：{e}")
                continue
            raise
    raise last


def compose(cfg, intent, raw="", zh="", timeout=45):
    """返回 [{"en":..., "cn":...}, ...]"""
    api = (cfg or {}).get("api") or {}
    key = (api.get("key") or "").strip()
    if not key:
        raise ModelError("未配置 API Key（config.json → api.key）")

    body = {
        "model": api.get("model") or "deepseek-chat",
        "messages": [
            {"role": "system", "content": COMPOSE_SYS},
            {"role": "user", "content": _user_text(intent, raw, zh)},
        ],
        "temperature": 0.5,
        "max_tokens": 800,
        "response_format": {"type": "json_object"},
    }
    url = api.get("base_url") or "https://api.deepseek.com/v1/chat/completions"
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {key}"}
    try:
        resp = _post_chat(url, body, headers, timeout)
    except urllib.error.HTTPError as e:
        code = e.code
        if code == 401:
            raise ModelError("API Key 无效或已过期（401）")
        if code == 402:
            raise ModelError("账户额度不足（402）")
        if code == 429:
            raise ModelError("请求太频繁（429）")
        raise ModelError(f"接口返回 HTTP {code}")
    except urllib.error.URLError as e:
        raise ModelError(f"连不上接口：{e.reason}")
    except TimeoutError:
        raise ModelError(f"模型响应超时（{timeout}s）")

    _log_usage("compose", resp)
    content = _content_of(resp)

    try:
        payload = json.loads(_strip_fence(content))
    except Exception:
        log(f"compose 返回非 JSON: {content[:200]}")
        raise ModelError("模型返回的不是 JSON")

    out = []
    for x in (payload.get("replies") or []):
        if isinstance(x, dict) and str(x.get("en") or "").strip():
            out.append({
                "en": str(x["en"]).strip(),
                "cn": str(x.get("cn") or "").strip(),
            })
    if not out:
        raise ModelError("模型没有给出回复内容")

    log(f"compose 成功：{len(out)} 条，意图={intent[:20]!r}")
    return out[:3]

def vision(cfg, png_bytes, timeout=90, max_lines=8):
    """图片直读：把聊天截图交给模型，返回 {raw, translation, summary, replies}"""
    if os.environ.get("WOWTR_FORCE_VISION_FAIL") == "1":
        raise ModelError("（测试）图片直读被强制关闭")
    api = (cfg or {}).get("api") or {}
    key = (api.get("key") or "").strip()
    if not key:
        raise ModelError("未配置 API Key（config.json → api.key）")

    import base64
    b64 = base64.b64encode(png_bytes).decode("ascii")
    body = {
        "model": api.get("model") or "deepseek-chat",
        "messages": [
            {"role": "system", "content": VISION_SYS},
            {"role": "user", "content": [
                {"type": "text", "text": "请按系统要求处理这张聊天截图。"},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
        "temperature": 0.1,
        "max_tokens": 800,
        "response_format": {"type": "json_object"},
    }
    url = api.get("base_url") or "https://api.deepseek.com/v1/chat/completions"
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {key}"}

    try:
        resp = _post_chat(url, body, headers, timeout)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ModelError("API Key 无效或已过期（401）")
        if e.code == 402:
            raise ModelError("账户额度不足（402）")
        if e.code == 429:
            raise ModelError("请求太频繁（429）")
        raise ModelError(f"接口返回 HTTP {e.code}")
    except urllib.error.URLError as e:
        raise ModelError(f"连不上接口：{e.reason}")
    except TimeoutError:
        raise ModelError(f"模型响应超时（{timeout}s）")

    _log_usage("vision", resp)
    try:
        payload = json.loads(_strip_fence(_content_of(resp)))
    except ModelError:
        raise
    except Exception:
        raise ModelError("模型返回的不是 JSON")

    replies = []
    for x in (payload.get("replies") or []):
        if isinstance(x, dict) and str(x.get("en") or "").strip():
            replies.append({"en": str(x["en"]).strip(),
                            "cn": str(x.get("cn") or "").strip()})

    raw = str(payload.get("raw") or "").strip()
    zh = str(payload.get("translation") or "").strip()
    if not raw and not zh:
        note = str(payload.get("summary") or "").strip()
        # 图上本来就没有聊天：当成「空结果」返回，不报错、不浪费 OCR/文本二次调用
        if "没有聊天" in note or "无聊天" in note or "没识别到" in note:
            log("vision：图上没有可读聊天")
            return {"raw": "", "translation": "", "summary": note, "replies": []}
        raise ModelError("模型没读出聊天内容")

    log(f"vision 成功：raw {len(raw.splitlines())} 行 / 译文 {len(zh.splitlines())} 行")
    raw, zh = _drop_system(raw, zh, limit=max_lines)
    return {"raw": raw, "translation": zh,
            "summary": str(payload.get("summary") or "").strip(),
            "replies": replies[:3]}

TEXT_SYS = """你是《魔兽世界》英文聊天翻译器。输入是 OCR 出来的聊天文本（一行一条，可能有错字）。
输出 JSON：{"translation":"逐行中文","summary":"一句话判断","replies":[{"en":"...","cn":"..."}]}
规则：
1 一行对一行、顺序不变；最多 12 行
2 原文是英文就译成中文；原文本身就是中文的行，照抄，不要反译成英文
3 时间统一写 08:08；物品链接、价格、数量照原样保留数字；`[[32+] 名称]` 这类链接逐字照抄（方括号、等级、链接里的中文都不许改），链接前留一个空格
4 缩写按此展开：wts=出售 wtb=求购 cod=货到付款 pst=密我 inv=拉我 lfm=招人 brb=马上回来 ty=谢谢 np=不客气 need=缺；称呼类口语按意思译，dog / man / bro / dude 都是「兄弟 / 哥们」
5 系统通知、获得物品、成就、频道公告忽略
6 OCR 看不清的字符按最接近的英文保留，不要留空、不要凭空补内容
7 summary 一句话、最多 20 字：大概在聊什么 + 要不要理会（可回 / 不用回 / 可回可不回都行）；不写人名、不提职业种族
8 需要回复才给 3 条 replies（每条配一句中文说明），纯闲聊给空数组；不要假设玩家种族职业、阵营、等级
9 只输出 JSON，不要 markdown 代码块，不要解释"""


def translate_text(cfg, chat_text, timeout=30):
    """OCR 之后的文本翻译（视觉接口不可用时的退路）"""
    api = (cfg or {}).get("api") or {}
    key = (api.get("key") or "").strip()
    if not key:
        raise ModelError("未配置 API Key（config.json → api.key）")
    if not (chat_text or "").strip():
        raise ModelError("没有识别到聊天内容")

    body = {
        "model": api.get("model") or "deepseek-chat",
        "messages": [
            {"role": "system", "content": TEXT_SYS},
            {"role": "user", "content": chat_text},
        ],
        "temperature": 0.3,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }
    url = api.get("base_url") or "https://api.deepseek.com/v1/chat/completions"
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {key}"}
    try:
        resp = _post_chat(url, body, headers, timeout)
        _log_usage("translate_text", resp)
        payload = json.loads(_strip_fence(_content_of(resp)))
    except urllib.error.HTTPError as e:
        raise ModelError(f"文本接口 HTTP {e.code}")
    except TimeoutError:
        raise ModelError(f"文本接口超时（{timeout}s）")
    except Exception as e:
        raise ModelError(f"文本接口异常：{e}")

    replies = [{"en": str(x["en"]).strip(), "cn": str(x.get("cn") or "").strip()}
               for x in (payload.get("replies") or [])
               if isinstance(x, dict) and str(x.get("en") or "").strip()]
    zh = str(payload.get("translation") or "").strip()
    if not zh:
        raise ModelError("文本接口没返回译文")
    return {
        "raw": chat_text,
        "translation": zh,
        "summary": str(payload.get("summary") or "").strip(),
        "replies": replies[:3],
    }
