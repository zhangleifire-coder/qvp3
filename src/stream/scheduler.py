"""任务队列调度器：多路排序队列 + 自适应并发 worker 池。

导入的 Query 不再立即扇出并行跑，而是进入优先级队列（urgent > normal > scheduled，
同级 FIFO），由固定数量的 worker 协程多路消费；总并发容量由 AdaptiveLimiter
按上游成败/限流信号统计复用式动态伸缩。
"""
import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from src.config import settings
from src.db.session import SessionLocal
from src.models.tasks import Task
from src.stream.bus import bus
from src.stream.limiter import AdaptiveLimiter

# 优先级 → 队列排序权重（小者先出队），同级按入队序号 FIFO
_PRIORITY = {"urgent": 0, "normal": 1, "scheduled": 2}


def is_throttled(exc: Exception) -> bool:
    """识别限流/并发超限类错误（429、rate limit、too many 等）。"""
    name = type(exc).__name__.lower()
    if "ratelimit" in name or "throttl" in name or "toomany" in name:
        return True
    s = str(exc).lower()
    return any(k in s for k in ("429", "rate limit", "rate_limit", "too many",
                                "限流", "并发", "quota", "exceeded"))


class TaskScheduler:
    def __init__(self):
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.limiter = AdaptiveLimiter(
            min_c=settings.min_concurrency,
            max_c=settings.max_concurrency,
            initial=settings.initial_concurrency,
        )
        self._workers: list[asyncio.Task] = []
        self._started = False
        self._meta: dict[str, dict] = {}   # task_id(str) -> {"query":..., "status":...}
        self._paused = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._seq = 0                      # 入队序号：同优先级内保 FIFO
        self._running: dict[str, asyncio.Task] = {}  # 执行中的 _process 协程（供手工中断）
        self._cancel_pending: set[str] = set()       # 排队中被标记中断的任务

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        await self._recover_pending()
        for _ in range(self.limiter.max_c):
            self._workers.append(asyncio.create_task(self._worker()))

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        self._started = False

    def _put(self, task_id, priority: str) -> None:
        self._seq += 1
        self.queue.put_nowait((_PRIORITY.get(priority, 1), self._seq, task_id))

    async def _recover_pending(self) -> None:
        """重启后恢复未完成任务（draft/processing 重新入队，按创建时间保序）。

        有待处理驳回标记的任务恢复为定点重生成（partial_regen），
        避免崩溃恢复时全链重跑把定点已重生的内容再洗一遍。
        """
        from sqlalchemy import func
        from src.models.review import RejectMark
        async with SessionLocal() as session:
            tasks = list((await session.execute(
                select(Task).where(Task.status.in_(["draft", "processing"]))
                .order_by(Task.created_at))).scalars())
            for task in tasks:
                task.status = "draft"
            await session.commit()
            for task in tasks:
                open_marks = (await session.execute(
                    select(func.count(RejectMark.id)).where(
                        RejectMark.task_id == task.id,
                        RejectMark.status == "open"))).scalar() or 0
                kind = "partial_regen" if open_marks else "pipeline"
                tid = str(task.id)
                self._meta[tid] = {"query": task.query, "status": "queued",
                                   "kind": kind}
                self._put(task.id, task.priority)
                await bus.publish("task_enqueued", {"query": task.query}, task_id=tid)

    async def enqueue(self, task_id, query: str, priority: str = "normal",
                      kind: str = "pipeline") -> None:
        tid = str(task_id)
        self._meta[tid] = {"query": query, "status": "queued", "kind": kind}
        self._put(task_id, priority)
        await bus.publish("task_enqueued", {"query": query}, task_id=tid)

    def pause(self) -> None:
        """暂停取新任务（检修停机），已在跑的任务继续完成。"""
        self._paused = True
        self._pause_event.clear()

    def resume(self) -> None:
        """恢复取新任务。"""
        self._paused = False
        self._pause_event.set()

    def clear(self) -> None:
        """清空内存中的队列与任务元数据（配合数据库清空）。"""
        self._meta.clear()
        self._cancel_pending.clear()
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break

    async def cancel(self, task_id) -> str:
        """手工中断任务：取消执行中的协程 / 出队排队中的任务。

        返回 "running"（已发取消信号）/ "queued"（已出队）/ "not_found"（不在调度器内，
        调用方直接改库即可）。CancelledError 不会经过 execute_node 的 except Exception，
        节点事件自动回滚，中断后可幂等重试（已完成节点跳过）。
        """
        tid = str(task_id)
        proc = self._running.get(tid)
        if proc is not None:
            proc.cancel()
            return "running"
        if tid in self._meta and self._meta[tid].get("status") == "queued":
            self._cancel_pending.add(tid)
            return "queued"
        return "not_found"

    def update_meta(self, task_id, query: str | None = None) -> None:
        """任务条目被编辑后同步内存元数据（监控/事件里显示最新 Query）。"""
        m = self._meta.get(str(task_id))
        if m is not None and query:
            m["query"] = query

    async def _worker(self) -> None:
        while True:
            await self._pause_event.wait()
            _, _, task_id = await self.queue.get()
            await self.limiter.acquire()
            tid = str(task_id)
            if tid in self._cancel_pending:
                # 排队期间被手工中断：直接丢弃，不再执行
                self._cancel_pending.discard(tid)
                self._meta[tid]["status"] = "cancelled"
                await bus.publish("task_cancelled",
                                  {"query": self._meta[tid].get("query", ""),
                                   "phase": "queued"}, task_id=tid)
                await self.limiter.release()
                self.queue.task_done()
                continue
            self._meta[tid]["status"] = "processing"
            await bus.publish("task_started", {"query": self._meta[tid]["query"]}, task_id=tid)
            # _process 独立成协程：手工中断时才能精准 cancel 而不动 worker 本身
            proc = asyncio.create_task(
                self._process(task_id, self._meta[tid].get("kind", "pipeline")))
            self._running[tid] = proc
            try:
                await proc
                # 参考图关卡挂起：管线正常返回但任务停在 awaiting_refs（非完成）；
                # 查询失败（如测试用假 id）按完成处理，不影响主流程
                _st = None
                try:
                    async with SessionLocal() as _s:
                        from sqlalchemy import select as _sel
                        _st = (await _s.execute(
                            _sel(Task.status).where(Task.id == task_id))).scalar()
                except Exception:  # noqa: BLE001
                    _st = None
                if _st in ("awaiting_refs", "awaiting_text"):
                    self._meta[tid]["status"] = "awaiting_refs"
                    await self.limiter.report(success=True, throttled=False)
                    await bus.publish("task_refs_awaiting",
                                      {"query": self._meta[tid].get("query", ""),
                                       "msg": "参考图候选就绪，等待人工确认"},
                                      task_id=tid)
                else:
                    self._meta[tid]["status"] = "done"
                    await self.limiter.report(success=True, throttled=False)
                    await bus.publish("task_finished", {"query": self._meta[tid]["query"]}, task_id=tid)
            except asyncio.CancelledError:
                if not proc.cancelled():
                    raise  # 是 worker 自身被取消（停机），不是手工中断
                # 手工中断：不计入限流器失败统计；CancelledError 不经过
                # execute_node 的 except Exception，节点事件自动回滚，
                # 重试时幂等续跑（已完成节点跳过）
                self._meta[tid]["status"] = "cancelled"
                await bus.publish("task_cancelled",
                                  {"query": self._meta[tid].get("query", ""),
                                   "phase": "running"}, task_id=tid)
                await self._mark_status(task_id, "cancelled")
            except Exception as e:  # noqa: BLE001
                throttled = is_throttled(e)
                self._meta[tid]["status"] = "failed"
                await self.limiter.report(success=False, throttled=throttled)
                await bus.publish("task_failed",
                                  {"query": self._meta[tid]["query"], "error": str(e),
                                   "throttled": throttled}, task_id=tid)
                await self._mark_status(task_id, "failed")
            finally:
                self._running.pop(tid, None)
                await self.limiter.release()
                self.queue.task_done()

    async def _mark_status(self, task_id, status: str) -> None:
        try:
            async with SessionLocal() as session:
                task = (await session.execute(
                    select(Task).where(Task.id == task_id))).scalars().first()
                if task:
                    task.status = status
                    await session.commit()
        except Exception:  # noqa: BLE001
            pass

    async def _process(self, task_id, kind: str = "pipeline") -> None:
        from src.pipeline.orchestrator import run_pipeline
        async with SessionLocal() as session:
            task = (await session.execute(
                select(Task).where(Task.id == task_id))).scalar_one()
            task.status = "processing"
            await session.commit()
        if kind == "partial_regen":
            # 定点重生成：只重做被驳回标记的页文案/配图，其余产物保留
            from src.services.regen import partial_regen
            await partial_regen(task_id)
        else:
            await run_pipeline(task_id)
        async with SessionLocal() as session:
            task = (await session.execute(
                select(Task).where(Task.id == task_id))).scalar_one()
            # 人审关卡挂起（文字核查/参考图确认）保持挂起状态，不覆盖为 review
            if task.status not in ("awaiting_text", "awaiting_refs"):
                task.status = "review"
            await session.commit()

    def snapshot(self) -> dict:
        counts = {"queued": 0, "processing": 0, "done": 0, "failed": 0}
        for m in self._meta.values():
            st = m.get("status")
            if st in counts:
                counts[st] += 1
        return {
            "counts": counts,
            "queue_size": self.queue.qsize(),
            "limiter": self.limiter.snapshot(),
            "tasks": list(self._meta.values()),
        }


scheduler = TaskScheduler()
