# Dockerfile — «рецепт» сборки образа: как упаковать бота вместе с Python и всеми библиотеками.
# Образ потом запускается на любом сервере одинаково, независимо от того, что там установлено.

# Берём готовый лёгкий образ с Python 3.12.
FROM python:3.12-slim

# Не создавать .pyc-файлы и сразу выводить логи (без буферизации) — их видно в `docker logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Рабочая папка внутри контейнера.
WORKDIR /app

# Сначала копируем только список зависимостей и ставим их.
# Docker кэширует этот шаг: если requirements.txt не менялся, повторная сборка будет быстрой.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Теперь копируем код проекта.
COPY app ./app
COPY tenants ./tenants
COPY scripts ./scripts

# Запускаем не от root — так безопаснее.
RUN useradd --create-home bot
USER bot

# Порт, который слушает приложение внутри контейнера.
EXPOSE 8000

# Команда запуска. --proxy-headers: доверять заголовкам от веб-сервера (Caddy), который стоит перед ботом.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
