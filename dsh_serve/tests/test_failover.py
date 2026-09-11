"""failover 触发逻辑单测（mock SDK 层）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_serve.failover import (ModelRefusalError, run_with_failover)  # noqa: E402
from dsh_serve.sdk_runner import HarnessStartError  # noqa: E402

from .conftest import FakePool, FakeResult, emit_dsh_events, make_settings  # noqa: E402


def _pool(tmp_path, script):
    return FakePool(make_settings(tmp_path), script)


def _collect():
    chunks = []
    return chunks, chunks.append


def test_primary_ok_no_fallback(tmp_path):
    pool = _pool(tmp_path, {})
    chunks, emit = _collect()
    out = run_with_failover(pool, "hi", "s1", emit)
    assert out.route == "primary" and not out.degraded
    assert out.text == "fake answer"
    assert out.prompt_tokens == 150 and out.completion_tokens == 20
    assert [c[0] for c in pool.calls] == ["primary"]
    # 流式 chunk 已按序 emit：reasoning → content → content → usage
    deltas = [c["choices"][0]["delta"] for c in chunks if "usage" not in c]
    assert deltas[0] == {"reasoning_content": "thinking..."}
    assert "usage" in chunks[-1]


def test_model_prefix_dsh(tmp_path):
    """model 字段带 dsh: 前缀（灰度按栈筛数据），降级时为 dsh:k3。"""
    pool = _pool(tmp_path, {})
    chunks, emit = _collect()
    out = run_with_failover(pool, "hi", "s1", emit)
    assert out.model == "dsh:deepseek-v4-flash"
    assert all(c.get("model") == "dsh:deepseek-v4-flash" for c in chunks)

    def bad(_p, _s, _e, _pool):
        raise TimeoutError("t")

    pool2 = _pool(tmp_path / "b", {"primary": bad})
    chunks2, emit2 = _collect()
    out2 = run_with_failover(pool2, "hi", "s1", emit2)
    assert out2.model == "dsh:kimi-k3"   # 备1：开放平台 kimi-k3（2026-09-10 起）
    assert all(c.get("model") == "dsh:kimi-k3" for c in chunks2)


def test_usage_chunks_emit_cumulative(tmp_path):
    """多 step usage：发给客户端的每条 usage chunk 是累计值（last-wins 消费端兼容）。"""
    def two_steps(prompt, session_id, on_event, pool):
        for inp, cache, out in ((100, 50, 20), (300, 200, 30)):
            on_event({"type": "assistant/chunk", "data": {"chunk": {
                "type": "usage", "usage": {"inputTokens": inp,
                                           "cacheReadTokens": cache,
                                           "outputTokens": out}}}})
        on_event({"type": "assistant/chunk", "data": {"chunk": {
            "type": "text-delta", "text": "done"}}})
        return FakeResult("done")

    pool = _pool(tmp_path, {"primary": two_steps})
    chunks, emit = _collect()
    out = run_with_failover(pool, "hi", "s1", emit)
    assert out.prompt_tokens == 650 and out.completion_tokens == 50
    usages = [c["usage"] for c in chunks if "usage" in c]
    assert usages[0]["prompt_tokens"] == 150      # 第一条累计
    assert usages[-1]["prompt_tokens"] == 650     # 末条 = 全 session 总量
    assert usages[-1]["completion_tokens"] == 50


def test_cost_tracker_parses_dsh_prefix():
    """后端 cost_tracker.estimate_cost 对 dsh: 前缀的解析兼容（不改 src 的实证）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "cost_tracker",
        Path(__file__).resolve().parents[2] / "src" / "gateway" / "cost_tracker.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    deepseek = mod.estimate_cost("dsh:deepseek-v4-pro", 1_000_000, 0)
    kimi = mod.estimate_cost("dsh:k3", 1_000_000, 0)
    assert deepseek == mod.estimate_cost("deepseek/deepseek-v4-pro", 1_000_000, 0)
    assert kimi == mod.estimate_cost("anthropic/k3", 1_000_000, 0)


