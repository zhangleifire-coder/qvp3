"""视觉主体审核（2026-09-01，图文一致性铁律）：

文章说猫、图里画狗——这类图文脱节此前只能靠人工审图发现。本服务用 VL
多模态模型（dashscope qwen-vl，复用 OCR 的网关与密钥）看图回答：
图的实际主体是什么、是否与该页文案主题一致。

返回 {"ok": bool, "actual": str}；VL 不可用/解析失败返回 None——
审核层跳过不阻塞出图（人工关卡兜底），调用方绝不能因审核挂掉而失败。
"""
import json

from src.config import settings
from src.gateway.http_client import get_client

_CHECK_PROMPT = """你是图片质检员。看这张图文卡片，回答两个问题：
1. 图中画面主体是什么（15字内）？
2. 该页文案主题是：「{text}」。
判定口径（重要）：图中**主要主体**（占画面视觉主导的事物）必须是文案所述
事物之一——对比类文案（如「A和B怎么选」）画 A 或画 B 都算；同类事物或该
主题的典型场景也算。**不算通过**的情形：画面只是一片泛泛的场景（如厨房
全景/城市街景/一堆杂物）而文案讲的是某个具体产品；主体是别的产品（文案
说咖啡机画面是洗碗机）；主体与文案完全无关（文案说猫画面是狗）。
只输出严格 JSON，不要任何其他文字：
{{"ok": true/false, "actual": "<图中主体>"}}"""


