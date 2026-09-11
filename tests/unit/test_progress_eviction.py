"""progress.py 快照有界化（性能优化 P0-3）：终态任务只留最近 TERMINAL_KEEP 条，
进行中/挂起永不淘汰；每任务 debug 只留最近 DEBUG_KEEP 条；snapshot 结构兼容。
不触事件循环，直接驱动 _handle。
"""
from src.stream.progress import (
    DEBUG_KEEP, TERMINAL_KEEP, ProgressTracker)


def _tid(i: int) -> str:
    return f"task-{i:04d}"


def _enqueue(tr: ProgressTracker, i: int) -> str:
    tid = _tid(i)
    tr._handle({"type": "task_enqueued", "task_id": tid, "data": {"query": f"q{i}"}})
    return tid


def _finish(tr: ProgressTracker, tid: str, etype: str = "task_finished") -> None:
    tr._handle({"type": "task_started", "task_id": tid, "data": {}})
    tr._handle({"type": etype, "task_id": tid, "data": {}})


def test_terminal_tasks_evicted_beyond_keep():
    """终态任务超过 TERMINAL_KEEP 时按入队先后淘汰最旧，只留最近 N 条。"""
    tr = ProgressTracker()
    for i in range(TERMINAL_KEEP + 5):
        _finish(tr, _enqueue(tr, i))
    assert len(tr.tasks) == TERMINAL_KEEP
    assert _tid(0) not in tr.tasks and _tid(4) not in tr.tasks   # 最旧的被淘汰
    assert _tid(5) in tr.tasks                                   # 最近 N 条保留
    assert all(t["status"] == "done" for t in tr.tasks.values())
    assert tr.counts["done"] == TERMINAL_KEEP + 5                # 计数器不受淘汰影响
    snap = tr.snapshot()
    assert len(snap["tasks"]) == TERMINAL_KEEP


def test_mixed_terminal_statuses_count_toward_keep():
    """done/failed/cancelled 都算终态，合计超过 N 即淘汰。"""
    tr = ProgressTracker()
    for i, etype in enumerate(["task_finished", "task_failed", "task_cancelled"] * 12):
        _finish(tr, _enqueue(tr, i), etype)
    assert len(tr.tasks) == TERMINAL_KEEP
    assert set(t["status"] for t in tr.tasks.values()) <= {"done", "failed", "cancelled"}


def test_processing_and_queued_never_evicted():
    """进行中/排队任务无论多旧都不淘汰，且不占终态名额。"""
    tr = ProgressTracker()
    old_processing = _enqueue(tr, 0)
    tr._handle({"type": "task_started", "task_id": old_processing, "data": {}})
    old_queued = _enqueue(tr, 1)
    for i in range(2, TERMINAL_KEEP + 10):
        _finish(tr, _enqueue(tr, i))
    assert old_processing in tr.tasks and old_queued in tr.tasks
    assert len(tr.tasks) == TERMINAL_KEEP + 2   # 终态 N 条 + 2 个活跃任务


def test_suspended_tasks_never_evicted():
    """人审挂起（awaiting_refs/awaiting_text）按活跃处理，不淘汰。"""
    tr = ProgressTracker()
    for i, st in enumerate(("awaiting_refs", "awaiting_text")):
        tid = _enqueue(tr, i)
        tr.tasks[tid]["status"] = st   # 调度器挂起不发 task_finished，状态由 DB 侧维护
    for i in range(2, TERMINAL_KEEP + 10):
        _finish(tr, _enqueue(tr, i))
    assert _tid(0) in tr.tasks and _tid(1) in tr.tasks
    assert len(tr.tasks) == TERMINAL_KEEP + 2


def test_debug_trimmed_to_keep():
    """每任务 debug 只留最近 DEBUG_KEEP 条（traceback 随旧行淘汰）。"""
    tr = ProgressTracker()
    tid = _enqueue(tr, 0)
    for i in range(DEBUG_KEEP + 5):
        tr._handle({"type": "node_finished", "task_id": tid,
                    "data": {"node": f"n{i}", "elapsed": 1}})
    dbg = tr.tasks[tid]["debug"]
    assert len(dbg) == DEBUG_KEEP
    assert dbg[-1]["node"] == f"n{DEBUG_KEEP + 4}"   # 留下的是最新的
    assert dbg[0]["node"] == "n5"
    # node_failed 的 trace 行同样受截断约束
    for i in range(DEBUG_KEEP + 5):
        tr._handle({"type": "node_failed", "task_id": tid,
                    "data": {"node": "x", "error": "e", "traceback": "tb" * 500}})
    dbg = tr.tasks[tid]["debug"]
    assert len(dbg) == DEBUG_KEEP and all(d["phase"] == "error" for d in dbg)


def test_snapshot_structure_compatible():
    """snapshot 输出结构不变：counts/tasks/node_order + 任务字段齐全。"""
    tr = ProgressTracker()
    tid = _enqueue(tr, 0)
    tr._handle({"type": "node_progress", "task_id": tid,
                "data": {"node": "rule_check", "msg": "x"}})
    snap = tr.snapshot()
    assert set(snap) == {"counts", "tasks", "node_order"}
    t = snap["tasks"][0]
    for key in ("id", "query", "status", "nodes", "current_node", "preview",
                "imgs", "model", "error", "debug", "node_streams"):
        assert key in t