def test_primary_crash_before_content_falls_back(tmp_path):
    def bad_primary(prompt, session_id, on_event, pool):
        raise HarnessStartError("dsh 子进程启动失败（route=primary）：boom")

    pool = _pool(tmp_path, {"primary": bad_primary})
    _, emit = _collect()
    out = run_with_failover(pool, "hi", "s1", emit)
    assert out.degraded and out.route == "fallback"
    assert out.original_error
    assert [c[0] for c in pool.calls] == ["primary", "fallback"]


def test_timeout_classified_and_falls_back(tmp_path):
    def slow_primary(prompt, session_id, on_event, pool):
        raise TimeoutError("read timeout after 3000s")

    pool = _pool(tmp_path, {"primary": slow_primary})
    _, emit = _collect()
    out = run_with_failover(pool, "hi", "s1", emit)
    assert out.degraded


def test_refusal_triggers_fallback(tmp_path):
    def refusing_primary(prompt, session_id, on_event, pool):
        emit_dsh_events(on_event, text="抱歉，我无法完成这个请求")
        return FakeResult("抱歉，我无法完成这个请求")

    pool = _pool(tmp_path, {"primary": refusing_primary})
    _, emit = _collect()
    # 拒答词在正文里 → 正文已流出但内容即拒答，仍应降级：
    # check_quality 在 run 返回后对全文判定，拒答 → 降级重试
    out = run_with_failover(pool, "hi", "s1", emit)
    assert out.degraded


def test_failure_with_partial_content_falls_back_cleanly(tmp_path):
    """中途崩溃：已产出的正文 chunk 被缓冲丢弃，整体降级重发，客户端流无污染。"""
    def crash_midway(prompt, session_id, on_event, pool):
        on_event({"type": "assistant/chunk",
                  "data": {"chunk": {"type": "text-delta", "text": "半截正文"}}})
        raise RuntimeError("stream broken")

    pool = _pool(tmp_path, {"primary": crash_midway})
    chunks, emit = _collect()
    out = run_with_failover(pool, "hi", "s1", emit)
    assert out.degraded and out.route == "fallback"
    assert [c[0] for c in pool.calls] == ["primary", "fallback"]
    contents = [c["choices"][0]["delta"].get("content") for c in chunks
                if "usage" not in c and c["choices"][0]["delta"].get("content")]
    assert "半截正文" not in contents  # 主路由半截正文未泄漏到客户端


def test_both_fail_raises(tmp_path):
    def bad(_p, _s, _e, _pool):
        raise TimeoutError("t")

    pool = _pool(tmp_path, {"primary": bad, "fallback": bad})
    _, emit = _collect()
    with pytest.raises(RuntimeError, match="All routes failed"):
        run_with_failover(pool, "hi", "s1", emit)


def test_fallback_disabled_when_no_kimi_key(tmp_path):
    def bad(_p, _s, _e, _pool):
        raise TimeoutError("t")

    pool = FakePool(make_settings(tmp_path, kimi_api_key=""),
                    {"primary": bad, "fallback": bad})
    _, emit = _collect()
    # 无备路由：保留原始错误类型（超时→504 语义），不包装成双失败
    with pytest.raises(TimeoutError):
        run_with_failover(pool, "hi", "s1", emit)
    assert [c[0] for c in pool.calls] == ["primary"]  # 未尝试备路由


# ── 三级路由（2026-09-10：flash 主 → kimi-k3 备1 → Kimi Code k3 备2）──

def test_fallback2_used_when_two_routes_fail(tmp_path):
    """主 + 备1 都挂 → 备2（kimi-code/k3）成功，degraded=True。"""
    def bad(_p, _s, _e, _pool):
        raise TimeoutError("t")

    pool = FakePool(make_settings(tmp_path, kimi_code_api_key="k2"),
                    {"primary": bad, "fallback": bad})
    chunks, emit = _collect()
    out = run_with_failover(pool, "hi", "s1", emit)
    assert out.degraded and out.route == "fallback2"
    assert out.model == "dsh:k3"
    assert [c[0] for c in pool.calls] == ["primary", "fallback", "fallback2"]
    assert out.original_error  # 带前级错误留痕