async def check_subject_match(image_url: str, page_text: str) -> dict | None:
    """VL 看图比对主体一致性。返回 {ok, actual}；失败/未启用返回 None。"""
    if not settings.visual_subject_check_enabled or not (page_text or "").strip():
        return None
    try:
        from src.gateway.ocr import _image_to_data_url
        data_url = await _image_to_data_url(image_url)
        prompt = _CHECK_PROMPT.replace("{text}", page_text.strip()[:80])
        payload = {
            "model": settings.visual_check_model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": prompt},
            ]}],
            "max_tokens": 200,
        }
        resp = await get_client("visual_check", timeout=60).post(
            f"{settings.ocr_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            json=payload)
        if resp.status_code != 200:
            return None
        raw = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        obj = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        if isinstance(obj.get("ok"), bool) and obj.get("actual"):
            return {"ok": obj["ok"], "actual": str(obj["actual"])[:30]}
        return None
    except Exception:
        import traceback
        traceback.print_exc()   # VL 审核失败不阻塞出图
        return None


_TEXT_APPEAL_PROMPT = """你是图片文字质检员。看这张图文卡片，把图中实际渲染的
文字与给定文案逐字比对（文案：「{text}」）。
判定口径：忽略标点、空白、全半角差异；繁体/异体字与对应简体视为一致；
图中文字与文案逐字相同（不增字、不漏字、不改写）才算一致。
注意：只依据图中真实渲染的文字判定，不要因为字体风格（粗黑/宋体/手写感）
或文字被装饰元素部分遮挡而判不一致——遮挡到无法辨认该字才算缺失。
只输出严格 JSON，不要任何其他文字：
{{"consistent": true/false, "actual": "<图中实际文字，30字内概述>"}}"""


async def check_text_match(image_url: str, expected_text: str) -> dict | None:
    """100% OCR 标准的 VL 申诉通道（2026-09-14 P2）。

    OCR 判不合格的页，由 VL 直接看图复核「图中文字是否与文案逐字一致」：
    consistent=true → OCR 误判申诉成功，放行；false/None → 维持 OCR 判定
    （调用方走重生）。放行口径仍是 100%，只给 OCR 误判一个复核出口。
    返回 {"ok": bool, "actual": str}；VL 不可用/解析失败返回 None。
    """
    if not settings.visual_text_appeal_enabled or not (expected_text or "").strip():
        return None
    try:
        from src.gateway.ocr import _image_to_data_url
        data_url = await _image_to_data_url(image_url)
        prompt = _TEXT_APPEAL_PROMPT.replace("{text}", expected_text.strip())
        payload = {
            "model": settings.visual_check_model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": prompt},
            ]}],
            "max_tokens": 200,
        }
        resp = await get_client("visual_check", timeout=60).post(
            f"{settings.ocr_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            json=payload)
        if resp.status_code != 200:
            return None
        raw = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        obj = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        if isinstance(obj.get("consistent"), bool):
            return {"ok": obj["consistent"],
                    "actual": str(obj.get("actual", ""))[:30]}
        return None
    except Exception:
        import traceback
        traceback.print_exc()   # VL 复核失败不阻塞（维持 OCR 判定，走重生）
        return None


_GLYPH_VERIFY_PROMPT = """你是中文印刷质量检查员。图中是一张图文卡片。

标准文案（必须逐字出现在图中）：「{text}」

请逐项检查（图中**每一个汉字**都要过目，不是泛泛识别）：
1. locked_ok：标准文案是否逐字完整出现在图中——不得改写、增字、漏字、
   换字、换标点；繁体/异体写法视为不一致。
2. locked_errors：差异明细：[{{"标准": "...", "图中": "..."}}]，无则为[]。
3. glyph_errors：逐字核对图中**所有汉字**（含标准文案以外的标签、说明、
   图标内文字）是否为规范简体字形。对每个字**必须看它的偏旁部首**：
   火/土、扌/木、日/目、礻/衤、冫/氵、贝/见 等偏旁混淆是高频错字源，
   逐字确认偏旁正确（例如「烘焙」是火字旁，写成土字旁「烘培」即错字）。
   只列出**不规范**的字：["图中字: 问题描述"]——正确的字一律不要列出，
   一条都不要；若全部规范，必须返回空数组 []。
4. extra_text_gist：标准文案以外文字的概述（30字内），无则空串。
5. render_ok：文字渲染质量——每个字（含小字）是否锐利清晰、笔画分明、
   无粘连/模糊/断笔/缺笔/糊团。任何字糊成团、笔画粘连无法分辨即 false。

只输出严格 JSON，不要任何其他文字：
{{"locked_ok": true/false, "locked_errors": [...],
  "glyph_errors": [...], "extra_text_gist": "...", "render_ok": true/false,
  "render_issues": ["问题描述，如：小字粘连成块/标题笔画模糊"]}}"""


async def verify_page_glyphs(image_url: str, expected_text: str) -> dict | None:
    """逐字字形校验（2026-09-18）：不是开放识别"图上有什么字"，
    而是拿标准文案逐字核对 + 全图每个汉字规范性检查——专抓相似度
    对撞抓不到的错误：LOCKED 文案字形变形、自由文字错字（如 烘培/烘焙）。

    返回 {"locked_ok": bool, "glyph_errors": [str], "extra_text_gist": str}
    或 None（VL 不可用/解析失败——调用方按"未校验"处理，不阻塞）。
    """
    if not (expected_text or "").strip():
        return None
    try:
        from src.gateway.ocr import _image_to_data_url
        data_url = await _image_to_data_url(image_url)
        prompt = _GLYPH_VERIFY_PROMPT.replace("{text}", expected_text.strip())
        payload = {
            "model": settings.visual_check_model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": prompt},
            ]}],
            "max_tokens": 1500,
        }
        resp = await get_client("visual_check", timeout=90).post(
            f"{settings.ocr_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            json=payload)
        if resp.status_code != 200:
            return None
        raw = (resp.json()["choices"][0]["message"]["content"] or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        # 截断防护：输出被 max_tokens 截断时 JSON 不完整，不硬解析（返 None 不误杀）
        if not raw.rstrip().endswith("}"):
            return None
        obj = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        if isinstance(obj.get("locked_ok"), bool):
            # 过滤防御：模型若把"字: 规范"类审计条目误塞进 glyph_errors，
            # 只保留真正的问题条目（含"错/不规范/伪/异体/混淆"等判定词）
            _BAD_WORDS = ("错", "不规范", "伪", "异体", "变形", "混淆", "误")
            errors = [str(e) for e in (obj.get("glyph_errors") or [])
                      if any(w in str(e) for w in _BAD_WORDS)]
            render_ok = obj.get("render_ok", True)
            if not isinstance(render_ok, bool):
                render_ok = True   # 模型未输出该维度时不误判
            render_issues = [str(i)[:60] for i in (obj.get("render_issues") or [])][:5]
            return {"locked_ok": obj["locked_ok"],
                    "glyph_errors": [e[:60] for e in errors][:20],
                    "locked_errors": obj.get("locked_errors") or [],
                    "extra_text_gist": str(obj.get("extra_text_gist", ""))[:60],
                    "render_ok": render_ok,
                    "render_issues": render_issues}
        return None
    except Exception:
        import traceback
        traceback.print_exc()   # 校验失败不阻塞（维持其他判定）
        return None
