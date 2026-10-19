"""
Отправка событий в Jivo (Bot API v4.14.1).

Адрес Jivo: {JIVO_API_BASE}/{provider_id}/{token}
  provider_id — выдаёт Jivo нашему сервису;
  token       — токен конкретной компании (тот же, что в URL нашего вебхука).
Все события — POST с JSON.
"""

import time
import uuid

import httpx

from app.config import get_settings
from app.logs import console


def _event(event: str, chat_id: str, client_id: str, **extra) -> dict:
    """Общая «шапка» любого события: тип, уникальный id, чат и клиент."""
    return {"event": event, "id": str(uuid.uuid4()), "chat_id": chat_id, "client_id": client_id, **extra}


class JivoClient:
    def __init__(self, http: httpx.AsyncClient | None = None):
        settings = get_settings()
        self.base = settings.jivo_api_base.rstrip("/")
        self.provider_id = settings.jivo_provider_id
        # Один HTTP-клиент на всё приложение: он переиспользует соединения — так быстрее.
        self.http = http or httpx.AsyncClient(timeout=10)

    async def send(self, token: str, payload: dict) -> None:
        """Отправляет событие в Jivo. При сетевой ошибке пробуем ещё раз (всего 3 попытки)."""
        url = f"{self.base}/{self.provider_id}/{token}"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self.http.post(url, json=payload)
                if resp.status_code == 200:
                    return
                # 4xx — ошибка в нашем запросе, повтор не поможет.
                if 400 <= resp.status_code < 500:
                    raise RuntimeError(f"Jivo ответил {resp.status_code}: {resp.text[:300]}")
                last_error = RuntimeError(f"Jivo ответил {resp.status_code}")
            except httpx.HTTPError as e:
                last_error = e
            console.warning("Jivo: попытка %s не удалась: %s", attempt + 1, last_error)
        raise RuntimeError(f"Не удалось отправить событие в Jivo: {last_error}")

    # --- Удобные обёртки для конкретных событий ---

    async def send_text(self, token: str, chat_id: str, client_id: str, text: str) -> dict:
        payload = _event(
            "BOT_MESSAGE", chat_id, client_id,
            message={"type": "TEXT", "text": text, "timestamp": int(time.time())},
        )
        await self.send(token, payload)
        return payload

    async def send_buttons(
        self, token: str, chat_id: str, client_id: str, title: str, buttons: list[str]
    ) -> dict:
        """Сообщение с кнопками (Jivo допускает максимум 3 кнопки)."""
        buttons = buttons[:3]
        payload = _event(
            "BOT_MESSAGE", chat_id, client_id,
            message={
                "type": "BUTTONS",
                "title": title,
                # text — запасной вариант для каналов, где кнопок нет.
                "text": f"{title} " + " / ".join(buttons),
                "buttons": [{"text": b, "id": str(i + 1)} for i, b in enumerate(buttons)],
                "timestamp": int(time.time()),
            },
        )
        await self.send(token, payload)
        return payload

    async def invite_agent(
        self, token: str, chat_id: str, client_id: str, return_to_bot: bool = False
    ) -> dict:
        """Перевод диалога на живого оператора."""
        payload = _event("INVITE_AGENT", chat_id, client_id, has_next_transition=return_to_bot)
        await self.send(token, payload)
        return payload

    async def typing(self, token: str, chat_id: str, client_id: str) -> None:
        """Показывает клиенту «печатает...». Ошибка здесь не критична — просто пишем в лог."""
        try:
            await self.send(token, _event("BOT_TYPING", chat_id, client_id))
        except Exception as e:  # noqa: BLE001
            console.warning("BOT_TYPING не отправлен: %s", e)

    async def aclose(self) -> None:
        await self.http.aclose()
