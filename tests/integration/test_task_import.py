import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from src.api.main import app


@pytest.mark.asyncio
async def test_import_csv_creates_tasks():
    uniq = uuid.uuid4().hex[:8]
    csv = f"query,content_type,platform\n拉萨八中_{uniq},school_compare,xhs\n小米17_{uniq},phone_compare,xhs\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tasks/import",
                             files={"file": ("t.csv", csv.encode(), "text/csv")})
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 2
    assert data["errors"] == []
