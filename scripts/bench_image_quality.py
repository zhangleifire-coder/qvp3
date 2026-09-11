"""生图质量基准批（WS4，2026-09-11）：基准集 → 批量投递 → 终态轮询 → 缩略图拼版。

用法：
  # 先看计划（不投递、不花钱）
  PYTHONUTF8=1 python scripts/bench_image_quality.py --dry-run
  # 真实批（需用户确认；一轮约 20 题 × 6-8 张 × ¥0.4 ≈ ¥50-65）：
  # 目标栈请先确认 MOCK_IMAGE_GEN 口径——联调一律 mock（0 成本），真实批 ≤4 轮
  PYTHONUTF8=1 python scripts/bench_image_quality.py --base http://localhost:8005 \
      --actor 张三 --out docs/生图基准-$(date +%Y%m%d).md

流程：
  1. 读 data/bench/bench_set.json（20 题固定快照：query/mode/风格快照参数）；
  2. 按 mode 分组 POST /api/tasks/import_queries 批量投递（幂等键去重）；
  3. 轮询 /api/tasks?limit= 直至全部到达出图终态（review/approved/rejected/failed）
     或超时（默认 90 分钟，真实生图 compare 模式慢网需 30-40 分钟）；
  4. 逐题拉 /api/tasks/{id}/detail 取 6 页配图 display_url，生成缩略图拼版
     markdown（docs/生图基准-<日期>.md）供人工 10 分钟快审对比。

机制纪律：
  - 本脚本只建机制；真实批由用户确认后另行触发；
  - 快审通过率不达基线 → 对应风格条目在 DB 置 enabled=false（数据层回滚，
    零代码回滚），并回改 data/styles.json 留痕。
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib import request, error

ROOT = Path(__file__).resolve().parent.parent
BENCH_SET = ROOT / "data" / "bench" / "bench_set.json"

# 出图终态：到达这些状态即「本轮出图流程结束」（review=待人工审核，图已产出可拼版）
TERMINAL_STATUSES = {"review", "approved", "rejected", "failed", "cancelled"}
POLL_INTERVAL = 30          # 秒
POLL_TIMEOUT = 90 * 60      # 秒


def _http(method: str, url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(url, data=data, method=method,
                          headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        raise RuntimeError(f"{method} {url} → HTTP {e.code}: {e.read()[:200]}")


def load_bench(path: Path | None = None) -> list[dict]:
    doc = json.loads((path or BENCH_SET).read_text(encoding="utf-8"))
    items = doc["items"]
    assert len(items) == 20, f"基准集应为 20 题，当前 {len(items)}"
    return items


def submit(base: str, items: list[dict], actor: str) -> dict[str, str]:
    """按 mode 分组批量投递；返回 {bench_id: task_id}。"""
    by_mode: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_mode[it.get("mode") or "general"].append(it)
    bench_to_task: dict[str, str] = {}
    for mode, group in by_mode.items():
        r = _http("POST", f"{base}/api/tasks/import_queries", {
            "queries": [g["query"] for g in group],
            "content_type": "generic", "mode": mode, "actor": actor})
        # import_queries 幂等去重：已存在的 query 不在 task_ids 里，按 query 反查
        for tid in r.get("task_ids", []):
            bench_to_task[f"__new__{tid}"] = tid
        print(f"[bench] 投递 {mode} 组 {len(group)} 题：新增 {len(r.get('task_ids', []))}"
              f"（重复自动跳过）")
    # 按 query 反查全部 task_id（含历史已存在的）
    listing = _http("GET", f"{base}/api/tasks?limit=200")
    by_query = {t["query"]: t["id"] for t in listing.get("items", [])}
    missing = []
    for it in items:
        tid = by_query.get(it["query"])
        if tid:
            bench_to_task[it["id"]] = tid
        else:
            missing.append(it["id"])
    if missing:
        print(f"[bench] 警告：{len(missing)} 题未找到任务：{missing}")
    return bench_to_task


def poll_until_terminal(base: str, bench_to_task: dict[str, str]) -> dict[str, dict]:
    """轮询至全部终态或超时；返回 {bench_id: task_row}。"""
    deadline = time.time() + POLL_TIMEOUT
    pending = dict(bench_to_task)
    final: dict[str, dict] = {}
    while pending and time.time() < deadline:
        listing = _http("GET", f"{base}/api/tasks?limit=200")
        by_id = {t["id"]: t for t in listing.get("items", [])}
        for bid, tid in list(pending.items()):
            row = by_id.get(tid)
            if row and row.get("status") in TERMINAL_STATUSES:
                final[bid] = row
                del pending[bid]
        if pending:
            print(f"[bench] 待终态 {len(pending)} 题，{POLL_INTERVAL}s 后再查…")
            time.sleep(POLL_INTERVAL)
    for bid, tid in pending.items():
        final[bid] = {"id": tid, "status": "timeout"}
    return final


def export_board(base: str, items: list[dict], results: dict[str, dict],
                 out_path: Path) -> None:
    """逐题拉 detail 取配图，生成缩略图拼版 markdown 供人工快审。"""
    lines = ["# 生图基准拼版", "",
             f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M')} ｜ 目标栈：{base}",
             "> 人工快审口径：逐题看 6 页拼版——风格统一性 / 图上文字正确性 /"
             " 主体贴合度 / 版式轮换；不达标题记在题后，汇总通过率与上轮对比。", ""]
    for it in items:
        row = results.get(it["id"], {})
        status = row.get("status", "?")
        lines.append(f"## {it['id']}｜{it['query']}（{it.get('mode', 'general')}）"
                     f"｜状态 {status}")
        if it.get("gen_image_style"):
            lines.append(f"- 风格快照：{it['gen_image_style']}"
                         f"（内容风格 {it.get('gen_style', '—')}）")
        tid = row.get("id")
        if tid and status not in ("failed", "timeout", "cancelled"):
            try:
                detail = _http("GET", f"{base}/api/tasks/{tid}/detail")
            except RuntimeError as e:
                lines.append(f"- detail 拉取失败：{e}")
                detail = {}
            shots = [a for a in detail.get("assets", [])
                     if a.get("source_type") == "ai_generated"
                     and not a.get("is_history")]
            shots.sort(key=lambda a: a.get("page_index", 0))
            if shots:
                lines.append("")
                lines.append(" ".join(
                    f"![p{a['page_index']}]({base}{a['display_url']})"
                    for a in shots))
            else:
                lines.append("- （无配图产出）")
        lines.append("")
        lines.append("快审结论：☐ 通过　☐ 不通过（原因：____）")
        lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[bench] 拼版已写出：{out_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://localhost:8005", help="目标栈 base URL")
    ap.add_argument("--actor", default="张三", help="投递账号（须存在于 users）")
    ap.add_argument("--bench", default=str(BENCH_SET), help="基准集 json 路径")
    ap.add_argument("--out", default="", help="拼版输出 md（默认 docs/生图基准-<日期>.md）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不投递")
    args = ap.parse_args()

    items = load_bench(Path(args.bench))
    n_oss = sum(1 for i in items if i["source"] == "oss_distilled")
    print(f"[bench] 基准集 {len(items)} 题（开源蒸馏 {n_oss} + 历史真实 "
          f"{len(items) - n_oss}）；目标 {args.base}")
    if args.dry_run:
        for it in items:
            print(f"  {it['id']:>7} [{it.get('mode', 'general'):7}] {it['query']}"
                  + (f"（快照风格 {it['gen_image_style']}）" if it.get("gen_image_style") else ""))
        print("[bench] dry-run：未投递。真实批请先确认 MOCK_IMAGE_GEN 口径与成本预算"
              "（约 ¥50-65/轮）。")
        return 0

    bench_to_task = submit(args.base, items, args.actor)
    results = poll_until_terminal(args.base, bench_to_task)
    out = Path(args.out) if args.out else (
        ROOT / "docs" / f"生图基准-{time.strftime('%Y%m%d')}.md")
    export_board(args.base, items, results, out)
    n_ok = sum(1 for r in results.values() if r.get("status") in ("review", "approved"))
    print(f"[bench] 完成：{n_ok}/{len(items)} 题到达可审核态；"
          f"请人工快审拼版并与上轮基线对比。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
