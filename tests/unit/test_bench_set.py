"""WS4 基准集守卫（2026-09-11）：bench_set.json 结构与内容校验。"""
import json
from pathlib import Path

BENCH_SET = Path(__file__).resolve().parents[2] / "data" / "bench" / "bench_set.json"


def _items() -> list[dict]:
    return json.loads(BENCH_SET.read_text(encoding="utf-8"))["items"]


class TestBenchSet:
    def test_total_20(self):
        items = _items()
        assert len(items) == 20

    def test_composition(self):
        items = _items()
        oss = [i for i in items if i["source"] == "oss_distilled"]
        his = [i for i in items if i["source"] != "oss_distilled"]
        assert len(oss) == 8 and len(his) == 12

    def test_unique_ids_and_valid_mode(self):
        items = _items()
        ids = [i["id"] for i in items]
        assert len(set(ids)) == len(ids)
        for i in items:
            assert i["query"].strip() and len(i["query"]) >= 4
            assert i.get("mode", "general") in ("general", "single", "compare")

    def test_unique_queries(self):
        # import_queries 幂等键含 query：重复 query 会被跳过导致轮询缺题
        queries = [i["query"] for i in _items()]
        assert len(set(queries)) == len(queries)

    def test_load_bench_helper(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from bench_image_quality import load_bench, TERMINAL_STATUSES
        assert len(load_bench()) == 20
        assert "review" in TERMINAL_STATUSES and "approved" in TERMINAL_STATUSES
