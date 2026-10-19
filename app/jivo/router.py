"""
Вебхук для Jivo: сюда Jivo присылает события (новое сообщение клиента, закрытие чата и т.д.).

Главное ограничение Jivo: ответить нужно за 3 секунды, иначе Jivo повторит запрос,
а после 2 повторов переведёт клиента на оператора. Модель думает дольше, поэтому:
  1. Сразу отвечаем Jivo «200 OK».
  2. Обрабатываем сообщение в фоне (BackgroundTasks).
  3. Ответ клиенту отправляем отдельным запросом BOT_MESSAGE (см. jivo/client.py).
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import orchestrator
from app.db import get_session
from app.logs import write_log
from app.models import ProcessedEvent, Tenant

router = APIRouter()

# События, которые нам не нужны, но и ошибкой не являются — просто отвечаем 200.
IGNORED_EVENTS = {
    "CLIENT_TYPING",
    "CLIENT_UNFINISHED_MESSAGE",
    "CLIENT_STATUS_MESSAGE",
    "CLIENT_RATED",
    "CLIENT_UPDATED",  # будет использоваться для идентификации клиента (неделя 2)
    "AGENT_JOINED",
    "AGENT_LEFT",
    "AGENT_MESSAGE",
}


def jivo_error(status: int, code: str, message: str) -> JSONResponse:
    """Ошибка в формате, который рекомендует документация Jivo."""
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@router.post("/jivo/{token}")
async def jivo_webhook(
    token: str,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    # 1. По токену из URL находим компанию.
    tenant = await session.scalar(select(Tenant).where(Tenant.jivo_token == token, Tenant.is_active.is_(True)))
    if tenant is None:
        return jivo_error(401, "invalid_client", "Unknown bot token")

    # 2. Читаем JSON события.
    try:
        event = await request.json()
    except ValueError:
        return jivo_error(400, "invalid_request", "Body must be JSON")
    event_type = event.get("event")
    event_id = event.get("id")
    chat_id = event.get("chat_id")
    if not event_type or not event_id:
        return jivo_error(400, "invalid_request", "Fields 'event' and 'id' are required")

    # 3. Защита от повторов: если событие с таким id уже было — ничего не делаем.
    session.add(ProcessedEvent(event_id=event_id, tenant_id=tenant.id))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return {"result": "duplicate"}

    # В журнал пишем только служебные поля. Текст сообщения содержит ПДн —
    # его в замаскированном виде запишет оркестратор.
    write_log(
        session, "jivo_in",
        {"event": event_type, "id": event_id, "message_type": (event.get("message") or {}).get("type"),
         "channel": (event.get("channel") or {}).get("type")},
        tenant_id=tenant.id, chat_id=chat_id,
    )
    await session.commit()

    services = request.app.state.services

    # 4. Распределяем событие по обработчикам. Все тяжёлые действия — в фоне.
    if event_type == "CLIENT_MESSAGE":
        background.add_task(orchestrator.handle_client_message, services, tenant.id, event)
    elif event_type == "AGENT_UNAVAILABLE":
        background.add_task(orchestrator.handle_agent_unavailable, services, tenant.id, event)
    elif event_type == "CHAT_CLOSED":
        background.add_task(orchestrator.handle_chat_closed, tenant.id, event)
    elif event_type in IGNORED_EVENTS:
        pass
    else:
        # Неизвестное событие. По документации Jivo: ответить 422 и уведомить клиента.
        if chat_id and event.get("client_id"):
            background.add_task(orchestrator.notify_unsupported_event, services, tenant.id, event)
        return jivo_error(422, "invalid_request", f"Event {event_type} is not supported")

    return {"result": "ok"}
