"""dsh_client 接入层单测：流式 SSE 解析 / usage 聚合 / 空响应与不可达错误。"""
import json

import httpx
import pytest

from src.gateway import dsh_client
from src.gateway.dsh_client import DshServeUnavailableError, call_agent, health


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

    monkeypatch.setattr(dsh_client.httpx, "AsyncClient", _factory)
    yield holder
    # 共享 client（P1-7）跨用例复用会带着本用例的 mock transport，清注册表隔离
    from src.gateway.http_client import reset_clients
    reset_clients()


async def test_call_agent_streams_and_aggregates(mock_transport):
    big = "好" * 130
    mock_transport["handler"] = lambda req: _sse_response([
        {"model": "deepseek-v4-pro", "choices": [{"delta": {"content": big}}]},
        {"choices": [{"delta": {"content": "，世界"}}]},
        {"choices": [{"delta": {}}],
         "usage": {"prompt_tokens": 11, "completion_tokens": 22}},
    ])
    deltas = []
    totals = []
    r = await call_agent("hi", session_id="s1",
                         on_delta=lambda p, t: (deltas.append(p), totals.append(t)))
    assert r["text"] == big + "，世界"
    assert r["model_version"] == "dsh:deepseek-v4-pro"
    assert r["prompt_tokens"] == 11 and r["completion_tokens"] == 22
    # 120 字符节流：首块 130 字触发一次；第二块仅 3 字不触发，流末尾部 flush 补终态
    assert deltas == [big, "，世界"]
    assert totals[-1] == r["text"]


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
    with pytest.raises(DshServeUnavailableError):
        await call_agent("hi", session_id="s4")


async def test_health_false_when_down(mock_transport):
    def _boom(request):
        raise httpx.ConnectError("refused")
    mock_transport["handler"] = _boom
    assert await health() is False


async def test_dsh_prefixed_model_passes_through(mock_transport):
    """dsh 网关自带前缀的 model 不再叠加 dsh_client: 前缀（防 dsh_client:dsh: 双前缀）。"""
    mock_transport["handler"] = lambda req: _sse_response([
        {"model": "dsh:deepseek-v4-pro", "choices": [{"delta": {"content": "好"}}]},
    ])
    r = await call_agent("hi", session_id="s5")
    assert r["model_version"] == "dsh:deepseek-v4-pro"
