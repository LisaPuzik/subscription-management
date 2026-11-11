# Sub Management

Backend-сервис управления подписками и анализа расходов.

Стек: Python 3.12, FastAPI, FastAPI-Users, SQLAlchemy 2 (async), PostgreSQL 16, Alembic.

## Возможности

- регистрация, подтверждение email, вход, refresh-токены с ротацией и обнаружением повторного использования, выход;
- восстановление и смена пароля, смена email с резервированием адреса, удаление учетной записи;
- подписки: создание, фильтры, сортировка, пагинация, пауза, отмена, архивирование и восстановление;
- расписание платежей с сохранением дня месяца (31 января → 28/29 февраля → 31 марта) и корректной обработкой 29 февраля;
- платежи, частичные и полные возвраты, сторнирование ошибочных записей;
- пользовательские и системные категории;
- аналитика: сводка, динамика по месяцам, расходы по категориям, прогноз на месяц;
- идемпотентность создания подписок, платежей и возвратов через заголовок `Idempotency-Key`;
- журнал аудита, единый формат ошибок, `request_id` в каждом ответе и в логах сервера.

## Запуск через Docker

```bash
cp .env.example .env
docker compose up --build
```

API: http://localhost:8000/api/v1, документация: http://localhost:8000/docs.

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

Если `SMTP_HOST` не задан, письма со ссылками подтверждения выводятся в stdout (только для разработки). При `ENVIRONMENT=production` без `SMTP_HOST` приложение не запускается.

Для локальной разработки по HTTP установите `COOKIE_SECURE=false`, иначе браузер не сохранит refresh-cookie.

## Конфигурация

Все настройки задаются переменными окружения или файлом `.env` (см. `.env.example`).

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `DATABASE_URL` | строка подключения `postgresql+asyncpg://...` | обязательна |
| `JWT_SECRET` | секрет подписи access-токенов, не короче 32 символов | обязательна |
| `ENVIRONMENT` | `development`, `test`, `production` | `development` |
| `DEFAULT_CURRENCY` | валюта профиля по умолчанию, проверяется при старте | `EUR` |
| `REQUIRE_EMAIL_VERIFICATION` | запрет доступа до подтверждения email | `true` |
| `FRONTEND_URL` | база ссылок в письмах | `http://localhost:3000` |
| `COOKIE_SECURE` | атрибут `Secure` у auth-cookie | `true` |
| `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS` | лимит запросов к auth-endpoint-ам с одного IP | `10` за `60` с |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_SENDER` | отправка почты | — |

## Аутентификация

1. `POST /api/v1/auth/register` создает пользователя и отправляет письмо со ссылкой подтверждения.
2. `POST /api/v1/auth/verify` с токеном из письма подтверждает email.
3. `POST /api/v1/auth/jwt/login` (form-data `username`, `password`) возвращает access token (JWT, 15 минут). Refresh token (30 дней) и `csrf_token` приходят в cookie с `Path=/api/v1/auth`.
4. Защищенные запросы передают `Authorization: Bearer <access_token>`.
5. `POST /api/v1/auth/refresh` с cookie и заголовком `X-CSRF-Token`, равным значению cookie `csrf_token`, выдает новую пару токенов. Старый refresh token отзывается; его повторное использование отзывает всё семейство сессий.

Refresh-токены и одноразовые токены хранятся в БД только в виде SHA-256-хешей. Пароли хешируются Argon2id.

## Основные правила

- Денежные суммы передаются строками (`"15.99"`), хранятся в `NUMERIC(20, 8)`, обрабатываются через `Decimal`. Точность проверяется по minor unit валюты (EUR — 2 знака, JPY — 0, KWD — 3).
- Суммы в разных валютах никогда не складываются: все итоги разбиты по валютам.
- `date_from` и `date_to` — локальные даты пользователя, интервал полуоткрытый `[date_from, date_to)`.
- Чужие ресурсы возвращают 404, а не 403.
- Платежи не удаляются и не редактируются: ошибочное списание сторнируется через `POST /payments/{id}/reverse`, возврат оформляется через `POST /payments/{id}/refunds`.
- Платеж по подписке за текущий `next_payment_date` сдвигает расписание. Платеж за прошедший период расписание не меняет. Платеж за будущий период отклоняется.

## Endpoint-ы

| Метод | Путь |
|---|---|
| POST | `/auth/register`, `/auth/jwt/login`, `/auth/jwt/logout`, `/auth/refresh` |
| POST | `/auth/forgot-password`, `/auth/reset-password`, `/auth/request-verify-token`, `/auth/verify`, `/auth/confirm-email-change` |
| GET, PATCH, DELETE | `/users/me` |
| POST | `/users/me/password-change`, `/users/me/email-change` |
| GET, POST | `/categories` |
| GET, PATCH, DELETE | `/categories/{id}` |
| GET, POST | `/subscriptions` |
| GET, PATCH, DELETE | `/subscriptions/{id}` |
| POST | `/subscriptions/{id}/restore` |
| GET, POST | `/payments` |
| GET | `/payments/{id}` |
| POST | `/payments/{id}/refunds`, `/payments/{id}/reverse` |
| GET | `/analytics/summary`, `/analytics/monthly`, `/analytics/categories`, `/analytics/upcoming` |

Все пути начинаются с `/api/v1`. Полные схемы запросов и ответов — в `/docs` и `/openapi.json`.

## Структура проекта

```
app/
  auth/       настройка FastAPI-Users, JWT, зависимости текущего пользователя
  domain/     чистая бизнес-логика: расписание платежей, агрегация аналитики
  routers/    HTTP-слой
  schemas/    Pydantic-схемы запросов и ответов
  services/   работа с БД: подписки, платежи, категории, сессии, идемпотентность
  models.py   модели SQLAlchemy
