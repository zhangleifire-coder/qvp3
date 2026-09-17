"""FastAPI 应用：OpenAI 兼容 /v1/chat/completions + /health。

线程模型：dsh SDK 是同步阻塞 API，每请求一个 worker 线程跑
run_with_failover，dsh 事件经 queue 桥回 async SSE 生成器；全局
asyncio.Semaphore 限并发（默认 4），同 session_id 加 per-session 锁串行。
错误映射：首字节前失败 → HTTP 502/504；流式中途失败 → SSE error chunk。
"""
import asyncio
import logging
import queue
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from .config import Settings, get_settings
from .failover import (RunOutcome, UpstreamCrashError, UpstreamTimeoutError,
                       run_with_failover)
from .sdk_runner import HarnessPool
from .sse import final_chunk, sse_line

logger = logging.getLogger("dsh_serve.app")

_DONE = "[DONE]"


class _State:
    pool: HarnessPool | None = None
    semaphore: asyncio.Semaphore | None = None
    session_locks: dict[str, asyncio.Lock] = {}
    started_at: float = time.time()
    active_requests: int = 0
    store = None  # SessionStore，lifespan 里初始化


_STATE = _State()


def create_app(settings: Settings | None = None,
               pool: HarnessPool | None = None) -> FastAPI:
    cfg = settings or get_settings()

    WATCHDOG_INTERVAL_SECONDS: float = 60.0

    async def _watchdog() -> None:
        """后台看门狗：定期清理超请求数/长时间无成功的 harness 路由。"""
        while True:
            try:
                await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
                p = _STATE.pool
                if p is not None:
                    p.maybe_restart_stale_routes()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("watchdog error: %s", e)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from .session_store import SessionStore

        _STATE.pool = pool or HarnessPool(cfg)
        _STATE.store = SessionStore(cfg.dsh_home)
        _STATE.semaphore = asyncio.Semaphore(cfg.dsh_max_concurrent)
        _STATE.started_at = time.time()
        watchdog_task = asyncio.create_task(_watchdog())
        logger.info("dsh_serve up port=%d dsh_home=%s max_concurrent=%d",
                    cfg.dsh_serve_port, cfg.dsh_home, cfg.dsh_max_concurrent)
        try:
            yield
        finally:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
            _STATE.pool.close()
            logger.info("dsh_serve down: dsh 子进程已关闭")

    app = FastAPI(title="dsh-serve", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        p = _STATE.pool
        body = p.health() if p else {"primary": {}, "fallback": {}, "mcp": {}}
        return {
            "status": "ok",
            "uptime_seconds": round(time.time() - _STATE.started_at, 1),
            "active_requests": _STATE.active_requests,
            "max_concurrent": cfg.dsh_max_concurrent,
            **body,
        }

    @app.post("/v1/chat/completions")
    @app.post("/chat/completions")
    async def chat_completions(req: Request):
        body = await req.json()
        messages = body.get("messages") or []
        prompt = "\n".join(m.get("content", "") for m in messages
                           if m.get("role") == "user")
        if not prompt.strip():
            raise HTTPException(400, "messages 中缺少 user 内容")
        session_id = body.get("session_id") or f"session-{uuid.uuid4().hex}"
        stream = bool(body.get("stream"))
        model_req = body.get("model")
        if model_req and model_req not in {cfg.primary_model, cfg.fallback_model}:
            logger.warning("unknown model ignored model=%s session=%s",
                           model_req, session_id)

        sem = _STATE.semaphore
        if sem is None:
            raise HTTPException(503, "service not ready")
        await sem.acquire()
        _STATE.active_requests += 1
        lock = _STATE.session_locks.setdefault(session_id, asyncio.Lock())
        try:
            async with lock:  # 同 session 串行（dsh 会话追加写）
                if not stream:
                    return await _run_unary(prompt, session_id)
                return await _run_stream(prompt, session_id)
        finally:
            _STATE.active_requests -= 1
            sem.release()

    async def _run_unary(prompt: str, session_id: str) -> dict:
        q: queue.Queue = queue.Queue()
        _start_worker(prompt, session_id, q)
        while True:
            kind, payload = await asyncio.to_thread(q.get)
            if kind == "chunk":
                continue  # 非流式模式丢弃增量（reasoning 直播不需要）
            if kind == "error":
                _raise_http(payload)
            outcome: RunOutcome = payload
            break
        _record_turn(session_id, prompt, outcome)
        _log_done(outcome, session_id)
        return {
            "id": outcome.cid, "object": "chat.completion",
            "created": int(time.time()), "model": outcome.model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": outcome.text}}],
            "usage": {"prompt_tokens": outcome.prompt_tokens,
                      "completion_tokens": outcome.completion_tokens,
                      "prompt_cache_hit_tokens": outcome.cache_hit_tokens,
                      "prompt_cache_miss_tokens": outcome.cache_miss_tokens},
        }

    async def _run_stream(prompt: str, session_id: str) -> StreamingResponse:
        q: queue.Queue = queue.Queue()
        _start_worker(prompt, session_id, q)
        # 等首条消息再定状态码：首条即 error = 首字节前失败 → 4xx/5xx
        first_kind, first = await asyncio.to_thread(q.get)
        if first_kind == "error":
            _raise_http(first)

        async def gen():
            try:
                kind, payload = first_kind, first
                while True:
                    if kind == "chunk":
                        yield sse_line(payload)
                    elif kind == "done":
                        outcome: RunOutcome = payload
                        _record_turn(session_id, prompt, outcome)
                        _log_done(outcome, session_id)
                        yield sse_line(final_chunk(outcome.cid, outcome.model))
                        yield sse_line(_DONE)
                        return
                    elif kind == "error":
                        yield sse_line({"error": str(payload)})
                        yield sse_line(_DONE)
                        return
                    kind, payload = await asyncio.to_thread(q.get)
            except asyncio.CancelledError:
                raise

        return StreamingResponse(gen(), media_type="text/event-stream")

    def _start_worker(prompt: str, session_id: str, q: queue.Queue) -> None:
        pool = _STATE.pool

        def emit(chunk: dict) -> None:
            q.put(("chunk", chunk))

        def work() -> None:
            try:
                outcome = run_with_failover(pool, prompt, session_id, emit)
                q.put(("done", outcome))
            except BaseException as e:  # noqa: BLE001
                q.put(("error", e))

        threading.Thread(target=work, daemon=True,
                         name=f"dsh-serve-{session_id[:16]}").start()

    def _raise_http(e: BaseException):
        if isinstance(e, UpstreamTimeoutError):
            raise HTTPException(504, f"dsh 上游超时：{e}")
        if isinstance(e, UpstreamCrashError):
            raise HTTPException(502, f"dsh 子进程错误：{e}")
        raise HTTPException(502, f"主备模型均失败：{e}")

    def _record_turn(session_id: str, prompt: str, o: RunOutcome) -> None:
        try:
            if _STATE.store is not None and o.text.strip():
                _STATE.store.append_turn(session_id, prompt, o.text)
        except Exception:  # noqa: BLE001
            logger.warning("transcript append failed session=%s", session_id)

    def _log_done(o: RunOutcome, session_id: str) -> None:
        logger.info(
            "chat done session=%s route=%s model=%s degraded=%s elapsed=%.2fs "
            "tokens=%d/%d finish=%s%s",
            session_id, o.route, o.model, o.degraded, o.elapsed_seconds,
            o.prompt_tokens, o.completion_tokens, o.finish_reason,
            f" original_error={o.original_error}" if o.original_error else "")

    return app
