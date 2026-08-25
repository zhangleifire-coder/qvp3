"""组合生成导入 集成测试：analyze_query（mock LLM）+ import_combo 落库与去重。"""
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.db.session import SessionLocal
from src.models.tasks import Task


def _row(query, pool, rounds=3, style="解读·经验分享", category="汽车"):
    from src.api.tasks import ComboRowIn
    return ComboRowIn(query=query, supplement_pool=pool, gen_style=style,
                      gen_category=category, rounds=rounds)


async def test_analyze_query_parses_llm_list():
    from src.api.tasks import AnalyzeQueryIn, analyze_query
    fake = {"text": "1. 空调散热器更换多少钱\n2. 空调不凉怎么判断缺氟\n3. 多久清洗一次",
            "model_version": "m", "cost_cny": 0, "degraded": False}
    with patch("src.gateway.failover.call_with_failover",
               return_value=fake) as mock:
        r = await analyze_query(AnalyzeQueryIn(query="我的车空调不凉了", count=10))
    assert r["questions"] == ["空调散热器更换多少钱", "空调不凉怎么判断缺氟", "多久清洗一次"]
    assert "我的车空调不凉了" in mock.call_args[0][0]


async def test_analyze_query_rejects_short():
    from src.api.tasks import AnalyzeQueryIn, analyze_query
    with pytest.raises(Exception):
        await analyze_query(AnalyzeQueryIn(query="短"))


async def test_import_combo_creates_tasks_with_fields():
    from src.api.tasks import ImportComboIn, import_combo
    src_q = f"组合导入测试{uuid.uuid4().hex[:6]}：我的车空调不凉了，修理厂说要换散热器"
    pool = "散热器更换多少钱，多久加一次氟，怎么判断缺氟，清洗空调有用吗，更换要注意什么"
    r = await import_combo(ImportComboIn(rows=[_row(src_q, pool, rounds=3)],
                                         mode="general", actor="张三"))
    assert r["imported"] == 3 and len(r["task_ids"]) == 3
    async with SessionLocal() as session:
        tasks = []
        for tid in r["task_ids"]:
            t = (await session.execute(
                select(Task).where(Task.id == uuid.UUID(tid)))).scalar_one()
            tasks.append(t)
        pool_items = {"散热器更换多少钱", "多久加一次氟", "怎么判断缺氟",
                      "清洗空调有用吗", "更换要注意什么"}
        assert len({t.query for t in tasks}) == 3          # 不重复抽取
        assert all(t.query in pool_items for t in tasks)
        assert all(t.source_query == src_q for t in tasks)
        assert all(t.supplement_question == t.query for t in tasks)
        assert all(t.gen_style == "解读·经验分享" for t in tasks)
        assert all(t.gen_category == "汽车" for t in tasks)
        assert all(t.status == "draft" and t.mode == "general" for t in tasks)


async def test_import_combo_dedupes_reimport():
    from src.api.tasks import ImportComboIn, import_combo
    src_q = f"组合去重{uuid.uuid4().hex[:6]}"
    pool = ["甲问题", "乙问题"]  # 池大小 == 抽取数 → 二次导入必然全部撞车
    first = await import_combo(ImportComboIn(rows=[_row(src_q, pool, rounds=2)]))
    assert first["imported"] == 2
    again = await import_combo(ImportComboIn(rows=[_row(src_q, pool, rounds=2)]))
    assert again["imported"] == 0
    assert all("已存在" in s["reason"] for s in again["skipped"])


async def test_import_combo_skips_empty_pool():
    from src.api.tasks import ImportComboIn, import_combo
    r = await import_combo(ImportComboIn(
        rows=[_row(f"空池{uuid.uuid4().hex[:6]}", "", rounds=2)]))
    assert r["imported"] == 0 and r["skipped"][0]["reason"].startswith("泛化问题池为空")
