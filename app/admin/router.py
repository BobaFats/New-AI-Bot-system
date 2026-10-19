"""
Админка (API без фронтенда).

Для MVP удобно пользоваться автоматической страницей FastAPI: http://127.0.0.1:8000/docs
Там можно нажать «Authorize», ввести ADMIN_TOKEN и вызывать методы прямо из браузера.

Позже поверх этих же методов можно сделать личный кабинет с нормальным интерфейсом.
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import build_system_prompt
from app.config import get_settings
from app.db import get_session
from app.models import LogEntry, Tenant
from app.tenant_settings import TenantSettings, load_settings

# Админ передаёт секрет в заголовке X-Admin-Token.
admin_header = APIKeyHeader(name="X-Admin-Token", auto_error=False)


def require_admin(token: str | None = Security(admin_header)) -> None:
    expected = get_settings().admin_token
    # Если ADMIN_TOKEN не задан — админка выключена (безопасное поведение по умолчанию).
    # compare_digest сравнивает строки за одинаковое время — защита от подбора по времени ответа.
    if not expected or not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


async def _get_tenant(session: AsyncSession, slug: str) -> Tenant:
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.get("/tenants")
async def list_tenants(session: AsyncSession = Depends(get_session)):
    rows = await session.scalars(select(Tenant).order_by(Tenant.id))
    return [{"slug": t.slug, "name": t.name, "is_active": t.is_active} for t in rows]


@router.get("/tenants/{slug}/settings", response_model=TenantSettings)
async def get_tenant_settings(slug: str, session: AsyncSession = Depends(get_session)):
    """Текущие настройки компании (с подставленными значениями по умолчанию)."""
    tenant = await _get_tenant(session, slug)
    return load_settings(tenant.settings)


@router.put("/tenants/{slug}/settings", response_model=TenantSettings)
async def put_tenant_settings(slug: str, new: TenantSettings, session: AsyncSession = Depends(get_session)):
    """Полностью заменить настройки компании. FastAPI сам проверит формат по модели TenantSettings."""
    tenant = await _get_tenant(session, slug)
    tenant.settings = new.model_dump()
    await session.commit()
    return new


@router.get("/tenants/{slug}/prompt")
async def preview_prompt(slug: str, session: AsyncSession = Depends(get_session)):
    """Посмотреть итоговый системный промпт, который получит модель."""
    tenant = await _get_tenant(session, slug)
    return {"prompt": build_system_prompt(load_settings(tenant.settings))}


@router.get("/logs")
async def get_logs(
    tenant: str | None = None,
    chat_id: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
):
    """Журнал: фильтры по компании, чату и типу записи. Новые записи — первыми."""
    query = select(LogEntry).order_by(LogEntry.id.desc()).limit(min(limit, 1000))
    if tenant:
        t = await _get_tenant(session, tenant)
        query = query.where(LogEntry.tenant_id == t.id)
    if chat_id:
        query = query.where(LogEntry.chat_id == chat_id)
    if kind:
        query = query.where(LogEntry.kind == kind)
    rows = await session.scalars(query)
    return [
        {"id": r.id, "at": r.created_at, "chat_id": r.chat_id, "kind": r.kind, "data": r.data}
        for r in rows
    ]
