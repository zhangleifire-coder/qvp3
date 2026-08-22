"""Nanobot 接入层单测：流式 SSE 解析 / usage 聚合 / 空响应与不可达错误。"""
import json

import httpx
import pytest

from src.gateway import nanobot_client
from src.gateway.nanobot_client import NanobotUnavailableError, call_agent, health


def _sse_response(events: list[dict], status: int = 200):
    async def gen():
        for e in events:
            yield f"data: {json.dumps(e)}\n\n".encode()
        yield b"data: [DONE]\n\n"
    return httpx.Response(status, content=gen(),
                          headers={"Content-Type": "text/event-stream"})


@pytest.fixture
def mock_transport(monkeypatch):
    holder = {"handler": None}
    _orig_client = httpx.AsyncClient

    def _handler(request):
        return holder["handler"](request)

    transport = httpx.MockTransport(_handler)

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return _orig_client(*args, **kwargs)

    monkeypatch.setattr(nanobot_client.httpx, "AsyncClient", _factory)
    return holder


async def test_call_agent_streams_and_aggregates(mock_transport):
    mock_transport["handler"] = lambda req: _sse_response([
        {"model": "deepseek-v4-pro", "choices": [{"delta": {"content": "你好"}}]},
        {"choices": [{"delta": {"content": "，世界"}}]},
        {"choices": [{"delta": {}}],
         "usage": {"prompt_tokens": 11, "completion_tokens": 22}},
    ])
    deltas = []
    r = await call_agent("hi", session_id="s1",
                         on_delta=lambda p, t: deltas.append(p))
    assert r["text"] == "你好，世界"
    assert r["model_version"] == "nanobot:deepseek-v4-pro"
    assert r["prompt_tokens"] == 11 and r["completion_tokens"] == 22
    assert deltas == ["你好", "，世界"]


async def test_call_agent_empty_response_raises(mock_transport):
    mock_transport["handler"] = lambda req: _sse_response([
        {"choices": [{"delta": {"content": "  "}}]}])
    with pytest.raises(RuntimeError, match="空响应"):
        await call_agent("hi", session_id="s2")


async def test_call_agent_http_error_raises(mock_transport):
    mock_transport["handler"] = lambda req: _sse_response(
        [], status=500) if False else httpx.Response(500, content=b"boom")
    with pytest.raises(RuntimeError, match="500"):
        await call_agent("hi", session_id="s3")


async def test_unreachable_raises_friendly(mock_transport):
    def _boom(request):
        raise httpx.ConnectError("refused")
    mock_transport["handler"] = _boom
    with pytest.raises(NanobotUnavailableError):
        await call_agent("hi", session_id="s4")


async def test_health_false_when_down(mock_transport):
    def _boom(request):
        raise httpx.ConnectError("refused")
    mock_transport["handler"] = _boom
    assert await health() is False
