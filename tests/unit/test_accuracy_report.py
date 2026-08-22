import pytest
from src.dashboard.accuracy_report import accuracy_report


@pytest.mark.asyncio
async def test_accuracy_report_returns_dict():
    report = await accuracy_report()
    assert "time_inconsistency_rate" in report
    assert "anomaly_rate" in report
