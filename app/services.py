"""
Контейнер внешних сервисов: клиент ИИ-модели и клиент Jivo.

Зачем отдельный контейнер: в тестах мы подменяем настоящие сервисы на «фейковые»
(без интернета и без оплаты), не меняя остальной код.
"""

from dataclasses import dataclass

from app.agent.llm import LlmClient
from app.jivo.client import JivoClient


@dataclass
class Services:
    llm: LlmClient
    jivo: JivoClient
