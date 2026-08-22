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
                        api_base: str = None, max_tokens: int = 1024) -> dict:
    litellm.api_key = api_key or settings.deepseek_api_key
    start = time.time()
    kwargs = dict(model=model, messages=[{"role": "user", "content": prompt}],
                  max_tokens=max_tokens)
    if api_base:
        kwargs["api_base"] = api_base
    response = await litellm.acompletion(**kwargs)
    elapsed = time.time() - start
    text = response.choices[0].message.content if response.choices else None
    if text is None or text.strip() == "":
        raise EmptyResponseError(f"model {model} returned empty response")
    refusal_markers = ["我无法", "我不能", "抱歉，我无法", "I cannot", "I'm sorry"]
    if any(m in text for m in refusal_markers):
        raise ModelRefusalError(f"model {model} refused: {text[:80]}")
    usage = response.usage
    cost = estimate_cost(model, usage.prompt_tokens, usage.completion_tokens)
    return {
        "text": text,
        "model_version": model,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "cost_cny": cost,
        "elapsed_seconds": elapsed,
    }
