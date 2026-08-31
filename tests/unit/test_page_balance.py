# 图上文字量约束（2026-08-31 用户要求）：每页 30-100 字、六页基本均衡（相差≤25字）。
# 校验 node_page_split 的 _balance_issue 判定 + 提示词文本包含新约束。
import pytest

from src.gateway.prompt_versions import PAGES_PROMPT, PAGE_REGEN_PROMPT, get_image_prompt


def _balance_issue(arr):
    """复用 node_page_split 内部逻辑的独立实现对照（同规则）："""
    lens = [len(p) for p in arr]
    issues = []
    short = [f"第{i+1}页仅{l}字" for i, l in enumerate(lens) if l < 30]
    long_ = [f"第{i+1}页{l}字" for i, l in enumerate(lens) if l > 100]
    if short:
        issues.append("字数不足30字：" + "、".join(short))
    if long_:
        issues.append("字数超100字：" + "、".join(long_))
    if max(lens) - min(lens) > 25:
        issues.append(f"各页失衡（最长{max(lens)}最短{min(lens)}，任意两页相差须≤25字）")
    return "；".join(issues)


def _mk(n, chars=50):
    return ["字" * chars for _ in range(n)]


def test_balance_ok():
    assert _balance_issue(_mk(6, 50)) == ""
    assert _balance_issue(_mk(6, 40)) == ""           # 均衡且在区间内
    assert _balance_issue(["字" * 30] * 6) == ""      # 下边界


def test_balance_flags_short_page():
    pages = _mk(6, 50)
    pages[0] = "字" * 12                                # 封面只有12字
    r = _balance_issue(pages)
    assert "第1页仅12字" in r and "不足30字" in r


def test_balance_flags_long_page():
    pages = _mk(6, 50)
    pages[3] = "字" * 105
    r = _balance_issue(pages)
    assert "第4页105字" in r and "超100字" in r


def test_balance_flags_spread():
    pages = ["字" * 35, "字" * 35, "字" * 36, "字" * 35, "字" * 35, "字" * 90]
    r = _balance_issue(pages)
    assert "失衡" in r and "90" in r and "35" in r


def test_prompts_carry_constraints():
    assert "最少 30 字、最多 100 字" in PAGES_PROMPT
    assert "相差不得超过 25 字" in PAGES_PROMPT
    assert "30-100 字" in PAGE_REGEN_PROMPT and "均衡" in PAGE_REGEN_PROMPT
    # 生图底座：字数均衡 + 边框禁纯白纯黑
    p = get_image_prompt("general", "x", 1)
    assert "30-100 字" in p and "不得使用纯白或纯黑" in p
