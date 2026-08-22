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
