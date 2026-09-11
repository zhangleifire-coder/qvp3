"""从最新会话日志的 request/header 提取真实工具目录。"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import zstandard  # noqa: E402

root = Path(r"C:\Users\Zhang\OneDrive\Desktop\ANJU项目\图文平台_20260903\tp2create1.0\dsh-home\sessions")
target = sys.argv[1]
for d in root.rglob(target):
    log = d / "session.jsonl.zstd"
    if not log.exists():
        continue
    text = io.TextIOWrapper(
        zstandard.ZstdDecompressor().stream_reader(log.read_bytes()),
        encoding="utf-8").read()
    for line in text.splitlines():
        if '"request/header"' not in line:
            continue
        ev = json.loads(line)
        tools = (ev["data"]["header"].get("tools") or [])
        names = [t.get("name") if isinstance(t, dict) else str(t) for t in tools]
        print(f"session={d.name} tools({len(names)}):", ", ".join(sorted(names)))
