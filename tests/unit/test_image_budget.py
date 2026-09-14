"""任务级出图总预算（image_total，2026-09-14 P1 止血）单测。

守卫语义（WS4 实测单任务 46 张 ≈ ¥9.2 的回归防线）：
- 所有出图路径共用累计计数，硬顶 = settings.image_budget_per_task；
- 超限 deny；节点重跑 reset 不返还累计额度（堵配额重置漏洞）；
- 非累计 kind（如 image）维持「新一轮生产全额返还」语义。
"""
import pytest

from src.config import settings
from src.gateway.tool_ledger import (CUMULATIVE_KINDS, consume_image_budget,
                                     task_quotas)


@pytest.fixture
def fresh_task():
    task_quotas._used.pop("budget-test-task", None)
    yield "budget-test-task"
    task_quotas._used.pop("budget-test-task", None)


class TestImageTotalBudget:
    async def test_cumulative_up_to_hard_cap(self, fresh_task):
        limit = settings.image_budget_per_task
        assert "image_total" in CUMULATIVE_KINDS
        for _ in range(limit):
            assert await consume_image_budget(fresh_task) is True
        assert await consume_image_budget(fresh_task) is False
        r = await task_quotas.acquire(fresh_task, "image_total", 1)
        assert r["allowed"] is False and r["used"] == limit

    async def test_reset_preserves_cumulative_only(self, fresh_task):
        # 非累计 kind：reset 全额返还
        assert (await task_quotas.acquire(fresh_task, "image", 3))["allowed"]
        # 累计 kind：吃到上限
        for _ in range(settings.image_budget_per_task):
            await consume_image_budget(fresh_task)
        await task_quotas.reset(fresh_task)
        used = task_quotas._used[fresh_task]
        assert "image" not in used              # 非累计已清空
        assert used["image_total"] == settings.image_budget_per_task  # 累计保留
        # 重跑后累计额度仍拒绝（这就是要堵的烧钱漏洞）
        assert await consume_image_budget(fresh_task) is False
        # 非累计额度已返还
        assert (await task_quotas.acquire(fresh_task, "image", 1))["allowed"]

    async def test_batch_acquire_denies_beyond_cap(self, fresh_task):
        n = settings.image_budget_per_task
        assert (await task_quotas.acquire(fresh_task, "image_total", n))["allowed"]
        assert not (await task_quotas.acquire(fresh_task, "image_total", 1))["allowed"]

    async def test_default_limit_is_14(self):
        # 与交接文档口径一致：6 张交付 + 8 张重生余量
        assert settings.image_budget_per_task == 14
