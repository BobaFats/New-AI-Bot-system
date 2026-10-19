# AI-бот для Jivo

SaaS-сервис: ИИ-консультант для компаний. Подключается к Jivo через Bot API и работает во всех каналах Jivo (сайт, Telegram, WhatsApp, VK, Avito…). Консультирует по данным компании, в будущем работает с CRM: создаёт заявки, контакты, задачи.

Модель — **DeepSeek**. Персональные данные клиентов **маскируются** перед отправкой в модель.

## Как это работает

```
Клиент ──► Jivo ──POST /jivo/{token}──► Бот ──► сразу 200 OK (Jivo ждёт не больше 3 сек)
                                         │
                                         ▼  (в фоне)
                                 маскирование ПДн: +7999... → [PHONE_1]
                                         │
                                         ▼
                        Оркестратор (DeepSeek + инструменты)
                        ├─ invite_agent — перевод на оператора
                        ├─ CRM-модуль            (неделя 2)
                        ├─ поиск по базе клиента (неделя 2)
                        └─ сайт / файлы / FAQ    (неделя 3)
                                         │
                                         ▼
                        [PHONE_1] → +7999...  ──BOT_MESSAGE──► Jivo ──► Клиент
```

* **Один агент + модули.** Модель решает, что делать, а правила доступа, маскирование и запросы к API выполняет код. Так дешевле, быстрее и безопаснее, чем несколько ИИ-агентов.
* **SaaS.** Каждая компания — отдельная запись `Tenant` со своим токеном и настройками. По токену из URL бот понимает, чья это переписка.
* **Всё, что касается инструкций ИИ, редактируется без кода:** роль, стиль, информация о компании, контакты, правила, FAQ, правила перевода на оператора, параметры модели, готовые тексты. Пример: [tenants/demo.json](tenants/demo.json).
* **Журнал** (таблица `logs`): переписка, вызовы модели, вызовы инструментов с *причиной*, решения бота, ошибки. Всё в замаскированном виде.

## Структура проекта

```
app/
  main.py              точка входа FastAPI
  config.py            глобальные настройки из .env
  db.py, models.py     база данных и таблицы
  tenant_settings.py   настройки компании (промпты, тексты, FAQ, модель)
  pii.py               маскирование персональных данных
  logs.py              журнал
  jivo/router.py       вебхук: приём событий от Jivo
  jivo/client.py       отправка событий в Jivo
  agent/orchestrator.py главный агент: цикл «модель → инструменты → ответ»
  agent/tools.py       инструменты, доступные модели
  agent/prompts.py     сборка системного промпта
  agent/llm.py         клиент DeepSeek (формат OpenAI)
  admin/router.py      админка (API)
  cli.py               команды терминала
tenants/demo.json      настройки демо-компании
scripts/jivo_simulator.py  симулятор Jivo для локальной проверки
tests/                 автотесты
```

## Локальный запуск (Windows, PowerShell, VS Code)

Все команды выполняются в папке `Bot`.

**1. Виртуальное окружение** — отдельная папка `.venv` с библиотеками только этого проекта.
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
После активации в начале строки терминала появится `(.venv)`. Активировать нужно в каждом новом терминале.
Если PowerShell не даёт запустить `Activate.ps1`, один раз выполните `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

**2. Настройки.** Скопируйте шаблон и впишите значения:
```powershell
Copy-Item .env.example .env
```
Для локальной проверки с симулятором в `.env` укажите:
```
DEEPSEEK_API_KEY=ваш-ключ
JIVO_PROVIDER_ID=sim
JIVO_API_BASE=http://127.0.0.1:9000/webhooks
ADMIN_TOKEN=придумайте-длинную-строку
```

**3. Загрузить демо-компанию** (создаёт файл базы `bot.db`):
```powershell
python -m app.cli upsert-tenant tenants/demo.json --token local-demo-token
```
Отредактировали `tenants/demo.json`? Выполните эту команду ещё раз — настройки обновятся.

**4. Запустить бота** (терминал 1):
```powershell
uvicorn app.main:app --reload --port 8000
```

**5. Переписываться через симулятор Jivo** (терминал 2, тоже с активированным `.venv`):
```powershell
python scripts/jivo_simulator.py --token local-demo-token
```
Команды: `/photo`, `/close`, `/new`, `/quit`.

**6. Админка:** откройте http://127.0.0.1:8000/docs → кнопка **Authorize** → введите `ADMIN_TOKEN`.
Полезные методы:
* `GET /admin/tenants/{slug}/prompt` — итоговый промпт, который видит модель;
* `PUT /admin/tenants/{slug}/settings` — изменить настройки;
* `GET /admin/logs?chat_id=...` — журнал по чату.

**7. Тесты:**
```powershell
pytest
```

## Библиотеки (requirements.txt)

| Пакет | Зачем |
|---|---|
| fastapi | веб-фреймворк: вебхуки Jivo и админка |
| uvicorn | сервер, который запускает FastAPI |
| pydantic-settings | чтение настроек из `.env` |
| sqlalchemy | работа с БД через Python-классы |
| aiosqlite / asyncpg | драйверы SQLite (локально) и PostgreSQL (сервер) |
| httpx | HTTP-запросы в Jivo, CRM и другие API |
| openai | SDK формата OpenAI — DeepSeek с ним совместим |
| pytest, pytest-asyncio | автотесты |

## Запуск на сервере (VPS + Docker)

Подробная инструкция будет на 4-й неделе. Кратко:
1. Направить домен на IP сервера (A-запись).
2. Установить Docker на сервер, скопировать проект, создать `.env` (с `POSTGRES_PASSWORD`, `DOMAIN`, настоящими `JIVO_PROVIDER_ID` и `JIVO_API_BASE=https://bot.jivosite.com/webhooks`).
3. `docker compose up -d --build` — поднимутся бот, PostgreSQL и Caddy (HTTPS).
4. Загрузить компанию: `docker compose exec bot python -m app.cli upsert-tenant tenants/demo.json`.
5. В Jivo указать адрес вебхука `https://ВАШ-ДОМЕН/jivo/{token}`.

## План MVP

- [x] **Неделя 1.** Каркас, приём событий Jivo, ответ DeepSeek, маскирование ПДн, журнал, админка, симулятор, тесты, Docker.
- [ ] **Неделя 2.** CRM-адаптер на шаблонах + тестовый CRM-сервер, поиск по базе клиента, идентификация клиента, права доступа, подтверждения кнопками.
- [ ] **Неделя 3.** Демо-сайт и его парсинг, загрузка файлов и FAQ, документы, голосовые (Yandex SpeechKit).
- [ ] **Неделя 4.** Выкладка на VPS, прогон демо-сценария в Jivo, исправления.

## Безопасность

* Токены и ключи — только в `.env`, он не попадает в git.
* Токен компании для Jivo хранится только в БД, не в `tenants/*.json`.
* Персональные данные не уходят в DeepSeek: модель видит только метки `[PHONE_1]`, `[EMAIL_1]` и т.д.
* Контекст диалога и «сейф» ПДн чата удаляются при закрытии чата (`CHAT_CLOSED`).
