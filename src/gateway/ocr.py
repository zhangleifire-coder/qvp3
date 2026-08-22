"""OCR 网关：阿里百炼 qwen 系列视觉模型（OpenAI 兼容接口）。

模型可用 OCR_MODEL 环境变量切换（qwen-vl-ocr / qwen3.5-ocr / qwen3-vl-flash 等）。
"""
import base64
import json

import httpx

from src.config import settings
from src.gateway.cost_tracker import estimate_cost

OCR_PROMPT = "请提取图片中的全部文字，按阅读顺序直接输出文字内容，不要加任何解释或Markdown标记。"


async def fetch_image_bytes(image_url: str) -> tuple:
    """取配图原始字节，返回 (bytes, content_type)。

    生图代理（openox）部分上游返回的是"签名内联 URL"——图片数据直接编码在
    URL 路径里（/v1/images/content/<base64url>.<sig>），这种 URL 超过 4MB，
    任何 HTTP 客户端都无法请求（414），需要本地解码出内嵌的图片数据。
    本地产出（/static/generated/...）直接读磁盘。
    """
    if image_url.startswith("/static/"):
        from pathlib import Path
        ctype = {".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
                     Path(image_url).suffix.lower(), "image/png")
        local = Path(__file__).resolve().parent.parent.parent / image_url.lstrip("/")
        return local.read_bytes(), ctype
    if "/v1/images/content/" in image_url:
        payload = image_url.split("/v1/images/content/", 1)[1].rsplit(".", 1)[0]
        wrapper = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        data_url = wrapper["url"]  # 内嵌的 data:image/...;base64,...
        ctype = data_url.split(";")[0].split(":")[1]
        if ctype == "image/jpg":  # 非标准 MIME，浏览器不渲染
            ctype = "image/jpeg"
        return base64.b64decode(data_url.split(",", 1)[1]), ctype
    headers = {
        # 部分图床（如 699pic）有防盗链/UA 校验，模拟浏览器取图
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Referer": "https://www.baidu.com/",
    }
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(image_url, headers=headers)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "image/png").split(";")[0]
        if ctype == "image/jpg":
            ctype = "image/jpeg"
        return resp.content, ctype


async def _image_to_data_url(image_url: str) -> str:
    data, ctype = await fetch_image_bytes(image_url)
    return f"data:{ctype};base64," + base64.b64encode(data).decode()


async def ocr_image(image_url: str) -> dict:
    """识别一张图的全部文字，返回 {raw_text, cost_cny, model}。"""
    data_url = await _image_to_data_url(image_url)
    payload = {
        "model": settings.ocr_model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": data_url}},
            {"type": "text", "text": OCR_PROMPT},
        ]}],
        "max_tokens": 1500,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{settings.ocr_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"ocr failed ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
    usage = data.get("usage") or {}
    cost = estimate_cost(settings.ocr_model,
                         usage.get("prompt_tokens", 0),
                         usage.get("completion_tokens", 0))
    return {
        "raw_text": data["choices"][0]["message"]["content"].strip(),
        "cost_cny": cost,
        "model": settings.ocr_model,
    }