migrations/   миграции Alembic
tests/        unit-, integration- и API-тесты
```

## Целостность данных в PostgreSQL

- уникальность email, `refresh_token_hash`, `token_hash`, `(user_id, operation, key)` для идемпотентности;
- частичный уникальный индекс `lower(pending_email)`;
- уникальность названия пользовательской категории без учета регистра;
- частичный уникальный индекс на `(subscription_id, billing_period_start)` для активных списаний;
- составные внешние ключи `payment(subscription_id, user_id)` и `payment(original_payment_id, user_id)`, не позволяющие ссылаться на чужие записи;
- триггер, запрещающий назначать подписке или платежу категорию другого пользователя;
- CHECK-ограничения на суммы, валюты, статусы, интервалы и цвета.

## Тесты

```bash
pytest
```

Тестам нужен PostgreSQL. Если задана переменная `TEST_DATABASE_URL`, используется она (база пересоздается миграциями). Иначе тесты поднимают локальный PostgreSQL через пакет `pgserver` (`pip install pgserver`).

Покрыто: расчет дат и периодичности, валюты и точность, аналитика и прогноз, авторизация и ротация токенов, смена email и пароля, удаление аккаунта, права доступа, идемпотентность (повторные и конкурентные запросы), конкурентные возвраты, ограничения БД и триггеры.

## Покрытие

```bash
pytest --cov
```

Бизнес-логика (`app/domain`, `app/services`, `app/currency.py`): 99,5% операторов, 100% ветвей. Весь проект: 96,0% операторов, 94,4% ветвей. CI падает, если общее покрытие ниже 90%.

## Смоук-тесты

`scripts/smoke.sh` проходит основные сценарии через curl: регистрация, подтверждение email, вход, refresh, категории, подписки, идемпотентность, платежи, возвраты, аналитика, права доступа, удаление аккаунта. Токены подтверждения скрипт берет из вывода сервера, поэтому SMTP не настраивается.

```bash
COOKIE_SECURE=false uvicorn app.main:app > server.log 2>&1 &
SERVER_LOG=server.log bash scripts/smoke.sh
```

## CI

GitHub Actions на каждый push и pull request: `ruff check`, `ruff format --check`, `pip-audit` по зафиксированным зависимостям и `pytest --cov` с PostgreSQL 16.

## Ограничения версии 1

- конвертация валют не выполняется;
- кэширование аналитики не используется;
- rate limiter хранит счетчики в памяти процесса; при нескольких экземплярах приложения его нужно вынести в Redis или на уровень reverse proxy;
- HTTPS обеспечивается reverse proxy перед приложением.
