"""后台管理接口：工作周期状态、手动检修、导出记录、删除内容、重新开启、用户管理。"""
import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text

from src.db.session import SessionLocal
from src.models.tasks import Task
from src.services.activity import log_action
from src.stream.maintenance import cycle
from src.stream.progress import progress
from src.stream.scheduler import scheduler

router = APIRouter()

# 工作内容表（不含 users / organizations，删除时保留账号）
CONTENT_TABLES = [
    "tasks", "entity_snapshots", "claims", "evidence", "drafts", "page_copies",
    "assets", "ocr_results", "rule_results", "cross_checks",
    "risk_classifications", "review_sessions", "review_actions", "issues",
    "batches", "batch_members", "approvals", "publish_snapshots", "node_events",
    "reject_marks",
]


@router.get("/api/admin/status")
async def status():
    async with SessionLocal() as session:
        total = await session.execute(select(func.count(Task.id)))
        by_status = dict((await session.execute(
            select(Task.status, func.count(Task.id)).group_by(Task.status))).all())
    return {
        "cycle": cycle.snapshot(),
        "scheduler": scheduler.snapshot(),
        "tasks": {"total": total.scalar() or 0, "by_status": by_status},
    }


@router.post("/api/admin/maintenance/start")
async def start_maintenance(actor: str = "anonymous"):
    await log_action(actor, "maintenance_start", "进入手动检修（生产暂停）")
    return {"ok": True, "cycle": await cycle.enter_maintenance("manual")}


@router.post("/api/admin/maintenance/end")
async def end_maintenance(actor: str = "anonymous"):
    await log_action(actor, "maintenance_end", "结束检修，恢复生产")
    return {"ok": True, "cycle": await cycle.exit_maintenance("manual")}


@router.post("/api/admin/restart")
async def restart_work():
    """结束检修并重新开启工作任务。"""
    return {"ok": True, "cycle": await cycle.exit_maintenance("manual")}


@router.post("/api/admin/clear")
async def clear_work(actor: str = "anonymous"):
    """删除全部工作内容（保留账号），并清空队列内存状态。"""
    import asyncpg
    from src.config import settings
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = await asyncpg.connect(dsn)
        try:
            # 锁超时 10s，避免在有任务执行时无限挂起
            await conn.execute("SET lock_timeout = '10s'")
            await conn.execute(f"TRUNCATE TABLE {', '.join(CONTENT_TABLES)} CASCADE")
        finally:
            await conn.close()
        scheduler.clear()
        progress.clear()
        await log_action(actor, "admin_clear",
                         f"清空全部工作内容（{len(CONTENT_TABLES)} 张表）")
        return {"ok": True, "cleared_tables": len(CONTENT_TABLES)}
    except asyncpg.exceptions.LockNotAvailableError:
        return {"ok": False, "error": "仍有任务执行中，请等待任务完成后再删除"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@router.get("/api/admin/costs")
async def cost_details():
    """成本明细（管理员决策支持）：按任务 × 节点拆分的全部计费事件 + 多维汇总。

    数据源是 node_events.cost_estimate_cny（每次模型/搜索/生图调用的估算成本）。
    """
    from datetime import timedelta
    from src.models.events import NodeEvent
    from src.stream.progress import NODE_LABEL
    from src.gateway.cost_tracker import CATEGORY_LABELS, classify_category
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(NodeEvent.task_id, NodeEvent.node_name,
                   NodeEvent.cost_estimate_cny, NodeEvent.model_version,
                   NodeEvent.finished_at)
            .where(NodeEvent.cost_estimate_cny > 0)
            .order_by(NodeEvent.finished_at))).all()
        task_ids = list({r[0] for r in rows})
        tasks = {}
        if task_ids:
            tasks = {t.id: t for t in (await session.execute(
                select(Task).where(Task.id.in_(task_ids)))).scalars().all()}
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    per_task: dict = {}
    node_totals: dict = {}
    model_totals: dict = {}
    category_totals: dict = {}
    total = 0.0
    total_24h = 0.0
    for tid, node, cost, model, finished in rows:
        c = float(cost or 0)
        cat = classify_category(node, model)
        total += c
        if finished and finished >= day_ago:
            total_24h += c
        category_totals[cat] = category_totals.get(cat, 0.0) + c
        t = per_task.setdefault(tid, {"total": 0.0, "items": [], "by_category": {}})
        t["total"] += c
        t["by_category"][cat] = t["by_category"].get(cat, 0.0) + c
        t["items"].append({
            "node": node, "label": NODE_LABEL.get(node, node),
            "cost": round(c, 4), "model": model,
            "category": cat, "category_label": CATEGORY_LABELS[cat],
            "finished_at": finished.isoformat() if finished else None})
        nt = node_totals.setdefault(
            node, {"label": NODE_LABEL.get(node, node), "count": 0, "cost": 0.0})
        nt["count"] += 1
        nt["cost"] += c
        if model:
            model_totals[model] = model_totals.get(model, 0.0) + c
    task_list = []
    for tid, t in per_task.items():
        task = tasks.get(tid)
        task_list.append({
            "task_id": str(tid),
            "query": task.query if task else "(已清空任务)",
            "mode": task.mode if task else None,
            "status": task.status if task else None,
            "total": round(t["total"], 4),
            "by_category": [
                {"category": cat, "label": CATEGORY_LABELS[cat],
                 "cost": round(c, 4)}
                for cat, c in sorted(t["by_category"].items())],
            "items": t["items"],
        })
    task_list.sort(key=lambda x: -x["total"])
    return {
        "summary": {
            "total_cny": round(total, 4),
            "total_24h_cny": round(total_24h, 4),
            "task_count": len(task_list),
            "avg_per_task_cny": round(total / len(task_list), 4) if task_list else 0,
        },
        "by_category": [
            {"category": cat, "label": CATEGORY_LABELS[cat],
             "cost": round(category_totals.get(cat, 0.0), 4)}
            for cat in ("text_llm", "image_gen", "search", "ocr")],
        "by_node": sorted(({"node": k, "label": v["label"], "count": v["count"],
                            "cost": round(v["cost"], 4)}
                           for k, v in node_totals.items()),
                          key=lambda x: -x["cost"]),
        "by_model": sorted(({"model": k, "cost": round(v, 4)}
                            for k, v in model_totals.items()),
                           key=lambda x: -x["cost"]),
        "tasks": task_list,
    }


