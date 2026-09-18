"""提取旧网关备份会话里的视觉记忆笔记（历史一次性迁移，已完成；role=user 且以 Remember this reviewer feedback 开头）。"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = Path(r"C:\Users\Zhang\OneDrive\Desktop\ANJU项目\图文平台_20260903\tp2create1.0\nanobot-sessions-backup")
OUT = Path(__file__).parent

notes = []
for rel in ["YXBpOnF2cC12aXN1YWwtbWVtb3J5LXYx.jsonl",
            "qvp2-nanobot-sessions/YXBpOnF2cC12aXN1YWwtbWVtb3J5LXYx.jsonl"]:
    for line in (BASE / rel).read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        # 兼容两种结构：平铺 message 或嵌套
        msg = rec.get("message", rec)
        role = msg.get("role") or rec.get("role")
        content = msg.get("content") or rec.get("content") or ""
        if isinstance(content, list):
            content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        if role == "user" and content.startswith("Remember this reviewer feedback"):
            notes.append((rec.get("timestamp") or rec.get("time") or "?", content, rel))

print(f"extracted {len(notes)} notes")
for i, (ts, content, src) in enumerate(notes, 1):
    out = OUT / f"memory-note-{i}.txt"
    out.write_text(content, encoding="utf-8")
    print(f"note {i}: ts={ts} src={src} len={len(content)} head={content[:80]!r}")
