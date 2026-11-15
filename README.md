# Афиша Башкортостана

Культурная афиша республики: сайт с поиском и карточками событий, панель модерации, API, Telegram/MAX-боты и общий агент для запросов на естественном языке. События поступают из ГКЗ «Башкортостан» и трёх площадок Уфы; предложения пользователей проходят отдельную модерацию.

## Что работает

- **Публичный сайт:** главная с подборкой, каталог с фильтрами, расписание внутри карточки, страницы событий, профиль, обратная связь и заявка на новое мероприятие. На `/developers` можно настроить iframe-виджет.
- **Админ-панель `/admin`:** обзор, события, модерация заявок, обратная связь, импорт CSV/JSON, аудит и мониторинг. Роль модератора имеет меньше прав, чем администратор.
- **Источники:** плановый сбор ГКЗ, Башкирского театра оперы и балета (`bashopera`), Башкирской государственной филармонии (`bashgf`) и театра «Нур» (`nur`). Сборщики работают независимо и записывают новые карточки черновиками.
- **Данные:** PostgreSQL, сырые записи отдельно от нормализованного каталога, история источников, аудит действий, защита решения модератора от перезаписи повторным импортом. Повторные показы объединяются в одну карточку с несколькими датами и временем; межисточниковые совпадения проверяются на дубли и неоднозначность.
- **Боты и агент:** Telegram и опциональный MAX используют общий поиск, подписки, рекомендации и агентный контур. Базовый режим работает без моделей. Локальные модели, адаптивная маршрутизация и структурированная обработка сложных запросов включаются отдельно. Голосовой ввод на сайте и в ботах использует Groq Whisper при наличии ключа.
- **Наблюдаемость:** `/metrics`, Prometheus и Grafana.

Проект предназначен для демонстрационного контура и не является официальным сервисом органов культуры республики.

## Стек и структура

| Часть | Технологии | Основные файлы |
| --- | --- | --- |
| Сайт | Next.js 16, React 19, TypeScript | `frontend/src/app`, `frontend/src/components` |
| API и боты | Python 3.11, FastAPI, httpx | `src/http`, `src/bot`, `src/api.py` |
| Каталог и сбор | PostgreSQL 16, SQLite для локальных тестов | `src/services`, `src/gkz_scheduler.py`, `src/venue_scheduler.py` |
| Агент | Детерминированный контур, опциональные локальные LLM | `src/agent` |
| Схема БД | Версионированные SQL-миграции | `migrations` |
| Мониторинг | Prometheus, Grafana | `observability` |
| Тесты и образцы | pytest, Node test runner, HTML-фикстуры | `tests`, `frontend/tests`, `fixtures`, `data` |

`data/seed_events.json` и `data/import_100_events.csv` нужны для демонстрации и тестов. Обычный запуск Compose **не загружает** эти события в рабочую базу. Папки `models/`, `vpn/`, `.runtime/` и файлы `.env` являются локальными и не входят в Git.

## Быстрый запуск

Нужны Docker Engine с Compose и свободные порты `3000`, `8000`, `5433`, `9090`, `3002`. На Windows скопируйте шаблон настроек:

```powershell
Copy-Item .env.example .env
```

Задайте в `.env` собственные `POSTGRES_PASSWORD`, `API_DB_PASSWORD`, `BOT_DB_PASSWORD`, `MAINTENANCE_DB_PASSWORD`, `RATE_LIMIT_SECRET` и `ADMIN_PASSWORD`. Примерные значения в шаблоне подходят только для локальной демонстрации. При смене `WEB_PORT` обновите `WEB_ORIGIN`. Для сайта по HTTPS установите `ADMIN_COOKIE_SECURE=true`.

```powershell
docker compose up -d --build --wait
docker compose ps
docker compose logs --tail=50 api gkz-collector venue-collector
```

| Адрес | Назначение |
| --- | --- |
| `http://localhost:3000` | Сайт |
| `http://localhost:3000/admin` | Админ-панель |
| `http://localhost:8000/docs` | OpenAPI |
| `http://localhost:8000/health` | Проверка API и БД |
| `http://localhost:9090` | Prometheus |
| `http://localhost:3002` | Grafana |

