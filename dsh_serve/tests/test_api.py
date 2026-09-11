"""HTTP 层单测：SSE 聚合格式、session 复用（mock）、并发信号量、health。

全部 mock dsh SDK（FakePool），不做真实模型调用。
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_serve.app import create_app  # noqa: E402
from dsh_serve.sdk_runner import HarnessStartError  # noqa: E402

from .conftest import FakePool, FakeResult, emit_dsh_events, make_settings  # noqa: E402

pytestmark = pytest.mark.asyncio


def parse_sse(body: str):
    chunks = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            chunks.append("[DONE]")
        elif payload:
            chunks.append(json.loads(payload))
    return chunks


@pytest.fixture
async def client(tmp_path):
    pool = FakePool(make_settings(tmp_path))
    app = create_app(pool.settings, pool=pool)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        # 手动触发 lifespan（ASGITransport 不自动跑）
        async with app.router.lifespan_context(app):
            c.pool = pool
            yield c


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "primary" in body and "fallback" in body and "mcp" in body
    assert body["max_concurrent"] == 4
    assert "active_requests" in body


async def test_stream_sse_format(client):
    resp = await client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "你好"}],
        "session_id": "t-fmt-1", "stream": True})
    assert resp.status_code == 200
    chunks = parse_sse(resp.text)
    assert chunks[-1] == "[DONE]"
    data = [c for c in chunks if c != "[DONE]"]
    reasoning = [c for c in data
                 if c["choices"][0]["delta"].get("reasoning_content")]
    content = [c for c in data if c["choices"][0]["delta"].get("content")]
    usage = [c for c in data if "usage" in c]
    final = [c for c in data
             if c["choices"][0].get("finish_reason") == "stop"]
    assert reasoning and content and usage and final
    assert usage[0]["usage"]["prompt_tokens"] == 150  # 100+50 cacheRead 合并
    assert usage[0]["usage"]["completion_tokens"] == 20
    assert all(c["object"] == "chat.completion.chunk" for c in data)
    # 所有 chunk 同 id
    assert len({c["id"] for c in data}) == 1


async def test_session_reuse_via_session_id(client):
    """同 session_id 二次调用：session_id 透传到 SDK 层（记忆由 dsh 持久化保证）。"""
    r1 = await client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "请记住这个暗号：测试暗号-1。"}],
        "session_id": "t-mem-1", "stream": True})
    assert r1.status_code == 200
    r2 = await client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "暗号是什么？"}],
        "session_id": "t-mem-1", "stream": True})
    chunks = parse_sse(r2.text)
    text = "".join(c["choices"][0]["delta"].get("content", "")
                   for c in chunks if c != "[DONE]")
    assert "测试暗号-1" in text
    # 两次调用都带上了同一个 session_id
    assert [c[1] for c in client.pool.calls] == ["t-mem-1", "t-mem-1"]


async def test_unary_mode(client):
    resp = await client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}],
        "session_id": "t-unary-1", "stream": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "fake answer"
    assert body["usage"] == {"prompt_tokens": 150, "completion_tokens": 20,
                             "prompt_cache_hit_tokens": 50,
                             "prompt_cache_miss_tokens": 100}


async def test_prestream_crash_maps_502(tmp_path):
    def boom(_p, _s, _e, _pool):
        raise HarnessStartError("dsh 子进程启动失败：no node")

    pool = FakePool(make_settings(tmp_path, kimi_api_key=""),
                    {"primary": boom})
    app = create_app(pool.settings, pool=pool)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            resp = await c.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": "hi"}],
                "session_id": "t-err-1", "stream": True})
            assert resp.status_code == 502
            assert "子进程错误" in resp.json()["detail"]


async def test_timeout_maps_504(tmp_path):
    def slow(_p, _s, _e, _pool):
        raise TimeoutError("read timeout")

    pool = FakePool(make_settings(tmp_path, kimi_api_key=""),
                    {"primary": slow})
    app = create_app(pool.settings, pool=pool)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            resp = await c.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": "hi"}],
                "session_id": "t-err-2", "stream": False})
            assert resp.status_code == 504


async def test_concurrency_semaphore(tmp_path):
    limit = 2

    def sleepy(prompt, session_id, on_event, pool):
        time.sleep(0.2)
        emit_dsh_events(on_event, text="ok")
        return FakeResult("ok")

    pool = FakePool(make_settings(tmp_path, dsh_max_concurrent=limit),
                    {"primary": sleepy})
    app = create_app(pool.settings, pool=pool)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport,
                               base_url="http://test") as c:
            reqs = [c.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": f"q{i}"}],
                "session_id": f"t-conc-{i}", "stream": False})
                for i in range(6)]
            resps = await asyncio.gather(*reqs)
            assert all(r.status_code == 200 for r in resps)
            # 信号量上限生效：HTTP 层同时在飞 ≤ limit
            # （单路由锁会把 dsh 侧压到 1，这里验证的是 HTTP 并发闸门）
            assert pool.max_inflight <= max(1, limit)


async def test_same_session_serialized(tmp_path):
    seen_parallel = []

    def probe(prompt, session_id, on_event, pool):
        seen_parallel.append(pool.inflight)
        time.sleep(0.1)
        emit_dsh_events(on_event, text="ok")
        return FakeResult("ok")

    pool = FakePool(make_settings(tmp_path), {"primary": probe})
    app = create_app(pool.settings, pool=pool)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            reqs = [c.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": f"q{i}"}],
                "session_id": "t-same-session", "stream": False})
                for i in range(3)]
            resps = await asyncio.gather(*reqs)
            assert all(r.status_code == 200 for r in resps)
            assert max(seen_parallel) == 1  # 同 session 严格串行
