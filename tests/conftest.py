"""
Общая подготовка для тестов.

Тесты не ходят в интернет: вместо DeepSeek — FakeLlm с заранее заданными ответами,
вместо Jivo — FakeJivo, который просто запоминает отправленные события.
"""

import os
import tempfile

# Отдельная временная БД для тестов. Задаём ДО импорта app, т.к. app.db читает настройку при импорте.
_db_file = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_file}"
os.environ["ADMIN_TOKEN"] = "test-admin"
os.environ["JIVO_PROVIDER_ID"] = "prov"

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app import models  # noqa: E402
from app.agent.llm import LlmReply, ToolCall  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.jivo.client import JivoClient  # noqa: E402
from app.main import app  # noqa: E402
from app.services import Services  # noqa: E402

TOKEN = "tenant-token"


class FakeLlm:
    """Возвращает ответы из очереди replies и запоминает, что ему отправили."""

    def __init__(self):
        self.replies: list = []
        self.calls: list[list[dict]] = []

    async def chat(self, messages, tools, model, temperature, max_tokens):
        self.calls.append([dict(m) for m in messages])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeJivo(JivoClient):
    """Настоящие обёртки JivoClient, но send() только записывает событие."""

    def __init__(self):
        super().__init__(http=httpx.AsyncClient())
        self.sent: list[dict] = []

    async def send(self, token, payload):
        self.sent.append(payload)

    def events(self, kind: str | None = None) -> list[dict]:
        return [p for p in self.sent if kind is None or p["event"] == kind]


def text_reply(text: str) -> LlmReply:
    return LlmReply(content=text)


def tool_reply(name: str, args: str, call_id: str = "call_1") -> LlmReply:
    return LlmReply(content=None, tool_calls=[ToolCall(id=call_id, name=name, arguments=args)])


@pytest_asyncio.fixture
async def services():
    # Чистая БД перед каждым тестом.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        s.add(models.Tenant(slug="t", name="Test", jivo_token=TOKEN, settings={"show_typing": False}))
        await s.commit()
    svc = Services(llm=FakeLlm(), jivo=FakeJivo())
    app.state.services = svc
    yield svc


@pytest_asyncio.fixture
async def client(services):
    # ASGITransport вызывает приложение напрямую, без настоящего сетевого сервера.
    # Фоновые задачи выполняются до того, как post() вернёт ответ, — удобно для проверок.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def client_message(text: str = "", event_id: str = "e1", msg_type: str = "TEXT", chat_id: str = "c1", **msg) -> dict:
    return {
        "event": "CLIENT_MESSAGE",
        "id": event_id,
        "client_id": "42",
        "chat_id": chat_id,
        "agents_online": True,
        "sender": {"id": 42, "name": "Иван", "has_contacts": False},
        "channel": {"id": "ch", "type": "widget"},
        "message": {"type": msg_type, "text": text, "timestamp": 1700000000, **msg},
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"
