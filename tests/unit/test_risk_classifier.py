from src.risk.classifier import classify


def test_all_passed_is_green():
    level, reasons = classify(
        rule_results=[{"passed": True}] * 5,
        cross_checks=[{"matched": True}] * 3,
        evidence_complete=True,
        has_p0_issue=False,
    )
    assert level == "green"
    assert reasons == []


def test_rule_failure_is_yellow():
    level, reasons = classify(
        rule_results=[{"passed": True}, {"passed": False, "rule_name": "no_absolute_words"}],
        cross_checks=[{"matched": True}] * 3,
        evidence_complete=True,
        has_p0_issue=False,
    )
    assert level == "yellow"
    assert "no_absolute_words" in reasons


def test_ocr_mismatch_is_red():
    level, reasons = classify(
        rule_results=[{"passed": True}] * 5,
        cross_checks=[{"matched": True}, {"matched": True}, {"matched": False}],
        evidence_complete=True,
        has_p0_issue=False,
    )
    assert level == "red"
    assert "ocr_mismatch" in reasons


def test_p0_issue_is_red():
    level, reasons = classify(
        rule_results=[{"passed": True}] * 5,
        cross_checks=[{"matched": True}] * 3,
        evidence_complete=True,
        has_p0_issue=True,
    )
    assert level == "red"
    assert "p0_issue" in reasons
