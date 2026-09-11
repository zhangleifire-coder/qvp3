"""ref_seed（搜图①）+ ref_collect（搜图②候选池叠加）单测（2026-09-07 两段式）。

覆盖：
- ref_seed 自动执行不挂起（无 ref_gate、不动任务状态）+ text_ref 落库；
- general 模式 flag off 跳过（与 ref_collect 同门控，整条链路零差异）；
- ref_seed 幂等（已有 text_ref/candidate/confirmed 即跳过）；
- ref_collect 候选池 = text_ref + 新搜（hash 去重），text_ref 全部并入
  candidate 不留残留；确认后未 keep 的（含 text_ref 遗留）转 rejected（删除）。

mock 手法同 test_refs_gate.py：fake_search 产本地 SVG（conftest 的
fetch_image_bytes mock 对 /static/ 读真实文件），patch mock_image_gen=True
跳过 OCR；DB 走 conftest 测试库。
"""
import uuid
from unittest.mock import patch

from sqlalchemy import select

from src.db.session import SessionLocal
from src.models.assets import Asset
from src.models.tasks import Task
from src.pipeline.ref_collect import node_ref_collect
from src.pipeline.ref_seed import node_ref_seed

_QUERY = "德龙EC685和柏翠PE3690入门咖啡机对比"


async def _new_task(mode="compare", status="draft") -> uuid.UUID:
    async with SessionLocal() as s:
        t = Task(idempotency_key=f"seed-{uuid.uuid4().hex[:8]}",
                 query=_QUERY, content_type="x", mode=mode, status=status)
        s.add(t)
        await s.commit()
        return t.id


def _svg(tag: str) -> str:
    import hashlib
    from pathlib import Path
    h = hashlib.md5(tag.encode()).hexdigest()[:10]
    d = Path("static/generated")
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"seedtest_{h}.svg"
    f.write_text(f"<svg xmlns='http://www.w3.org/2000/svg' width='576' height='768'>"
                 f"<text>{h}</text></svg>", encoding="utf-8")
    return f"/static/generated/{f.name}"


def _fake_search(tag_prefix: str, n: int = 6):
    async def fake(q, count=6):
        return [{"image_url": _svg(f"{tag_prefix}-{q[:6]}-{i}"), "title": "t",
                 "engine": "bing"} for i in range(min(n, count))]
    return fake


async def _assets(task_id, status=None) -> list:
    async with SessionLocal() as s:
        q = select(Asset).where(Asset.task_id == task_id)
        if status:
            q = q.where(Asset.selection_status == status)
        return list((await s.execute(q.order_by(Asset.page_index))).scalars().all())


