"""工具成本回调：每次工具调用完成后 POST 给业务后端记账。

后端收到后写入进程内台账（src/gateway/tool_ledger.py），
agent_production 节点收尾时取走合并进 node_events 成本——
成本明细页（/api/admin/costs）口径不变。

回调失败不阻塞工具返回（成本是估算口径，丢一次可接受），只打印留痕。
"""
import httpx

from src.config import settings


async def report_usage(task_id: str, tool: str, cost_cny: float, detail: dict):
    url = f"{settings.mcp_callback_base_url}/api/internal/tool_usage"
    payload = {
        "task_id": str(task_id),
        "tool": tool,
        "cost_cny": round(float(cost_cny), 6),
        "detail": detail,
    }
    headers = {"X-Internal-Token": settings.internal_callback_token}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                print(f"[mcp-cost] 回调被拒({resp.status_code}): {resp.text[:120]}",
                      flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[mcp-cost] 回调失败(不影响工具结果): {type(e).__name__}: {e}",
              flush=True)
