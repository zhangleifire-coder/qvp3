"""v0.1.4 模板对齐升级验证批（5 题，用户上限 2026-09-22）。

复用 bench_image_quality 的 submit/poll/export；额外收集 v0.1.4 指标：
- 结构化分页生效：detail.task.page_specs 非空且条数=页数
- 跨页质检告警：reject_marks 含「[跨页]」前缀
- 质检告警总量：reject_marks 按类型分组（garble/综合质检/跨页）
- 生图成本：node_events / task 字段可得的 cost 口径

用法：PYTHONUTF8=1 python scripts/bench_v014.py [--base http://localhost:8005]
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_image_quality import submit, poll_until_terminal, export_board, _http  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SET = ROOT / "data" / "bench" / "bench_set_v014.json"


def collect_metrics(base: str, results: dict) -> list[str]:
    lines = ["", "## v0.1.4 指标汇总", "",
             "| 题 | 状态 | 结构化分页 | reject_marks（类型:页） | 跨页告警 |", "|---|---|---|---|---|"]
    for bid, row in sorted(results.items()):
        if bid.startswith("__new__"):
            continue
        tid = row.get("id")
        status = row.get("status")
        specs, marks, cross = "—", "—", "无"
        if tid and status not in ("failed", "timeout", "cancelled"):
            try:
                d = _http("GET", f"{base}/api/tasks/{tid}/detail")
                task = d.get("task") or {}
                ps = task.get("page_specs")
                n_pages = len(d.get("page_copies") or [])
                specs = ("✓" if isinstance(ps, list) and len(ps) == n_pages and n_pages
                         else f"✗({len(ps) if isinstance(ps, list) else '无'}"
                              f"/{n_pages}页)")
                rm = d.get("reject_marks") or []
                if rm:
                    marks = "；".join(
                        f"{m.get('item_type', '?')}@P{m.get('page_index', '?')}"
                        f"({str(m.get('reason', ''))[:24]})" for m in rm[:6])
                cross = "⚠有" if any("[跨页]" in str(m.get("reason", "")) for m in rm) else "无"
            except RuntimeError as e:
                marks = f"detail失败:{e}"
        lines.append(f"| {bid} | {status} | {specs} | {marks} | {cross} |")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8005")
    args = ap.parse_args()
    items = json.loads(SET.read_text(encoding="utf-8"))["items"]
    print(f"[v014] 验证批 {len(items)} 题 → {args.base}（真实出图计费，上限 5 题）")
    bench_to_task = submit(args.base, items, "张三")
    print(f"[v014] 映射 {len(bench_to_task)} 题，轮询终态…")
    results = poll_until_terminal(args.base, bench_to_task)
    out = ROOT / "docs" / f"生图基准-{time.strftime('%Y%m%d')}-v014.md"
    export_board(args.base, items, results, out)
    metrics = collect_metrics(args.base, results)
    with out.open("a", encoding="utf-8") as f:
        f.write("\n".join(metrics) + "\n")
    n_ok = sum(1 for r in results.values()
               if r.get("status") in ("review", "approved"))
    print(f"[v014] 完成：{n_ok}/{len(items)} 题到达可审核态；报告 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
