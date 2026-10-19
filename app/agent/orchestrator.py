"""
Оркестратор — главный агент. Обрабатывает сообщение клиента от начала до конца:

  сообщение из Jivo
    -> маскируем ПДн
    -> собираем контекст: системный промпт + последние сообщения чата
    -> цикл: модель отвечает текстом ИЛИ просит вызвать инструменты
             (инструменты выполняем, результат отдаём модели, повторяем)
    -> возвращаем ПДн на место и отправляем ответ в Jivo
    -> при необходимости переводим на оператора
  Каждый шаг пишется в журнал.
"""

import asyncio
import json
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import build_system_prompt
from app.agent.tools import ToolContext, get_tools
from app.db import SessionLocal
from app.logs import console, write_log
from app.models import ChatMessage, PiiValue, Tenant
from app.pii import PiiVault, load_vault, save_vault
from app.services import Services
from app.tenant_settings import TenantSettings, load_settings

# Блокировки по чатам: если клиент быстро отправил два сообщения подряд,
# второе обработается только после первого (иначе ответы перемешаются).
_chat_locks: dict[tuple[int, str], asyncio.Lock] = {}


def _lock_for(tenant_id: int, chat_id: str) -> asyncio.Lock:
    return _chat_locks.setdefault((tenant_id, chat_id), asyncio.Lock())


@dataclass
class AgentResult:
    text: str
    invite_agent: bool = False
    invite_reason: str = ""


# ---------------------------------------------------------------------------
# Обработчики событий Jivo (вызываются в фоне из jivo/router.py)
# ---------------------------------------------------------------------------

async def handle_client_message(services: Services, tenant_id: int, event: dict) -> None:
    chat_id = str(event.get("chat_id"))
    client_id = str(event.get("client_id") or (event.get("sender") or {}).get("id"))
    async with _lock_for(tenant_id, chat_id):
        # Фоновая задача работает уже после ответа Jivo, поэтому открываем свою сессию БД.
        async with SessionLocal() as session:
            tenant = await session.get(Tenant, tenant_id)
            settings = load_settings(tenant.settings)
            # Запоминаем токен заранее: после rollback() поля объекта tenant «сбрасываются»
            # и обращение к ним в асинхронном режиме вызовет ошибку.
            token = tenant.jivo_token
            try:
                await _process_message(services, session, tenant, settings, chat_id, client_id, event)
            except Exception as e:  # noqa: BLE001 — любая ошибка не должна оставить клиента без ответа
                console.exception("Ошибка обработки сообщения")
                await session.rollback()
                write_log(session, "error", {"where": "handle_client_message", "error": repr(e)},
                          tenant_id=tenant_id, chat_id=chat_id)
                await session.commit()
                # Запасной сценарий: извиниться и позвать оператора.
                try:
                    await services.jivo.send_text(token, chat_id, client_id, settings.texts.error_fallback)
                    await services.jivo.invite_agent(token, chat_id, client_id)
                except Exception:  # noqa: BLE001
                    console.exception("Не удалось отправить запасной ответ в Jivo")


