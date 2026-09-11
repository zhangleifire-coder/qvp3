"""SSE 映射与聚合的纯函数单测。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_serve.sse import (StreamAggregate, final_chunk, map_dsh_event,  # noqa: E402
                           sse_line)

CID = "chatcmpl-test"
MODEL = "deepseek-v4-pro"


def _ev(chunk: dict) -> dict:
    return {"type": "assistant/chunk", "data": {"chunk": chunk}}


def test_reasoning_delta_maps_to_reasoning_content():
    out = map_dsh_event(_ev({"type": "reasoning-delta", "text": "想"}), CID, MODEL)
    assert len(out) == 1
    delta = out[0]["choices"][0]["delta"]
    assert delta == {"reasoning_content": "想"}
    assert out[0]["id"] == CID and out[0]["model"] == MODEL


def test_text_delta_maps_to_content():
    out = map_dsh_event(_ev({"type": "text-delta", "text": "正文"}), CID, MODEL)
    assert out[0]["choices"][0]["delta"] == {"content": "正文"}


def test_usage_merges_cache_read_into_prompt_tokens():
    out = map_dsh_event(_ev({"type": "usage", "usage": {
        "inputTokens": 100, "cacheReadTokens": 50,
        "outputTokens": 20, "totalTokens": 170}}), CID, MODEL)
    assert out[0]["usage"] == {"prompt_tokens": 150,
                               "completion_tokens": 20, "total_tokens": 170,
                               "prompt_cache_hit_tokens": 50,
                               "prompt_cache_miss_tokens": 100}


def test_unrelated_events_are_dropped():
    assert map_dsh_event({"type": "turn/start", "data": {}}, CID, MODEL) == []
    assert map_dsh_event(_ev({"type": "block-start", "blockType": "reasoning"}),
                         CID, MODEL) == []
    assert map_dsh_event("not-a-dict", CID, MODEL) == []


def test_final_chunk_and_done_line():
    fc = final_chunk(CID, MODEL)
    assert fc["choices"][0]["finish_reason"] == "stop"
    assert sse_line(_DONE := "[DONE]") == "data: [DONE]\n\n"
    assert sse_line(fc).startswith("data: {")


def test_aggregate_text_and_usage():
    agg = StreamAggregate()
    for chunk in map_dsh_event(_ev({"type": "reasoning-delta", "text": "r"}), CID, MODEL):
        agg.feed_chunk(chunk)
    assert not agg.content_started
    for piece in ("ab", "cd"):
        for chunk in map_dsh_event(_ev({"type": "text-delta", "text": piece}), CID, MODEL):
            agg.feed_chunk(chunk)
    for chunk in map_dsh_event(_ev({"type": "usage", "usage": {
            "inputTokens": 10, "outputTokens": 5}}), CID, MODEL):
        agg.feed_chunk(chunk)
    assert agg.text == "abcd" and agg.content_started
    assert agg.usage == {"prompt_tokens": 10, "completion_tokens": 5,
                         "prompt_cache_hit_tokens": 0,
                         "prompt_cache_miss_tokens": 10}


def test_aggregate_usage_accumulates_across_steps():
    """多 step 长循环：usage 逐条累加（非 last-chunk-wins），cacheRead 并入 prompt。"""
    agg = StreamAggregate()
    for inp, cache, out in ((100, 50, 20), (300, 200, 30), (500, 0, 40)):
        for chunk in map_dsh_event(_ev({"type": "usage", "usage": {
                "inputTokens": inp, "cacheReadTokens": cache,
                "outputTokens": out, "totalTokens": inp + cache + out}}),
                CID, MODEL):
            agg.feed_chunk(chunk)
    assert agg.usage == {"prompt_tokens": 150 + 500 + 500,  # (100+50)+(300+200)+(500+0)
                         "completion_tokens": 90,
                         "prompt_cache_hit_tokens": 50 + 200 + 0,
                         "prompt_cache_miss_tokens": 100 + 300 + 500}
