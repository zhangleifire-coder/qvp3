"""内部接口：MCP 工具进程的成本回调端点（仅本机/内网调用，token 鉴权）。"""
from fastapi import APIRouter, Header, HTTPException

from src.config import settings
from src.gateway.tool_ledger import tool_ledger

router = APIRouter()


@router.post("/api/internal/tool_usage")
async def tool_usage(body: dict, x_internal_token: str = Header(default="")):
    """qvp_mcp 每次工具调用后回调记账；agent_production 收尾时 drain 合并进 node_events。"""
    if x_internal_token != settings.internal_callback_token:
        raise HTTPException(status_code=403, detail="invalid internal token")
    task_id = body.get("task_id")
    tool = body.get("tool")
    if not task_id or not tool:
        raise HTTPException(status_code=422, detail="task_id and tool are required")
    await tool_ledger.record(task_id, {
        "tool": tool,
        "cost_cny": body.get("cost_cny", 0),
        "detail": body.get("detail", {}),
    })
    return {"ok": True}
