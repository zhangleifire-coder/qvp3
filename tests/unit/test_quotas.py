"""后端权威配额单测：申请、超限、重置（重试续跑重新获得预算）。"""
from src.gateway.tool_ledger import task_quotas


async def test_acquire_within_limit():
    await task_quotas.reset("t1")
    for _ in range(3):
        r = await task_quotas.acquire("t1", "web_search")
        assert r["allowed"] is True
    r = await task_quotas.acquire("t1", "web_search")
    assert r["allowed"] is False
    assert r["used"] == 3 and r["limit"] == 3


async def test_tasks_isolated():
    await task_quotas.reset("t2a"); await task_quotas.reset("t2b")
    for _ in range(3):
        await task_quotas.acquire("t2a", "web_search")
    assert (await task_quotas.acquire("t2b", "web_search"))["allowed"] is True


async def test_reset_restores_budget():
    """中断/失败后续跑：reset 后重新获得全额预算（真实缺陷的回归测试：
    旧实现配额在 MCP 进程内存且不释放，被中断的任务续跑时生图配额被锁死）。"""
    await task_quotas.reset("t3")
    r = await task_quotas.acquire("t3", "image", n=6)
    assert r["allowed"] is True
    assert (await task_quotas.acquire("t3", "image", n=3))["allowed"] is False
    await task_quotas.reset("t3")                       # agent_production 重新开始
    r = await task_quotas.acquire("t3", "image", n=6)
    assert r["allowed"] is True


async def test_batch_acquire_counts_n():
    await task_quotas.reset("t4")
    r = await task_quotas.acquire("t4", "image", n=8)
    assert r["allowed"] is True and r["used"] == 8      # 默认上限 8
    assert (await task_quotas.acquire("t4", "image"))["allowed"] is False
