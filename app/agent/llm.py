"""
Клиент ИИ-модели.

DeepSeek поддерживает формат API OpenAI, поэтому используем библиотеку openai,
просто указывая другой адрес сервера. Тот же код подойдёт для любого
OpenAI-совместимого провайдера (GigaChat через прокси, YandexGPT, OpenRouter, локальная модель) —
меняется только адрес, ключ и название модели.

Остальной код работает не с библиотекой openai напрямую, а с нашим упрощённым
форматом LlmReply. Так проще подменять модель и писать тесты.
"""

from dataclasses import dataclass, field
from typing import Protocol

from openai import AsyncOpenAI

from app.config import get_settings


@dataclass
class ToolCall:
    """Модель просит вызвать инструмент: имя + аргументы (JSON-строка)."""

    id: str
    name: str
    arguments: str


@dataclass
class LlmReply:
    """Ответ модели: либо текст, либо список вызовов инструментов (иногда и то и другое)."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)  # сколько токенов потрачено


class LlmClient(Protocol):
    """Интерфейс: любой клиент модели должен уметь chat(...)."""

    async def chat(
        self, messages: list[dict], tools: list[dict], model: str, temperature: float, max_tokens: int
    ) -> LlmReply: ...


class DeepSeekClient:
    def __init__(self):
        settings = get_settings()
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=60,
        )

    async def chat(
        self, messages: list[dict], tools: list[dict], model: str, temperature: float, max_tokens: int
    ) -> LlmReply:
        resp = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            # Список инструментов, которые модель может вызвать. Пустой список API не принимает.
            tools=tools or None,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        msg = resp.choices[0].message
        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "{}")
            for c in (msg.tool_calls or [])
        ]
        usage = resp.usage.model_dump() if resp.usage else {}
        return LlmReply(content=msg.content, tool_calls=calls, usage=usage)
