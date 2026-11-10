"""
Команды для управления из терминала (удобно для MVP, пока нет личного кабинета).

Примеры:
    python -m app.cli upsert-tenant tenants/demo.json
        Создать компанию из JSON-файла или обновить её настройки.
        Для новой компании токен Jivo будет сгенерирован и выведен на экран.

    python -m app.cli upsert-tenant tenants/demo.json --token [ваш токен]
        То же, но с заданным токеном.

    python -m app.cli upsert-tenant tenants/demo.json --new-token
        Выпустить компании новый токен (старый перестанет работать).

    python -m app.cli list-tenants
        Список компаний и адреса их вебхуков.

Почему токена нет в JSON-файле: файлы из папки tenants/ попадают в GitHub,
а токены — секрет. Токен хранится только в БД.
"""

import argparse
import asyncio
import json
import secrets
import string
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Tenant
from app.tenant_settings import TenantSettings


def generate_token() -> str:
    """
    Токен бота в формате из примера документации Jivo: 8 букв/цифр, двоеточие, 40 hex-символов
    (вид: [8 символов]:[40 символов 0-9a-f]). По документации токен
    должен быть уникальным для каждого бота.
    """
    prefix = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
    return f"{prefix}:{secrets.token_hex(20)}"


async def upsert_tenant(path: str, token: str | None, new_token: bool = False) -> None:
    # utf-8 указываем явно: в Windows по умолчанию другая кодировка.
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    # Проверяем настройки до записи в БД — ошибка в JSON обнаружится сразу.
    settings = TenantSettings.model_validate(data.get("settings", {}))

    await init_db()
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == data["slug"]))
        if tenant is None:
            tenant = Tenant(slug=data["slug"], name=data["name"], jivo_token=token or generate_token())
            session.add(tenant)
            print(f"Создана компания {data['slug']}")
        else:
            tenant.name = data["name"]
            if token:
                tenant.jivo_token = token
            elif new_token:
                tenant.jivo_token = generate_token()
                print("Выпущен новый токен, старый больше не работает")
            print(f"Обновлена компания {data['slug']}")
        tenant.settings = settings.model_dump()
        await session.commit()
        print(f"Адрес вебхука для Jivo: https://[ваш домен]/jivo/{tenant.jivo_token}")


async def list_tenants() -> None:
    await init_db()
    async with SessionLocal() as session:
        for t in await session.scalars(select(Tenant).order_by(Tenant.id)):
            print(f"{t.slug:20} {t.name:30} active={t.is_active}  /jivo/{t.jivo_token}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Управление ботом")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("upsert-tenant", help="Создать/обновить компанию из JSON-файла")
    p.add_argument("path")
    p.add_argument("--token", default=None)
    p.add_argument("--new-token", action="store_true", help="Выпустить новый токен")

    sub.add_parser("list-tenants", help="Список компаний")

    args = parser.parse_args()
    if args.command == "upsert-tenant":
        asyncio.run(upsert_tenant(args.path, args.token, args.new_token))
    elif args.command == "list-tenants":
        asyncio.run(list_tenants())


if __name__ == "__main__":
    main()
