import asyncio
import base64
import hashlib
import httpx
from src.config import settings

IMAGE_MODEL = settings.image_model
IMAGE_SIZE = settings.image_size

# 双通道负载均衡：轮询游标（每次调用取下一个可用通道）
_channel_cursor = 0


def _channels() -> list[str]:
    """当前可用的生图通道列表（按配置，moacode 无 key 自动剔除）。"""
    cfg = [c.strip() for c in settings.image_gen_channels.split(",") if c.strip()]
    avail = []
    for c in cfg:
        if c == "linkai" and settings.openai_image_api_key != "sk-xxx":
            avail.append("linkai")
        elif c == "moacode" and settings.moacode_api_key:
            avail.append("moacode")
        elif c == "fusion" and settings.fusionai_api_key:
            avail.append("fusion")
    return avail or ["linkai"]  # 兜底防全不可用


def _next_channel() -> str:
    global _channel_cursor
    avail = _channels()
    ch = avail[_channel_cursor % len(avail)]
    _channel_cursor += 1
    return ch


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.openai_image_api_key}"}


async def _download_image_bytes(url: str) -> tuple:
    """取参考图字节，返回 (bytes, content_type)。

    复用 fetch_image_bytes：支持本地产出（/static/... 读磁盘）、openox 签名
    内联 URL 本地解码、远程带浏览器 UA/Referer 防盗链。直接用 httpx 裸 GET
    会对本地路径抛 UnsupportedProtocol、对防盗链图床 403，导致图生图被静默
    降级成文生图（2026-08-20 对比模式实景图未生效的根因）。
    """
    from src.gateway.ocr import fetch_image_bytes
    return await fetch_image_bytes(url)


def _mock_result(prompt: str) -> dict:
    """开发阶段模拟生图：返回占位 SVG（data URI），不调用真实 API。"""
    h = hashlib.md5(prompt.encode()).hexdigest()
    svg = (
        "data:image/svg+xml;utf8,"
        f"<svg xmlns='http://www.w3.org/2000/svg' width='576' height='768'>"
        f"<rect width='100%' height='100%' fill='%23{h[:6]}'/>"
        f"<text x='50%' y='50%' font-size='40' fill='white' text-anchor='middle'"
        f" font-family='sans-serif'>MOCK {h[:4]}</text></svg>"
    )
    return {"image_url": svg, "hash": h, "model_version": "mock"}


async def generate_image(prompt: str, size: str = None,
                         reference_image_urls: list[str] | None = None,
                         max_retries: int = 3) -> dict:
    """调用 gpt-image-2 生成一张图；reference_image_urls 非空则图生图。

    双通道轮询负载均衡（_next_channel）：LinkAI / Moacode 交替使用，
    单通道失败自动降级另一通道；mock_image_gen 开启时返回占位图。
    """
    if settings.mock_image_gen:
        return _mock_result(prompt)
    size = size or IMAGE_SIZE
    channel = _next_channel()
    for attempt in range(max_retries):
        try:
            if reference_image_urls:
                try:
                    # 图生图按通道路由：fusion=主(edits multipart b64) /
                    # moacode=垫图(input_image) / linkai=images/edits
                    if channel == "fusion":
                        return await _edit_fusion(prompt, reference_image_urls, size)
                    if channel == "moacode":
                        return await _edit_moacode(prompt, reference_image_urls, size)
                    return await _edit_with_references(prompt, reference_image_urls, size)
                except Exception as e:  # noqa: BLE001
                    # 当前通道图生图失败 → 先试另一通道的图生图（保住参考图语义），
                    # 两通道都败才降级文生图，且必须留痕（静默降级会让对比模式失效）
                    print(f"[image_gen] {channel} 图生图失败: {type(e).__name__}: {e}",
                          flush=True)
                    try:
                        if channel == "moacode":
                            return await _edit_with_references(prompt, reference_image_urls, size)
                        if "moacode" in _channels():
                            return await _edit_moacode(prompt, reference_image_urls, size)
                    except Exception as e2:  # noqa: BLE001
                        print(f"[image_gen] 备用通道图生图也失败（{type(e2).__name__}），降级文生图",
                              flush=True)
                    return await _generate_by_channel(prompt, size, channel)
            return await _generate_by_channel(prompt, size, channel)
        except Exception as e:  # noqa: BLE001
            if attempt == max_retries - 1:
                # 当前通道耗尽重试 → 降级另一通道再试一次
                other = [c for c in _channels() if c != channel]
                if other:
                    print(f"[image_gen] {channel} 通道失败，降级 {other[0]}: {type(e).__name__}",
                          flush=True)
                    return await _generate_by_channel(prompt, size, other[0])
                raise
            await asyncio.sleep(2 * (2 ** attempt))


