"""深挖指定 dsh 会话日志：每轮的请求配置、工具调用、错误、产出形态、usage。"""
import io
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import zstandard  # noqa: E402

path = Path(sys.argv[1])
text = io.TextIOWrapper(
    zstandard.ZstdDecompressor().stream_reader(path.read_bytes()),
    encoding="utf-8").read()

types = Counter()
turns = Counter()
tool_calls = []
errors = []
text_chars = Counter()   # turn -> 正文字符
reason_chars = Counter() # turn -> 推理字符
usages = []
headers = []

for line in text.splitlines():
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        continue
    t = ev.get("type")
    types[t] += 1
    d = ev.get("data") or {}
    if t == "request/header":
        headers.append(d.get("header", {}).get("config"))
    if t == "turn/start":
        cur = d.get("turn")
    if t == "tool/call":
        tool_calls.append((d.get("turn"), d.get("step"), d.get("name"),
                           len(d.get("arguments", ""))))
    if t == "tool/result":
        res = str(d.get("result"))[:200]
        tool_calls.append((d.get("turn"), d.get("step"),
                           "RESULT:" + str(d.get("name", ""))[:40], len(res)))
    if t == "turn/end":
        turns[json.dumps(d.get("reason"), ensure_ascii=False)] += 1
    if t == "assistant/message":
        msg = d.get("message") or {}
        for b in msg.get("content", []):
            if b.get("type") == "text":
                text_chars[d.get("turn")] += len(b.get("text", ""))
            if b.get("type") == "reasoning":
                reason_chars[d.get("turn")] += len(b.get("text", ""))
        u = d.get("usage")
        if u:
            usages.append((d.get("turn"), d.get("step"), u))
        src = (msg.get("source") or {})
        if src.get("kind") == "model" and not msg.get("content"):
            errors.append(f"empty assistant message turn={d.get('turn')}")
    if "error" in s.lower() if (s := json.dumps(d, ensure_ascii=False)) else False:
        pass

print("== event types ==", dict(types))
print("== turn/end reasons ==")
for k, v in turns.items():
    print(f"  {v}x {k[:300]}")
print("== request configs ==", json.dumps(headers, ensure_ascii=False))
print("== tool calls ==")
for tc in tool_calls:
    print(" ", tc)
print("== per-turn text/reasoning chars ==")
for turn in sorted(set(text_chars) | set(reason_chars)):
    print(f"  turn {turn}: text={text_chars.get(turn,0)} reasoning={reason_chars.get(turn,0)}")
print("== usages ==")
for u in usages:
    print(" ", json.dumps(u, ensure_ascii=False))
print("== errors ==", errors)
