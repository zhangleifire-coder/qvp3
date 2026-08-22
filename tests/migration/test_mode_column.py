import pytest
import asyncpg


@pytest.mark.asyncio
async def test_tasks_has_mode_column():
    conn = await asyncpg.connect("postgresql://qvp:qvp@localhost:5432/qvp_test")
    try:
        col = await conn.fetchrow(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name='tasks' AND column_name='mode'")
    finally:
        await conn.close()
    assert col is not None
    assert "general" in (col["column_default"] or "")
