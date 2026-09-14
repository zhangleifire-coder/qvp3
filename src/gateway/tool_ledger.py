"""任务级工具配额（后端权威）：防 Agent 失控烧钱的硬限制。

配额生命周期与 agent_production 节点执行绑定：节点开始时 reset（重试/
续跑重新获得全额预算），MCP 工具进程每次调用前经 HTTP acquire 申请。
比放在 MCP 进程内存里可靠——进程崩溃/任务中断都不会把配额「锁死」。
"""
import asyncio

from src.config import settings

_LIMITS = {
    "image": settings.mcp_max_images_per_task,
    "web_search": settings.mcp_max_web_searches_per_task,
    "image_search": settings.mcp_max_image_searches_per_task,
    "ocr": settings.mcp_max_ocr_per_task,
}

# qvp_mcp v2 能力工具（2026-09-03 功能项独立化）：LLM 类与校验类分两档限额，
# 每个工具各自计数（kind=工具名）。未登记的 kind 上限为 0（一律拒绝）。
LLM_TOOL_KINDS = ("draft_write", "page_split", "page_regen", "visual_write",
                  "text_draft", "prompt_analyze", "page_subject")
CHECK_TOOL_KINDS = ("rule_check", "cross_check", "risk_classify", "visual_check")
_LIMITS.update({k: settings.mcp_max_llm_tools_per_task for k in LLM_TOOL_KINDS})
_LIMITS.update({k: settings.mcp_max_check_tools_per_task for k in CHECK_TOOL_KINDS})

# 任务级出图总预算（2026-09-14 P1 止血）：所有出图路径（MCP image 首轮 +
# garble 重生 + 主体审核重画 + AI 审核重生）共用的累计硬顶。累计语义——
# 节点重跑/中断续跑不重置，堵「reset 全额返还预算」的烧钱漏洞
# （WS4 实测单任务 46 张 ≈ ¥9.2，预算假设 6-8 张）。
_LIMITS["image_total"] = settings.image_budget_per_task
# 累计类 kind：reset() 时保留计数（其余 kind 维持「新一轮生产全额返还」语义）
CUMULATIVE_KINDS = frozenset({"image_total"})


class TaskQuotas:
    def __init__(self):
        self._used: dict[str, dict[str, int]] = {}
        self._lock = asyncio.Lock()

    async def reset(self, task_id: str) -> None:
        """新一轮生产开始：清空该任务的配额计数（节点入口调用）。

        累计类 kind（image_total）保留——出图预算是任务全生命周期硬顶，
        重跑不返还（2026-09-14 P1）。
        """
        async with self._lock:
            used = self._used.get(str(task_id))
            if used is None:
                return
            keep = {k: v for k, v in used.items() if k in CUMULATIVE_KINDS}
            if keep:
                self._used[str(task_id)] = keep
            else:
                self._used.pop(str(task_id), None)

    async def acquire(self, task_id: str, kind: str, n: int = 1) -> dict:
        """申请 n 个配额。返回 {allowed, used, limit}（不抛错，由调用方决策）。"""
        limit = _LIMITS.get(kind, 0)
        async with self._lock:
            t = self._used.setdefault(str(task_id), {})
            used = t.get(kind, 0)
            if used + n > limit:
                return {"allowed": False, "used": used, "limit": limit}
            t[kind] = used + n
            return {"allowed": True, "used": t[kind], "limit": limit}


task_quotas = TaskQuotas()


async def consume_image_budget(task_id) -> bool:
    """任务级出图总预算扣减（image_total，累计不重置）。

    所有自动出图路径（MCP 首轮/garble 重生/主体重画/AI 审核重生）在每次
    generate_image 前调用；返回 False = 预算到顶，调用方必须停止重生并
    把该页打标记进人工审核（2026-09-14 P1 止血）。
    """
    r = await task_quotas.acquire(str(task_id), "image_total", 1)
    return bool(r.get("allowed"))


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
