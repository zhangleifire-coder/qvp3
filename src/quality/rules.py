ABSOLUTE_WORDS = ["绝对", "100%", "最", "第一", "唯一", "永久", "终身"]
SAFETY_WORDS = ["安全", "无害", "无副作用", "治疗", "疗效", "保证"]
DISCLAIMER_PATTERNS = ["仅供参考", "不构成专业建议", "请咨询"]


def check_rules(text: str, title: str) -> list[dict]:
    results = []
    char_count = len([c for c in text if c.strip()])
    results.append({
        "rule_name": "word_count_400_700",
        "passed": 400 <= char_count <= 700,
        "details": {"char_count": char_count},
    })
    results.append({
        "rule_name": "title_max_25",
        "passed": len(title) <= 25,
        "details": {"title_length": len(title)},
    })
    has_absolute = any(w in text for w in ABSOLUTE_WORDS)
    results.append({
        "rule_name": "no_absolute_words",
        "passed": not has_absolute,
        "details": {"found": [w for w in ABSOLUTE_WORDS if w in text]},
    })
    has_safety = any(w in text for w in SAFETY_WORDS)
    has_disclaimer = any(p in text for p in DISCLAIMER_PATTERNS)
    if has_safety:
        results.append({
            "rule_name": "has_disclaimer",
            "passed": has_disclaimer,
            "details": {"has_safety_words": True},
        })
    return results