# ============ 费率与余额（2026-09-09，迁移 020） ============

from src.gateway.cost_tracker import DEFAULT_RATES, refresh_rates


@router.get("/api/admin/rates")
async def get_rates():
    """费率表（model_rates）全量查看；DB 不可用时回退代码兜底值并标记。"""
    await refresh_rates(force=True)
    try:
        async with SessionLocal() as session:
            rows = (await session.execute(text(
                "SELECT model_key, label, input_hit_peak, input_miss_peak,"
                " output_peak, offpeak_ratio, per_call_cny, updated_at"
                " FROM model_rates ORDER BY model_key"))).all()
        return {"source": "db", "rates": [
            {"model_key": r[0], "label": r[1], "input_hit_peak": r[2],
             "input_miss_peak": r[3], "output_peak": r[4],
             "offpeak_ratio": r[5], "per_call_cny": r[6],
             "updated_at": r[7].isoformat() if r[7] else None}
            for r in rows]}
    except Exception as e:  # noqa: BLE001
        return {"source": "code_default", "error": str(e), "rates": [
            {"model_key": k, **v, "updated_at": None}
            for k, v in sorted(DEFAULT_RATES.items())]}


class RateRow(BaseModel):
    model_key: str
    label: str = ""
    input_hit_peak: float = 0
    input_miss_peak: float = 0
    output_peak: float = 0
    offpeak_ratio: float = 1.0
    per_call_cny: float = 0


class RatesIn(BaseModel):
    actor: str
    rates: list[RateRow]


