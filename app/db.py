"""
Подключение к базе данных.

Используем SQLAlchemy в асинхронном режиме: пока бот ждёт ответа от БД,
он может обрабатывать другие запросы.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Базовый класс для всех таблиц (см. app/models.py)."""


# engine — «двигатель»: знает, к какой БД подключаться.
engine = create_async_engine(get_settings().database_url)

# SessionLocal — фабрика сессий. Сессия = один «разговор» с БД.
# expire_on_commit=False: после сохранения объекты остаются доступными для чтения.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Создаёт таблицы, если их ещё нет. Вызывается при старте приложения."""
    # Импорт нужен, чтобы классы таблиц зарегистрировались в Base.metadata.
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: выдаёт сессию на время обработки одного запроса."""
    async with SessionLocal() as session:
        yield session
