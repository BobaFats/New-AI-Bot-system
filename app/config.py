"""
Глобальные настройки сервиса (общие для всех компаний).

Значения берутся из переменных окружения или из файла .env.
Настройки конкретной компании (промпты, CRM и т.д.) хранятся НЕ здесь,
а в базе данных — см. app/tenant_settings.py.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Говорим pydantic: читать файл .env; лишние переменные игнорировать.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Строка подключения к БД. По умолчанию — локальный файл SQLite.
    database_url: str = "sqlite+aiosqlite:///./bot.db"

    # DeepSeek использует формат API OpenAI, поэтому нужен только ключ и адрес.
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    # Jivo: provider_id выдаётся нашему сервису один раз.
    jivo_provider_id: str = ""
    # Базовый адрес Jivo. Полный адрес: {jivo_api_base}/{provider_id}/{token}
    jivo_api_base: str = "https://bot.jivosite.com/webhooks"

    # Секрет для доступа к админке (передаётся в заголовке X-Admin-Token).
    admin_token: str = ""

    log_level: str = "INFO"


@lru_cache  # создаём объект настроек один раз и потом переиспользуем
def get_settings() -> Settings:
    return Settings()
