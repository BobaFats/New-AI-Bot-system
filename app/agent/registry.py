"""
Какие инструменты доступны модели для конкретной компании.

Инструмент попадает в список, только если он включён в настройках компании
И нужный сервис реально работает. Модели не стоит предлагать инструмент,
который всё равно вернёт ошибку.
"""

from app.agent.tools import INVITE_AGENT, Tool
from app.agent.web_tools import FETCH_URL, WEB_SEARCH
from app.services import Services
from app.tenant_settings import TenantSettings


def get_tools(settings: TenantSettings, services: Services) -> list[Tool]:
    tools = [INVITE_AGENT]
    if settings.web.enabled and services.web is not None:
        tools.append(FETCH_URL)
        if settings.web.search_enabled and services.search is not None:
            tools.append(WEB_SEARCH)
    # Позже здесь добавятся CRM и поиск по базе клиента.
    return tools


def search_available(settings: TenantSettings, services: Services) -> bool:
    """Нужно промпту: подсказать модели искать сайт самой или просить ссылку у клиента."""
    return any(t.name == WEB_SEARCH.name for t in get_tools(settings, services))
