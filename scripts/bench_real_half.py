"""真实批（减半 10 题）runner：复用 bench_image_quality 的函数，绕过 20 题断言。

用法：PYTHONUTF8=1 python scripts/bench_real_half.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_image_quality import submit, poll_until_terminal, export_board, _http  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HALF = ROOT / "data" / "bench" / "bench_set_half.json"
BASE = "http://localhost:8005"


def main() -> int:
    items = json.loads(HALF.read_text(encoding="utf-8"))["items"]
    print(f"[real] 减半真实批 {len(items)} 题 → {BASE}（MOCK 已关，真实出图计费）")
    bench_to_task = submit(BASE, items, "张三")
    print(f"[real] 映射 {len(bench_to_task)} 题，轮询终态…")
    results = poll_until_terminal(BASE, bench_to_task)
    out = ROOT / "docs" / f"生图基准-{time.strftime('%Y%m%d')}-real-half.md"
    export_board(BASE, items, results, out)
    n_ok = sum(1 for r in results.values() if r.get("status") in ("review", "approved"))
    print(f"[real] 完成：{n_ok}/{len(items)} 题到达可审核态；拼版 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
