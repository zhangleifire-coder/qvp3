from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from src.config import settings

engine = create_async_engine(settings.database_url, pool_size=10, max_overflow=20,
                             # 与 execute_node 短事务化互补的廉价保险：取连接前先
                             # ping，丢掉被中间层掐断的空闲连接，避免 checkout 即死
                             pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
