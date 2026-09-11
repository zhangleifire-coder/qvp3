"""测试公共件：FakePool —— mock 掉 dsh SDK，不做真实模型调用。"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # code/ 根

from dsh_serve.config import Settings  # noqa: E402
from dsh_serve.sdk_runner import Route  # noqa: E402


class FakeResult:
    def __init__(self, text="fake answer", finish_reason="completed", events=None):
        self.final_response = text
        self.finish_reason = finish_reason
        self.events = events or []


class FakePool:
    """HarnessPool 的 mock：run() 按剧本发 dsh 事件并返回 FakeResult。

    script: dict[route_name, callable(prompt, session_id, on_event) -> FakeResult]
    """

    def __init__(self, settings: Settings, script: dict | None = None):
        self.settings = settings
        self.routes = {
            "primary": Route("primary", settings.primary_provider, settings.primary_model),
            "fallback": Route("fallback", settings.fallback_provider, settings.fallback_model),
            "fallback2": Route("fallback2", settings.fallback2_provider, settings.fallback2_model),
        }
        self.script = script or {}
        self.calls: list[tuple[str, str, str]] = []  # (route, session_id, prompt)
        self.inflight = 0
        self.max_inflight = 0
        self.mem: dict[str, list[str]] = {}          # session 记忆模拟
        self.closed = False

    def run(self, route_name, prompt, session_id, on_event):
        self.calls.append((route_name, session_id, prompt))
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            fn = self.script.get(route_name)
            if fn:
                return fn(prompt, session_id, on_event, self)
            return default_script(prompt, session_id, on_event, self)
        finally:
            self.inflight -= 1

    def health(self):
        return {"primary": {"alive": True}, "fallback": {"enabled": True},
                "mcp": {"enabled": False}, "dsh_home": self.settings.dsh_home}

    def close(self):
        self.closed = True


def emit_dsh_events(on_event, text="fake answer", reasoning="thinking..."):
    """模拟 dsh 一路流式事件：reasoning-delta → text-delta → usage。"""
    for piece in (reasoning,):
        on_event({"type": "assistant/chunk",
                  "data": {"chunk": {"type": "reasoning-delta", "text": piece}}})
    for piece in (text[: len(text) // 2], text[len(text) // 2:]):
        on_event({"type": "assistant/chunk",
                  "data": {"chunk": {"type": "text-delta", "text": piece}}})
    on_event({"type": "assistant/chunk",
              "data": {"chunk": {"type": "usage", "usage": {
                  "inputTokens": 100, "cacheReadTokens": 50,
                  "outputTokens": 20, "totalTokens": 170}}}})


def default_script(prompt, session_id, on_event, pool: FakePool):
    """默认剧本：模拟 session 记忆——记得同 session 里出现过的暗号。"""
    pool.mem.setdefault(session_id, []).append(prompt)
    history = pool.mem[session_id]
    text = "fake answer"
    for prev in history[:-1]:
        if "暗号" in prev:
            # 从 "请记住这个暗号：X" 提取暗号并在本轮复述
            text = prev.split("：", 1)[-1].split("。")[0]
    emit_dsh_events(on_event, text=text)
    return FakeResult(text)


def make_settings(tmp_path: Path, **over) -> Settings:
    base = dict(dsh_home=str(tmp_path / "dsh-home"),
                deepseek_api_key="test-primary-key",
                kimi_api_key="test-fallback-key",
                mcp_enabled=False, dsh_max_concurrent=4)
    base.update(over)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)
