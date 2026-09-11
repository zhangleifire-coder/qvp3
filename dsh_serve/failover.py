"""主备 failover：dsh 原生不做跨路由切换（spike 已证），在薄层自做。

语义参照 src/gateway/failover.py + litellm_adapter.py（代码独立，不 import src/）：
- 失败判定：连接/崩溃异常、超时、finish_reason=error、max-tokens 截断、
  空响应、拒答词命中；
- 流式纪律：reasoning-delta 实时透传（监控进度主要来源）；content/usage
  chunk 缓冲到当轮质量检查通过后才 flush——这样拒答/空响应/中途崩溃都能
  干净地整体降级重发，客户端永远只看到一条完整正文（创作任务正文出现在
  末尾，缓冲不影响时效；降级时推理重流与 nanobot 回调语义一致）；
- 降级按路由计划逐级的顺序试：三级全挂 → RuntimeError("All routes failed: ...")。
"""
import asyncio
import logging
import time
from dataclasses import dataclass

from .config import refusal_markers
from .sdk_runner import HarnessPool, HarnessStartError

logger = logging.getLogger("dsh_serve.failover")


class UpstreamTimeoutError(TimeoutError):
    """dsh 单轮请求超时（映射 HTTP 504）。"""


class UpstreamCrashError(RuntimeError):
    """dsh 子进程崩溃/协议错误（映射 HTTP 502）。"""


class ModelRefusalError(RuntimeError):
    """模型拒答（触发降级）。"""


class EmptyResponseError(RuntimeError):
    """空响应（触发降级）。"""


class TruncatedOutputError(RuntimeError):
    """finish=max-tokens：输出被截断（契约 JSON 不完整即废品，触发降级）。"""


@dataclass
class RunOutcome:
    text: str
    route: str
    model: str
    finish_reason: str | None
    cid: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    elapsed_seconds: float = 0.0
    degraded: bool = False
    original_error: str | None = None


def classify_error(e: BaseException) -> BaseException:
    """把 SDK/底层异常归类为薄层错误类型。"""
    if isinstance(e, (UpstreamTimeoutError, UpstreamCrashError,
                      ModelRefusalError, EmptyResponseError,
                      TruncatedOutputError)):
        return e
    name = type(e).__name__
    if isinstance(e, (TimeoutError, asyncio.TimeoutError)) or "Timeout" in name:
        return UpstreamTimeoutError(str(e))
    if isinstance(e, HarnessStartError) or name in {
            "SdkProtocolError", "BrokenPipeError", "ConnectionError"}:
        return UpstreamCrashError(str(e))
    return UpstreamCrashError(f"{name}: {e}")


def check_quality(text: str, route_name: str, finish: str | None = None) -> None:
    """空响应 / 拒答 → 抛错触发降级（移植 litellm_adapter 语义）。

    finish 透传进错误信息：max-tokens 截断导致的空响应在日志里可一眼识别
    （推理吃光输出预算的典型形态）。
    """
    if not text or not text.strip():
        raise EmptyResponseError(
            f"route {route_name} returned empty response (finish={finish})")
    if any(m in text for m in refusal_markers()):
        raise ModelRefusalError(f"route {route_name} refused: {text[:80]}")


def extract_turn_error(result) -> str:
    """从 RunResult.events 里取 turn/end 的错误文本（无则空串）。"""
    for ev in reversed(getattr(result, "events", None) or []):
        if isinstance(ev, dict) and ev.get("type") == "turn/end":
            reason = (ev.get("data") or {}).get("reason") or {}
            err = reason.get("error") or {}
            return str(err.get("message") or "")
    return ""


