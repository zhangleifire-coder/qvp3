"""基准批关卡代确认（bench 辅助）：轮询 awaiting_text / awaiting_refs，
对基准集 query 命中的任务自动放行（text 用草稿原样；refs 全保留候选）。

用法：PYTHONUTF8=1 python scripts/bench_gatekeeper.py --base http://localhost:8005
"""
import argparse
import json
import time
from pathlib import Path
from urllib import request, error

ROOT = Path(__file__).resolve().parent.parent
BENCH_QUERIES = {it["query"] for it in json.loads(
    (ROOT / "data" / "bench" / "bench_set.json").read_text(encoding="utf-8"))["items"]}


def _http(method: str, url: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(url, data=data, method=method,
                          headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except error.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.read()[:200]}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8005")
    ap.add_argument("--actor", default="张三")
    ap.add_argument("--max-minutes", type=int, default=100)
    args = ap.parse_args()
    deadline = time.time() + args.max_minutes * 60
    confirmed_text = confirmed_refs = 0
    while time.time() < deadline:
        listing = _http("GET", f"{args.base}/api/tasks?limit=200")
        acted = False
        for t in listing.get("items", []):
            if t.get("query") not in BENCH_QUERIES:
                continue
            status = t.get("status")
            if status == "awaiting_text":
                r = _http("POST", f"{args.base}/api/tasks/{t['id']}/text/confirm",
                          {"actor": args.actor})
                print(f"[gate] text/confirm {t['id'][:8]} {t['query'][:20]} -> {r}",
                      flush=True)
                confirmed_text += 1
                acted = True
            elif status == "awaiting_refs":
                detail = _http("GET", f"{args.base}/api/tasks/{t['id']}/detail")
                keep = [a["id"] for a in detail.get("assets", [])
                        if a.get("source_type") == "official"
                        and a.get("selection_status") in ("candidate", "confirmed")]
                r = _http("POST", f"{args.base}/api/tasks/{t['id']}/refs/confirm",
                          {"keep_ids": keep, "actor": args.actor})
                print(f"[gate] refs/confirm {t['id'][:8]} keep={len(keep)} -> {r}",
                      flush=True)
                confirmed_refs += 1
                acted = True
        if not acted:
            time.sleep(20)
    print(f"[gate] 结束：text 放行 {confirmed_text}，refs 放行 {confirmed_refs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
