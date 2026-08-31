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
                             max_retries: int = 2, on_delta=None) -> dict:
    # on_delta（可选）：流式回调透传给 call_provider（监控实时显示生成过程）。
    # 重试/降级会重新从头生成——回调侧收到重新增长的 total 属预期。
    for attempt in range(max_retries + 1):
        try:
            result = await call_provider(primary_model, prompt,
                                         api_key=_api_key_for(primary_model),
                                         api_base=_api_base_for(primary_model),
                                         on_delta=on_delta)
            result["degraded"] = False
            return result
        except Exception as e:
            # 超时/挂起类错误不会自愈，重试只会白等（90s×N），直接降级备用模型
            is_timeout = "Timeout" in type(e).__name__ or isinstance(e, asyncio.TimeoutError)
            if is_timeout or attempt == max_retries:
                try:
                    result = await call_provider(fallback_model, prompt,
                                                 api_key=_api_key_for(fallback_model),
                                                 api_base=_api_base_for(fallback_model),
                                                 on_delta=on_delta)
                    result["degraded"] = True
                    result["original_error"] = str(e)
                    return result
                except Exception as e2:
                    raise RuntimeError(f"Both providers failed: {e}; fallback: {e2}")
            await asyncio.sleep(2 ** attempt)
