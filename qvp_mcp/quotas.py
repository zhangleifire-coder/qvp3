"""task_id 级工具配额申请（经后端权威判定，见 src/gateway/tool_ledger.py）。

后端不可达时放行（fail-open）：后端挂了整条流水线本来就停了，
不能让配额检查反过来阻塞工具调用；调用会照常记账（回调失败仅打印）。
"""
import httpx

from src.config import settings


class QuotaExceededError(RuntimeError):
    pass


async def check_and_consume(task_id: str, kind: str, n: int = 1) -> None:
    """向后端申请 n 个配额，超限抛 QuotaExceededError（Agent 会看到并收敛）。"""
    url = f"{settings.mcp_callback_base_url}/api/internal/quota_acquire"
    payload = {"task_id": str(task_id), "kind": kind, "n": n}
    headers = {"X-Internal-Token": settings.internal_callback_token}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                print(f"[mcp-quota] 后端拒绝({resp.status_code}): {resp.text[:120]}", flush=True)
                return  # fail-open：非 200 视为配额服务异常，放行
            r = resp.json()
    except Exception as e:  # noqa: BLE001
        print(f"[mcp-quota] 后端不可达，放行: {type(e).__name__}: {e}", flush=True)
        return
    if not r.get("allowed"):
        raise QuotaExceededError(
            f"任务 {task_id} 的 {kind} 配额已用尽（上限 {r.get('limit')}，"
            f"已用 {r.get('used')}）。请停止继续调用该工具，用现有结果继续完成任务。")


async def fetch_image_gen_plan(task_id: str) -> int:
    """查询该任务每页候选张数（双候选选优门控，2026-09-14）。

    后端按风格首轮 sim 风险返回 1/2；任何失败（后端不可达/异常）回退 1
    ——门控只影响成本与选优，绝不阻断生图。
    """
    url = f"{settings.mcp_callback_base_url}/api/internal/image_gen_plan"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                url, params={"task_id": str(task_id)},
                headers={"X-Internal-Token": settings.internal_callback_token})
            if resp.status_code != 200:
                return 1
            n = int(resp.json().get("candidates") or 1)
            return n if n in (1, 2) else 1
    except Exception as e:  # noqa: BLE001
        print(f"[mcp-quota] 生图方案查询失败，回退单候选: {type(e).__name__}", flush=True)
        return 1
