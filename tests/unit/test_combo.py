"""组合生成导入：问题池解析 / 随机抽取 / 风格提示 / 分析结果解析 单测。"""
import random

from src.services.combo import (analyze_prompt, parse_analyzed_questions,
                                parse_pool, pick_rounds, style_hint)
from src.pipeline.agent_production import _build_combo_section, _compose_message
from types import SimpleNamespace


class TestParsePool:
    def test_mixed_separators(self):
        assert parse_pool("空调多少钱，换散热器注意什么；多久加一次氟、怎么判断缺氟\n清洗多少钱") == [
            "空调多少钱", "换散热器注意什么", "多久加一次氟", "怎么判断缺氟", "清洗多少钱"]

    def test_dedupe_and_empty(self):
        assert parse_pool("a，a， b，, ") == ["a", "b"]
        assert parse_pool("") == []

    def test_list_input(self):
        assert parse_pool([" x ", "y", "x"]) == ["x", "y"]


class TestPickRounds:
    def test_sample_no_repeat(self):
        pool = [f"问题{i}" for i in range(10)]
        picked = pick_rounds(pool, 5, rng=random.Random(42))
        assert len(picked) == 5 and len(set(picked)) == 5
        assert set(picked) <= set(pool)

    def test_pad_when_pool_small(self):
        pool = ["a", "b"]
        picked = pick_rounds(pool, 5, rng=random.Random(1))
        assert len(picked) == 5
        assert set(pool) <= set(picked)  # 小池先全量洗牌再循环补齐

    def test_clamp_and_empty(self):
        assert len(pick_rounds(["a"], 0)) == 1
        assert len(pick_rounds(["a"], 999)) == 50  # 上限 50
        assert pick_rounds([], 3) == []


class TestStyleHint:
    def test_known_style(self):
        assert "亲历者" in style_hint("解读·经验分享")

    def test_unknown_style_passthrough(self):
        assert "深度科普" in style_hint("深度科普")

    def test_empty(self):
        assert style_hint(None) == "" and style_hint("") == ""


class TestAnalyzeParsing:
    def test_numbered_list(self):
        text = "1. 汽车空调散热器更换多少钱\n2. 空调不凉怎么判断缺氟\n3. 保养周期是多久"
        assert parse_analyzed_questions(text) == [
            "汽车空调散热器更换多少钱", "空调不凉怎么判断缺氟", "保养周期是多久"]

    def test_fenced_and_noise(self):
        text = "```\n1. 问题甲\n2. 问题乙\n```\n好的，以上是问题。"
        assert parse_analyzed_questions(text) == ["问题甲", "问题乙"]

    def test_prompt_contains_query_and_count(self):
        p = analyze_prompt("空调不凉了怎么办", 15)
        assert "空调不凉了怎么办" in p and "15 个" in p


class TestComboSection:
    def _task(self, **kw):
        base = dict(query="散热器多少钱", source_query="我有一台2017年的吉普指南者…",
                    supplement_question="散热器多少钱", gen_style="解读·经验分享",
                    gen_category="汽车")
        base.update(kw)
        return SimpleNamespace(**base)

    def test_section_injected_into_instructions(self):
        sec = _build_combo_section(self._task())
        assert "吉普指南者" in sec and "解读·经验分享" in sec and "汽车" in sec
        msg = _compose_message("t1", "散热器多少钱", "general", "D", "P", "I",
                               [], sec)
        assert "组合创作上下文" in msg and "吉普指南者" in msg

    def test_plain_task_no_section(self):
        assert _build_combo_section(
            self._task(source_query=None, gen_style=None, gen_category=None)) == ""
        msg = _compose_message("t1", "普通query", "general", "D", "P", "I", [])
        assert "组合创作上下文" not in msg