Порты можно изменить через `WEB_PORT`, `API_PORT`, `POSTGRES_PORT`, `PROMETHEUS_PORT`, `GRAFANA_PORT`. API, PostgreSQL и мониторинг публикуются только на `127.0.0.1`; сайт — на `WEB_PORT`. Браузер обращается к API через same-origin путь `/backend/*` на Next.js.

`prepare` применяет миграции и загружает справочники до старта API. Данные PostgreSQL хранятся в volume `pgdata_live`; `docker compose down` его сохраняет. Не удаляйте volume, если хотите сохранить события, аккаунты и историю модерации. Сервисы `gkz-collector` и `venue-collector` стартуют вместе с основным стеком и повторяют сбор примерно раз в шесть часов.

## Сбор событий и модерация

Проверка источника без записи в БД:

```powershell
docker compose exec api python -m src.cli collect-gkz --dry-run
docker compose exec api python -m src.cli collect-venue bashopera --dry-run
docker compose exec api python -m src.cli collect-venue bashgf --dry-run
docker compose exec api python -m src.cli collect-venue nur --dry-run
```

Однократный сбор с записью: уберите `--dry-run`. Логи планового сбора:

```powershell
docker compose logs --tail=100 gkz-collector venue-collector
```

Сборщики используют прямые HTTP-клиенты (`trust_env=False`), поэтому переменные прокси контейнера на эти запросы не влияют. Системную маршрутизацию VPN это не переопределяет. Если на Windows соединение Python/OpenSSL с ГКЗ зависает, задайте `GKZ_FETCH_URL=http://host.docker.internal:18082/afisha` в `.env`, запустите `py scripts/gkz_windows_relay.py` в отдельном окне и пересоздайте `gkz-collector`. Мост принимает только фиксированный путь ГКЗ и должен оставаться запущенным во время сбора.

Площадки и их адреса задаются в `config/event_sources.json`; путь можно переопределить переменной `VENUE_SOURCES_CONFIG`. Например, для ещё одного сайта с разметкой шаблона театра оперы добавьте объект `{"code":"newtheatre","name":"Новый театр","url":"https://newtheatre.example/afisha/","address":"Уфа, ...","parser":"opera"}` в JSON-массив. Допустимы `parser`: `opera`, `philharmonic` и `nur`; это ограниченные существующие шаблоны (заголовок `h3` и строка состава с датой; карточки `article.type-event` с `abbr.dtstart`; либо `h4` с датой и временем перед заголовком). Конфигурация не превращает произвольную HTML-страницу в поддерживаемый источник: для другой структуры нужен отдельный адаптер в коде. Источники требуют HTTPS и таймаут ограничивается `VENUE_COLLECTION_TIMEOUT_SECONDS` (по умолчанию 20 секунд); недоступный источник завершает только собственный сбор.

Новые собранные и предложенные пользователями события попадают на модерацию. Публичный поиск показывает только `published`; отменённое событие остаётся доступным по прямой ссылке с указанием статуса. Неизвестная цена хранится как `null` и не считается бесплатным входом. Несколько показов одного события отображаются в общей карточке с расписанием; заявитель тоже может указать несколько дат и язык события.

Импорт из файла и ручные изменения доступны в админке. CLI остаётся доступным для обслуживания: `python -m src.cli --help`. Миграции и очистка данных выполняются отдельным сервисом `prepare` и `maintenance`; API и боты не меняют схему при старте.

**Существующая БД:** базовая миграция предназначена для пустой схемы. Известная история миграций `001–006` обновляется совместимым путём, но неизвестная схема отклоняется. Перед переносом заполненной БД сделайте резервную копию и выполните `python -m scripts.check_legacy_upgrade "<DATABASE_URL>"` на доступной копии.

## Боты, голос и модели

Для Telegram задайте `TELEGRAM_BOT_TOKEN` в `.env` и пересоздайте `bot`: `docker compose up -d --force-recreate bot`. Без токена бот остаётся в режиме ожидания, сайт и API продолжают работать. Если Telegram нужен через отдельный шлюз, настройте локальный `vpn/telegram-gateway.json` и запустите Compose с `compose.telegram-vpn.yml`. ГКЗ и площадки при этом остаются в обычной сети Compose.

