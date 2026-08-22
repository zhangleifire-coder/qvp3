import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from src.api.main import app


@pytest.mark.asyncio
async def test_create_entity_snapshot():
    uniq = uuid.uuid4().hex[:8]
    payload = {
        "entity_type": "school",
        "canonical_name": f"拉萨市第八中学_{uniq}",
        "version": "v1",
        "valid_from": "2026-01-01T00:00:00Z",
        "attributes": {"founded": 1990, "address": "纳金路29号"}
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/entities", json=payload)
    assert resp.status_code == 201
    assert resp.json()["canonical_name"] == payload["canonical_name"]
