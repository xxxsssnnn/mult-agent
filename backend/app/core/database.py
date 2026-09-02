import re
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings

# Check if using SQLite
is_sqlite = bool(re.search(r'sqlite\+', settings.DATABASE_URL))

# Configure engine based on database type
if is_sqlite:
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DATABASE_ECHO
    )
else:
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DATABASE_ECHO,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20
    )

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
