"""双候选选优（2026-09-14 A 方案）单测：OCR 选优纯函数 + 风格风险门控阈值。

门控经济性背景：P2 VL 申诉上线后重生率已低位，每页固定双候选会把成本从
期望 ~7 张锁死到 12 张——故只对 first_sim 数据显示系统性不合格的风格启用。
"""
from qvp_mcp.server import pick_best_candidate
from src.services.style_risk import (_decide_dual, DUAL_FAIL_PCT,
                                     DUAL_MIN_PAGES)


class TestPickBestCandidate:
    def test_highest_sim_wins(self):
        c1, c2 = {"hash": "a"}, {"hash": "b"}
        best, sim = pick_best_candidate([(c1, 0.71), (c2, 1.0)])
        assert best is c2 and sim == 1.0

    def test_tie_prefers_earlier_candidate(self):
        c1, c2 = {"hash": "a"}, {"hash": "b"}
        best, _ = pick_best_candidate([(c1, 1.0), (c2, 1.0)])
        assert best is c1

    def test_single_candidate_passthrough(self):
        c1 = {"hash": "a"}
        best, sim = pick_best_candidate([(c1, 0.83)])
        assert best is c1 and sim == 0.83


class TestDualCandidateGate:
    def test_threshold_requires_min_pages(self):
        # 不合格率再高，页数不够也不开双候选（数据不可信）
        assert _decide_dual(DUAL_MIN_PAGES - 1, DUAL_MIN_PAGES) == 1

    def test_threshold_fail_rate_boundary(self):
        pages = DUAL_MIN_PAGES * 2
        need = int(pages * DUAL_FAIL_PCT / 100 + 0.999999)  # 恰好达阈值
        assert _decide_dual(pages, need) == 2
        assert _decide_dual(pages, need - 1) == 1

    def test_below_threshold_single(self):
        assert _decide_dual(100, 10) == 1   # 10% 不合格率，正常风格
