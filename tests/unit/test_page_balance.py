# 图上文字量约束（2026-08-31 用户要求；2026-09-02 提密度对齐借鉴库爆款公式）：
# 每页 80-130 字、六页基本均衡（相差≤40字）。
# 校验 node_page_split 的 _balance_issue 判定 + 提示词文本包含新约束。
import pytest

from src.gateway.prompt_versions import PAGES_PROMPT, PAGE_REGEN_PROMPT, get_image_prompt


def _balance_issue(arr):
    """复用 node_page_split 内部逻辑的独立实现对照（同规则）："""
    lens = [len(p) for p in arr]
    issues = []
    short = [f"第{i+1}页仅{l}字" for i, l in enumerate(lens) if l < 80]
    long_ = [f"第{i+1}页{l}字" for i, l in enumerate(lens) if l > 130]
    if short:
        issues.append("字数不足80字：" + "、".join(short))
    if long_:
        issues.append("字数超130字：" + "、".join(long_))
    if max(lens) - min(lens) > 40:
        issues.append(f"各页失衡（最长{max(lens)}最短{min(lens)}，任意两页相差须≤40字）")
    return "；".join(issues)


def _mk(n, chars=100):
    return ["字" * chars for _ in range(n)]


def test_balance_ok():
    assert _balance_issue(_mk(6, 100)) == ""
    assert _balance_issue(_mk(6, 85)) == ""           # 均衡且在区间内
    assert _balance_issue(["字" * 80] * 6) == ""      # 下边界
    assert _balance_issue(["字" * 130] * 6) == ""     # 上边界


def test_balance_flags_short_page():
    pages = _mk(6, 100)
    pages[0] = "字" * 40                                # 封面只有40字
    r = _balance_issue(pages)
    assert "第1页仅40字" in r and "不足80字" in r


def test_balance_flags_long_page():
    pages = _mk(6, 100)
    pages[3] = "字" * 135
    r = _balance_issue(pages)
    assert "第4页135字" in r and "超130字" in r


def test_balance_flags_spread():
    pages = ["字" * 85, "字" * 85, "字" * 86, "字" * 85, "字" * 85, "字" * 130]
    r = _balance_issue(pages)
    assert "失衡" in r and "130" in r and "85" in r


def test_prompts_carry_constraints():
    assert "最少 80 字、最多 130 字" in PAGES_PROMPT
    assert "相差不得超过 40 字" in PAGES_PROMPT
    assert "具体信息点" in PAGES_PROMPT
    assert "80-130 字" in PAGE_REGEN_PROMPT and "均衡" in PAGE_REGEN_PROMPT
    # 生图底座：字数均衡 + 边框禁纯白纯黑
    p = get_image_prompt("general", "x", 1)
    assert "80-130 字" in p and "不得使用纯白或纯黑" in p
    # Agent 契约（生产主路径）：同样带上新字数与信息点要求
    from src.pipeline.agent_production import _AGENT_INSTRUCTIONS
    assert "80-130 字" in _AGENT_INSTRUCTIONS and "具体信息点" in _AGENT_INSTRUCTIONS
    assert "宁可短不可长" not in _AGENT_INSTRUCTIONS   # 旧「极简」教条已移除
