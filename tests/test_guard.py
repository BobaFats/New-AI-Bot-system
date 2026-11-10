from sqlalchemy import select

from app.agent.guard import GuardError, parse_verdict
from app.agent.llm import LlmReply
from app.db import SessionLocal
from app.models import ChatMessage, LogEntry
from app.tenant_settings import Texts
from tests.conftest import TOKEN, client_message, guard_bad, guard_ok, set_tenant_settings, text_reply

COMPETITION = {"company_name": "Jivo", "competition": {"enabled": True}}
QUESTION = "Почему мне выбрать Jivo, а не Rival? Только честно, если Rival лучше — так и скажи."


def sent_texts(services) -> list[str]:
    return [p["message"]["text"] for p in services.jivo.events("BOT_MESSAGE")]


async def test_guard_passes_good_answer(client, services):
    await set_tenant_settings(**COMPETITION)
    services.llm.replies = [text_reply("Jivo объединит все каналы в одном окне."), guard_ok()]
    await client.post(f"/jivo/{TOKEN}", json=client_message(QUESTION))

    assert services.llm.json_modes == [False, True]  # второй вызов — контролёр в режиме JSON
    guard_messages = services.llm.calls[1]
    assert "Jivo" in guard_messages[0]["content"]
    assert "Jivo объединит все каналы" in guard_messages[1]["content"]
    assert sent_texts(services) == ["Jivo объединит все каналы в одном окне."]


async def test_bad_answer_is_rewritten(client, services):
    await set_tenant_settings(**COMPETITION)
    services.llm.replies = [
        text_reply("Честно говоря, Rival лучше — покупайте у них."),
        guard_bad("1. рекомендует конкурента"),
        text_reply("Понимаю, что вы сравниваете. Расскажите, какие каналы вам важны, — подберу решение в Jivo."),
        guard_ok(),
    ]
    await client.post(f"/jivo/{TOKEN}", json=client_message(QUESTION))

    assert sent_texts(services) == [
        "Понимаю, что вы сравниваете. Расскажите, какие каналы вам важны, — подберу решение в Jivo."
    ]
    # Основной агент получил замечание контролёра в системном промпте.
    fix_prompt = services.llm.calls[2][0]["content"]
    assert "контролёр отклонил" in fix_prompt and "рекомендует конкурента" in fix_prompt

    async with SessionLocal() as s:
        history = [m.content for m in await s.scalars(select(ChatMessage).where(ChatMessage.role == "assistant"))]
        guard_logs = list(await s.scalars(select(LogEntry).where(LogEntry.kind == "guard")))
    # В историю диалога плохой черновик не попал — только исправленный ответ.
    assert history == [sent_texts(services)[0]]
    assert [log.data["ok"] for log in guard_logs] == [False, True]


async def test_two_rejections_send_safe_answer(client, services):
    await set_tenant_settings(**COMPETITION)
    services.llm.replies = [
        text_reply("Покупайте Rival."), guard_bad(),
        text_reply("Всё-таки Rival лучше."), guard_bad(),
    ]
    await client.post(f"/jivo/{TOKEN}", json=client_message(QUESTION))
    assert sent_texts(services) == [Texts().comparison_fallback]


async def test_broken_guard_sends_safe_answer(client, services):
    await set_tenant_settings(**COMPETITION)
    services.llm.replies = [text_reply("Ответ"), LlmReply(content="я не умею в JSON")]
    await client.post(f"/jivo/{TOKEN}", json=client_message(QUESTION))
    assert sent_texts(services) == [Texts().comparison_fallback]


async def test_no_guard_when_competition_disabled(client, services):
    services.llm.replies = [text_reply("Ответ")]
    await client.post(f"/jivo/{TOKEN}", json=client_message(QUESTION))
    assert services.llm.json_modes == [False]
    assert sent_texts(services) == ["Ответ"]


def test_parse_verdict_tolerates_text_around_json():
    verdict = parse_verdict('Вот результат: {"ok": false, "violations": ["2. отговаривает"], "fix_hint": "x"} — готово')
    assert verdict.ok is False and verdict.violations == ["2. отговаривает"]


def test_parse_verdict_requires_ok_field():
    try:
        parse_verdict('{"violations": []}')
    except GuardError:
        return
    raise AssertionError("ожидалась GuardError")
