import time
import litellm
from src.config import settings
from src.gateway.cost_tracker import estimate_cost, refresh_rates


# LLM 响应质量失败的四类显式异常（spec §17.2 决策 9）
class MalformedOutputError(Exception):
    pass


class EmptyResponseError(Exception):
    pass


class InvalidStructureError(Exception):
    pass


class ModelRefusalError(Exception):
    pass


async def call_provider(model: str, prompt: str, api_key: str = None,
                        api_base: str = None, max_tokens: int = 16384,
                        timeout: int = 90, on_delta=None) -> dict:
    litellm.api_key = api_key or settings.deepseek_api_key
    start = time.time()
    kwargs = dict(model=model, messages=[{"role": "user", "content": prompt}],
                  max_tokens=max_tokens, timeout=timeout)
    if api_base:
        kwargs["api_base"] = api_base
    # max_tokens 默认 16384（2026-09-10 上调）：DeepSeek V4 Pro 是推理模型，
    # max_tokens 把 reasoning_tokens 也计入——长提示词推理可达 6k+ token，
    # 旧默认 4096 会被推理吃光导致正文零产出（finish_reason=length，实测）。
    # 4096 旧值的设立背景（2026-08-29 文字核查空内容事故，防 JSON 尾部截断）。
    # timeout 90s：上游偶发挂起（DeepSeek 长生成 120s+ 无响应），及时切断走降级链。
    if on_delta is not None:
        # 流式分支（2026-09-01 用户要求：LLM 思考与生成过程实时上监控）：
        # litellm acompletion(stream=True) 逐块返回，on_delta(piece, total) 按
        # 120 字符节流回调（消费方本就 120 字节流，全量 join 已前移到阈值触发）；
        # usage 在末块（openai 兼容流均带），缺失则按字符估算保持成本口径。
        text, usage = await _call_streaming(kwargs, on_delta)
    else:
        response = await litellm.acompletion(**kwargs)
        text = response.choices[0].message.content if response.choices else None
        usage = response.usage
    elapsed = time.time() - start
    if text is None or text.strip() == "":
        raise EmptyResponseError(f"model {model} returned empty response")
    refusal_markers = ["我无法", "我不能", "抱歉，我无法", "I cannot", "I'm sorry"]
    if any(m in text for m in refusal_markers):
        raise ModelRefusalError(f"model {model} refused: {text[:80]}")
    pt = getattr(usage, "prompt_tokens", 0) or 0
    ct = getattr(usage, "completion_tokens", 0) or 0
    if pt == 0 and ct == 0:
        # 流式末块可能不带 usage：按字符估算（中文≈1.7字/token）
        pt, ct = int(len(prompt) / 1.7), int(len(text) / 1.7)
    cache_hit = _extract_cache_hit_tokens(usage)
    await refresh_rates()  # 费率 DB 动态化：TTL 60s，失败静默走缓存/兜底
    cost = estimate_cost(model, pt, ct, cache_hit_tokens=cache_hit)
    return {
        "text": text,
        "model_version": model,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "cache_hit_tokens": cache_hit,
        "cost_cny": cost,
        "elapsed_seconds": elapsed,
    }


def _extract_cache_hit_tokens(usage) -> int | None:
    """从 litellm usage 提取缓存命中 token 数（DeepSeek: prompt_cache_hit_tokens，
    Anthropic 口径: cache_read_input_tokens）；上游不带则 None=按全部未命中计。"""
    if usage is None:
        return None
    for attr in ("prompt_cache_hit_tokens", "cache_read_input_tokens"):
        v = getattr(usage, attr, None)
        if v:
            return int(v)
    if isinstance(usage, dict):
        for key in ("prompt_cache_hit_tokens", "cache_read_input_tokens"):
            v = usage.get(key)
            if v:
                return int(v)
    return None


async def _call_streaming(kwargs: dict, on_delta) -> tuple[str, object]:
    """流式调用并按 120 字符节流回调 on_delta(piece, total)；返回 (text, usage)。

    累计长度差 <120 时不 join、不回调（原实现每 chunk 全量 join，长流式 O(n²)），
    与 nodes._stream_reporter 的 120 字节流口径一致；流结束补一次尾部回调，
    保证消费方拿到完整终态文本。
    """
    parts: list[str] = []
    usage = None
    total_len = 0
    last_report_len = 0
    last_piece = ""
    response = await litellm.acompletion(**kwargs, stream=True)
    async for chunk in response:
        try:
            u = getattr(chunk, "usage", None)
            if u is not None and (getattr(u, "prompt_tokens", 0) or getattr(u, "completion_tokens", 0)):
                usage = u
        except Exception:  # noqa: BLE001
            pass
        try:
            choices = chunk.choices or []
        except Exception:  # noqa: BLE001
            choices = []
        for ch in choices:
            piece = ""
            try:
                piece = (getattr(ch.delta, "content", None)
                         or getattr(ch, "text", None) or "")
            except Exception:  # noqa: BLE001
                piece = ""
            if piece:
                parts.append(piece)
                total_len += len(piece)
                last_piece = piece
                if total_len - last_report_len >= 120:
                    last_report_len = total_len
                    try:
                        on_delta(piece, "".join(parts))
                    except Exception:  # noqa: BLE001
                        pass  # 监控回调异常不影响主流程
    if total_len - last_report_len > 0:
        try:
            on_delta(last_piece, "".join(parts))  # 尾部 flush：终态全文
        except Exception:  # noqa: BLE001
            pass  # 监控回调异常不影响主流程
    return "".join(parts), usage
