"""
Точка входа приложения.

Запуск локально (из папки Bot, с активированным виртуальным окружением):
    uvicorn app.main:app --reload --port 8000

--reload — сервер сам перезапускается при изменении кода (удобно при разработке).
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.admin.router import router as admin_router
from app.agent.llm import DeepSeekClient
from app.config import get_settings
from app.db import init_db
from app.jivo.client import JivoClient
from app.jivo.router import router as jivo_router
from app.services import Services


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Код до yield выполняется при старте сервера, после yield — при остановке."""
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await init_db()
    # Если сервисы уже подставлены (например, в тестах) — не перезаписываем.
    if not hasattr(app.state, "services"):
        app.state.services = Services(llm=DeepSeekClient(), jivo=JivoClient())
    yield
    await app.state.services.jivo.aclose()


app = FastAPI(title="AI Bot for Jivo", lifespan=lifespan)
app.include_router(jivo_router)
app.include_router(admin_router)


@app.get("/health")
async def health():
    """Проверка «жив ли сервер» — пригодится для мониторинга и Docker."""
    return {"status": "ok"}
