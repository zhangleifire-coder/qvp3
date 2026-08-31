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
    # default=str：task_id 等字段可能是 uuid.UUID / datetime——json.dumps 对
    # 其抛 TypeError 会当场杀死整条 SSE generator（连接从此静默无帧，浏览器
    # EventSource 重连后又被下一条事件杀死——2026-09-01 「监控页数据流失效」
    # 的最终根因；实测 TypeError: Object of type UUID is not JSON serializable）
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


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
                # 断开检测靠 yield 写回失败（客户端断开时抛异常退出），
                # 不用 request.is_disconnected() 轮询——该调用在部分
                # uvicorn/平台组合下会误判断连，导致 SSE 收完快照即静默关闭
                #（2026-09-01 排查：监控页「事件流不动」的传输层根因）
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
