# -*- coding: utf-8 -*-
"""冒烟脚本：登录 → 导入任务 → 轮询到终态 → 输出节点/产物/成本摘要。"""
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
QUERY = sys.argv[1] if len(sys.argv) > 1 else "手机碎屏险有必要买吗"
MODE = sys.argv[2] if len(sys.argv) > 2 else "general"


def main():
    # Windows 下空闲 keepalive 连接偶发被重置（10053/10054）：关 keepalive + 重试
    c = httpx.Client(base_url=BASE, timeout=30,
                     headers={"Connection": "close"}, limits=httpx.Limits(
                         max_keepalive_connections=0))
    r = c.post("/api/auth/login", json={"username": "张三", "password": "1qaz@WSX"})
    r.raise_for_status()
    token = r.json().get("token") or r.json().get("access_token")
    auth = {"Authorization": f"Bearer {token}"}
    print(f"[smoke] 登录 OK, query={QUERY!r} mode={MODE}")

    r = c.post("/api/tasks/import_queries",
               json={"queries": [QUERY], "content_type": "article", "mode": MODE},
               headers=auth)
    r.raise_for_status()
    tasks = r.json()
    print(f"[smoke] 导入返回: {str(tasks)[:200]}")

    # 等调度入队并拿到 task id
    task_id = None
    for _ in range(20):
        r = c.get("/api/tasks?status=processing", headers=auth)
        rows = r.json().get("items") or r.json().get("tasks") or []
        if rows:
            task_id = rows[0].get("id")
            if task_id:
                break
        r = c.get("/api/tasks?status=review", headers=auth)
        rows = r.json().get("items") or r.json().get("tasks") or []
        if rows:
            task_id = rows[0].get("id")
            if task_id:
                break
        time.sleep(1)
    if not task_id:
        r = c.get("/api/tasks", headers=auth)
        rows = r.json().get("items") or r.json().get("tasks") or []
        task_id = rows[0]["id"] if rows else None
    print(f"[smoke] task_id={task_id}")
    if not task_id:
        sys.exit(1)

    deadline = time.time() + 900
    last_nodes = None
    d = {}
    while time.time() < deadline:
        try:
            r = c.get(f"/api/tasks/{task_id}/detail", headers=auth)
            d = r.json()
        except httpx.TransportError as e:
            print(f"[smoke] 轮询瞬时断连，重试: {type(e).__name__}")
            time.sleep(3)
            continue
        status = (d.get("task") or {}).get("status")
        nodes = d.get("completed_nodes") or []
        if nodes != last_nodes:
            print(f"[smoke {time.strftime('%H:%M:%S')}] status={status} nodes={nodes}")
            last_nodes = nodes
        if status in ("review", "approved", "rejected", "failed"):
            break
        time.sleep(5)

    print(f"[smoke] 终态: {status}")
    draft = d.get("draft") or {}
    body = draft.get("body") or ""
    print(f"[smoke] 正文 {len(body)} 字, model={draft.get('model_version')}, "
          f"prompt={draft.get('prompt_version')}")
    if body:
        print(f"[smoke] 正文开头: {body[:60]}")
    pages = d.get("page_copies") or []
    print(f"[smoke] 分页 {len(pages)} 页")
    assets = d.get("assets") or []
    ai = [a for a in assets if a.get("source_type") == "ai_generated"]
    print(f"[smoke] 配图 {len(ai)} 张 AI / {len(assets) - len(ai)} 官方参考")
    for a in ai[:6]:
        print(f"   p{a.get('page_index')}: {a.get('image_url')}")
    ocr = d.get("ocr_results") or []
    print(f"[smoke] OCR {len(ocr) if ocr else '(detail未含,见库)'}")
    risk = d.get("risk") or {}
    print(f"[smoke] 风险: {risk.get('level') if risk else '?'}")
    sys.exit(0 if status in ("review", "approved") else 2)


if __name__ == "__main__":
    main()
