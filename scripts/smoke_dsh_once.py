"""一次性 dsh 对比冒烟：导入 → 等 awaiting_text → 确认关卡 → 等 review → 产物摘要。

用法：PYTHONUTF8=1 .venv/Scripts/python scripts/smoke_dsh_once.py "query" general
"""
import asyncio
import os
import sys
import time

import httpx

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8003")
QUERY = sys.argv[1] if len(sys.argv) > 1 else "新手第一台咖啡机怎么选"
MODE = sys.argv[2] if len(sys.argv) > 2 else "general"
CONTENT_TYPE = sys.argv[3] if len(sys.argv) > 3 else "generic"


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=httpx.Timeout(60.0, read=120.0)) as c:
        r = await c.post("/api/auth/login", json={"username": "张三", "password": "1qaz@WSX"})
        token = r.json().get("token") or r.json().get("access_token")
        auth = {"Authorization": f"Bearer {token}"}

        r = await c.post("/api/tasks/import_queries", headers=auth,
                         json={"queries": [QUERY], "content_type": CONTENT_TYPE, "mode": MODE})
        print("[import]", r.status_code, r.text[:300], flush=True)
        task_id = r.json()["task_ids"][0]

        deadline = time.time() + 40 * 60
        gate_confirmed = False
        refs_confirmed = False
        last = ""
        final = None
        while time.time() < deadline:
            r = await c.get(f"/api/tasks/{task_id}/detail", headers=auth)
            d = r.json()
            status = (d.get("task") or {}).get("status")
            line = f"status={status} node={d.get('current_node')}"
            if line != last:
                print(f"[poll {time.strftime('%H:%M:%S')}]", line, flush=True)
                last = line
            if status == "awaiting_text" and not gate_confirmed:
                tr = (d.get("task") or {}).get("text_review") or {}
                pol = tr.get("polish") or {}
                print("[gate] 校稿留痕:", pol, flush=True)
                print("[gate] 正文头 120 字:", (tr.get("body_draft") or "")[:120], flush=True)
                rr = await c.post(f"/api/tasks/{task_id}/text/confirm", headers=auth, json={})
                print("[gate] text/confirm:", rr.status_code, rr.text[:150], flush=True)
                gate_confirmed = True
            if status == "awaiting_refs" and not refs_confirmed:
                cands = [a["id"] for a in (d.get("assets") or [])
                         if a.get("selection_status") == "candidate"]
                rr = await c.post(f"/api/tasks/{task_id}/refs/confirm", headers=auth,
                                  json={"keep_ids": cands})
                print(f"[gate] refs/confirm: {rr.status_code} keep={len(cands)}", rr.text[:150], flush=True)
                refs_confirmed = True
            if status in ("review", "failed", "approved", "cancelled"):
                final = d
                break
            await asyncio.sleep(15)

        if final is None:
            r = await c.get(f"/api/tasks/{task_id}/detail", headers=auth)
            final = d
        t = final.get("task") or {}
        draft = final.get("draft") or {}
        pages = final.get("page_copies") or []
        assets = final.get("assets") or []
        risk = final.get("risk") or {}
        tl = final.get("node_timeline") or []
        print("[done] task_id:", task_id)
        print("[done] 终态:", t.get("status"))
        print("[done] 正文:", len(draft.get("body") or ""), "字, model=", draft.get("model_version"))
        print("[done] 分页:", len(pages), "页", [len(p.get("body") or "") for p in pages])
        print("[done] 配图:", sum(1 for a in assets if a.get("source_type") == "ai_generated"),
              "AI /", sum(1 for a in assets if a.get("source_type") == "official"), "官方参考")
        print("[done] 风险:", risk.get("level"), risk.get("reasons"))
        print("[done] 节点耗时:", {n["node"]: n["duration_s"] for n in tl})
        print("[done] 节点成本:", {n["node"]: n["cost_cny"] for n in tl if n["cost_cny"]})


asyncio.run(main())
