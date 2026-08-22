import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from src.api.main import app
from src.db.session import SessionLocal
from src.models.tasks import Task


@pytest.mark.asyncio
async def test_import_queries_with_mode():
    uniq = uuid.uuid4().hex[:8]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tasks/import_queries",
                             json={"queries": [f"对比_{uniq}"], "mode": "compare"})
    assert resp.status_code == 200
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.query == f"对比_{uniq}"))).scalar_one()
        assert task.mode == "compare"


@pytest.mark.asyncio
async def test_import_csv_reads_mode_column():
    uniq = uuid.uuid4().hex[:8]
    csv = f"query,content_type,mode\n单品_{uniq},product,single\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tasks/import",
                             files={"file": ("t.csv", csv.encode(), "text/csv")})
    assert resp.status_code == 200
    async with SessionLocal() as session:
        task = (await session.execute(
            select(Task).where(Task.query == f"单品_{uniq}"))).scalar_one()
        assert task.mode == "single"
