"""生图分通道计费 + 费用四类拆分 + 余额基准扣减估算 + 迁移 021 幂等（2026-09-09）。"""
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
from sqlalchemy import text

from src.config import settings
from src.db.session import SessionLocal
from src.gateway.cost_tracker import (classify_category, per_call_cost,
                                      refresh_rates, tool_kind_category)


# ── 分通道按次费率 ──

def test_per_call_channel_rates():
    assert per_call_cost("gpt-image-2@fusion") == 0.2   # 账单实证
    assert per_call_cost("gpt-image-2@linkai") == 0.2   # 参照 fusion 待校准
    assert per_call_cost("gpt-image-2@moacode") == 0.2
    assert per_call_cost("gpt-image-2@openox") == 0.2
    # 无通道后缀 → 基准行；未识别模型 → fallback 全局兜底
    assert per_call_cost("gpt-image-2") == 0.2
    # 新默认模型 gpt-image-2.5-flare（025）
    assert per_call_cost("gpt-image-2.5-flare@fusion") == 0.2
    assert per_call_cost("gpt-image-2.5-flare@linkai") == 0.2
    assert per_call_cost("gpt-image-2.5-flare") == 0.2
    assert per_call_cost("gpt-image-2.5-sunburst@moacode") == 0.2
    assert per_call_cost("totally-unknown", fallback=0.35) == 0.35


async def test_channel_rate_db_override():
    async with SessionLocal() as session:
        await session.execute(text(
            "UPDATE model_rates SET per_call_cny = 0.15"
            " WHERE model_key = 'gpt-image-2@fusion'"))
        await session.commit()
    try:
        await refresh_rates(force=True)
        assert per_call_cost("gpt-image-2@fusion") == 0.15
        assert per_call_cost("gpt-image-2@linkai") == 0.2  # 其他通道不受影响
    finally:
        async with SessionLocal() as session:
            await session.execute(text(
                "UPDATE model_rates SET per_call_cny = 0.2"
                " WHERE model_key = 'gpt-image-2@fusion'"))
            await session.commit()
        await refresh_rates(force=True)


# ── 费用四类拆分归类 ──

def test_classify_category_by_node():
    assert classify_category("asset_gen") == "image_gen"
    assert classify_category("asset_regen") == "image_gen"
    assert classify_category("ocr_read") == "ocr"
    assert classify_category("entity_bind") == "search"
    assert classify_category("ref_collect") == "search"
    assert classify_category("draft_gen", "dsh:deepseek-v4-pro") == "text_llm"


def test_classify_category_by_model_fallback():
    assert classify_category("some_node", "qwen-vl-ocr") == "ocr"
    assert classify_category("some_node", "gpt-image-2@fusion") == "image_gen"
    assert classify_category("some_node", "gpt-image-2.5-flare@fusion") == "image_gen"
    assert classify_category("some_node", "nanobot:dsh:k3") == "text_llm"
    assert classify_category("some_node", None) == "text_llm"


def test_tool_kind_category_mapping():
    assert tool_kind_category("web_search") == "search"
    assert tool_kind_category("image_search") == "search"
    assert tool_kind_category("generate_images") == "image_gen"
    assert tool_kind_category("ocr") == "ocr"
    assert tool_kind_category("draft_write") == "text_llm"   # LLM 类能力工具
    assert tool_kind_category("rule_check") == "text_llm"    # 校验类工具


# ── 迁移 021 幂等 ──

def _dsn() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


async def test_migration_021_idempotent_and_guards_admin_edits():
    sql = (Path(__file__).resolve().parents[2]
           / "migrations" / "021_channel_rates_and_balance_baselines.sql").read_text(encoding="utf-8")
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(sql)
        await conn.execute(sql)  # 重复应用不炸
        v = await conn.fetchval(
            "SELECT per_call_cny FROM model_rates"
            " WHERE model_key = 'gpt-image-2@fusion'")
        assert v == 0.2
        # 旧种子 0.4 存量修正守卫：0.4 → 0.2
        await conn.execute(
            "UPDATE model_rates SET per_call_cny = 0.4 WHERE model_key = 'gpt-image-2'")
        await conn.execute(sql)
        v = await conn.fetchval(
            "SELECT per_call_cny FROM model_rates WHERE model_key = 'gpt-image-2'")
        assert v == 0.2
        # 管理员改过的值（非 0.4）重跑迁移不被覆盖
        await conn.execute(
            "UPDATE model_rates SET per_call_cny = 0.33 WHERE model_key = 'gpt-image-2'")
        await conn.execute(sql)
        v = await conn.fetchval(
            "SELECT per_call_cny FROM model_rates WHERE model_key = 'gpt-image-2'")
        assert v == 0.33
        # 还原基准价 + 刷新缓存，避免影响其他用例
        await conn.execute(
            "UPDATE model_rates SET per_call_cny = 0.2 WHERE model_key = 'gpt-image-2'")
    finally:
        await conn.close()
    await refresh_rates(force=True)


