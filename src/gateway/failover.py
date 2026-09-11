import asyncio
from src.config import settings
from src.gateway.litellm_adapter import call_provider

# 文本模型三级线路（2026-09-10 起）：
#   主   DeepSeek 官方 deepseek-v4-flash（价约 pro 1/3；推理模型 max_tokens
#        含 reasoning_tokens 见 litellm_adapter）
#   备1  Kimi 开放平台 kimi-k3（OpenAI 兼容协议，按量付费）
#   备2  Kimi Code 会员 k3（Anthropic 兼容协议，会员额度兜底；
#        KIMI_CODE_API_KEY 为空则该级自动禁用）
DEEPSEEK_MODEL = f"deepseek/{settings.deepseek_model}"
KIMI_MODEL = "openai/kimi-k3"        # 备1：开放平台走 OpenAI 兼容（litellm openai/ 前缀）
KIMI_API_BASE = "https://api.moonshot.cn/v1"
KIMI_CODE_MODEL = "anthropic/k3"     # 备2：Kimi Code 走 Anthropic 兼容协议
KIMI_CODE_API_BASE = "https://api.kimi.com/coding"


def deepseek_model() -> str:
    """主模型动态取值（2026-09-10 超管控制台在线切换用）。

    DEEPSEEK_MODEL 常量与各调用方的 from-import 拷贝都在 import 时刻冻结，
    进程内改 settings.deepseek_model 不会跟随；需要跟随切换的调用方
    一律用本函数（或省略 call_with_failover 的模型参数走默认解析）。
    """
    return f"deepseek/{settings.deepseek_model}"


def _api_key_for(model: str) -> str:
    if model.startswith("deepseek"):
        return settings.deepseek_api_key
    if model.startswith("anthropic"):
        return settings.kimi_code_api_key       # 备2 Kimi Code 会员
    if "kimi" in model:
        return settings.kimi_api_key            # 备1 开放平台
    return settings.deepseek_api_key


def _api_base_for(model: str):
    if model.startswith("anthropic"):
        return KIMI_CODE_API_BASE
    if "kimi" in model:
        return KIMI_API_BASE
    return None


async def call_with_failover(prompt: str, primary_model: str = "",
                             fallback_model: str = "",
                             fallback2_model: str = "",
                             max_retries: int = 2, on_delta=None) -> dict:
    # on_delta（可选）：流式回调透传给 call_provider（监控实时显示生成过程）。
    # 重试/降级会重新从头生成——回调侧收到重新增长的 total 属预期。
    # 默认链每次调用解析（2026-09-10 超管控制台在线切换主模型/备用链：
    # 原默认值绑模块常量，import 时刻冻结，进程内改 settings 不生效）。
    primary_model = primary_model or deepseek_model()
    fallback_model = fallback_model or KIMI_MODEL
    fallback2_model = fallback2_model or KIMI_CODE_MODEL
    errors: list[str] = []
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
                errors.append(f"primary({primary_model}): {e}")
                break
            await asyncio.sleep(2 ** attempt)
    for level, model in (("fallback", fallback_model),
                         ("fallback2", fallback2_model)):
        if level == "fallback" and not settings.text_fallback1_enabled:
            continue   # 超管控制台停用备1
        if level == "fallback2" and not settings.text_fallback2_enabled:
            continue   # 超管控制台停用备2
        key = _api_key_for(model)
        if not key:
            continue  # 该级未配 key（备2 默认空）→ 跳过
        try:
            result = await call_provider(model, prompt,
                                         api_key=key,
                                         api_base=_api_base_for(model),
                                         on_delta=on_delta)
            result["degraded"] = True
            result["original_error"] = "; ".join(errors)
            return result
        except Exception as e:
            errors.append(f"{level}({model}): {e}")
    raise RuntimeError("All providers failed: " + "; ".join(errors))