@router.put("/api/admin/rates")
async def update_rates(payload: RatesIn):
    """逐行 upsert 费率表（仅 admin），保存即生效（强制刷新进程内缓存）。"""
    await _require_admin(payload.actor)
    for r in payload.rates:
        if not r.model_key.strip():
            raise HTTPException(status_code=400, detail="model_key 不能为空")
        for f in ("input_hit_peak", "input_miss_peak", "output_peak",
                  "offpeak_ratio", "per_call_cny"):
            if getattr(r, f) < 0:
                raise HTTPException(status_code=400,
                                    detail=f"{r.model_key} 的 {f} 不能为负")
    async with SessionLocal() as session:
        for r in payload.rates:
            await session.execute(text(
                "INSERT INTO model_rates (model_key, label, input_hit_peak,"
                " input_miss_peak, output_peak, offpeak_ratio, per_call_cny,"
                " updated_at) VALUES (:k, :l, :ih, :im, :o, :r, :p, now())"
                " ON CONFLICT (model_key) DO UPDATE SET"
                " label=:l, input_hit_peak=:ih, input_miss_peak=:im,"
                " output_peak=:o, offpeak_ratio=:r, per_call_cny=:p,"
                " updated_at=now()"),
                {"k": r.model_key.strip(), "l": r.label, "ih": r.input_hit_peak,
                 "im": r.input_miss_peak, "o": r.output_peak,
                 "r": r.offpeak_ratio, "p": r.per_call_cny})
        await session.commit()
    await refresh_rates(force=True)
    await log_action(payload.actor, "rates_update",
                     f"更新费率表（{len(payload.rates)} 行："
                     f"{', '.join(r.model_key for r in payload.rates)}）")
    return {"ok": True, "updated": len(payload.rates)}


async def _fetch_deepseek_balance() -> dict:
    """实拉 DeepSeek 账户余额（GET /user/balance）；失败返回错误标记，密钥不外泄。"""
    from src.config import settings
    from src.gateway.http_client import get_client
    if not settings.deepseek_api_key or settings.deepseek_api_key.startswith("sk-xxx"):
        return {"ok": False, "error": "未配置 DeepSeek API Key"}
    try:
        # 共享 client 承载连接池（P1-7）
        resp = await get_client("admin_balance", timeout=10.0).get(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}"})
        if resp.status_code != 200:
            return {"ok": False, "error": f"DeepSeek 接口 HTTP {resp.status_code}"}
        data = resp.json()
        infos = data.get("balance_infos") or []
        cny = next((b for b in infos if b.get("currency") == "CNY"),
                   infos[0] if infos else {})
        return {
            "ok": True,
            "currency": cny.get("currency"),
            "total_balance": float(cny.get("total_balance") or 0),
            "granted_balance": float(cny.get("granted_balance") or 0),
            "topped_up_balance": float(cny.get("topped_up_balance") or 0),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"余额拉取失败：{type(e).__name__}"}


async def _fetch_kimi_balance() -> dict:
    """实拉 Kimi (Moonshot) 账户余额（GET /v1/users/me/balance）；失败返回错误标记。"""
    from src.config import settings
    from src.gateway.http_client import get_client
    key = settings.kimi_api_key
    if not key or key.startswith("sk-xxx"):
        return {"ok": False, "error": "未配置 Kimi API Key"}
    try:
        resp = await get_client("admin_balance", timeout=10.0).get(
            "https://api.moonshot.cn/v1/users/me/balance",
            headers={"Authorization": f"Bearer {key}"})
        if resp.status_code != 200:
            return {"ok": False, "error": f"Kimi 接口 HTTP {resp.status_code}"}
        data = resp.json()
        d = data.get("data") or {}
        return {
            "ok": True,
            "currency": "CNY",
            "available_balance": float(d.get("available_balance") or 0),
            "voucher_balance": float(d.get("voucher_balance") or 0),
            "cash_balance": float(d.get("cash_balance") or 0),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"余额拉取失败：{type(e).__name__}"}


# 支持手工录入/校准余额的厂商（有公开 API 的厂商，手工值优先于 API 实拉值展示）
_MANUAL_BALANCE_PROVIDERS = {
    "deepseek": "DeepSeek：可手工校准余额；未校准时自动实拉 /user/balance",
    "kimi": "Kimi：可手工校准余额；未校准时自动实拉 /v1/users/me/balance",
    "fusion": "FusionAI：无公开余额 API，必须手工录入控制台余额作为估算基准",
}


async def _fetch_manual_balance(provider: str) -> dict | None:
    """从 balance_baselines 读取某厂商的手工余额校准记录。"""
    async with SessionLocal() as session:
        row = (await session.execute(text(
            "SELECT balance_cny, recorded_at, updated_by"
            " FROM balance_baselines WHERE provider = :p"),
            {"p": provider})).first()
    if not row:
        return None
    return {
        "balance_cny": round(float(row[0]), 4),
        "recorded_at": row[1].isoformat() if row[1] else None,
        "updated_by": row[2],
    }


