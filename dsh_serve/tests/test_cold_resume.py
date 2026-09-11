"""跨进程会话恢复（id collision → fork + transcript 前言重放）单测。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_serve.failover import run_with_failover  # noqa: E402
from dsh_serve.session_store import SessionStore, build_resume_prompt  # noqa: E402

from .conftest import FakePool, FakeResult, emit_dsh_events, make_settings  # noqa: E402

COLLISION_MSG = ('session "s1" already has a persisted log on disk that does '
                 'not match this live session (id collision)')


def _collision_result():
    return FakeResult("", finish_reason="error", events=[{
        "type": "turn/end",
        "data": {"turn": 1, "reason": {"kind": "error",
                                       "error": {"message": COLLISION_MSG,
                                                 "code": "UNKNOWN"}}}}])


def test_id_collision_forks_alias_and_replays_history(tmp_path):
    settings = make_settings(tmp_path)
    # 模拟"上一进程"留下的 transcript
    store = SessionStore(settings.dsh_home)
    store.append_turn("s1", "请记住这个暗号：甲-66。", "已记住")

    def script(prompt, session_id, on_event, pool):
        if session_id == "s1":
            return _collision_result()  # 原 id 撞盘
        # fork 后的别名：正常应答；校验 prompt 带了历史前言
        assert session_id == "s1--r1"
        assert "甲-66" in prompt and "当前消息" in prompt
        emit_dsh_events(on_event, text="甲-66")
        return FakeResult("甲-66")

    pool = FakePool(settings, {"primary": script})
    chunks, emit = [], lambda c: chunks.append(c)
    out = run_with_failover(pool, "暗号是什么？", "s1", emit)
    assert out.text == "甲-66" and not out.degraded
    assert [c[1] for c in pool.calls] == ["s1", "s1--r1"]
    # 映射已持久化：下一进程重启后直接走别名
    assert SessionStore(settings.dsh_home).resolve_alias("s1") == "s1--r1"


def test_alias_reused_within_process(tmp_path):
    """已 fork 过的 session，本进程后续调用直接用别名，不再撞原 id。"""
    settings = make_settings(tmp_path)
    store = SessionStore(settings.dsh_home)
    store.append_turn("s2", "第一轮", "好")
    store.fork("s2")  # 模拟此前已 fork 到 s2--r1

    def script(prompt, session_id, on_event, pool):
        assert session_id == "s2--r1"
        emit_dsh_events(on_event, text="ok")
        return FakeResult("ok")

    pool = FakePool(settings, {"primary": script})
    out = run_with_failover(pool, "第二轮", "s2", lambda c: None)
    assert out.text == "ok"
    assert [c[1] for c in pool.calls] == ["s2--r1"]


def test_resume_prompt_without_history_passthrough(tmp_path):
    assert build_resume_prompt("", "原始问题") == "原始问题"
    with_history = build_resume_prompt("用户：a\n助手：b", "接着问")
    assert "用户：a" in with_history and with_history.endswith("接着问")
