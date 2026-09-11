"""记忆重放：把 Nanobot 时代的 3 条视觉记忆笔记按序发进 dsh 同名会话。"""
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import httpx  # noqa: E402

HERE = Path(__file__).parent
URL = "http://127.0.0.1:8901/v1/chat/completions"
SID = "qvp-visual-memory-v1"

for i in (1, 2, 3):
    note = (HERE / f"memory-note-{i}.txt").read_text(encoding="utf-8")
    body = {"messages": [{"role": "user", "content": note}],
            "session_id": SID, "stream": False}
    t0 = time.time()
    r = httpx.post(URL, json=body, timeout=600)
    elapsed = time.time() - t0
    resp = r.json()
    text = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
    print(f"note {i}: http={r.status_code} {elapsed:.1f}s "
          f"model={resp.get('model')} reply={text[:80]!r}")
    if r.status_code != 200:
        print(json.dumps(resp, ensure_ascii=False)[:400])
        sys.exit(1)

# 验证轮
body = {"messages": [{"role": "user",
                      "content": "根据你记住的视觉方向基准，内页 headline 应该配什么？"}],
        "session_id": SID, "stream": False}
t0 = time.time()
r = httpx.post(URL, json=body, timeout=600)
resp = r.json()
answer = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
print(f"verify: http={r.status_code} {time.time()-t0:.1f}s")
print("ANSWER:", answer)
low = answer.lower()
print("HAS_DARK_BANNER:", ("dark banner" in low) or ("深色" in answer and "横幅" in answer))