async def _generate_by_channel(prompt: str, size: str, channel: str) -> dict:
    """按通道路由生图：fusion=主通道(Images API b64) / moacode=Responses API
    SSE / linkai=Images API url。"""
    if channel == "fusion":
        return await _generate_fusion(prompt, size)
    if channel == "moacode":
        return await _generate_moacode(prompt, size)
    return await _generate(prompt, size)


def _fusion_headers() -> dict:
    return {"Authorization": f"Bearer {settings.fusionai_api_key}"}


async def _generate_fusion(prompt: str, size: str) -> dict:
    """FusionAI 通道（主）：POST /images/generations，返回 b64_json → data URI。
    生图可能数分钟（官方口径），读超时 600s。"""
    url = f"{settings.fusionai_base_url.rstrip('/')}/images/generations"
    payload = {"model": IMAGE_MODEL, "prompt": prompt, "size": size, "n": 1,
               "quality": settings.image_quality}
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0, read=600.0,
                                                       write=30.0, pool=10.0)) as client:
        resp = await client.post(url, json=payload, headers=_fusion_headers())
        if resp.status_code >= 400:
            raise RuntimeError(f"fusion gen failed ({resp.status_code}): {resp.text[:300]}"
                               f" [X-Request-Id: {resp.headers.get('X-Request-Id', '-')}]")
        data = resp.json()
    item = (data.get("data") or [{}])[0]
    b64 = item.get("b64_json")
    if not b64:
        raise RuntimeError(f"fusion 响应缺 b64_json: {str(data)[:200]}")
    return {"image_url": f"data:image/png;base64,{b64}",
            "hash": hashlib.md5(b64.encode()).hexdigest(),
            "model_version": f"{IMAGE_MODEL}@fusion"}


async def _edit_fusion(prompt: str, reference_image_urls: list[str], size: str) -> dict:
    """FusionAI 图生图：/images/edits multipart，参考图压缩上传，b64_json 返回。"""
    url = f"{settings.fusionai_base_url.rstrip('/')}/images/edits"
    files = []
    for i, ref_url in enumerate(reference_image_urls[:3]):
        content, ctype = await _download_image_bytes(ref_url)
        ext = {"image/png": "png", "image/jpeg": "jpg",
               "image/webp": "webp"}.get(ctype, "png")
        files.append(("image[]", (f"ref_{i}.{ext}", content, ctype)))
    data = {"model": IMAGE_MODEL, "prompt": prompt, "size": size, "n": "1",
            "quality": settings.image_quality}
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0, read=600.0,
                                                       write=60.0, pool=10.0)) as client:
        resp = await client.post(url, data=data, files=files, headers=_fusion_headers())
        if resp.status_code >= 400:
            raise RuntimeError(f"fusion edit failed ({resp.status_code}): {resp.text[:300]}"
                               f" [X-Request-Id: {resp.headers.get('X-Request-Id', '-')}]")
        j = resp.json()
    item = (j.get("data") or [{}])[0]
    b64 = item.get("b64_json")
    if not b64:
        raise RuntimeError(f"fusion 编辑响应缺 b64_json: {str(j)[:200]}")
    return {"image_url": f"data:image/png;base64,{b64}",
            "hash": hashlib.md5(b64.encode()).hexdigest(),
            "model_version": f"{IMAGE_MODEL}@fusion"}


