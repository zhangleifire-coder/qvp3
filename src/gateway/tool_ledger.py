"""MCP 工具成本台账：接收 qvp_mcp 进程的回调，供 agent_production 收尾合并。

进程内内存台账（成本是估算口径，重启丢失可接受——工具返回值里也带
cost_cny 字段，极端情况下以后端聚合的估算为准）。
"""
import asyncio


class ToolUsageLedger:
    def __init__(self):
        self._items: dict[str, list[dict]] = {}
        self._lock = asyncio.Lock()

    async def record(self, task_id: str, item: dict) -> None:
        async with self._lock:
            self._items.setdefault(str(task_id), []).append(item)

    async def drain(self, task_id: str) -> tuple[float, list[dict]]:
        """取走并清空该任务累计的工具成本：返回 (合计, 明细)。"""
        async with self._lock:
            items = self._items.pop(str(task_id), [])
        return round(sum(i.get("cost_cny", 0) for i in items), 6), items


tool_ledger = ToolUsageLedger()
