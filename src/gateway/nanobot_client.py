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
    from src.gateway.http_client import get_client
    try:
        # 共享 client 承载连接池；timeout 按请求覆盖，保持签名语义
        resp = await get_client("nanobot", timeout=10.0).get(
            _health_url(), headers=_headers(), timeout=timeout)
        return resp.status_code < 400
    except Exception:  # noqa: BLE001
        return False


async def call_agent(user_message: str, *, session_id: str,
                     on_delta=None) -> dict:
    """发起一次创作 Agent 调用（流式），返回聚合结果。

    - session_id：调用方保证「每节点执行一次」唯一（任务间上下文隔离），
      同一次执行内的纠错追问复用同一 session（Agent 记得自己的输出）。
    - on_delta(chunk_text, total_text)：文本增量回调，按 120 字符节流 +
      流末尾部 flush（监控/调试用；消费方本就 120 字节流）。
    """
    body: dict = {
        "messages": [{"role": "user", "content": user_message}],
        "session_id": session_id,
        "stream": True,
    }
    if settings.nanobot_model:
        body["model"] = settings.nanobot_model

    start = time.time()
    text_parts: list[str] = []      # 最终正文（只收 content，供 JSON 解析）
    thinking_parts: list[str] = []  # 推理过程（reasoning_content，仅供监控展示）
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "cache_hit_tokens": 0}
    model_version = ""
    # 120 字节流前移：消费方（nodes._stream_reporter / agent_stages / agent_production）
    # 本来就按 120 字节流，这里累计长度差 <120 时不 join、不回调（原实现每 chunk
    # 全量 join 一次，15-25 分钟长流式 O(n²)）；流结束补一次尾部回调保证终态完整
    total_len = 0
    last_report_len = 0
    last_piece = ""

    timeout = httpx.Timeout(connect=10.0, read=settings.nanobot_request_timeout_seconds,
                            write=30.0, pool=10.0)
    from src.gateway.http_client import get_client
    # 共享 client 承载连接池（P1-7）；流式超时按请求覆盖，保留动态配置语义
    client = get_client("nanobot", timeout=timeout)
    try:
        async with client.stream("POST",
                                 f"{settings.nanobot_base_url.rstrip('/')}/chat/completions",
                                 json=body, headers=_headers(), timeout=timeout) as resp:
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
                    # 缓存命中拆分（dsh 链路发累计值，last-non-zero-wins 一致）
                    hit = u.get("prompt_cache_hit_tokens")
                    if hit:
                        usage["cache_hit_tokens"] = hit
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    # 正文 content → 最终输出；推理 reasoning_content → 监控展示
                    # （deepseek 等模型工具调用阶段的输出全在推理字段里，漏了它
                    # 监控页就一直是 0 字；推理内容不进正文，防 JSON 解析污染）
                    content_piece = delta.get("content") or ""
                    think_piece = delta.get("reasoning_content") or ""
                    if content_piece:
                        text_parts.append(content_piece)
                    if think_piece:
                        thinking_parts.append(think_piece)
                    if on_delta and (content_piece or think_piece):
                        total_len += len(content_piece) + len(think_piece)
                        last_piece = content_piece or think_piece
                        if total_len - last_report_len >= 120:
                            last_report_len = total_len
                            try:
                                # 监控流式显示 正文+推理 合计进度（用户要看全部工作数据流）
                                total = "".join(text_parts) + "".join(thinking_parts)
                                on_delta(content_piece or think_piece, total)
                            except Exception:  # noqa: BLE001
                                pass  # 监控回调异常不影响主流程
        if on_delta and total_len - last_report_len > 0:
            try:
                # 尾部 flush：不足 120 的尾巴也让监控看到终态全文（piece 取最后一片）
                total = "".join(text_parts) + "".join(thinking_parts)
                on_delta(last_piece, total)
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
    from src.gateway.cost_tracker import refresh_rates
    await refresh_rates()  # 费率 DB 动态化：TTL 60s，失败静默走缓存/兜底
    estimated = False
    if usage["prompt_tokens"] == 0 and usage["completion_tokens"] == 0:
        # 流式响应可能不带 usage：按字符估算（中文约 1.7 字/token），
        # 保持成本口径非零（node_events 的 cost_estimate_cny 本就是估算值）
        usage["prompt_tokens"] = int(len(user_message) / 1.7)
        usage["completion_tokens"] = int(len(text) / 1.7)
        estimated = True
    return {
        "text": text,
        # 网关已带前缀（如 dsh:deepseek-v4-pro）时不再叠加，避免 nanobot:dsh: 双前缀
        "model_version": (model_version if ":" in model_version
                          else f"nanobot:{model_version}" if model_version else "nanobot"),
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "cache_hit_tokens": usage["cache_hit_tokens"],
        "usage_estimated": estimated,
        "elapsed_seconds": round(time.time() - start, 2),
    }
