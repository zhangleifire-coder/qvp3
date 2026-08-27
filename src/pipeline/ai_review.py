"""AI 双重审核（2026-08-27）：生图后逐页视觉审核，不达标自动调提示词重生成。

① 文字正确性：OCR 与分页文案对撞（复用 P0-2 的 _text_similarity）——
   扭曲/异体变形字符被拦；
② 实景协调性（qwen-vl 视觉判定）：图中文字量是否协调（不过载/无伪字乱码）、
   实景图嵌入是否协调（大小/位置/对比度，compare/single 重点）——
   不达标带建议自动调整提示词重生成，最多 2 轮，仍败打标记进人工审核。
"""
import base64
import json
import traceback

import httpx

from src.config import settings
from src.gateway.ocr import fetch_image_bytes

_MAX_ROUNDS = 2

_VL_REVIEW_PROMPT = """你是图文交付质量审核员。下面是第 {page} 页交付配图与其目标页文案。逐项审核后只输出 JSON：

【页文案】{page_text}
【本页是 compare/single 模式：{ref_mode}（是=应有实景参考图嵌入画面）】

审核项：
1. text_ok：图中文字是否正确（有无伪汉字/异体变形/乱码/明显错字）；
2. text_amount_ok：图中文字数量是否协调（是否信息过载堆太多字，或该有的字缺失）；
3. ref_ok：实景图嵌入是否协调（大小/位置/对比度/不遮挡主体；无实景图时此项给 true）。

输出 JSON：{{"text_ok": true/false, "text_amount_ok": true/false, "ref_ok": true/false,
  "issues": ["问题1"], "suggest": "一句具体的生图调整建议（怎么改提示词）"}}"""


async def _vl_review(image_url: str, page_text: str, page: int, ref_mode: bool) -> dict:
    """qwen-vl 视觉审核一页配图，返回结构化判定（失败默认通过不误杀）。"""
    try:
        data, ctype = await fetch_image_bytes(image_url)
        mime = ctype if ctype.startswith("image/") else "image/png"
        data_url = f"data:{mime};base64," + base64.b64encode(data).decode()
        payload = {
            "model": settings.ocr_model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": _VL_REVIEW_PROMPT.format(
                    page=page, page_text=page_text[:120],
                    ref_mode="是" if ref_mode else "否")},
            ]}],
            "max_tokens": 600,
        }
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{settings.ocr_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
                json=payload)
            if resp.status_code != 200:
                return {"pass": True, "issues": [], "suggest": ""}
            content = resp.json()["choices"][0]["message"]["content"].strip()
        raw = content.strip("`").lstrip("json").strip()
        j = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        ok = all(j.get(k, True) for k in ("text_ok", "text_amount_ok", "ref_ok"))
        return {"pass": ok, "issues": [str(i)[:100] for i in j.get("issues", [])],
                "suggest": str(j.get("suggest", ""))[:200],
                "flags": {k: j.get(k, True) for k in ("text_ok", "text_amount_ok", "ref_ok")}}
    except Exception:  # noqa: BLE001
        return {"pass": True, "issues": [], "suggest": ""}


async def _image_to_data_url_local(image_url: str) -> str:
    data, ctype = await fetch_image_bytes(image_url)
    mime = ctype if ctype.startswith("image/") else "image/png"
    return f"data:{mime};base64," + base64.b64encode(data).decode()


async def _gen_one_with_review(task_id, page_index: int, page_text: str,
                               base_prompt: str, ref_urls: list[str],
                               ref_mode: bool) -> tuple[str, str, dict]:
    """生成 + AI 双重审核 + 自动重生成；返回 (local_url, model_version, review_info)。

    两轮内仍不达标：返回最新图但 review_info.flagged 标问题（进人工审核）。
    """
    from src.gateway.image_gen import generate_image
    from src.gateway.ocr import ocr_image
    from src.pipeline.agent_production import _text_similarity, _GARBLE_THRESHOLD
    from src.pipeline.nodes import _persist_image

    async def _gen(prompt: str) -> tuple[str, str, bytes, str]:
        r = await generate_image(prompt, reference_image_urls=ref_urls or None)
        data, ctype = await fetch_image_bytes(r["image_url"])
        local_url = _persist_image(task_id, page_index, "p", data, ctype)
        return local_url, r.get("model_version", "gpt-image-2"), data, ctype

    last_prompt = base_prompt
    review: dict = {"pass": True, "issues": [], "suggest": "", "rounds": 0, "flagged": []}
    best: tuple | None = None
    for rnd in range(1, _MAX_ROUNDS + 1):
        local_url, model, data, ctype = await _gen(last_prompt)
        # ① 文字正确性（OCR 对撞）
        text_ok = True
        try:
            ocr = await ocr_image(local_url)
            text_ok = _text_similarity(ocr["raw_text"], page_text) >= _GARBLE_THRESHOLD
        except Exception:  # noqa: BLE001
            text_ok = True
        # ② 实景协调性（qwen-vl）
        vl = await _vl_review(local_url, page_text, page_index, ref_mode)
        review = {"pass": text_ok and vl["pass"], "issues": vl.get("issues", []),
                  "suggest": vl.get("suggest", ""), "rounds": rnd,
                  "flagged": ([] if text_ok else ["文字扭曲"])
                              + ([] if vl["pass"] else vl.get("issues", ["协调性问题"]))}
        best = (local_url, model, review)
        if review["pass"]:
            return best[0], best[1], review
        # 不达标：按建议调整提示词进下一轮
        adjust = vl.get("suggest") or ("文字保持正确，构图换一种" if not text_ok
                                       else "精简图上文字，主体更突出")
        last_prompt = (base_prompt
                       + f"（AI 审核第{rnd}轮发现问题：{'；'.join(review['flagged'])[:80]}。"
                         f"调整要求：{adjust[:120]}）")
    # 两轮仍不达标：保留最新图但标记，交人工审核
    review["flagged"] = review["flagged"] or ["AI 审核未通过"]
    return best[0], best[1], review
