import asyncio
from src.config import settings
from src.gateway.litellm_adapter import call_provider

# 文本模型选型（spec §1.1）：DeepSeek 主 + Kimi Code 备
DEEPSEEK_MODEL = "deepseek/deepseek-v4-pro"   # deepseek-chat 已弃用（2026-07-24）
KIMI_MODEL = "anthropic/k3"          # Kimi Code 走 Anthropic 兼容协议
KIMI_API_BASE = "https://api.kimi.com/coding"


def _api_key_for(model: str) -> str:
    if model.startswith("deepseek"):
        return settings.deepseek_api_key
    if "k3" in model or model.startswith("anthropic"):
        return settings.kimi_api_key
    return settings.deepseek_api_key


def _api_base_for(model: str):
    if "k3" in model or model.startswith("anthropic"):
        return KIMI_API_BASE
    return None


async def call_with_failover(prompt: str, primary_model: str = DEEPSEEK_MODEL,
                             fallback_model: str = KIMI_MODEL,
                             max_retries: int = 2) -> dict:
    for attempt in range(max_retries + 1):
        try:
            result = await call_provider(primary_model, prompt,
                                         api_key=_api_key_for(primary_model),
                                         api_base=_api_base_for(primary_model))
            result["degraded"] = False
            return result
        except Exception as e:
            if attempt == max_retries:
                try:
                    result = await call_provider(fallback_model, prompt,
                                                 api_key=_api_key_for(fallback_model),
                                                 api_base=_api_base_for(fallback_model))
                    result["degraded"] = True
                    result["original_error"] = str(e)
                    return result
                except Exception as e2:
                    raise RuntimeError(f"Both providers failed: {e}; fallback: {e2}")
            await asyncio.sleep(2 ** attempt)
