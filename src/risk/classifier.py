def classify(rule_results: list, cross_checks: list,
             evidence_complete: bool, has_p0_issue: bool) -> tuple:
    reasons = []
    if has_p0_issue:
        reasons.append("p0_issue")
        return "red", reasons
    if any(not c["matched"] for c in cross_checks):
        reasons.append("ocr_mismatch")
        return "red", reasons
    if not evidence_complete:
        reasons.append("evidence_incomplete")
        return "yellow", reasons
    failed_rules = [r.get("rule_name", "unknown") for r in rule_results if not r["passed"]]
    if failed_rules:
        reasons.extend(failed_rules)
        return "yellow", reasons
    return "green", []
