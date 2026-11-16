# -*- coding: utf-8 -*-
"""截屏与图片预处理（规格 §2 第 4–5 条）。"""

import io

from PIL import Image, ImageGrab


def grab(cfg):
    """按配置区域截屏，返回 PIL Image"""
    c = (cfg or {}).get("capture") or {}
    if not c:
        raise ValueError("还没有设置截图区域（请在设置里框选）")
    bbox = (int(c.get("left", 0)), int(c.get("top", 1000)),
            int(c.get("right", 700)), int(c.get("bottom", 1400)))
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise ValueError(f"截图区域不合法: {bbox}")
    return ImageGrab.grab(bbox=bbox, all_screens=True)


def prepare_png(img, scale=1.0, max_w=2200):
    """放大后转 PNG：聊天字号小，放大能让模型读得更准"""
    w, h = img.size
    if w and scale != 1.0 and w * scale <= max_w:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