def _provider_consumption(rows, provider: str, since) -> float:
    """rows: (node_name, model_version, cost, finished_at)；按 provider 口径累计
    since 之后的台账消耗。fusion=生图类别；kimi=模型归一为 k3 的行。"""
    from src.gateway.cost_tracker import classify_category, resolve_model_key
    total = 0.0
    for node, model, cost, finished in rows:
        if finished and finished < since:
            continue
        c = float(cost or 0)
        if provider == "fusion" and classify_category(node, model) == "image_gen":
            total += c
        elif provider == "kimi" and model and resolve_model_key(model) in ("k3", "kimi-k2.6"):
            total += c
    return total


async def _provider_estimate(provider: str, rows, daily_avg: float) -> dict:
    """基准点 + 台账扣减 → 估算当前余额；未录入基准时给错误标记。"""
    async with SessionLocal() as session:
        row = (await session.execute(text(
            "SELECT balance_cny, recorded_at, updated_by"
            " FROM balance_baselines WHERE provider = :p"),
            {"p": provider})).first()
    base = {"ok": False, "provider": provider,
            "note": _MANUAL_BALANCE_PROVIDERS[provider],
            "daily_avg_7d_cny": round(daily_avg, 4)}
    if not row:
        return {**base, "error": "未录入余额基准（请从厂商控制台抄录后保存）"}
    balance_cny, recorded_at, _by = float(row[0]), row[1], row[2]
    consumed = _provider_consumption(rows, provider, recorded_at)
    estimated = round(balance_cny - consumed, 4)
    return {**base,
            "ok": True,
            "baseline_cny": round(balance_cny, 4),
            "recorded_at": recorded_at.isoformat() if recorded_at else None,
            "consumed_since_cny": round(consumed, 4),
            "estimated_balance_cny": estimated,
            "est_available_days": (round(estimated / daily_avg, 1)
                                   if daily_avg > 0 else None)}


class BalanceBaselineIn(BaseModel):
    actor: str
    provider: str
    balance_cny: float
    recorded_at: datetime | None = None  # 缺省=保存时刻


@router.put("/api/admin/balance_baseline")
async def put_balance_baseline(payload: BalanceBaselineIn):
    """手工录入/校准厂商余额（DeepSeek / Kimi / FusionAI）。

    有公开余额 API 的厂商（deepseek/kimi），手工值会优先展示；
    FusionAI 无公开 API，手工值作为估算基准。
    """
    await _require_admin(payload.actor)
    provider = payload.provider.strip().lower()
    if provider not in _MANUAL_BALANCE_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"provider 必须是 {'/'.join(_MANUAL_BALANCE_PROVIDERS)}")
    if payload.balance_cny < 0:
        raise HTTPException(status_code=400, detail="balance_cny 不能为负")
    recorded_at = payload.recorded_at or datetime.now(timezone.utc)
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    async with SessionLocal() as session:
        await session.execute(text(
            "INSERT INTO balance_baselines (provider, balance_cny, recorded_at,"
            " updated_by) VALUES (:p, :b, :t, :u)"
            " ON CONFLICT (provider) DO UPDATE SET"
            " balance_cny=:b, recorded_at=:t, updated_by=:u"),
            {"p": provider, "b": payload.balance_cny, "t": recorded_at,
             "u": payload.actor})
        await session.commit()
    await log_action(payload.actor, "balance_baseline",
                     f"校准 {provider} 余额为 ¥{payload.balance_cny:.2f}"
                     f"（{recorded_at.isoformat()}）")
    return {"ok": True, "provider": provider,
            "balance_cny": payload.balance_cny,
            "recorded_at": recorded_at.isoformat()}