async def _process_message(
    services: Services,
    session: AsyncSession,
    tenant: Tenant,
    settings: TenantSettings,
    chat_id: str,
    client_id: str,
    event: dict,
) -> None:
    jivo, token = services.jivo, tenant.jivo_token
    message = event.get("message") or {}
    msg_type = str(message.get("type", "")).upper()

    # --- Нетекстовые сообщения (полная обработка медиа — 3-я неделя) ---
    if msg_type == "PHOTO":
        await jivo.send_text(token, chat_id, client_id, settings.texts.photo_received)
        write_log(session, "decision", {"action": "photo_received", "invite_agent": settings.photo_invites_agent},
                  tenant_id=tenant.id, chat_id=chat_id)
        if settings.photo_invites_agent:
            await jivo.invite_agent(token, chat_id, client_id, settings.return_to_bot_after_agent)
        await session.commit()
        return
    if msg_type not in ("TEXT", "MARKDOWN"):
        await jivo.send_text(token, chat_id, client_id, settings.texts.unsupported_media)
        write_log(session, "decision", {"action": "unsupported_media", "type": msg_type},
                  tenant_id=tenant.id, chat_id=chat_id)
        await session.commit()
        return

    text = message.get("text") or message.get("content") or ""
    if not text.strip():
        return

    # --- 1. Маскируем ПДн ---
    vault = await load_vault(session, tenant.id, chat_id)
    sender_name = (event.get("sender") or {}).get("name")
    masked_text = vault.mask(text, known_values={"NAME": sender_name} if sender_name else None)
    session.add(ChatMessage(tenant_id=tenant.id, chat_id=chat_id, role="user", content=masked_text))
    write_log(session, "client_message", {"text": masked_text}, tenant_id=tenant.id, chat_id=chat_id)
    await session.flush()  # отправить в БД, чтобы сообщение попало в историю ниже

    if settings.show_typing:
        await jivo.typing(token, chat_id, client_id)

    # --- 2. Запускаем агента ---
    result = await run_agent(services, session, tenant.id, settings, chat_id, client_id, vault)

    # --- 3. Сохраняем всё и отправляем ответ ---
    save_vault(session, tenant.id, chat_id, vault)
    await session.commit()

    answer = vault.unmask(result.text)  # клиент видит свои настоящие данные, а не метки
    if answer.strip():
        await jivo.send_text(token, chat_id, client_id, answer)
    write_log(session, "jivo_out", {"type": "BOT_MESSAGE", "text": result.text}, tenant_id=tenant.id, chat_id=chat_id)

    if result.invite_agent:
        await jivo.invite_agent(token, chat_id, client_id, settings.return_to_bot_after_agent)
        write_log(session, "decision", {"action": "invite_agent", "reason": result.invite_reason},
                  tenant_id=tenant.id, chat_id=chat_id)
    await session.commit()


async def run_agent(
    services: Services,
    session: AsyncSession,
    tenant_id: int,
    settings: TenantSettings,
    chat_id: str,
    client_id: str,
    vault: PiiVault,
) -> AgentResult:
    """Цикл «модель -> инструменты -> модель» до финального текстового ответа."""
    tools = get_tools(settings)
    tools_by_name = {t.name: t for t in tools}
    tool_schemas = [t.schema() for t in tools]
    ctx = ToolContext(tenant_id=tenant_id, settings=settings, chat_id=chat_id, client_id=client_id,
                      vault=vault, session=session)

    messages = [{"role": "system", "content": build_system_prompt(settings)}]
    messages += await load_history(session, tenant_id, chat_id, settings.llm.history_limit)

    for round_no in range(settings.llm.max_tool_rounds):
        reply = await services.llm.chat(
            messages, tool_schemas,
            model=settings.llm.model, temperature=settings.llm.temperature, max_tokens=settings.llm.max_tokens,
        )
        write_log(session, "llm_call",
                  {"round": round_no, "content": reply.content,
                   "tool_calls": [{"name": c.name, "arguments": c.arguments} for c in reply.tool_calls],
                   "usage": reply.usage},
                  tenant_id=tenant_id, chat_id=chat_id)

        # Модель ответила текстом без инструментов — это финальный ответ.
        if not reply.tool_calls:
            text = reply.content or ""
            session.add(ChatMessage(tenant_id=tenant_id, chat_id=chat_id, role="assistant", content=text))
            return AgentResult(text=text, invite_agent=ctx.invite_agent, invite_reason=ctx.invite_reason)

        # Модель просит вызвать инструменты. Запоминаем её запрос в истории...
        calls_json = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in reply.tool_calls
        ]
        messages.append({"role": "assistant", "content": reply.content or "", "tool_calls": calls_json})
        session.add(ChatMessage(tenant_id=tenant_id, chat_id=chat_id, role="assistant",
                                content=reply.content or "", tool_calls=calls_json))

        # ...и выполняем каждый вызов.
        for call in reply.tool_calls:
            result_text = await _execute_tool(ctx, tools_by_name, call.name, call.arguments)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result_text})
            session.add(ChatMessage(tenant_id=tenant_id, chat_id=chat_id, role="tool",
                                    content=result_text, tool_call_id=call.id))

    # Модель так и не дала текстовый ответ — не зацикливаемся, зовём оператора.
    write_log(session, "decision", {"action": "max_tool_rounds_exceeded"}, tenant_id=tenant_id, chat_id=chat_id)
    return AgentResult(text=settings.texts.error_fallback, invite_agent=True, invite_reason="max_tool_rounds_exceeded")


