"""
Запись в журнал (таблица logs) и в консоль.

Журнал нужен, чтобы потом разобрать любой диалог: что написал клиент,
что модель решила, какие инструменты вызвала и почему, что ушло в Jivo.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LogEntry

# Обычный логгер Python — пишет в консоль (видно в терминале и в логах Docker).
console = logging.getLogger("bot")


def write_log(
    session: AsyncSession,
    kind: str,
    data: dict,
    tenant_id: int | None = None,
    chat_id: str | None = None,
) -> None:
    """Добавляет запись в журнал. Сохранится при ближайшем session.commit()."""
    session.add(LogEntry(tenant_id=tenant_id, chat_id=chat_id, kind=kind, data=data))
    console.info("[%s] tenant=%s chat=%s %s", kind, tenant_id, chat_id, data)