@router.get("/api/admin/balance")
async def account_balance():
    """账户余额掌握：DeepSeek / Kimi 真实余额（API 实拉，失败给错误标记）；
    FusionAI 无公开余额 API → 基准录入 + 台账类别消耗扣减估算；
    附本地台账累计、按近 7 天日均的预计可用天数，以及关键模型单次计费口径。"""
    from datetime import timedelta
    from src.models.events import NodeEvent
    from src.gateway.cost_tracker import resolve_model_key
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        total = (await session.execute(select(
            func.coalesce(func.sum(NodeEvent.cost_estimate_cny), 0)))).scalar() or 0
        last_24h = (await session.execute(select(
            func.coalesce(func.sum(NodeEvent.cost_estimate_cny), 0))
            .where(NodeEvent.finished_at >= now - timedelta(hours=24)))).scalar() or 0
        last_7d = (await session.execute(select(
            func.coalesce(func.sum(NodeEvent.cost_estimate_cny), 0))
            .where(NodeEvent.finished_at >= now - timedelta(days=7)))).scalar() or 0
        rows = (await session.execute(
            select(NodeEvent.node_name, NodeEvent.model_version,
                   NodeEvent.cost_estimate_cny, NodeEvent.finished_at)
            .where(NodeEvent.cost_estimate_cny > 0))).all()
        # 关键单次计费口径（展示用，不用于实际扣费）
        fusion_per_call = (
            await session.execute(text(
                "SELECT per_call_cny FROM model_rates WHERE model_key='gpt-image-2.5-flare@fusion'"))
        ).scalar()
        if fusion_per_call is None:
            fusion_per_call = (
                await session.execute(text(
                    "SELECT per_call_cny FROM model_rates WHERE model_key='gpt-image-2.5-flare'"))
            ).scalar() or 0
        kimi_rate_row = (await session.execute(text(
            "SELECT input_hit_peak, output_peak FROM model_rates WHERE model_key='kimi-k2.6'"))).first() or (0, 0)
    daily_avg = float(last_7d) / 7
    ds = await _fetch_deepseek_balance()
    ds["manual_balance"] = await _fetch_manual_balance("deepseek")
    display_ds = (ds["manual_balance"]["balance_cny"]
                  if ds.get("manual_balance")
                  else (ds.get("total_balance") if ds.get("ok") else 0))
    est_days = (round(display_ds / daily_avg, 1)
                if display_ds and daily_avg > 0 else None)
    ds["daily_avg_7d_cny"] = round(daily_avg, 4)
    ds["est_available_days"] = est_days
    # 各 provider 的近 7 天日均消耗（同类口径）：用于估算可用天数
    week_rows = [(n, m, c, f) for n, m, c, f in rows
                 if f and f >= now - timedelta(days=7)]
    fusion_avg = _provider_consumption(week_rows, "fusion",
                                       now - timedelta(days=7)) / 7
    kimi_avg = sum(float(c or 0) for n, m, c, f in week_rows
                   if m and resolve_model_key(m) in ("k3", "kimi-k2.6")) / 7
    fusion = await _provider_estimate("fusion", rows, fusion_avg)
    fusion["manual_balance"] = await _fetch_manual_balance("fusion")
    fusion["rate"] = {"model": "gpt-image-2.5-flare", "per_call_cny": round(float(fusion_per_call), 4)}
    kimi = await _fetch_kimi_balance()
    kimi["manual_balance"] = await _fetch_manual_balance("kimi")
    if kimi.get("ok"):
        kimi["daily_avg_7d_cny"] = round(kimi_avg, 4)
        display_kimi = (kimi["manual_balance"]["balance_cny"]
                        if kimi.get("manual_balance")
                        else kimi["available_balance"])
        kimi["est_available_days"] = (
            round(display_kimi / kimi_avg, 1)
            if kimi_avg > 0 else None)
        kimi["rate"] = {
            "model": "kimi-k2.6",
            "input_hit_peak": round(float(kimi_rate_row[0]), 4),
            "output_peak": round(float(kimi_rate_row[1]), 4),
        }
    return {
        "deepseek": ds,
        "fusion": fusion,
        "kimi": kimi,
        "ledger": {
            "total_cny": round(float(total), 4),
            "last_24h_cny": round(float(last_24h), 4),
            "last_7d_cny": round(float(last_7d), 4),
            "daily_avg_7d_cny": round(daily_avg, 4),
        },
        "est_available_days": est_days,
    }