async def test_seed_collects_text_ref_without_gate():
    """搜图①自动执行：text_ref 落库、无 ref_gate、任务状态不动。"""
    task_id = await _new_task()
    with patch("src.gateway.image_search.search_image", new=_fake_search("seed")), \
         patch("src.config.settings.mock_image_gen", True):
        r = await node_ref_seed({"task_id": task_id})
    assert r["text_refs"] == 12 and "ref_gate" not in r   # 两主体 6+6，不挂起
    rows = await _assets(task_id, "text_ref")
    assert len(rows) == 12
    assert all(a.image_url.startswith("/static/generated/") for a in rows)
    async with SessionLocal() as s:
        t = (await s.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert t.status == "draft"                        # 状态未被改动


async def test_seed_skips_general_when_flag_off():
    """general 模式 flag off：ref_seed 跳过（与 ref_collect 同门控）。"""
    from src.config import settings
    assert settings.ref_for_general_enabled is False
    task_id = await _new_task(mode="general")
    r = await node_ref_seed({"task_id": task_id})
    assert r.get("skipped") is True and "general" in r["reason"]
    assert await _assets(task_id) == []


async def test_seed_idempotent_skips_existing():
    """幂等：已有 text_ref / candidate / confirmed 任一即跳过，不重复搜图。"""
    for status in ("text_ref", "candidate", "confirmed"):
        task_id = await _new_task()
        async with SessionLocal() as s:
            s.add(Asset(task_id=task_id, page_index=1, subject=_QUERY,
                        source_type="official", copyright_status="unknown",
                        hash=f"h-{uuid.uuid4().hex[:8]}",
                        image_url="/static/generated/x.png",
                        model_version="bing", is_illustration=False,
                        selection_status=status))
            await s.commit()
        called = {"n": 0}

        async def counting(q, count=6):
            called["n"] += 1
            return []
        with patch("src.gateway.image_search.search_image", new=counting):
            r = await node_ref_seed({"task_id": task_id})
        assert r.get("skipped") is True and called["n"] == 0


async def test_collect_pool_merges_seed_and_new():
    """搜图②：候选池 = 搜图① text_ref + 新搜；text_ref 全部并入 candidate。"""
    task_id = await _new_task()
    with patch("src.gateway.image_search.search_image", new=_fake_search("s1", 6)), \
         patch("src.config.settings.mock_image_gen", True):
        await node_ref_seed({"task_id": task_id})
    seed_urls = {a.image_url for a in await _assets(task_id, "text_ref")}
    assert len(seed_urls) == 12

    with patch("src.gateway.image_search.search_image", new=_fake_search("s2", 6)), \
         patch("src.config.settings.mock_image_gen", True):
        r = await node_ref_collect({"task_id": task_id})
    assert r["ref_gate"] is True
    cands = await _assets(task_id, "candidate")
    cand_urls = {a.image_url for a in cands}
    assert seed_urls <= cand_urls                        # text_ref 图全在候选池
    assert len(cand_urls - seed_urls) == 12              # 新搜图（两主体 6+6）也在
    assert await _assets(task_id, "text_ref") == []      # 不留 text_ref 残留


async def test_collect_dedupes_seed_by_hash():
    """新搜命中与 text_ref 同图（同内容 hash）→ 去重不重复进池。"""
    task_id = await _new_task()
    with patch("src.gateway.image_search.search_image", new=_fake_search("same", 6)), \
         patch("src.config.settings.mock_image_gen", True):
        await node_ref_seed({"task_id": task_id})
        r = await node_ref_collect({"task_id": task_id})   # 同样的图再搜一遍
    assert r["ref_gate"] is True
    assert r["candidates"] == 12                           # 无翻倍
    cands = await _assets(task_id, "candidate")
    assert len({a.hash for a in cands}) == 12


async def test_confirm_rejects_unkept_seed_leftover():
    """人工确认：keep→confirmed；未 keep 的（含 text_ref 并入项）→ rejected 清除，
    任务回到队列（text_ref 状态无残留）。"""
    from unittest.mock import AsyncMock
    from src.api.tasks import RefsConfirmIn, confirm_refs
    task_id = await _new_task()
    with patch("src.gateway.image_search.search_image", new=_fake_search("cf", 6)), \
         patch("src.config.settings.mock_image_gen", True):
        await node_ref_seed({"task_id": task_id})
        await node_ref_collect({"task_id": task_id})
    async with SessionLocal() as s:                        # 模拟编排器挂起
        t = (await s.execute(select(Task).where(Task.id == task_id))).scalar_one()
        t.status = "awaiting_refs"
        await s.commit()
    cands = await _assets(task_id, "candidate")
    keep = [str(cands[0].id), str(cands[1].id)]
    with patch("src.api.tasks.scheduler.enqueue", new=AsyncMock()):
        out = await confirm_refs(str(task_id), RefsConfirmIn(keep_ids=keep, actor="张三"))
    assert out["ok"] and out["kept"] == 2
    assert len(await _assets(task_id, "confirmed")) == 2
    assert await _assets(task_id, "text_ref") == []
    assert await _assets(task_id, "candidate") == []
    # 确认后 ref_collect 幂等跳过
    r = await node_ref_collect({"task_id": task_id})
    assert r.get("skipped") is True


async def test_collect_without_seed_zero_diff():
    """无搜图①结果（如 general flag on 直接进②/老任务）：候选池=纯新搜，照旧。"""
    task_id = await _new_task()
    with patch("src.gateway.image_search.search_image", new=_fake_search("ns", 6)), \
         patch("src.config.settings.mock_image_gen", True):
        r = await node_ref_collect({"task_id": task_id})
    assert r["ref_gate"] is True and r["candidates"] == 12
    assert len(await _assets(task_id, "candidate")) == 12
