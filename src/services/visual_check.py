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