Для MAX задайте `MAX_BOT_ENABLED=true` и `MAX_BOT_TOKEN`. Без `MAX_WEBHOOK_URL` используется polling для разработки. Для webhook нужен публичный HTTPS URL, заканчивающийся на `/webhook`, и `MAX_WEBHOOK_SECRET` длиной не менее 16 символов; внешний reverse proxy направляет запросы на `127.0.0.1:18081` (или порт из `MAX_WEBHOOK_PORT`). Telegram и MAX могут работать одновременно.

Для голосового ввода задайте `GROQ_API_KEY`. API проверяет тип, размер и фактическую длительность записи: максимум 20 МБ и 20 секунд. Контейнер содержит `ffprobe`; WAV можно проверить и без него. Без ключа текстовые запросы работают, а голосовой ввод сообщает о недоступности распознавания. Сам ключ не передаётся в браузер.

`AI_MODE=no-ai` — режим по умолчанию. Для локальных моделей используйте `python scripts/setup_models.py`, затем:

```powershell
docker compose -f docker-compose.yml -f compose.local-ai.yml up -d --build --wait
```

Веса GGUF хранятся в игнорируемой папке `models/`. Режимы `ADAPTIVE_AGENT_ENABLED`, `STRUCTURED_DEEP_ENABLED`, `HEAVY_SINGLE_PASS` и `HEAVY_COMPACT_CONTINUATION` по умолчанию выключены; включайте их явно при настроенных моделях. Внешний OpenAI-совместимый сервер можно подключить через `compose.native-ai.yml`. Необязательный Redis-кеш подключается через `compose.redis.yml` и профиль `redis`; API и бот сохраняют связь с PostgreSQL в основной сети.

## API и доступ

Основные публичные маршруты: `GET /api/v1/events`, `GET /api/v1/events/{id}`, справочники `/api/v1/cities`, `/categories`, `/venues`, `GET /api/v1/recommendations`, `POST /api/v1/weekend-plan`, заявки `/api/v1/event-submissions`, обратная связь и подписки. Агент: `POST /api/v1/agent/chat`; голос: `POST /api/v1/speech/transcribe`. Полный контракт, параметры фильтров и схемы ответов находятся в `/docs`.

`/api/v1/admin/*` требует сессию. Изменяющие запросы проверяют Origin и CSRF; у администратора есть импорт и аудит, у модератора — работа с карточками и заявками. Для отдельной роли задайте `MODERATOR_USERNAME` и `MODERATOR_PASSWORD`. Публичные данные не раскрывают внутренние поля сырых записей и аудита. Настройки CORS и кеширования публичных GET — `CORS_ORIGINS` и `PUBLIC_CACHE_MAX_AGE`.

## Разработка и проверки

Для Python используйте зависимости из `requirements-dev.txt` и UTF-8 режим на Windows:

```powershell
python -m pip install -r requirements-dev.txt
python -X utf8 -m pytest -q
```

В Docker используется Python 3.11; локально подойдёт совместимая версия с установленными зависимостями. PostgreSQL-тесты запускаются отдельно при заданном `TEST_POSTGRES_URL` на **тестовую** БД. Без него они пропускаются. Проверки, которым нужны реальные модели, также пропускаются без соответствующего окружения.

```powershell
cd frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

Для локальной разработки фронтенда API должен быть доступен отдельно на `127.0.0.1:8000`; `API_BASE_URL` задаётся до старта или сборки Next.js. На Windows: `$env:API_BASE_URL='http://127.0.0.1:8000'; npm run dev`. Production Compose уже настраивает адрес API. `npm run smoke:embed` выполняйте после запуска `npm run start`.

Проверка уже запущенного стека: `docker compose ps`, `docker compose logs --tail=50 api bot gkz-collector venue-collector`, затем откройте сайт и `/health`. Логи источников содержат количество разобранных, добавленных, обновлённых, повторных и отклонённых событий.
