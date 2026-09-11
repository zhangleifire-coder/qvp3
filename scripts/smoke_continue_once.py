"""一次性冒烟续跑：确认 text 关卡 → 轮询到终态 → 打印产物摘要。"""
import asyncio
import os
import sys
import time

import httpx

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8003")
TASK_ID = sys.argv[1] if len(sys.argv) > 1 else "3b723f2c-7917-4453-8b01-ef166ba31510"


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=httpx.Timeout(60.0, read=120.0)) as c:
        r = await c.post("/api/auth/login", json={"username": "张三", "password": "1qaz@WSX"})
        token = r.json().get("token") or r.json().get("access_token")
        auth = {"Authorization": f"Bearer {token}"}

        deadline = time.time() + 30 * 60
        gate_confirmed = False
        last = ""
        while time.time() < deadline:
            r = await c.get(f"/api/tasks/{TASK_ID}/detail", headers=auth)
            d = r.json()
            status = (d.get("task") or {}).get("status")
            nodes = d.get("current_node") or ""
            line = f"status={status} node={nodes}"
            if line != last:
                print(f"[poll {time.strftime('%H:%M:%S')}]", line, flush=True)
                last = line
            if status == "awaiting_text" and not gate_confirmed:
                tr = (d.get("task") or {}).get("text_review") or {}
                print("[gate] 校稿留痕:", tr.get("polish"), flush=True)
                print("[gate] 正文头 120 字:", (tr.get("body_draft") or "")[:120], flush=True)
                rr = await c.post(f"/api/tasks/{TASK_ID}/text/confirm", headers=auth, json={})
                print("[gate] text/confirm:", rr.status_code, rr.text[:150], flush=True)
                gate_confirmed = True
            if status in ("review", "failed", "approved", "cancelled"):
                break
            await asyncio.sleep(15)

        r = await c.get(f"/api/tasks/{TASK_ID}/detail", headers=auth)
        d = r.json()
        t = d.get("task") or {}
        draft = (d.get("draft") or {})
        pages = d.get("page_copies") or []
        assets = d.get("assets") or []
        risk = d.get("risk") or {}
        print("[done] 终态:", t.get("status"))
        print("[done] 正文:", len(draft.get("body") or ""), "字, model=", draft.get("model_version"))
        print("[done] 分页:", len(pages), "页", [len(p.get("body") or "") for p in pages])
        print("[done] 配图:", sum(1 for a in assets if a.get("source_type") == "ai_generated"),
              "AI /", sum(1 for a in assets if a.get("source_type") == "official"), "官方参考")
        print("[done] 风险:", risk.get("level"), risk.get("reasons"))


asyncio.run(main())
