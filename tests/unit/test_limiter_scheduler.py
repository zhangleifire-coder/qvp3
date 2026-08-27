"""统计复用并发（AdaptiveLimiter 冷却期 / 节点级上报）+ 多路排序队列测试。"""
import asyncio

import pytest

from src.stream.limiter import AdaptiveLimiter
from src.stream.scheduler import TaskScheduler


@pytest.mark.asyncio
async def test_throttle_halves_capacity_and_starts_cooldown():
    lim = AdaptiveLimiter(min_c=1, max_c=4, initial=4, cooldown_seconds=120)
    await lim.report_throttle()
    assert lim.capacity == 2
    assert lim.snapshot()["cooldown"] is True
    # 冷却期内成功再多也不升容
    for _ in range(20):
        await lim.report(success=True, throttled=False)
    assert lim.capacity == 2
    assert lim._consecutive_success > 0  # 成功数在积累，只是没升容


@pytest.mark.asyncio
async def test_ramp_up_after_cooldown():
    lim = AdaptiveLimiter(min_c=1, max_c=4, initial=1, cooldown_seconds=0.05)
    await lim.report_throttle()   # capacity 仍是 1（下限），但进入冷却
    assert lim.snapshot()["cooldown"] is True
    await asyncio.sleep(0.06)     # 过冷却期
    # 连续成功 ≥ max(2, capacity*2) → +1
    await lim.report(success=True, throttled=False)
    assert lim.capacity == 1
    await lim.report(success=True, throttled=False)
    assert lim.capacity == 2


@pytest.mark.asyncio
async def test_consecutive_failures_step_down():
    lim = AdaptiveLimiter(min_c=1, max_c=4, initial=3)
    for _ in range(3):
        await lim.report(success=False, throttled=False)
    assert lim.capacity == 2
    for _ in range(3):
        await lim.report(success=False, throttled=False)
    assert lim.capacity == 1      # 到下限不再降


@pytest.mark.asyncio
async def test_priority_queue_orders_urgent_first_fifo_within_level():
    sch = TaskScheduler()
    await sch.enqueue("t-normal-1", "普通1")
    await sch.enqueue("t-sched", "定时", priority="scheduled")
    await sch.enqueue("t-urgent", "加急", priority="urgent")
    await sch.enqueue("t-normal-2", "普通2")
    order = []
    while not sch.queue.empty():
        _, _, tid = sch.queue.get_nowait()
        order.append(tid)
    # urgent 最先；scheduled 最后；两个 normal 按入队顺序
    assert order == ["t-urgent", "t-normal-1", "t-normal-2", "t-sched"]


@pytest.mark.asyncio
async def test_cancel_running_task_marks_cancelled(monkeypatch):
    """手工中断执行中的任务：协程被取消，任务标记 cancelled（不算 failed）。"""
    from src.stream.limiter import AdaptiveLimiter
    sch = TaskScheduler()
    sch.limiter = AdaptiveLimiter(min_c=1, max_c=1, initial=1)  # 单 worker，确定性
    started = asyncio.Event()
    cancel_seen = asyncio.Event()

    async def fake_process(task_id, kind="pipeline"):
        started.set()
        try:
            await asyncio.sleep(30)   # 模拟长跑的 agent_production
        except asyncio.CancelledError:
            cancel_seen.set()
            raise

    async def noop_mark(task_id, status):
        sch._marked = (task_id, status)

    async def noop_recover():
        return None

    monkeypatch.setattr(sch, "_process", fake_process)
    monkeypatch.setattr(sch, "_mark_status", noop_mark)
    monkeypatch.setattr(sch, "_recover_pending", noop_recover)
    await sch.start()

    from src.stream.bus import bus
    events = []
    q = bus.subscribe()
    try:
        await sch.enqueue("t-run", "跑着")
        await asyncio.wait_for(started.wait(), timeout=2)
        assert await sch.cancel("t-run") == "running"
        await asyncio.wait_for(cancel_seen.wait(), timeout=2)
        await asyncio.sleep(0.05)
        assert sch._meta["t-run"]["status"] == "cancelled"
        assert sch._marked == ("t-run", "cancelled")
        while not q.empty():
            events.append(q.get_nowait()["type"])
        assert "task_cancelled" in events
        assert "task_failed" not in events
    finally:
        bus.unsubscribe(q)
        await sch.stop()


@pytest.mark.asyncio
async def test_cancel_queued_task_skips_execution(monkeypatch):
    """排队中被中断：出队即丢弃，不执行 _process。"""
    from src.stream.limiter import AdaptiveLimiter
    sch = TaskScheduler()
    # 强制单 worker 单并发：不受 .env 并发配置影响，保证第二个任务停在排队态
    sch.limiter = AdaptiveLimiter(min_c=1, max_c=1, initial=1)
    executed = []

    async def fake_process(task_id, kind="pipeline"):
        executed.append(task_id)

    async def noop_mark(task_id, status):
        pass

    async def noop_recover():
        return None

    monkeypatch.setattr(sch, "_mark_status", noop_mark)
    monkeypatch.setattr(sch, "_recover_pending", noop_recover)
    # 占住唯一并发位，让后续任务停在排队态
    gate = asyncio.Event()

    async def gated(task_id, kind="pipeline"):
        await gate.wait()
        executed.append(task_id)

    monkeypatch.setattr(sch, "_process", gated)
    await sch.start()
    try:
        await sch.enqueue("t-block", "占位")
        await sch.enqueue("t-queued", "排队")
        await asyncio.sleep(0.05)
        assert sch._meta["t-queued"]["status"] == "queued"
        assert await sch.cancel("t-queued") == "queued"
        gate.set()                            # 放行占位任务
        for _ in range(60):                   # 轮询最多 3s（worker 收尾含 DB 状态查询）
            if (sch._meta["t-queued"]["status"] == "cancelled"
                    and sch._meta["t-block"]["status"] == "done"):
                break
            await asyncio.sleep(0.05)
        assert "t-queued" not in executed     # 从未执行
        assert sch._meta["t-queued"]["status"] == "cancelled"
        assert sch._meta["t-block"]["status"] == "done"
    finally:
        await sch.stop()
