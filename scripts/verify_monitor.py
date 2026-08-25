# -*- coding: utf-8 -*-
"""监控增强端到端验证：导入 → 观察流式/阶段事件 → 中途中断 → 重试续跑完成。"""
import json
import sys
import threading
import time

import httpx

BASE = "http://127.0.0.1:8003"
c = httpx.Client(base_url=BASE, timeout=30, headers={"Connection": "close"},
                 limits=httpx.Limits(max_keepalive_connections=0))

events = []          # (time, type, payload)
stop_flag = threading.Event()


def sse_listener():
    """后台收 SSE 事件流，抓 agent_progress / agent_tool / task_cancelled。"""
    try:
        with httpx.Client(timeout=httpx.Timeout(10, read=900), headers={"Connection": "close"}) as hc:
            with hc.stream("GET", f"{BASE}/api/stream/events") as r:
                for line in r.iter_lines():
                    if stop_flag.is_set():
                        break
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        d = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    t = d.get("type", "")
                    if t in ("agent_progress", "agent_tool", "task_cancelled",
                             "node_started", "task_started", "task_finished", "task_failed"):
                        events.append((time.strftime("%H:%M:%S"), t, d.get("data") or {}))
    except Exception as e:
        print(f"[sse] 结束: {type(e).__name__}: {e}")


def wait_status(task_id, targets, timeout=900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = c.get(f"/api/tasks/{task_id}/detail").json()
        st = (d.get("task") or {}).get("status")
        if st in targets:
            return st, d
        time.sleep(4)
    return "timeout", {}


def main():
    th = threading.Thread(target=sse_listener, daemon=True)
    th.start()

    r = c.post("/api/tasks/import_queries",
               json={"queries": [f"监控链路验证：中断续跑 {int(time.time()) % 100000}"],
                     "content_type": "article", "mode": "general", "actor": "张三"})
    task_id = r.json()["task_ids"][0]
    print(f"[verify] task={task_id[:8]} 已导入")

    # 1) 等 Agent 流式输出出现（chars>0 的 agent_progress）
    deadline = time.time() + 240
    saw_stream = saw_tool = False
    while time.time() < deadline:
        for _, t, d in events:
            if t == "agent_progress" and d.get("chars", 0) > 300:
                saw_stream = True
            if t == "agent_tool":
                saw_tool = True
        if saw_stream:
            break
        time.sleep(2)
    stream_ev = [d for _, t, d in events if t == "agent_progress" and d.get("chars", 0) > 300]
    if stream_ev:
        print(f"[verify] 流式事件: True（tokens_est 字段: {'tokens_est' in stream_ev[-1]}，"
              f"最新 {stream_ev[-1].get('chars')} 字 / 约 {stream_ev[-1].get('tokens_est')} token）")
    else:
        print(f"[verify] 流式事件: {saw_stream}")
    tool_ev = [d for _, t, d in events if t == "agent_tool"]

    def _tool_desc(d):
        name = d.get("tool", "?")
        if name == "image_gen_progress":
            return f"{name} P{d.get('page')}/{d.get('total')}"
        return name

    print(f"[verify] 工具阶段事件: {saw_tool} → {[_tool_desc(d) for d in tool_ev][:8]}")

    # 2) 中途手工中断
    r = c.post(f"/api/tasks/{task_id}/cancel?actor=张三")
    print(f"[verify] cancel → {r.json()}")
    st, _ = wait_status(task_id, ("cancelled", "review"), timeout=60)
    print(f"[verify] 中断后状态: {st}")
    assert st == "cancelled", "中断失败"

    # 3) 重试续跑（已完成节点跳过）→ review
    c.post(f"/api/tasks/{task_id}/retry?actor=张三")
    st, d = wait_status(task_id, ("review", "failed", "cancelled"))
    print(f"[verify] 续跑终态: {st}")
    draft = d.get("draft") or {}
    assets = [a for a in (d.get("assets") or []) if a.get("source_type") == "ai_generated"]
    print(f"[verify] 续跑产物: 正文{len(draft.get('body') or '')}字 / {len(assets)}图 / "
          f"prompt={draft.get('prompt_version')}")

    # 4) 汇总事件统计
    time.sleep(2)
    stop_flag.set()
    from collections import Counter
    cnt = Counter(t for _, t, _ in events)
    print(f"[verify] 事件统计: {dict(cnt)}")
    cancelled_ev = [d for _, t, d in events if t == "task_cancelled"]
    print(f"[verify] task_cancelled 事件: {cancelled_ev[:1]}")
    ok = (st == "review" and saw_stream and saw_tool and cnt.get("task_cancelled", 0) >= 1)
    print(f"[verify] 结论: {'全部通过 ✅' if ok else '存在失败项 ❌'}")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