def run_with_failover(pool: HarnessPool, prompt: str, session_id: str,
                      emit) -> RunOutcome:
    """同步执行一轮（worker 线程内）；emit(openai_chunk_dict) 流式回调。

    emit 收到 reasoning chunk 是实时的；content/usage chunk 在当轮质量
    检查通过后按原序补发。
    """
    from .sse import StreamAggregate, map_dsh_event, new_chunk_id

    cid = new_chunk_id()
    last_error: BaseException | None = None
    attempted = 0
    from .session_store import SessionStore, build_resume_prompt

    store = SessionStore(pool.settings.dsh_home)
    live_sid = store.resolve_alias(session_id)  # 重启后首聊可能已是 fork 别名

    # 三级路由计划：primary → fallback（备1 开放平台）→ fallback2（备2 Kimi
    # Code 会员）；未启用或无 key 的路由不进计划（2026-09-10 三级化）
    s = pool.settings
    route_plan = ["primary"]
    if s.fallback_enabled and s.kimi_api_key:
        route_plan.append("fallback")
    if s.fallback2_enabled and s.kimi_code_api_key:
        route_plan.append("fallback2")

    for route_name in route_plan:
        attempted += 1
        route = pool.routes[route_name]
        wire_model = f"dsh:{route.model}"  # 灰度期按栈筛数据（对比分析差距 7）
        agg = StreamAggregate()
        buffered: list[dict] = []  # content/usage chunk，过检后 flush
        start = time.time()

        def on_event(ev: dict, _agg=agg, _buf=buffered, _m=wire_model) -> None:
            for chunk in map_dsh_event(ev, cid, _m):
                _agg.feed_chunk(chunk)
                if "usage" in chunk:
                    # 发累计值（消费端 nanobot_client 是 last-non-zero-wins）
                    chunk["usage"]["prompt_tokens"] = _agg.prompt_tokens
                    chunk["usage"]["completion_tokens"] = _agg.completion_tokens
                    chunk["usage"]["total_tokens"] = (
                        _agg.prompt_tokens + _agg.completion_tokens)
                    chunk["usage"]["prompt_cache_hit_tokens"] = _agg.cache_hit_tokens
                    chunk["usage"]["prompt_cache_miss_tokens"] = _agg.cache_miss_tokens
                delta = chunk["choices"][0]["delta"]
                if delta.get("content") or "usage" in chunk:
                    _buf.append(chunk)
                else:
                    emit(chunk)

        try:
            result = pool.run(route_name, prompt, live_sid, on_event)
            finish = result.finish_reason
            if finish == "error":
                message = extract_turn_error(result)
                if "id collision" in message:
                    # 进程重启后续聊：dsh sdk 路径不支持冷恢复，fork 新别名
                    # + transcript 历史前言重发（不耗模型 token，碰撞在
                    # turn 开始即失败）
                    live_sid = store.fork(session_id)
                    resume_prompt = build_resume_prompt(
                        store.history_text(session_id), prompt)
                    logger.warning("session id collision, forked session=%s alias=%s",
                                   session_id, live_sid)
                    result = pool.run(route_name, resume_prompt, live_sid, on_event)
                    finish = result.finish_reason
            if finish == "error":
                raise UpstreamCrashError(
                    f"dsh turn/end reason=error, route={route_name}: "
                    f"{extract_turn_error(result)[:200]}")
            if finish == "max-tokens":
                raise TruncatedOutputError(
                    f"route {route_name} hit max-tokens "
                    f"(max_tokens={pool.settings.dsh_max_tokens}，"
                    f"deepseek 推理模型该预算含 reasoning_tokens)")
            text = agg.text or (result.final_response or "")
            check_quality(text, route_name, finish)
            for chunk in buffered:
                emit(chunk)
            return RunOutcome(
                text=text, route=route_name, model=wire_model,
                finish_reason=finish, cid=cid,
                prompt_tokens=agg.prompt_tokens,
                completion_tokens=agg.completion_tokens,
                cache_hit_tokens=agg.cache_hit_tokens,
                cache_miss_tokens=agg.cache_miss_tokens,
                elapsed_seconds=round(time.time() - start, 2),
                degraded=route_name != "primary",
                original_error=str(last_error) if last_error else None,
            )
        except BaseException as e:  # noqa: BLE001 — 统一归类后决定是否降级
            err = classify_error(e)
            last_error = err
            logger.warning("route failed route=%s session=%s error=%.300s",
                           route_name, session_id, str(err))
            continue  # 正文缓冲未 flush，降级重发不污染客户端流

    if attempted <= 1 and last_error is not None:
        raise last_error  # 无备路由可用：保留原始错误类型（502/504 语义）
    raise RuntimeError(f"All routes failed: {last_error}")