@router.get("/api/admin/export")
async def export_records(actor: str = "anonymous"):
    """导出工作记录为 CSV（任务 + 正文长度 + 图/证据数 + 成本 + 风险）。"""
    from src.models.drafts import Draft
    from src.models.entities import Claim, Evidence
    from src.models.assets import Asset
    from src.models.events import NodeEvent
    from src.models.review import RiskClassification

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["task_id", "query", "content_type", "status", "risk_level",
                     "draft_chars", "assets", "evidence", "node_events", "cost_cny",
                     "model_version", "created_at"])

    async with SessionLocal() as session:
        tasks = (await session.execute(select(Task).order_by(Task.created_at))).scalars().all()
        for t in tasks:
            draft = (await session.execute(
                select(Draft).where(Draft.task_id == t.id)
                .order_by(Draft.version.desc()))).scalars().first()
            assets = (await session.execute(
                select(func.count(Asset.id)).where(Asset.task_id == t.id))).scalar() or 0
            evidence = (await session.execute(
                select(func.count(Evidence.id)).where(
                    Evidence.claim_id.in_(select(Claim.id).where(Claim.task_id == t.id))))).scalar() or 0
            node_count = (await session.execute(
                select(func.count(NodeEvent.id)).where(NodeEvent.task_id == t.id))).scalar() or 0
            cost = (await session.execute(
                select(func.coalesce(func.sum(NodeEvent.cost_estimate_cny), 0))
                .where(NodeEvent.task_id == t.id))).scalar() or 0
            risk = (await session.execute(
                select(RiskClassification).where(RiskClassification.task_id == t.id))).scalars().first()
            writer.writerow([
                str(t.id), t.query, t.content_type, t.status,
                risk.level if risk else "",
                len(draft.body) if draft else 0,
                assets, evidence, node_count,
                float(cost) if cost else 0,
                draft.model_version if draft else "",
                t.created_at.isoformat() if t.created_at else "",
            ])

    csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
    filename = f"工作记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    await log_action(actor, "admin_export", "导出工作记录 CSV")
    from urllib.parse import quote
    content_disposition = (
        "attachment; filename=work_records.csv; "
        f"filename*=UTF-8''{quote(filename)}")
    return StreamingResponse(
        iter([csv_bytes]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition})


@router.get("/api/admin/logs")
async def get_logs():
    """返回运行日志（内存缓冲，最近 3000 条）。"""
    return {"count": len(progress.log), "log": progress.get_log()}


@router.get("/api/admin/logs/download")
async def download_logs():
    """下载运行日志为文本文件。"""
    from urllib.parse import quote
    content = "\n".join(progress.get_log())
    text_bytes = ("\ufeff" + content).encode("utf-8")
    filename = f"运行日志_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    content_disposition = (
        "attachment; filename=run_log.txt; "
        f"filename*=UTF-8''{quote(filename)}")
    return StreamingResponse(
        iter([text_bytes]), media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": content_disposition})


# ============ 用户管理（仅 admin，2026-08-20） ============

_VALID_ROLES = ("A", "B", "C", "admin")


async def _require_admin(actor: str):
    """校验 actor 是在职管理员，返回其用户 id。"""
    if not actor:
        raise HTTPException(status_code=401, detail="缺少 actor 参数")
    async with SessionLocal() as session:
        row = (await session.execute(
            text("SELECT id, role, active FROM users WHERE name = :n"),
            {"n": actor})).first()
    if not row:
        raise HTTPException(status_code=401, detail=f"用户不存在: {actor}")
    if row[1] != "admin" or not row[2]:
        raise HTTPException(status_code=403, detail="仅管理员可访问用户管理")
    return row[0]


@router.get("/api/admin/users")
async def list_users(actor: str):
    await _require_admin(actor)
    async with SessionLocal() as session:
        rows = (await session.execute(text(
            "SELECT id, name, role, active, created_at FROM users ORDER BY created_at"))).all()
    return {"users": [
        {"id": str(r[0]), "name": r[1], "role": r[2], "active": r[3],
         "created_at": r[4].isoformat() if r[4] else None}
        for r in rows]}


class UserIn(BaseModel):
    actor: str
    name: str
    password: str
    role: str = "A"


@router.post("/api/admin/users")
async def create_user(payload: UserIn):
    await _require_admin(payload.actor)
    name = payload.name.strip()
    if not name or not payload.password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    if payload.role not in _VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"角色必须是 {'/'.join(_VALID_ROLES)}")
    from src.api.auth import hash_password
    async with SessionLocal() as session:
        exists = await session.execute(
            text("SELECT 1 FROM users WHERE name = :n"), {"n": name})
        if exists.first():
            raise HTTPException(status_code=400, detail="用户名已存在")
        r = await session.execute(text(
            "INSERT INTO users (name, role, password_hash) VALUES (:n, :r, :p) RETURNING id"),
            {"n": name, "r": payload.role, "p": hash_password(payload.password)})
        await session.commit()
        await log_action(payload.actor, "user_create",
                         f"新建用户 {name}（角色 {payload.role}）")
        return {"ok": True, "id": str(r.scalar()), "name": name, "role": payload.role}


