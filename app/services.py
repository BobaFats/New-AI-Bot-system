"""
Контейнер внешних сервисов: модель, Jivo, загрузка сайтов, поиск.

Зачем отдельный контейнер: в тестах настоящие сервисы подменяются на «фейковые»
(без интернета и без оплаты), а остальной код при этом не меняется.
"""

from dataclasses import dataclass

from app.agent.llm import LlmClient
from app.jivo.client import JivoClient
from app.web.fetcher import WebFetcher
from app.web.search import SearchProvider


@dataclass
class Services:
    llm: LlmClient
    jivo: JivoClient
    # Загрузка веб-страниц. None — бот не умеет открывать сайты.
    web: WebFetcher | None = None
    # Поиск в интернете. None — поиск не настроен (см. SEARCH_PROVIDER в .env).
    search: SearchProvider | None = None
