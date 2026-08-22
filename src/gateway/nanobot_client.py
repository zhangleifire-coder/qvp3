"""Nanobot OpenAI 兼容客户端（全链创作 Agent 调用层）。

职责与边界：
- 只负责「一次 Agent 会话调用」的传输层：流式 SSE、超时、鉴权、session 隔离、
  usage 统计；不懂业务，不做 DB。
- 返回结构与 litellm_adapter.call_provider 对齐（text/model_version/
  prompt_tokens/completion_tokens/elapsed_seconds），方便成本口径复用。
- 长任务（15-25 分钟）必须 stream=true：防中间层空闲断连，并把过程文本
  经 on_delta 回调实时抛给业务层（转 SSE/监控页）。
"""
import json
import time

import httpx

from src.config import settings


class NanobotUnavailableError(RuntimeError):
    """Nanobot 进程不可达（未启动/崩溃）——上层据此走告警/回退。"""


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if settings.nanobot_api_key:
        h["Authorization"] = f"Bearer {settings.nanobot_api_key}"
    return h


def _health_url() -> str:
    base = settings.nanobot_base_url.rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    return f"{root}/health"


async def health(timeout: float = 5.0) -> bool:
    """Nanobot 进程存活探测（agent_production 前置检查，失败快速报错）。"""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(_health_url(), headers=_headers())
            return resp.status_code < 400
    except Exception:  # noqa: BLE001
        return False


async def call_agent(user_message: str, *, session_id: str,
                     on_delta=None) -> dict:
    """发起一次创作 Agent 调用（流式），返回聚合结果。

    - session_id：调用方保证「每节点执行一次」唯一（任务间上下文隔离），
      同一次执行内的纠错追问复用同一 session（Agent 记得自己的输出）。
    - on_delta(chunk_text, total_text)：每个文本增量回调（监控/调试用）。
    """
    body: dict = {
        "messages": [{"role": "user", "content": user_message}],
        "session_id": session_id,
        "stream": True,
    }
    if settings.nanobot_model:
        body["model"] = settings.nanobot_model

    start = time.time()
    text_parts: list[str] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    model_version = ""

    timeout = httpx.Timeout(connect=10.0, read=settings.nanobot_request_timeout_seconds,
                            write=30.0, pool=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST",
                                     f"{settings.nanobot_base_url.rstrip('/')}/chat/completions",
                                     json=body, headers=_headers()) as resp:
                if resp.status_code >= 400:
                    detail = (await resp.aread()).decode("utf-8", "replace")[:400]
                    raise RuntimeError(
                        f"nanobot chat failed ({resp.status_code}): {detail}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    model_version = chunk.get("model") or model_version
                    u = chunk.get("usage")
                    if isinstance(u, dict):
                        usage["prompt_tokens"] = u.get("prompt_tokens") or usage["prompt_tokens"]
                        usage["completion_tokens"] = u.get("completion_tokens") or usage["completion_tokens"]
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta") or {}
                        piece = delta.get("content") or ""
                        if piece:
                            text_parts.append(piece)
                            if on_delta:
                                try:
                                    on_delta(piece, "".join(text_parts))
                                except Exception:  # noqa: BLE001
                                    pass  # 监控回调异常不影响主流程
    except httpx.ConnectError as e:
        raise NanobotUnavailableError(
            f"Nanobot 不可达（{settings.nanobot_base_url}）：{e}") from e
    except httpx.ReadTimeout as e:
        raise RuntimeError(
            f"Nanobot 响应超时（>{settings.nanobot_request_timeout_seconds}s），"
            f"session={session_id}") from e

    text = "".join(text_parts)
    if not text.strip():
        raise RuntimeError(f"nanobot 返回空响应，session={session_id}")
    estimated = False
    if usage["prompt_tokens"] == 0 and usage["completion_tokens"] == 0:
        # 流式响应可能不带 usage：按字符估算（中文约 1.7 字/token），
        # 保持成本口径非零（node_events 的 cost_estimate_cny 本就是估算值）
        usage["prompt_tokens"] = int(len(user_message) / 1.7)
        usage["completion_tokens"] = int(len(text) / 1.7)
        estimated = True
    return {
        "text": text,
        "model_version": f"nanobot:{model_version}" if model_version else "nanobot",
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "usage_estimated": estimated,
        "elapsed_seconds": round(time.time() - start, 2),
    }
