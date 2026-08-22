"""流式进度接口：SSE 推送队列/节点/内容/并发/限流事件。"""
import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.stream.bus import bus
from src.stream.progress import progress
from src.stream.scheduler import scheduler

router = APIRouter()


def _sse(event_type: str, data: dict | None = None) -> str:
    payload = {"type": event_type, "data": data or {}}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _full_snapshot() -> dict:
    return {
        **progress.snapshot(),
        "limiter": scheduler.limiter.snapshot(),
    }


@router.get("/api/stream/state")
async def stream_state():
    return _full_snapshot()


@router.get("/api/stream/events")
async def stream_events(request: Request):
    async def gen():
        q = bus.subscribe()
        try:
            # 先推一帧快照，让前端立即渲染当前状态（含每任务节点进度）
            yield _sse("snapshot", _full_snapshot())
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield _sse("ping", {})  # 保活
                    continue
                yield _sse(event["type"], {
                    "task_id": event.get("task_id"),
                    "ts": event.get("ts"),
                    **event.get("data", {}),
                })
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