class UserUpdate(BaseModel):
    actor: str
    name: str | None = None
    password: str | None = None
    role: str | None = None
    active: bool | None = None


async def _count_other_active_admins(session, user_id) -> int:
    return (await session.execute(text(
        "SELECT count(*) FROM users WHERE role = 'admin' AND active AND id <> :id"),
        {"id": user_id})).scalar() or 0


@router.put("/api/admin/users/{user_id}")
async def update_user(user_id: str, payload: UserUpdate):
    await _require_admin(payload.actor)
    from src.api.auth import hash_password
    async with SessionLocal() as session:
        row = (await session.execute(text(
            "SELECT id, name, role, active FROM users WHERE id = :id"),
            {"id": user_id})).first()
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        if payload.role is not None and payload.role not in _VALID_ROLES:
            raise HTTPException(status_code=400, detail=f"角色必须是 {'/'.join(_VALID_ROLES)}")
        # 护栏：不能把最后一个在职管理员降级/停用
        demoting = (payload.role is not None and payload.role != "admin" and row[2] == "admin")
        disabling = (payload.active is False and row[2] == "admin" and row[3])
        if (demoting or disabling) and await _count_other_active_admins(session, user_id) == 0:
            raise HTTPException(status_code=400, detail="至少保留一个在职管理员")
        if payload.name is not None and payload.name.strip() and payload.name.strip() != row[1]:
            dup = await session.execute(
                text("SELECT 1 FROM users WHERE name = :n AND id <> :id"),
                {"n": payload.name.strip(), "id": user_id})
            if dup.first():
                raise HTTPException(status_code=400, detail="用户名已存在")
            await session.execute(text("UPDATE users SET name = :v WHERE id = :id"),
                                  {"v": payload.name.strip(), "id": user_id})
        if payload.password:
            await session.execute(text("UPDATE users SET password_hash = :v WHERE id = :id"),
                                  {"v": hash_password(payload.password), "id": user_id})
        if payload.role is not None:
            await session.execute(text("UPDATE users SET role = :v WHERE id = :id"),
                                  {"v": payload.role, "id": user_id})
        if payload.active is not None:
            await session.execute(text("UPDATE users SET active = :v WHERE id = :id"),
                                  {"v": payload.active, "id": user_id})
        await session.commit()
        changes = []
        if payload.name is not None and payload.name.strip() and payload.name.strip() != row[1]:
            changes.append(f"改名 {row[1]}→{payload.name.strip()}")
        if payload.password:
            changes.append("重置密码")
        if payload.role is not None and payload.role != row[2]:
            changes.append(f"角色 {row[2]}→{payload.role}")
        if payload.active is not None and payload.active != row[3]:
            changes.append("启用" if payload.active else "停用")
        await log_action(payload.actor, "user_update",
                         f"修改用户 {row[1]}：{'，'.join(changes) or '无变化'}")
        return {"ok": True}


@router.delete("/api/admin/users/{user_id}")
async def delete_user(user_id: str, actor: str):
    admin_id = await _require_admin(actor)
    if str(admin_id) == user_id:
        raise HTTPException(status_code=400, detail="不能删除当前登录的管理员账号")
    async with SessionLocal() as session:
        row = (await session.execute(text(
            "SELECT name, role, active FROM users WHERE id = :id"), {"id": user_id})).first()
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        if row[1] == "admin" and row[2] and \
                await _count_other_active_admins(session, user_id) == 0:
            raise HTTPException(status_code=400, detail="至少保留一个在职管理员")
        try:
            await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            await session.commit()
            await log_action(actor, "user_delete", f"删除用户 {row[0]}（角色 {row[1]}）")
        except Exception:
            await session.rollback()
            raise HTTPException(
                status_code=400,
                detail="该用户名下有任务/审核数据，无法删除，建议改为停用")
        return {"ok": True}
