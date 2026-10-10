# Карта кода для защиты

Таблица помогает быстро найти, где что находится и как внести типичное изменение. Колонку «Объясняет» заполняют участники команды: каждый отвечает за свой участок.

## Backend (`backend/app`)

| Файл | Ответственность | Главное | Объясняет |
|---|---|---|---|
| `main.py` | HTTP API, валидация входа, права по ролям | Эндпоинты `/api/v1/...`; `verify` — приём кадров и финализация | |
| `auth.py` | Проверка JWT Keycloak | Подпись RS256, issuer, audience, срок; активный principal | |
| `db.py` | Транзакции, роли, права на объект | `transaction` задаёт `app.actor` и `app.roles`; `authorize_site` | |
| `passages.py` | Логика прохода | `daily_qr`, `start_attempt`, `confirm_device`, `finalize` | |
| `security.py` | Криптография | Проверка подписи устройства, шифрование QR, `canonical_challenge` | |
| `vision.py` | Распознавание лица и живости | `Engine.enroll`, `Engine.verify`, пороги и `MIN_*` в начале файла | |
| `geo.py` | Приём геопозиции | `record_location`: смена, интервал, граница, подозрительные точки | |
| `analytics.py` | Отчёты и heatmap | `heatmap`, `statistics`, `onsite`, `tool_query` | |
| `llm.py` | Единственный слой работы с моделью | `ModelGateway`, `facts_from_results`, `assistant` | |
| `storage.py` | Файлы в S3 | `put_media`, `get_media` | |
| `worker.py` | Фоновые задачи | Потеря геопозиции, напоминания, Web Push | |
| `bootstrap.py` | Первый запуск | Секреты, миграции, политики S3, прогрев Ollama | |
| `download_models.py` | Загрузка моделей | Скачивание, проверка SHA-256, конвертация в ONNX | |

SQL-схема и правила RLS — `backend/sql/001_schema.sql`. Промпты и схемы ответа модели — `backend/prompts/`.

## Frontend (`frontend`)

| Файл | Экран | Объясняет |
|---|---|---|
| `app/page.tsx` | Вход через Keycloak, выбор интерфейса по роли | |
| `components/Gate.tsx` | Проходная: камера, сканер QR, захват 16 кадров | |
| `components/Worker.tsx` | Телефон сотрудника: QR, подпись попытки, GPS, смены | |
| `components/Admin.tsx` | Сотрудники, эталоны, назначения, объекты, провайдер модели | |
| `components/Manager.tsx` | Обзор, heatmap, помощник, расписания, отчёт CSV | |
| `components/MapEditor.tsx`, `SiteMap.tsx` | Схема объекта и зоны | |
| `lib/api.ts`, `lib/device.ts` | Клиент API; ключ устройства и подпись | |

## Типичные изменения на защите

| Задача | Где менять |
|---|---|
| Порог совпадения лица или живости | Переменные `FACE_THRESHOLD`, `PAD_THRESHOLD` (`.env`, `config.py`) |
| Число кадров или время съёмки | `FRAME_COUNT`, `FRAME_INTERVAL_MS` в `Gate.tsx`; лимиты `MIN_FRAMES`, `MAX_FRAMES` в `vision.py` и проверка `8 <= len(frames) <= 32` в `main.py` |
| Срок попытки прохода (30 секунд) | `passages.start_attempt`, текст «Истекли 30 секунд» |
| Интервал геопозиции (10 минут) | `setInterval(sample, 600000)` в `Worker.tsx`; ограничения 590 секунд в `geo.py` и 600 в `analytics.py` |
| Порог потери связи (12 минут) | `worker.py` и `analytics.onsite` |
| Новый инструмент помощника | `analytics.tool_query`, `llm.facts_from_results`, `prompts/tools.json`, `prompts/select.txt` |
| Новая роль или право | `db.authorize_site`, `001_schema.sql` (политики RLS), `main.py` |
| Новая миграция схемы | Новый файл в `backend/sql/` с номером, запись в `schema_migrations` |
| Новая причина отказа прохода | Цепочка проверок в `passages.finalize` |

## Проверка после изменений

```bash
docker compose --profile test run --rm tests
docker compose --profile test run --rm browser-tests
```

Быстрые проверки без Docker: в `backend` — `pytest -q` (интеграционные тесты пропускаются), во `frontend` — `npm run typecheck`.