async def _generate_moacode(prompt: str, size: str,
                            reference_data_uris: list[str] | None = None) -> dict:
    """Moacode 通道：POST /v1/responses，SSE 流式（官方文档口径，2026-08-27）。

    - 取图两步缺一不可：优先 response.output_item.done 的最终图；
      流提前结束时退回最后一个 partial_image_b64（完整可用，画质略低）
    - size 参数不生效：比例写进提示词（本平台统一竖版 3:4 →「3:4 竖版构图」，
      实际输出 1086x1448，比例正好 3:4）
    - 垫图（input_image data URI）控制比例且支持图生图：reference_data_uris 非空时附加
    """
    url = f"{settings.moacode_base_url.rstrip('/')}/responses"
    headers = {"Authorization": f"Bearer {settings.moacode_api_key}",
               "Content-Type": "application/json", "Accept": "text/event-stream"}
    if "3:4" not in prompt and "2:3" not in prompt and "1:1" not in prompt:
        prompt = f"{prompt}，3:4 竖版构图"
    content: list[dict] = [{"type": "input_text", "text": prompt}]
    for uri in (reference_data_uris or [])[:3]:
        content.append({"type": "input_image", "image_url": uri})
    body = {
        "model": IMAGE_MODEL,
        "input": [{"type": "message", "role": "user", "content": content}],
        "stream": True,
        "store": False,
    }
    import json as _json
    b64_final = None
    b64_partial = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0, read=240.0,
                                                       write=30.0, pool=10.0)) as client:
        async with client.stream("POST", url, json=body, headers=headers) as resp:
            if resp.status_code >= 400:
                detail = (await resp.aread()).decode("utf-8", "replace")[:400]
                raise RuntimeError(f"moacode gen failed ({resp.status_code}): {detail}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    ev = _json.loads(payload)
                except Exception:  # noqa: BLE001
                    continue
                etype = ev.get("type", "")
                if (etype == "response.output_item.done"
                        and (ev.get("item") or {}).get("type") == "image_generation_call"):
                    b64_final = ev["item"].get("result") or b64_final
                    break
                if etype == "response.image_generation_call.partial_image":
                    b64_partial = ev.get("partial_image_b64") or b64_partial
    b64 = b64_final or b64_partial
    if not b64:
        raise RuntimeError("moacode 流式结束但未取到图（final 与 partial 均无）")
    # 与 LinkAI 的 URL 形态对齐：data URI，本地化时 fetch_image_bytes 解 base64
    data_uri = f"data:image/png;base64,{b64}"
    tag = "@moacode" if b64_final else "@moacode:partial"
    return {"image_url": data_uri,
            "hash": hashlib.md5(b64.encode()).hexdigest(),
            "model_version": f"{IMAGE_MODEL}{tag}"}


async def _edit_moacode(prompt: str, reference_image_urls: list[str],
                        size: str) -> dict:
    """Moacode 图生图：参考图转 data URI 垫图（input_image），输出跟随其比例。"""
    import base64 as _b64
    data_uris = []
    for u in reference_image_urls[:3]:
        data, ctype = await _download_image_bytes(u)
        mime = ctype if ctype.startswith("image/") else "image/png"
        data_uris.append(f"data:{mime};base64," + _b64.b64encode(data).decode())
    return await _generate_moacode(prompt, size, reference_data_uris=data_uris)


async def _generate(prompt: str, size: str) -> dict:
    """文生图：POST /v1/images/generations"""
    url = f"{settings.openai_image_base_url}/images/generations"
    payload = {"model": IMAGE_MODEL, "prompt": prompt, "size": size, "n": 1,
               "response_format": "url", "quality": settings.image_quality}
    async with httpx.AsyncClient(timeout=240) as client:
        resp = await client.post(url, json=payload, headers=_headers())
        if resp.status_code >= 400:
            raise RuntimeError(f"image gen failed ({resp.status_code}): {resp.text[:400]}")
        data = resp.json()
    u = data["data"][0].get("url")
    if not u:
        raise RuntimeError(f"image response missing url field: {str(data)[:200]}")
    return _result(u)


async def _edit_with_references(prompt: str, reference_image_urls: list[str],
                                size: str) -> dict:
    """图生图：POST /v1/images/edits，参考图 multipart 上传。"""
    url = f"{settings.openai_image_base_url}/images/edits"
    files = []
    for i, ref_url in enumerate(reference_image_urls):
        content, ctype = await _download_image_bytes(ref_url)
        ext = {"image/png": "png", "image/jpeg": "jpg",
               "image/webp": "webp"}.get(ctype, "png")
        files.append(("image[]", (f"ref_{i}.{ext}", content, ctype)))
    # gpt-image-2 编辑时自动高保真，传 input_fidelity 会返回 400，故不传
    data = {"model": IMAGE_MODEL, "prompt": prompt, "size": size,
            "n": "1", "response_format": "url", "quality": settings.image_quality}
    async with httpx.AsyncClient(timeout=240) as client:
        resp = await client.post(url, data=data, files=files, headers=_headers())
        if resp.status_code >= 400:
            raise RuntimeError(f"image edit failed ({resp.status_code}): {resp.text[:400]}")
        j = resp.json()
    u = j["data"][0].get("url")
    if not u:
        raise RuntimeError(f"image response missing url field: {str(j)[:200]}")
    return _result(u)


def _result(image_url: str) -> dict:
    return {"image_url": image_url, "hash": hashlib.md5(image_url.encode()).hexdigest(),
            "model_version": IMAGE_MODEL}
