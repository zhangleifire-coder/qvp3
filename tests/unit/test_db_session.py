import pytest
from sqlalchemy import text
from src.db.session import SessionLocal


@pytest.mark.asyncio
async def test_session_can_execute_select():
    async with SessionLocal() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1
