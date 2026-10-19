from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import ChatMessage, LogEntry, PiiValue
from tests.conftest import TOKEN, client_message, text_reply, tool_reply


async def test_unknown_token_rejected(client):
    r = await client.post("/jivo/wrong", json=client_message("hi"))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_client"


async def test_text_message_masked_for_llm_and_unmasked_for_client(client, services):
    services.llm.replies = [text_reply("Спасибо, [NAME_1]! Перезвоним на [PHONE_1].")]

    r = await client.post(f"/jivo/{TOKEN}", json=client_message("Я Иван, мой телефон 89991234567"))
    assert r.status_code == 200

    # Модель не видела ни имени, ни телефона.
    sent_to_llm = str(services.llm.calls[0])
    assert "89991234567" not in sent_to_llm and "Иван" not in sent_to_llm
    assert "[PHONE_1]" in sent_to_llm and "[NAME_1]" in sent_to_llm

    # Клиент получил ответ с настоящими данными.
    msgs = services.jivo.events("BOT_MESSAGE")
    assert msgs[0]["message"]["text"] == "Спасибо, Иван! Перезвоним на 79991234567."
    assert msgs[0]["chat_id"] == "c1" and msgs[0]["client_id"] == "42"


async def test_duplicate_event_processed_once(client, services):
    services.llm.replies = [text_reply("Ответ")]
    await client.post(f"/jivo/{TOKEN}", json=client_message("Привет", event_id="same"))
    r = await client.post(f"/jivo/{TOKEN}", json=client_message("Привет", event_id="same"))
    assert r.json() == {"result": "duplicate"}
    assert len(services.jivo.events("BOT_MESSAGE")) == 1


async def test_invite_agent_tool(client, services):
    services.llm.replies = [
        tool_reply("invite_agent", '{"reason": "клиент просит менеджера"}'),
        text_reply("Подключаю менеджера."),
    ]
    await client.post(f"/jivo/{TOKEN}", json=client_message("Позовите человека"))

    kinds = [p["event"] for p in services.jivo.sent]
    # Сначала ответ клиенту, потом перевод.
    assert kinds == ["BOT_MESSAGE", "INVITE_AGENT"]
    # Модель получила результат инструмента вторым запросом.
    assert services.llm.calls[1][-1]["role"] == "tool"

    async with SessionLocal() as s:
        log = await s.scalar(select(LogEntry).where(LogEntry.kind == "tool_call"))
        assert log.data["reason"] == "клиент просит менеджера"


async def test_history_is_sent_on_next_message(client, services):
    services.llm.replies = [text_reply("Первый ответ"), text_reply("Второй ответ")]
    await client.post(f"/jivo/{TOKEN}", json=client_message("Первый", event_id="a"))
    await client.post(f"/jivo/{TOKEN}", json=client_message("Второй", event_id="b"))
    second_call = services.llm.calls[1]
    assert [m["role"] for m in second_call] == ["system", "user", "assistant", "user"]
    assert second_call[-1]["content"] == "Второй"


async def test_photo_replies_and_invites(client, services):
    await client.post(f"/jivo/{TOKEN}", json=client_message(msg_type="PHOTO", file="https://x/y.jpg"))
    assert [p["event"] for p in services.jivo.sent] == ["BOT_MESSAGE", "INVITE_AGENT"]
    assert services.llm.calls == []  # модель не вызывалась


async def test_llm_error_falls_back_to_agent(client, services):
    services.llm.replies = [RuntimeError("DeepSeek недоступен")]
    await client.post(f"/jivo/{TOKEN}", json=client_message("Привет"))
    assert [p["event"] for p in services.jivo.sent] == ["BOT_MESSAGE", "INVITE_AGENT"]
    async with SessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(LogEntry).where(LogEntry.kind == "error")) == 1


async def test_chat_closed_clears_context(client, services):
    services.llm.replies = [text_reply("Ок")]
    await client.post(f"/jivo/{TOKEN}", json=client_message("Мой номер 89991234567"))
    await client.post(f"/jivo/{TOKEN}", json={"event": "CHAT_CLOSED", "id": "close1", "chat_id": "c1"})
    async with SessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(ChatMessage)) == 0
        assert await s.scalar(select(func.count()).select_from(PiiValue)) == 0


async def test_unknown_event_returns_422(client, services):
    r = await client.post(f"/jivo/{TOKEN}", json={"event": "SOMETHING_NEW", "id": "x", "chat_id": "c1", "client_id": "42"})
    assert r.status_code == 422
    assert len(services.jivo.events("BOT_MESSAGE")) == 1


async def test_ignored_event_ok(client, services):
    r = await client.post(f"/jivo/{TOKEN}", json={"event": "CLIENT_TYPING", "id": "t1", "chat_id": "c1", "client_id": "42"})
    assert r.status_code == 200
    assert services.jivo.sent == []