async def _execute_tool(ctx: ToolContext, tools_by_name: dict, name: str, raw_args: str) -> str:
    """Выполняет один инструмент. Ошибки превращаются в текст для модели, а не роняют бота."""
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        args = {}
    reason = args.get("reason", "")

    tool = tools_by_name.get(name)
    if tool is None:
        result = f"Ошибка: инструмента {name} не существует."
    else:
        try:
            # В инструмент передаём РЕАЛЬНЫЕ значения вместо меток ([PHONE_1] -> 79991234567)...
            real_args = ctx.vault.unmask_obj(args)
            result = await tool.handler(ctx, real_args)
        except Exception as e:  # noqa: BLE001
            console.exception("Ошибка инструмента %s", name)
            result = f"Ошибка при выполнении инструмента: {e}"
        # ...а результат (например, ответ CRM с телефонами) маскируем, прежде чем отдать модели.
        result = ctx.vault.mask(result)

    write_log(ctx.session, "tool_call", {"tool": name, "args": args, "reason": reason, "result": result},
              tenant_id=ctx.tenant_id, chat_id=ctx.chat_id)
    return result


async def load_history(session: AsyncSession, tenant_id: int, chat_id: str, limit: int) -> list[dict]:
    """Последние сообщения чата в формате API модели."""
    rows = list(await session.scalars(
        select(ChatMessage)
        .where(ChatMessage.tenant_id == tenant_id, ChatMessage.chat_id == chat_id)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
    ))
    rows.reverse()  # из БД брали с конца — разворачиваем в хронологический порядок
    # История должна начинаться с сообщения клиента: если обрезка попала на середину
    # цепочки «вызов инструмента -> результат», API модели вернёт ошибку.
    while rows and rows[0].role != "user":
        rows.pop(0)

    history = []
    for r in rows:
        item: dict = {"role": r.role, "content": r.content}
        if r.tool_calls:
            item["tool_calls"] = r.tool_calls
        if r.tool_call_id:
            item["tool_call_id"] = r.tool_call_id
        history.append(item)
    return history


async def handle_agent_unavailable(services: Services, tenant_id: int, event: dict) -> None:
    """Операторов нет в сети — просим оставить контакты."""
    async with SessionLocal() as session:
        tenant = await session.get(Tenant, tenant_id)
        settings = load_settings(tenant.settings)
        chat_id, client_id = str(event.get("chat_id")), str(event.get("client_id"))
        await services.jivo.send_text(tenant.jivo_token, chat_id, client_id, settings.texts.agent_unavailable)
        write_log(session, "decision", {"action": "agent_unavailable"}, tenant_id=tenant_id, chat_id=chat_id)
        await session.commit()


async def handle_chat_closed(tenant_id: int, event: dict) -> None:
    """
    Чат закрыт — удаляем контекст диалога и сейф ПДн этого чата.
    Журнал (в замаскированном виде) остаётся.
    """
    chat_id = str(event.get("chat_id"))
    async with SessionLocal() as session:
        await session.execute(delete(ChatMessage).where(ChatMessage.tenant_id == tenant_id, ChatMessage.chat_id == chat_id))
        await session.execute(delete(PiiValue).where(PiiValue.tenant_id == tenant_id, PiiValue.chat_id == chat_id))
        write_log(session, "decision", {"action": "chat_closed_context_cleared"}, tenant_id=tenant_id, chat_id=chat_id)
        await session.commit()
    _chat_locks.pop((tenant_id, chat_id), None)


async def notify_unsupported_event(services: Services, tenant_id: int, event: dict) -> None:
    """Неизвестное событие Jivo — по документации уведомляем клиента."""
    async with SessionLocal() as session:
        tenant = await session.get(Tenant, tenant_id)
        settings = load_settings(tenant.settings)
        chat_id, client_id = str(event.get("chat_id")), str(event.get("client_id"))
        await services.jivo.send_text(tenant.jivo_token, chat_id, client_id, settings.texts.unsupported_media)
        write_log(session, "decision", {"action": "unsupported_event", "event": event.get("event")},
                  tenant_id=tenant_id, chat_id=chat_id)
        await session.commit()
