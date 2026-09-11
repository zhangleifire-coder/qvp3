"""dsh 事件流 → OpenAI 兼容 SSE chunk 的纯函数映射（可单测）。

dsh SDK 事件（session.event 通知 payload.event）中与本协议相关的：
- assistant/chunk: data.chunk = block-start(reasoning|text) / reasoning-delta /
  text-delta / block-end / usage
- assistant/message: 汇总（含 usage），不映射为增量，仅用于聚合兜底
映射规则（对齐 nanobot_client.py 的消费口径）：
- reasoning-delta → choices[0].delta.reasoning_content
- text-delta      → choices[0].delta.content
- usage           → chunk.usage {prompt_tokens, completion_tokens, total_tokens}
  （dsh inputTokens 为未命中缓存部分，prompt_tokens = inputTokens + cacheReadTokens）
"""
import json
import time


def new_chunk_id() -> str:
    import uuid

    return f"chatcmpl-{uuid.uuid4().hex[:12]}"


def base_chunk(cid: str, model: str) -> dict:
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
    }


def sse_line(payload) -> str:
    if isinstance(payload, str):
        return f"data: {payload}\n\n"
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def map_dsh_event(ev: dict, cid: str, model: str) -> list[dict]:
    """把一个 dsh 事件映射为 0..n 个 OpenAI chunk；无关事件返回 []。"""
    if not isinstance(ev, dict) or ev.get("type") != "assistant/chunk":
        return []
    chunk = (ev.get("data") or {}).get("chunk") or {}
    ctype = chunk.get("type")
    if ctype == "reasoning-delta":
        out = base_chunk(cid, model)
        out["choices"][0]["delta"] = {"reasoning_content": chunk.get("text", "")}
        return [out]
    if ctype == "text-delta":
        out = base_chunk(cid, model)
        out["choices"][0]["delta"] = {"content": chunk.get("text", "")}
        return [out]
    if ctype == "usage":
        u = chunk.get("usage") or {}
        out = base_chunk(cid, model)
        cache_read = u.get("cacheReadTokens") or 0
        cache_miss = u.get("inputTokens") or 0
        out["usage"] = {
            "prompt_tokens": cache_miss + cache_read,
            "completion_tokens": u.get("outputTokens") or 0,
            "total_tokens": u.get("totalTokens") or 0,
            # 缓存命中/未命中拆分（DeepSeek 缓存命中按低价计费，2026-09-09）
            "prompt_cache_hit_tokens": cache_read,
            "prompt_cache_miss_tokens": cache_miss,
        }
        return [out]
    return []


def final_chunk(cid: str, model: str, finish_reason: str = "stop") -> dict:
    out = base_chunk(cid, model)
    out["choices"][0]["finish_reason"] = finish_reason
    return out


class StreamAggregate:
    """聚合一路流式输出：正文 / 推理 / usage（nanobot_client 聚合口径的镜像）。

    usage 累加口径（2026-09-07 修复，对比分析差距 6）：dsh 每个 step 发一条
    usage 事件（该次模型调用的真实计费），长 agent 循环有多条——必须按条
    累加，last-chunk-wins 会把成本低估约一个量级。cacheRead 合并进
    prompt_tokens 的既有口径保留（映射发生在 map_dsh_event）。
    """

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.thinking_parts: list[str] = []
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cache_hit_tokens = 0
        self.cache_miss_tokens = 0
        self.content_started = False

    def feed_chunk(self, chunk: dict) -> None:
        u = chunk.get("usage")
        if isinstance(u, dict):
            self.prompt_tokens += u.get("prompt_tokens") or 0
            self.completion_tokens += u.get("completion_tokens") or 0
            self.cache_hit_tokens += u.get("prompt_cache_hit_tokens") or 0
            self.cache_miss_tokens += u.get("prompt_cache_miss_tokens") or 0
        for choice in chunk.get("choices", []):
            delta = choice.get("delta") or {}
            if delta.get("content"):
                self.text_parts.append(delta["content"])
                self.content_started = True
            if delta.get("reasoning_content"):
                self.thinking_parts.append(delta["reasoning_content"])

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    @property
    def usage(self) -> dict:
        return {"prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "prompt_cache_hit_tokens": self.cache_hit_tokens,
                "prompt_cache_miss_tokens": self.cache_miss_tokens}
