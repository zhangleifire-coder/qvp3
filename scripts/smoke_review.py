# -*- coding: utf-8 -*-
"""审核闭环冒烟：通过一条（approved）+ 驳回一条并 retry（Agent 带反馈全链重生成）。"""
import sys
import time

import httpx

BASE = "http://127.0.0.1:8003"
c = httpx.Client(base_url=BASE, timeout=30,
                 headers={"Connection": "close"},
                 limits=httpx.Limits(max_keepalive_connections=0))


def login(name):
    r = c.post("/api/auth/login", json={"username": name, "password": "1qaz@WSX"})
    r.raise_for_status()
    assert r.json().get("ok"), "登录失败"
    return {}  # 当前版本 API 不校验 token（与 smoke_agent 行为一致）


def tasks(auth, status):
    r = c.get(f"/api/tasks?status={status}", headers=auth)
    return (r.json().get("items") or r.json().get("tasks") or [])


def main():
    auth = login("张三")
    reviews = tasks(auth, "review")
    assert len(reviews) >= 2, f"需要至少 2 条 review 任务，当前 {len(reviews)}"
    t_approve, t_reject = reviews[0]["id"], reviews[1]["id"]
    print(f"[review] 通过 {t_approve[:8]} / 驳回 {t_reject[:8]}")

    # 1. 通过
    r = c.post("/api/review/action", json={
        "task_id": t_approve, "role": "A", "reviewer_id": "张三",
        "action_type": "approve", "reason": "冒烟通过"}, headers=auth)
    print("[review] approve ->", r.json())
    st = [t for t in tasks(auth, "approved") if t["id"] == t_approve]
    print("[review] approved 列表可见:", bool(st))

    # 2. 驳回（无标记 → 整体重生成路径）
    r = c.post("/api/review/action", json={
        "task_id": t_reject, "role": "A", "reviewer_id": "张三",
        "action_type": "reject", "reason": "正文第二段数据没有来源支撑，请补充证据后重写；结论太含糊。"},
        headers=auth)
    print("[review] reject ->", r.json())
    st = [t for t in tasks(auth, "rejected") if t["id"] == t_reject]
    print("[review] rejected 列表可见:", bool(st))

    # 3. retry → Agent 全链重生成（带驳回反馈）
    r = c.post(f"/api/tasks/{t_reject}/retry", headers=auth)
    print("[review] retry ->", r.json())
    deadline = time.time() + 900
    while time.time() < deadline:
        d = c.get(f"/api/tasks/{t_reject}/detail", headers=auth).json()
        status = (d.get("task") or {}).get("status")
        if status in ("review", "failed"):
            break
        time.sleep(5)
    print(f"[review] 重生成终态: {status}")
    draft = d.get("draft") or {}
    print(f"[review] 新正文 {len(draft.get('body') or '')} 字, "
          f"prompt={draft.get('prompt_version')}（应含 _regen1）")
    acts = c.get("/api/activity?actor=张三", headers=auth).json()
    print("[review] 审计日志条数:", len(acts.get("items") or acts or []))
    sys.exit(0 if status == "review" and "_regen" in (draft.get("prompt_version") or "")
             else 2)


if __name__ == "__main__":
    main()
