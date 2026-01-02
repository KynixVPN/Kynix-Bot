from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import settings

DATABASE_URL = (
    f"mysql+aiomysql://{settings.DB_USER}:{settings.DB_PASSWORD}"
    f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    "?charset=utf8mb4"
)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args={
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30,
    },
)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
