"""
Симулятор Jivo — чтобы переписываться с ботом в терминале, без настоящего Jivo.

Что делает:
  * поднимает маленький сервер на порту 9000, который притворяется Jivo
    и печатает всё, что бот отправляет клиенту;
  * читает ваши сообщения из терминала и отправляет их боту как CLIENT_MESSAGE.

Как запустить (два терминала, оба в папке Bot с активированным .venv):
  1) В .env должно быть: JIVO_API_BASE=http://127.0.0.1:9000/webhooks
     Терминал 1:  uvicorn app.main:app --reload --port 8000
  2) Терминал 2:  python scripts/jivo_simulator.py --token ТОКЕН_КОМПАНИИ
     (токен показывает команда: python -m app.cli list-tenants)

Команды в симуляторе:
  /photo  — отправить «фото»
  /close  — закрыть чат (бот забудет контекст)
  /new    — начать новый чат
  /quit   — выход
"""

import argparse
import asyncio
import sys
import time
import uuid

import httpx
import uvicorn
from fastapi import FastAPI, Request

fake_jivo = FastAPI()


@fake_jivo.post("/webhooks/{provider_id}/{token}")
async def receive_from_bot(provider_id: str, token: str, request: Request):
    """Сюда бот присылает свои ответы (как в настоящий Jivo)."""
    event = await request.json()
    kind = event.get("event")
    msg = event.get("message") or {}
    if kind == "BOT_MESSAGE" and msg.get("type") == "BUTTONS":
        buttons = " | ".join(f"[{b['text']}]" for b in msg.get("buttons", []))
        print(f"\n🤖 {msg.get('title')}\n   {buttons}")
    elif kind == "BOT_MESSAGE":
        print(f"\n🤖 {msg.get('text')}")
    elif kind == "INVITE_AGENT":
        print("\n📞 [Бот перевёл диалог на оператора]")
    elif kind == "BOT_TYPING":
        print("\n… бот печатает")
    else:
        print(f"\n[{kind}] {event}")
    print("> ", end="", flush=True)
    return {"result": "ok"}


def make_event(event: str, chat_id: str, **extra) -> dict:
    return {"event": event, "id": str(uuid.uuid4()), "chat_id": chat_id, "client_id": "sim-client", **extra}


def client_message(chat_id: str, message: dict) -> dict:
    return make_event(
        "CLIENT_MESSAGE", chat_id,
        agents_online=True,
        sender={"id": 1, "name": "Тестовый клиент", "has_contacts": False},
        channel={"id": "sim", "type": "widget"},
        message={"timestamp": int(time.time()), **message},
    )


async def send(http: httpx.AsyncClient, url: str, payload: dict) -> None:
    resp = await http.post(url, json=payload)
    if resp.status_code != 200:
        print(f"⚠️  Бот ответил {resp.status_code}: {resp.text}")


async def main() -> None:
    # В Windows консоль может быть не в UTF-8 — иначе эмодзи и кириллица вызовут ошибку.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True, help="Токен компании (из list-tenants)")
    parser.add_argument("--bot-url", default="http://127.0.0.1:8000")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--once", help="Отправить одно сообщение, подождать ответ и выйти")
    parser.add_argument("--wait", type=float, default=20, help="Сколько секунд ждать ответ в режиме --once")
    args = parser.parse_args()

    # Запускаем «фейковый Jivo» в фоне.
    server = uvicorn.Server(uvicorn.Config(fake_jivo, port=args.port, log_level="warning"))
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.5)

    url = f"{args.bot_url}/jivo/{args.token}"
    chat_id = f"sim-{uuid.uuid4().hex[:6]}"

    async with httpx.AsyncClient(timeout=10) as http:
        if args.once:
            print(f"> {args.once}")
            await send(http, url, client_message(chat_id, {"type": "TEXT", "text": args.once}))
            await asyncio.sleep(args.wait)
        else:
            print(f"Чат {chat_id}. Пишите сообщения (/quit — выход).")
            while True:
                # input() блокирует программу, поэтому вызываем его в отдельном потоке —
                # тогда «фейковый Jivo» продолжает принимать ответы бота.
                line = (await asyncio.to_thread(input, "> ")).strip()
                if not line:
                    continue
                if line == "/quit":
                    break
                if line == "/new":
                    chat_id = f"sim-{uuid.uuid4().hex[:6]}"
                    print(f"Новый чат {chat_id}")
                elif line == "/close":
                    await send(http, url, make_event("CHAT_CLOSED", chat_id))
                elif line == "/photo":
                    await send(http, url, client_message(chat_id, {"type": "PHOTO", "file": "https://example.com/p.jpg"}))
                else:
                    await send(http, url, client_message(chat_id, {"type": "TEXT", "text": line}))

    server.should_exit = True
    await server_task


if __name__ == "__main__":
    asyncio.run(main())
