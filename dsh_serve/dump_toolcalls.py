"""提取指定会话的 tool/call 与 tool 结果摘要（地面真值证据）。"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import zstandard  # noqa: E402

root = Path(r"C:\Users\Zhang\OneDrive\Desktop\ANJU项目\图文平台_20260903\tp2create1.0\dsh-home\sessions")
for target in sys.argv[1:]:
    for d in sorted(root.rglob(target)):
        log = d / "session.jsonl.zstd"
        if not log.exists():
            continue
        text = io.TextIOWrapper(
            zstandard.ZstdDecompressor().stream_reader(log.read_bytes()),
            encoding="utf-8").read()
        for line in text.splitlines():
            if '"tool/call"' not in line and '"tool/result"' not in line and '"tool-end"' not in line and '"tool/' not in line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get("type", "")
            if not t.startswith("tool"):
                continue
            dta = ev.get("data") or {}
            s = json.dumps(dta, ensure_ascii=False)
            print(f"[{d.name}][{t}]", s[:360])
