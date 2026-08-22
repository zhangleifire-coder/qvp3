"""进程内事件总线：任务/节点进度、限流、并发变化等实时事件的发布订阅。

单进程 uvicorn 部署下足够；多 worker 需替换为 Redis pub/sub。
"""
import asyncio
import time


class EventBus:
    def __init__(self, maxsize: int = 2000):
        self._subscribers: set[asyncio.Queue] = set()
        self._maxsize = maxsize

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def publish(self, event_type: str, data: dict | None = None,
                      task_id: str | None = None) -> None:
        event = {
            "type": event_type,
            "data": data or {},
            "task_id": task_id,
            "ts": time.time(),
        }
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # 丢弃最旧一条，避免慢消费者阻塞生产者
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = EventBus()