def test_fallback2_skipped_without_key(tmp_path):
    """kimi_code_api_key 空 → 备2 不进路由计划。"""
    def bad(_p, _s, _e, _pool):
        raise TimeoutError("t")

    pool = FakePool(make_settings(tmp_path),  # 默认无 kimi_code_api_key
                    {"primary": bad, "fallback": bad})
    _, emit = _collect()
    with pytest.raises(RuntimeError, match="All routes failed"):
        run_with_failover(pool, "hi", "s1", emit)
    assert [c[0] for c in pool.calls] == ["primary", "fallback"]


def test_all_three_routes_fail(tmp_path):
    def bad(_p, _s, _e, _pool):
        raise TimeoutError("t")

    pool = FakePool(make_settings(tmp_path, kimi_code_api_key="k2"),
                    {"primary": bad, "fallback": bad, "fallback2": bad})
    _, emit = _collect()
    with pytest.raises(RuntimeError, match="All routes failed"):
        run_with_failover(pool, "hi", "s1", emit)
    assert [c[0] for c in pool.calls] == ["primary", "fallback", "fallback2"]


def test_fallback2_skipped_when_disabled(tmp_path):
    def bad(_p, _s, _e, _pool):
        raise TimeoutError("t")

    pool = FakePool(make_settings(tmp_path, kimi_code_api_key="k2",
                                  fallback2_enabled=False),
                    {"primary": bad, "fallback": bad})
    _, emit = _collect()
    with pytest.raises(RuntimeError, match="All routes failed"):
        run_with_failover(pool, "hi", "s1", emit)
    assert [c[0] for c in pool.calls] == ["primary", "fallback"]


def test_empty_response_triggers_fallback(tmp_path):
    def empty_primary(prompt, session_id, on_event, pool):
        return FakeResult("", finish_reason="completed")

    pool = _pool(tmp_path, {"primary": empty_primary})
    _, emit = _collect()
    out = run_with_failover(pool, "hi", "s1", emit)
    assert out.degraded


def test_max_tokens_truncation_triggers_fallback(tmp_path):
    """finish=max-tokens（含部分正文）视为截断废品 → 降级，半截 JSON 不出流。"""
    def truncated_primary(prompt, session_id, on_event, pool):
        emit_dsh_events(on_event, text='{"evidence": [ {"title": "半截')
        return FakeResult('{"evidence": [ {"title": "半截', finish_reason="max-tokens")

    pool = _pool(tmp_path, {"primary": truncated_primary})
    chunks, emit = _collect()
    out = run_with_failover(pool, "hi", "s1", emit)
    assert out.degraded and out.route == "fallback"
    contents = "".join(c["choices"][0]["delta"].get("content", "")
                       for c in chunks if "usage" not in c)
    assert "半截" not in contents


def test_max_tokens_message_names_cause(tmp_path):
    def truncated(prompt, session_id, on_event, pool):
        return FakeResult("x", finish_reason="max-tokens")

    pool = FakePool(make_settings(tmp_path, kimi_api_key=""),
                    {"primary": truncated})
    _, emit = _collect()
    with pytest.raises(RuntimeError, match="max-tokens"):
        run_with_failover(pool, "hi", "s1", emit)


def test_turn_error_reason_triggers_fallback(tmp_path):
    def error_primary(prompt, session_id, on_event, pool):
        return FakeResult("whatever", finish_reason="error")

    pool = _pool(tmp_path, {"primary": error_primary})
    _, emit = _collect()
    out = run_with_failover(pool, "hi", "s1", emit)
    assert out.degraded


def test_refusal_on_fallback_raises_refusal(tmp_path):
    def refusing(_p, _s, on_event, _pool):
        emit_dsh_events(on_event, text="I cannot do that")
        return FakeResult("I cannot do that")

    pool = _pool(tmp_path, {"primary": refusing, "fallback": refusing})
    _, emit = _collect()
    with pytest.raises(RuntimeError, match="All routes failed"):
        run_with_failover(pool, "hi", "s1", emit)
    # ModelRefusalError 可被单独识别
    assert ModelRefusalError.__name__ == "ModelRefusalError"
