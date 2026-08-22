from src.quality.rules import check_rules


def _body(n_chars: int) -> str:
    # "正常" 每轮 2 字，凑够 n_chars
    return "正常内容" * (n_chars // 4 + 1)


def test_word_count_in_range():
    text = _body(500)
    results = check_rules(text, "测试标题")
    passed_names = [r["rule_name"] for r in results if r["passed"]]
    assert "word_count_400_700" in passed_names


def test_absolute_word_rejected():
    text = _body(500) + "这是绝对最好的产品。"
    results = check_rules(text, "测试标题")
    failed = next(r for r in results if r["rule_name"] == "no_absolute_words")
    assert failed["passed"] is False


def test_disclaimer_required_for_safety_words():
    text = "本产品安全无害。" + _body(500)
    results = check_rules(text, "测试标题")
    failed = next(r for r in results if r["rule_name"] == "has_disclaimer")
    assert failed["passed"] is False


def test_title_too_long_rejected():
    results = check_rules(_body(500), "这是一个超过二十五个字的标题啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊")
    failed = next(r for r in results if r["rule_name"] == "title_max_25")
    assert failed["passed"] is False
