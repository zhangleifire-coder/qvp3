import time
import litellm
from src.config import settings
from src.gateway.cost_tracker import estimate_cost


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
                        api_base: str = None, max_tokens: int = 4096,
                        timeout: int = 90, on_delta=None) -> dict:
    litellm.api_key = api_key or settings.deepseek_api_key
    start = time.time()
    kwargs = dict(model=model, messages=[{"role": "user", "content": prompt}],
                  max_tokens=max_tokens, timeout=timeout)
    if api_base:
        kwargs["api_base"] = api_base
    # max_tokens 默认 4096：起草类任务（正文+分页+生图描述）约需 2000-3000 token，
    # 旧默认 1024 会截断 JSON 尾部导致解析失败（2026-08-29 文字核查空内容事故）。
    # timeout 90s：上游偶发挂起（DeepSeek 长生成 120s+ 无响应），及时切断走降级链。
    if on_delta is not None:
        # 流式分支（2026-09-01 用户要求：LLM 思考与生成过程实时上监控）：
        # litellm acompletion(stream=True) 逐块返回，on_delta(piece, total) 实时回调；
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
    cost = estimate_cost(model, pt, ct)
    return {
        "text": text,
        "model_version": model,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "cost_cny": cost,
        "elapsed_seconds": elapsed,
    }


async def _call_streaming(kwargs: dict, on_delta) -> tuple[str, object]:
    """流式调用并逐块回调 on_delta(piece, total)；返回 (text, usage)。"""
    parts: list[str] = []
    usage = None
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
                try:
                    on_delta(piece, "".join(parts))
                except Exception:  # noqa: BLE001
                    pass  # 监控回调异常不影响主流程
    return "".join(parts), usage
