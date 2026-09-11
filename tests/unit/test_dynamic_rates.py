"""动态费率核算 + 余额接口（2026-09-09）：峰谷边界、缓存命中计费、DB 覆盖生效、
余额接口容错。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import text

from src.db.session import SessionLocal
from src.gateway import cost_tracker
from src.gateway.cost_tracker import estimate_cost, is_peak_time, refresh_rates

_BJ = timezone(timedelta(hours=8))


def _bj(y, m, d, hh, mm=0):
    """构造北京时间时刻。"""
    return datetime(y, m, d, hh, mm, tzinfo=_BJ)


# ── 高峰/空闲判定边界（北京时间工作日 9:00-12:00、14:00-18:00） ──

def test_peak_boundary_morning():
    assert is_peak_time(_bj(2026, 9, 7, 9, 0))       # 周一 9:00 整 → 高峰
    assert is_peak_time(_bj(2026, 9, 7, 11, 59))     # 周一 11:59 → 高峰
    assert not is_peak_time(_bj(2026, 9, 7, 12, 0))  # 周一 12:00 整 → 空闲
    assert not is_peak_time(_bj(2026, 9, 7, 8, 59))  # 周一 8:59 → 空闲


def test_peak_boundary_afternoon_and_friday_evening():
    assert not is_peak_time(_bj(2026, 9, 7, 13, 59))
    assert is_peak_time(_bj(2026, 9, 7, 14, 0))
    assert is_peak_time(_bj(2026, 9, 11, 17, 59))    # 周五 17:59 → 高峰
    assert not is_peak_time(_bj(2026, 9, 11, 18, 0))  # 周五 18:00 整 → 空闲


def test_peak_weekend_always_offpeak():
    assert not is_peak_time(_bj(2026, 9, 12, 10, 0))  # 周六上午 → 空闲
    assert not is_peak_time(_bj(2026, 9, 13, 15, 0))  # 周日下午 → 空闲


def test_peak_judgement_is_beijing_not_server_local():
    # 同一 UTC 时刻（周一 UTC 02:00 = 北京 10:00）无论从何 tz 传入都应判高峰
    assert is_peak_time(datetime(2026, 9, 7, 2, 0, tzinfo=timezone.utc))


# ── 缓存命中计费 ──

PEAK = _bj(2026, 9, 7, 10, 0)
OFFPEAK = _bj(2026, 9, 13, 10, 0)  # 周日


def test_cache_hit_billing_peak():
    # 1M 命中(0.30) + 1M 未命中(9.0)，输出 0
    cost = estimate_cost("deepseek/deepseek-chat", 2_000_000, 0,
                         cache_hit_tokens=1_000_000, at_time=PEAK)
    assert abs(cost - (0.30 + 9.0)) < 0.001


def test_cache_hit_billing_offpeak_half_price():
    # 空闲时段整体半价（0.5）
    cost = estimate_cost("deepseek/deepseek-chat", 2_000_000, 1_000_000,
                         cache_hit_tokens=1_000_000, at_time=OFFPEAK)
    assert abs(cost - (0.30 + 9.0 + 27.0) * 0.5) < 0.001


def test_cache_hit_none_means_all_miss():
    cost = estimate_cost("deepseek/deepseek-chat", 1_000_000, 0,
                         cache_hit_tokens=None, at_time=PEAK)
    assert abs(cost - 9.0) < 0.001


def test_cache_hit_clamped_to_prompt_tokens():
    # 上游拆分异常（hit > prompt）时按全命中截断，不超计
    cost = estimate_cost("deepseek/deepseek-chat", 1_000_000, 0,
                         cache_hit_tokens=5_000_000, at_time=PEAK)
    assert abs(cost - 0.30) < 0.001


def test_no_offpeak_discount_for_kimi():
    a = estimate_cost("k3", 1_000_000, 1_000_000, at_time=PEAK)
    b = estimate_cost("k3", 1_000_000, 1_000_000, at_time=OFFPEAK)
    assert abs(a - b) < 0.001  # offpeak_ratio=1.0 → 峰谷同价


# ── 费率 DB 覆盖生效（改库即生效，无需重启） ──

async def test_rate_db_override_takes_effect():
    async with SessionLocal() as session:
        await session.execute(text(
            "UPDATE model_rates SET input_miss_peak = 99.0"
            " WHERE model_key = 'deepseek-v4-pro'"))
        await session.commit()
    try:
        await refresh_rates(force=True)
        cost = estimate_cost("deepseek/deepseek-chat", 1_000_000, 0, at_time=PEAK)
        assert abs(cost - 99.0) < 0.001
    finally:
        async with SessionLocal() as session:
            await session.execute(text(
                "UPDATE model_rates SET input_miss_peak = 9.0"
                " WHERE model_key = 'deepseek-v4-pro'"))
            await session.commit()
        await refresh_rates(force=True)
    assert abs(estimate_cost("deepseek/deepseek-chat", 1_000_000, 0,
                             at_time=PEAK) - 9.0) < 0.001


def test_code_default_fallback_when_cache_empty():
    # 缓存为空（如 DB 未迁移）时走代码兜底，与种子同值
    saved = dict(cost_tracker._rates_cache)
    cost_tracker._rates_cache.clear()
    try:
        cost = estimate_cost("deepseek/deepseek-chat", 1_000_000, 0, at_time=PEAK)
        assert abs(cost - 9.0) < 0.001
    finally:
        cost_tracker._rates_cache.update(saved)


# ── 余额接口容错（DeepSeek API 失败不炸，给错误标记） ──

async def test_balance_api_failure_tolerated():
    from src.api.admin import account_balance
    with patch("src.api.admin._fetch_deepseek_balance",
               new=AsyncMock(return_value={"ok": False, "error": "DeepSeek 接口 HTTP 401"})):
        r = await account_balance()
    assert r["deepseek"]["ok"] is False
    assert "error" in r["deepseek"]
    assert set(r["ledger"]) == {"total_cny", "last_24h_cny", "last_7d_cny",
                                "daily_avg_7d_cny"}
    assert r["est_available_days"] is None
    # 无公开余额 API 的厂商：未录入基准时给错误标记与口径说明
    for p in ("fusion", "kimi"):
        assert r[p]["ok"] is False and r[p]["note"] and "error" in r[p]


async def test_balance_no_est_days_without_consumption():
    from src.api.admin import account_balance
    with patch("src.api.admin._fetch_deepseek_balance", new=AsyncMock(return_value={
            "ok": True, "currency": "CNY", "total_balance": 50.0,
            "granted_balance": 10.0, "topped_up_balance": 40.0,
            "fetched_at": "2026-09-09T00:00:00+00:00"})):
        r = await account_balance()
    assert r["deepseek"]["total_balance"] == 50.0
    # 测试库无计费事件 → 日均 0 → 无法估算可用天数
    if r["ledger"]["daily_avg_7d_cny"] == 0:
        assert r["est_available_days"] is None


async def test_balance_response_never_leaks_key():
    from src.api.admin import account_balance
    from src.config import settings
    with patch("src.api.admin._fetch_deepseek_balance", new=AsyncMock(return_value={
            "ok": False, "error": "余额拉取失败：ConnectError"})):
        r = await account_balance()
    assert settings.deepseek_api_key not in str(r)
