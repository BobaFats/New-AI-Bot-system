"""
Команды для управления из терминала (удобно для MVP, пока нет личного кабинета).

Примеры:
    python -m app.cli upsert-tenant tenants/demo.json
        Создать компанию из JSON-файла или обновить её настройки.
        Для новой компании токен Jivo будет сгенерирован и выведен на экран.

    python -m app.cli upsert-tenant tenants/demo.json --token МОЙ_ТОКЕН
        То же, но с заданным токеном.

    python -m app.cli list-tenants
        Список компаний и адреса их вебхуков.

Почему токена нет в JSON-файле: файлы из папки tenants/ попадают в GitHub,
а токены — секрет. Токен хранится только в БД.
"""

import argparse
import asyncio
import json
import secrets
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Tenant
from app.tenant_settings import TenantSettings


async def upsert_tenant(path: str, token: str | None) -> None:
    # utf-8 указываем явно: в Windows по умолчанию другая кодировка.
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    # Проверяем настройки до записи в БД — ошибка в JSON обнаружится сразу.
    settings = TenantSettings.model_validate(data.get("settings", {}))

    await init_db()
    async with SessionLocal() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == data["slug"]))
        if tenant is None:
            tenant = Tenant(slug=data["slug"], name=data["name"], jivo_token=token or secrets.token_urlsafe(24))
            session.add(tenant)
            print(f"Создана компания {data['slug']}")
        else:
            tenant.name = data["name"]
            if token:
                tenant.jivo_token = token
            print(f"Обновлена компания {data['slug']}")
        tenant.settings = settings.model_dump()
        await session.commit()
        print(f"Адрес вебхука для Jivo: https://ВАШ-ДОМЕН/jivo/{tenant.jivo_token}")


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

    sub.add_parser("list-tenants", help="Список компаний")

    args = parser.parse_args()
    if args.command == "upsert-tenant":
        asyncio.run(upsert_tenant(args.path, args.token))
    elif args.command == "list-tenants":
        asyncio.run(list_tenants())


if __name__ == "__main__":
    main()
