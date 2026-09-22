ABSOLUTE_WORDS = ["绝对", "100%", "最", "第一", "唯一", "永久", "终身"]
SAFETY_WORDS = ["安全", "无害", "无副作用", "治疗", "疗效", "保证"]
DISCLAIMER_PATTERNS = ["仅供参考", "不构成专业建议", "请咨询"]

# 页间相似度阈值（v0.1.4 P1.5，模板铁律4 信息去重的机检兜底）：
# 中文二元字组 Jaccard，无分词依赖。超阈值只告警（passed=False 落规则表，
# 供 risk_classify 汇总与人工审核参考），不硬拦流程。
PAGE_OVERLAP_THRESHOLD = 0.45


def _char_bigrams(s: str) -> set:
    s = "".join((s or "").split())
    if not s:
        return set()
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def check_page_overlap(pages: list, threshold: float = PAGE_OVERLAP_THRESHOLD):
    """页文案两两 Jaccard 相似度 → 最相似页对的规则结果 dict。

    有效页 <2 返回 None（不产生规则行）。页码从 1 计。
    """
    texts = [(p or "").strip() for p in pages if (p or "").strip()]
    if len(texts) < 2:
        return None
    grams = [_char_bigrams(t) for t in texts]
    best_pair, best_sim = None, 0.0
    for i in range(len(grams)):
        for j in range(i + 1, len(grams)):
            u, v = grams[i], grams[j]
            if not u or not v:
                continue
            sim = len(u & v) / len(u | v)
            if sim > best_sim:
                best_sim, best_pair = sim, (i + 1, j + 1)
    if best_pair is None:
        return None
    return {
        "rule_name": "page_overlap",
        "passed": best_sim < threshold,
        "details": {"max_jaccard": round(best_sim, 3),
                    "pages": list(best_pair),
                    "threshold": threshold},
    }


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
