"""
Инструменты, которые может вызывать модель.

Как это работает:
  1. Мы передаём модели список инструментов: имя, описание, параметры (JSON Schema).
  2. Модель решает, что нужно, например, перевести клиента на оператора,
     и вместо текста отвечает: «вызови invite_agent с аргументами {...}».
  3. Наш код выполняет инструмент и возвращает модели результат.
  4. Модель с учётом результата пишет ответ клиенту.

У каждого инструмента есть обязательный параметр reason — модель пишет,
ПОЧЕМУ она его вызывает. Причина попадает в журнал (требование: логировать причины решений).

Чтобы добавить новый инструмент (CRM, поиск по базе и т.д.), достаточно
создать объект Tool и добавить его в список в get_tools().
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.pii import PiiVault
from app.tenant_settings import TenantSettings


@dataclass
class ToolContext:
    """Всё, что может понадобиться инструменту во время выполнения."""

    tenant_id: int
    settings: TenantSettings
    chat_id: str
    client_id: str
    vault: PiiVault
    session: AsyncSession
    # Флаги, которые инструменты выставляют для оркестратора:
    invite_agent: bool = False
    invite_reason: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    description: str
    # JSON Schema параметров (без reason — он добавляется автоматически).
    parameters: dict
    # Функция-обработчик: получает контекст и аргументы, возвращает текст для модели.
    handler: Callable[[ToolContext, dict], Awaitable[str]]

    def schema(self) -> dict:
        """Описание инструмента в формате, который понимает API модели."""
        params = {
            "type": "object",
            "properties": {
                **self.parameters.get("properties", {}),
                "reason": {"type": "string", "description": "Кратко: почему ты вызываешь этот инструмент."},
            },
            "required": [*self.parameters.get("required", []), "reason"],
        }
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": params}}


# ---------------------------------------------------------------------------
# Инструмент: перевод на оператора
# ---------------------------------------------------------------------------

async def _invite_agent(ctx: ToolContext, args: dict) -> str:
    # Сам перевод выполнит оркестратор ПОСЛЕ того, как отправит клиенту ответ модели.
    ctx.invite_agent = True
    ctx.invite_reason = args.get("reason", "")
    return (
        "Перевод на оператора будет выполнен сразу после твоего ответа. "
        "Коротко сообщи клиенту, что подключаешь менеджера."
    )


INVITE_AGENT = Tool(
    name="invite_agent",
    description=(
        "Перевести диалог на живого оператора. Вызывай, если клиент просит человека/менеджера/оператора, "
        "или если этого требуют правила перевода на оператора."
    ),
    parameters={"properties": {}, "required": []},
    handler=_invite_agent,
)


def get_tools(settings: TenantSettings) -> list[Tool]:
    """Набор инструментов для компании. Позже здесь добавятся CRM, поиск по базе, сайт."""
    return [INVITE_AGENT]
