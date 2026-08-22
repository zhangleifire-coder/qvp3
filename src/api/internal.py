"""内部接口：MCP 工具进程的回调端点（仅本机/内网调用，token 鉴权）。

两类回调：
- 计费回调（web_search/image_search/ocr_image/generate_images 等）：
  记入成本台账 + 发布 agent_tool 阶段事件（监控页展示 Agent 当前工具阶段）
- 进度回调（工具名以 _progress 结尾，如 image_gen_progress）：
  仅发布阶段事件，不进成本台账（cost 恒为 0）
"""
from fastapi import APIRouter, Header, HTTPException

from src.config import settings
from src.gateway.tool_ledger import task_quotas, tool_ledger
from src.stream.bus import bus

router = APIRouter()


@router.post("/api/internal/quota_acquire")
async def quota_acquire(body: dict, x_internal_token: str = Header(default="")):
    """MCP 工具调用前申请配额（后端权威；agent_production 开始时 reset）。"""
    if x_internal_token != settings.internal_callback_token:
        raise HTTPException(status_code=403, detail="invalid internal token")
    task_id, kind = body.get("task_id"), body.get("kind")
    if not task_id or not kind:
        raise HTTPException(status_code=422, detail="task_id and kind are required")
    r = await task_quotas.acquire(task_id, kind, int(body.get("n", 1) or 1))
    return r


@router.post("/api/internal/tool_usage")
async def tool_usage(body: dict, x_internal_token: str = Header(default="")):
    """qvp_mcp 每次工具调用后回调：成本记账 + Agent 阶段事件。"""
    if x_internal_token != settings.internal_callback_token:
        raise HTTPException(status_code=403, detail="invalid internal token")
    task_id = body.get("task_id")
    tool = body.get("tool")
    if not task_id or not tool:
        raise HTTPException(status_code=422, detail="task_id and tool are required")
    detail = body.get("detail", {}) or {}
    cost = float(body.get("cost_cny", 0) or 0)
    # Agent 阶段事件：监控页据此展示「检索证据/生成配图 P3/6/OCR自检」
    try:
        await bus.publish("agent_tool", {"tool": tool, "cost_cny": cost,
                                         **detail}, task_id=str(task_id))
    except Exception:  # noqa: BLE001
        pass  # 事件失败不影响记账
    if not tool.endswith("_progress"):
        await tool_ledger.record(task_id, {"tool": tool, "cost_cny": cost,
                                           "detail": detail})
    return {"ok": True}
