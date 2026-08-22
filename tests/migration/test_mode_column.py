import pytest
import asyncpg

from tests.conftest import _TEST_DSN


@pytest.mark.asyncio
async def test_tasks_has_mode_column():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        col = await conn.fetchrow(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name='tasks' AND column_name='mode'")
    finally:
        await conn.close()
    assert col is not None
    assert "general" in (col["column_default"] or "")
