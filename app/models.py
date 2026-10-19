"""
Таблицы базы данных.

Проект — SaaS: много компаний (tenant = «арендатор», компания-клиент сервиса).
Почти у каждой таблицы есть tenant_id, чтобы данные разных компаний не смешивались.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    """Компания, подключившая бота."""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Короткое имя для админки и CLI, например "demo-mebel".
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    # Токен из URL вебхука: Jivo шлёт нам POST /jivo/{token}.
    # По нему определяем, какой компании пришло сообщение.
    jivo_token: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Все редактируемые настройки компании (промпты, тексты, FAQ, модель...)
    # лежат одним JSON-объектом. Его структура описана в app/tenant_settings.py.
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatMessage(Base):
    """
    Контекст диалога для ИИ-модели.

    Полная история переписки хранится в Jivo, но Jivo не присылает нам историю,
    а модель «не помнит» прошлые сообщения. Поэтому храним последние сообщения
    чата сами — уже в замаскированном виде (без персональных данных).
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    chat_id: Mapped[str] = mapped_column(String(64), index=True)
    # Роль в терминах API модели: user / assistant / tool.
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    # Для role=assistant: какие инструменты модель решила вызвать.
    # Для role=tool: id вызова, на который это ответ.
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PiiValue(Base):
    """
    «Сейф» персональных данных.

    В модель уходит "[PHONE_1]", а настоящий номер лежит здесь.
    Когда модель вызывает инструмент с "[PHONE_1]", наш код подставляет реальное значение.
    """

    __tablename__ = "pii_values"
    __table_args__ = (UniqueConstraint("tenant_id", "chat_id", "placeholder"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    chat_id: Mapped[str] = mapped_column(String(64), index=True)
    placeholder: Mapped[str] = mapped_column(String(32))  # например "[PHONE_1]"
    kind: Mapped[str] = mapped_column(String(16))  # PHONE / EMAIL / INN / CARD ...
    value: Mapped[str] = mapped_column(String(255))  # реальное значение
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProcessedEvent(Base):
    """
    Уже обработанные события Jivo.

    Jivo повторяет запрос, если не получил ответ за 3 секунды. Чтобы не ответить
    клиенту дважды, запоминаем id каждого события и пропускаем повторы.
    """

    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LogEntry(Base):
    """
    Журнал: переписка, взаимодействие модулей и причины решений.

    kind — тип записи:
      jivo_in      — событие от Jivo
      jivo_out     — что отправили в Jivo
      llm_call     — запрос к модели и её ответ
      tool_call    — вызов инструмента (с причиной, которую указала модель)
      decision     — решение бота (например, перевод на оператора)
      error        — ошибка
    Тексты пишутся в замаскированном виде.
    """

    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
