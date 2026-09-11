"""progress.py node_streams 聚合：node_progress/agent_progress 事件 → 每节点最新一帧
（2026-09-08 监控页「节点过程」直播窗的数据来源）。不触事件循环，直接驱动 _handle。
"""
from src.stream.progress import ProgressTracker

TID = "11111111-2222-3333-4444-555555555555"


def _tracker() -> ProgressTracker:
    tr = ProgressTracker()
    tr._handle({"type": "task_enqueued", "task_id": TID,
                "data": {"query": "测试 query"}})
    return tr


def test_node_streams_init_empty():
    t = _tracker().tasks[TID]
    assert t["node_streams"] == {}


def test_node_progress_msg_frame():
    tr = _tracker()
    tr._handle({"type": "node_progress", "task_id": TID,
                "data": {"node": "rule_check", "msg": "规则质检 5 项通过 / 1 项不过"}})
    frame = tr.tasks[TID]["node_streams"]["rule_check"]
    assert frame["node"] == "rule_check"
    assert frame["msg"].startswith("规则质检")
    assert frame["chars"] == 0 and frame["preview"] == ""
    assert frame["ts"]


def test_node_progress_stream_frame_preview_truncated_300():
    tr = _tracker()
    long_preview = "字" * 500
    tr._handle({"type": "node_progress", "task_id": TID,
                "data": {"node": "draft_gen", "chars": 500, "preview": long_preview}})
    frame = tr.tasks[TID]["node_streams"]["draft_gen"]
    assert frame["chars"] == 500
    assert len(frame["preview"]) == 300


def test_node_progress_latest_frame_wins():
    tr = _tracker()
    tr._handle({"type": "node_progress", "task_id": TID,
                "data": {"node": "text_check", "chars": 120, "preview": "aaa"}})
    tr._handle({"type": "node_progress", "task_id": TID,
                "data": {"node": "text_check", "chars": 240, "preview": "bbb"}})
    frame = tr.tasks[TID]["node_streams"]["text_check"]
    assert frame["chars"] == 240 and frame["preview"] == "bbb"
    assert len(tr.tasks[TID]["node_streams"]) == 1  # 每节点只留最新一条


def test_msg_frame_does_not_clobber_stream():
    # msg-only 帧（瞬时摘要）不能清掉同节点此前的流式 chars/preview
    tr = _tracker()
    tr._handle({"type": "node_progress", "task_id": TID,
                "data": {"node": "text_check", "chars": 240, "preview": "流式内容"}})
    tr._handle({"type": "node_progress", "task_id": TID,
                "data": {"node": "text_check", "msg": "Kimi 校稿·第2轮"}})
    frame = tr.tasks[TID]["node_streams"]["text_check"]
    assert frame["chars"] == 240 and frame["preview"] == "流式内容"
    assert frame["msg"] == "Kimi 校稿·第2轮"


def test_node_started_resets_frame():
    tr = _tracker()
    tr._handle({"type": "node_progress", "task_id": TID,
                "data": {"node": "asset_gen", "chars": 300, "preview": "旧帧"}})
    tr._handle({"type": "node_started", "task_id": TID,
                "data": {"node": "asset_gen"}})
    frame = tr.tasks[TID]["node_streams"]["asset_gen"]
    assert frame["chars"] == 0 and frame["preview"] == "" and frame["msg"] == ""


def test_agent_progress_maps_to_agent_production():
    tr = _tracker()
    tr._handle({"type": "agent_progress", "task_id": TID,
                "data": {"chars": 800, "tokens_est": 470, "preview": "尾部预览"}})
    t = tr.tasks[TID]
    frame = t["node_streams"]["agent_production"]
    assert frame["chars"] == 800 and frame["preview"] == "尾部预览"
    assert t["stream"]["chars"] == 800  # 原有 stream 行为不受影响


def test_snapshot_carries_node_streams():
    tr = _tracker()
    tr._handle({"type": "node_progress", "task_id": TID,
                "data": {"node": "risk_classify", "msg": "风险分级 yellow：首条原因"}})
    snap = tr.snapshot()
    task = next(t for t in snap["tasks"] if t["id"] == TID)
    assert task["node_streams"]["risk_classify"]["msg"].startswith("风险分级")


def test_unknown_task_events_ignored():
    tr = ProgressTracker()
    tr._handle({"type": "node_progress", "task_id": "ghost",
                "data": {"node": "rule_check", "msg": "x"}})
    assert "ghost" not in tr.tasks
