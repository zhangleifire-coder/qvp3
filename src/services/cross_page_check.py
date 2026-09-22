"""跨页批量质检（v0.1.4 P3，G1 质检端 + G6 跨页维度）。

一次 VL 调用覆盖《供应商生产标准手册 V2.0》「图集一致性（主风格 ≥80%）/
图集多样性（信息维度）」两个验收维度 + 深底页数铁律：把全套页图拼成一张
缩略图网格（contact sheet）送检，查四个维度——跨页信息重复 / 跨页视觉
重复 / 风格一致性 / 深底页数。

语义：告警为主——结果转 RejectMark 进人工审核队列（强制人工、不自动
放行），不自动重生（重生由审图人工触发，与 garble 链语义对齐）。
拼版失败 / VL 不可用 / 解析失败返回 None（调用方按放行处理，人工兜底）。
"""
from __future__ import annotations

import json

_CROSS_PAGE_PROMPT = """你是图集跨页质检员。图中是一套 {n} 页竖版图文卡的缩略图拼版，按行排列（第 1 行是第 1-{cols} 页，第 2 行接续，依次类推）。各页标题与要点文案：

{pages_digest}

请检查四个维度（逐格过目）：
1. info_repeat：跨页信息重复——是否存在两页及以上在讲同一个知识点/结论/动作/风险提示（换角度补充新信息不算重复）。
2. visual_repeat：跨页视觉重复——是否存在两页及以上用了雷同的主场景/构图/道具/视觉隐喻（同一主体在不同景别/角度的必要延续不算）。
3. style_consistent：全套风格是否统一——底色色系、字体系统、版式语言一致，主风格占比 80% 以上，没有风格迥异的拼凑页。
4. dark_pages：大面积深色底压字的页数（全套应 ≤1 页）。

只输出严格 JSON，不要任何其他文字：
{{"info_repeat": true/false, "visual_repeat": true/false, "style_consistent": true/false, "dark_pages": 0, "suspect_pages": [重复涉及的页码], "issues": ["问题简述，如：第2/3页知识点重复"]}}"""


async def build_contact_sheet(task_id, image_urls: list, cols: int = 3,
                               thumb_w: int = 420) -> str | None:
    """全套页图 → 缩略图拼版 PNG → 落 static/generated 并返回 URL。

    任一页取图失败返回 None（拼版缺格会误导 VL 判页码）。
    """
    import io
    from PIL import Image
    from src.gateway.ocr import fetch_image_bytes
    from src.pipeline.nodes import _persist_image

    thumbs = []
    for url in image_urls:
        try:
            data, _ = await fetch_image_bytes(url)
            im = Image.open(io.BytesIO(data)).convert("RGB")
            im.thumbnail((thumb_w, thumb_w * 4 // 3))
            thumbs.append(im)
        except Exception:
            return None
    if not thumbs:
        return None
    cols = max(1, min(cols, len(thumbs)))
    rows = (len(thumbs) + cols - 1) // cols
    cw = max(t.width for t in thumbs)
    ch = max(t.height for t in thumbs)
    sheet = Image.new("RGB", (cw * cols, ch * rows), (245, 243, 238))
    for k, t in enumerate(thumbs):
        sheet.paste(t, ((k % cols) * cw, (k // cols) * ch))
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    return _persist_image(task_id, 0, "cs", buf.getvalue(), "image/png")


def _parse_cross_json(raw: str) -> dict | None:
    """VL 输出 → 四维结果（纯函数，供单测）；字段缺失/类型不对返回 None。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        obj = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except Exception:
        return None
    bools = ("info_repeat", "visual_repeat", "style_consistent")
    if not all(isinstance(obj.get(k), bool) for k in bools):
        return None
    try:
        dark = int(obj.get("dark_pages", 0))
    except Exception:
        dark = 0
    suspects = [int(p) for p in (obj.get("suspect_pages") or [])
                if str(p).strip().isdigit()][:8]
    return {"info_repeat": obj["info_repeat"],
            "visual_repeat": obj["visual_repeat"],
            "style_consistent": obj["style_consistent"],
            "dark_pages": dark, "suspect_pages": suspects,
            "issues": [str(i)[:80] for i in (obj.get("issues") or [])][:5]}


async def cross_page_check(task_id, image_urls: list,
                           pages: list) -> dict | None:
    """跨页四维检查主入口：拼版 → 一次 VL → {"ok", "issues", "detail"}。"""
    from src.config import settings
    if not (settings.cross_page_check_enabled and not settings.mock_image_gen):
        return None
    urls = [u for u in image_urls if u]
    if len(urls) < 2:
        return None
    sheet_url = await build_contact_sheet(task_id, urls)
    if not sheet_url:
        return None
    try:
        from src.gateway.ocr import _image_to_data_url
        from src.gateway.http_client import get_client
        data_url = await _image_to_data_url(sheet_url)
        digest = "\n".join(
            f"第{i}页：{(p or '').strip()[:80]}" for i, p in enumerate(pages, 1)
            if (p or "").strip()) or "（无文案）"
        cols = 3 if len(urls) > 3 else len(urls)
        prompt = (_CROSS_PAGE_PROMPT
                  .replace("{n}", str(len(urls)))
                  .replace("{cols}", str(cols))
                  .replace("{pages_digest}", digest))
        resp = await get_client("visual_check", timeout=90).post(
            f"{settings.ocr_base_url}/chat/completions",
            headers={"Authorization":
                     f"Bearer {settings.dashscope_api_key}"},
            json={"model": settings.visual_check_model,
                  "messages": [{"role": "user", "content": [
                      {"type": "image_url",
                       "image_url": {"url": data_url}},
                      {"type": "text", "text": prompt}]}],
                  "max_tokens": 400})
        if resp.status_code != 200:
            return None
        raw = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        parsed = _parse_cross_json(raw)
        if parsed is None:
            return None
        ok = (not parsed["info_repeat"] and not parsed["visual_repeat"]
              and parsed["style_consistent"] and parsed["dark_pages"] <= 1)
        return {"ok": ok, "issues": parsed["issues"],
                "detail": {"suspect_pages": parsed["suspect_pages"],
                           "dark_pages": parsed["dark_pages"],
                           "info_repeat": parsed["info_repeat"],
                           "visual_repeat": parsed["visual_repeat"],
                           "style_consistent": parsed["style_consistent"]}}
    except Exception:
        import traceback
        traceback.print_exc()
        return None


def cross_flags(result: dict, n: int) -> dict:
    """跨页检查结果 → 每页 RejectMark 标记（告警）。

    - suspect_pages 涉及页：逐页标注具体 issues；
    - 全局性问题（风格不一致 / 深底超限）：标第 1 页（避免全套噪音）。
    """
    flags: dict[int, list[str]] = {}
    if not result or result.get("ok"):
        return flags
    detail = result.get("detail") or {}
    issues = result.get("issues") or []
    for p in (detail.get("suspect_pages") or []):
        if isinstance(p, int) and 1 <= p <= n:
            flags.setdefault(p, []).append(
                "[跨页] " + ("；".join(issues)[:80] if issues else "跨页重复"))
    if detail.get("style_consistent") is False:
        flags.setdefault(1, []).append("[跨页] 全套风格不一致（主风格应≥80%）")
    if (detail.get("dark_pages") or 0) > 1:
        flags.setdefault(1, []).append(
            f"[跨页] 深色底压字页数 {detail['dark_pages']} 页（全套应≤1）")
    return flags