# ── 余额基准扣减估算 ──

async def _mk_task() -> str:
    tid = str(uuid.uuid4())
    async with SessionLocal() as session:
        await session.execute(text(
            "INSERT INTO tasks (id, idempotency_key, query, content_type)"
            " VALUES (:id, :k, 'q', 'general')"), {"id": tid, "k": uuid.uuid4().hex})
        await session.commit()
    return tid


async def _mk_node_event(task_id: str, node: str, model: str | None,
                         cost: float, finished: datetime):
    async with SessionLocal() as session:
        await session.execute(text(
            "INSERT INTO node_events (task_id, node_name, node_idempotency_key,"
            " enqueued_at, finished_at, model_version, cost_estimate_cny)"
            " VALUES (:t, :n, :k, :e, :f, :m, :c)"),
            {"t": task_id, "n": node, "k": uuid.uuid4().hex,
             "e": finished, "f": finished, "m": model, "c": cost})
        await session.commit()


async def test_balance_baseline_deduction_estimate():
    from src.api.admin import account_balance
    now = datetime.now(timezone.utc)
    tid = await _mk_task()
    # 基准时刻之后的消耗：生图 3.0（fusion 口径）+ k3 文本 1.0
    await _mk_node_event(tid, "asset_gen", None, 3.0, now - timedelta(hours=1))
    await _mk_node_event(tid, "rule_check", "dsh:k3", 1.0, now - timedelta(hours=1))
    # 基准时刻之前的生图消耗 5.0（不应计入扣减）
    baseline_at = now - timedelta(days=1)
    await _mk_node_event(tid, "asset_gen", None, 5.0, now - timedelta(days=2))
    async with SessionLocal() as session:
        await session.execute(text(
            "INSERT INTO balance_baselines (provider, balance_cny, recorded_at)"
            " VALUES ('fusion', 100.0, :t), ('kimi', 50.0, :t)"
            " ON CONFLICT (provider) DO UPDATE SET"
            " balance_cny = EXCLUDED.balance_cny,"
            " recorded_at = EXCLUDED.recorded_at"), {"t": baseline_at})
        await session.commit()
    try:
        with patch("src.api.admin._fetch_deepseek_balance",
                   new=AsyncMock(return_value={"ok": False, "error": "x"})):
            r = await account_balance()
        f, k = r["fusion"], r["kimi"]
        assert f["ok"] is True
        assert f["baseline_cny"] == 100.0
        assert f["consumed_since_cny"] == 3.0            # 基准前的 5.0 不计
        assert f["estimated_balance_cny"] == 97.0
        assert f["daily_avg_7d_cny"] == round(8.0 / 7, 4)  # 近7天生图 3+5
        assert f["est_available_days"] == round(97.0 / (8.0 / 7), 1)
        assert k["ok"] is True
        assert k["consumed_since_cny"] == 1.0            # 只计 k3 模型行
        assert k["estimated_balance_cny"] == 49.0
    finally:
        async with SessionLocal() as session:
            await session.execute(text("DELETE FROM balance_baselines"))
            await session.commit()


async def test_balance_baseline_put_validation():
    from fastapi import HTTPException
    from src.api.admin import BalanceBaselineIn, put_balance_baseline
    # 非 admin 拒绝
    try:
        await put_balance_baseline(BalanceBaselineIn(
            actor="nobody", provider="fusion", balance_cny=10))
        assert False, "should raise"
    except HTTPException as e:
        assert e.status_code in (401, 403)
    # 非法 provider / 负余额
    async with SessionLocal() as session:
        await session.execute(text(
            "INSERT INTO users (name, role, password_hash)"
            " VALUES ('t_admin', 'admin', 'x') ON CONFLICT DO NOTHING"))
        await session.commit()
    for kwargs in ({"provider": "deepseek", "balance_cny": 10},
                   {"provider": "fusion", "balance_cny": -1}):
        try:
            await put_balance_baseline(BalanceBaselineIn(actor="t_admin", **kwargs))
            assert False, "should raise"
        except HTTPException as e:
            assert e.status_code == 400
