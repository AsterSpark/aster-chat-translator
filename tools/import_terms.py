# -*- coding: utf-8 -*-
"""把两个 MIT 词表合并成本地术语表。

数据来源（都是 MIT，保留版权声明）：
  1. Yumash/BabelChat —— 436 条，英→中，12 个分类文件
     Copyright (c) 2025-2026 Andrey Yumashev / Pirson (WoW dictionary data)
  2. paokkerkir/wow-translate —— 500+ 条，中→英（这里反查成英→中）
     Copyright (c) 2026 WoWTranslate Team

用法：py tools/import_terms.py
产出：core/terms_data.py
"""

import io
import os
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "core", "terms_data.py")

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cache")
BABEL_BASE = "https://cdn.jsdelivr.net/gh/Yumash/BabelChat@main/addon/BabelChat/Data/"
BABEL_FILES = [
    "Social.lua", "Mazz_Raid.lua", "Clases.lua", "Slang.lua", "Combate.lua",
    "Grupos.lua", "Endgame.lua", "Estadisticas.lua", "Profesiones.lua",
    "Estado.lua", "Roles.lua", "Comercio.lua", "Hermandad.lua",
]
WOWTR = ("https://cdn.jsdelivr.net/gh/paokkerkir/wow-translate@main/"
         "Interface/AddOns/WoWTranslate/WoWTranslate_Glossary.lua")


def fetch(url, name=None):
    """优先读 tools/_cache 的本地副本，没有才联网（raw.githubusercontent 常被墙）"""
    local = os.path.join(CACHE, name or url.rsplit("/", 1)[-1])
    if os.path.isfile(local):
        with io.open(local, encoding="utf-8", errors="replace") as f:
            return f.read()
    req = urllib.request.Request(url, headers={"User-Agent": "wowtr-term-import"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_babel(text):
    """[ "lfm" ] = { enUS = "...", zhCN = "..." } → [(key, zh)]"""
    out = []
    # 每个词条块形如 ["xxx"] = { ... zhCN = "中文" ... }
    for m in re.finditer(r'\["([^"]+)"\]\s*=\s*\{(.*?)\n\s*\}', text, re.S):
        key, block = m.group(1), m.group(2)
        zh = re.search(r'zhCN\s*=\s*"([^"]*)"', block)
        en = re.search(r'enUS\s*=\s*"([^"]*)"', block)
        if not zh or not zh.group(1).strip():
            continue
        out.append((key.strip(), zh.group(1).strip(), (en.group(1).strip() if en else "")))
    return out


def parse_wowtr(text):
    """WoWTranslateGlossary["中文"] = "English" → (英文, 中文)

    只收左边是中文的条目：原表里还有 ["MC"] = "Molten Core" 这类英文别名，
    反查过来会变成 "Molten Core → MC"，把中文译名挤掉。
    """
    out = []
    for m in re.finditer(r'WoWTranslateGlossary\["([^"]+)"\]\s*=\s*"([^"]*)"', text):
        zh, en = m.group(1).strip(), m.group(2).strip()
        if not zh or not en:
            continue
        if not re.search(r"[\u4e00-\u9fff]", zh):   # 左边必须含中文
            continue
        out.append((en, zh))
    return out


def main():
    print("拉取 BabelChat…")
    bab = {}
    for f in BABEL_FILES:
        try:
            items = parse_babel(fetch(BABEL_BASE + f, f))
            for key, zh, en in items:
                bab.setdefault(key.lower(), (key, zh))
            print(f"  {f}: {len(items)} 条")
        except Exception as e:
            print(f"  {f} 失败: {e}")

    print(f"BabelChat 合计 {len(bab)} 条")

    print("拉取 wow-translate…")
    try:
        resp = parse_wowtr(fetch(WOWTR))
    except Exception as e:
        print(f"  失败: {e}")
        resp = []
    print(f"  解析 {len(resp)} 条（中→英，需反查）")

    # 合并：BabelChat 优先（更新勤、零售版全），wow-translate 只补缺
    merged = {}
    conflicts = []
    for key, (orig, zh) in bab.items():
        merged[key] = zh

    for en, zh in resp:
        k = en.lower()
        if k in merged:
            if merged[k] != zh:
                conflicts.append((en, merged[k], zh))
            continue
        merged[k] = zh

    print(f"合并后 {len(merged)} 条，冲突 {len(conflicts)} 条（保留 BabelChat 的译名）")
    for c in conflicts[:8]:
        print(f"  冲突: {c[0]}  Babel={c[1]}  WoWTR={c[2]}")

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write('# -*- coding: utf-8 -*-\n')
        f.write('"""WoW 术语表（自动生成，勿手改）\n\n')
        f.write('数据来源：\n')
        f.write('  BabelChat — https://github.com/Yumash/BabelChat (MIT License)\n')
        f.write('    Copyright (c) 2025-2026 Andrey Yumashev / Pirson (WoW dictionary data)\n')
        f.write('  wow-translate — https://github.com/paokkerkir/wow-translate (MIT License)\n')
        f.write('    Copyright (c) 2026 WoWTranslate Team\n\n')
        f.write('重新生成：py tools/import_terms.py\n"""\n\n')
        f.write('TERMS = {\n')
        for k in sorted(merged):
            zh = merged[k].replace('\\', '\\\\').replace('"', '\\"')
            f.write(f'    "{k}": "{zh}",\n')
        f.write('}\n')

    print(f"已写出 {OUT}（{os.path.getsize(OUT) // 1024} KB，{len(merged)} 条）")


if __name__ == "__main__":
    main()
