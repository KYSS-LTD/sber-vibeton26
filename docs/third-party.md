# Сторонние компоненты и лицензии

Лицензии указаны по сведениям авторов компонентов на момент подготовки документа; перед распространением сборки их нужно подтвердить по репозиториям-источникам. Собственный код проекта распространяется по лицензии из файла [LICENSE](../LICENSE) (GNU AGPL-3.0, выбрана владельцами репозитория).

## Модели

Адреса, ревизии и SHA-256 — в [backend/models.json](../backend/models.json) и [backend/llm-model.json](../backend/llm-model.json). Скачиваются при первом запуске и проверяются по контрольной сумме; в репозиторий веса не входят.

| Модель | Источник | Назначение | Лицензия |
|---|---|---|---|
| YOLOv8-face (`yolov8n-face`) | github.com/akanametov/yolo-face | Поиск лица | Основана на Ultralytics YOLOv8, вероятно AGPL-3.0 — **проверить** до распространения |
| YuNet 2023mar | OpenCV Zoo | Резервный поиск лица, точки лица | MIT |
| SFace 2021dec | OpenCV Zoo | Эмбеддинги лиц | Apache-2.0 |
| MiniFASNetV2 (2.7_80x80) | github.com/minivision-ai/Silent-Face-Anti-Spoofing | Проверка живости | Apache-2.0 |
| Qwen3 0.6B | реестр Ollama | Локальный помощник | Apache-2.0 |
| GigaChat | внешний API Сбера | Внешний помощник | Условия сервиса, не распространяется |

## Основные компоненты

| Компонент | Лицензия |
|---|---|
| FastAPI, Next.js, React | MIT |
| Leaflet | BSD-2-Clause |
| PostgreSQL | PostgreSQL License |
| PostGIS | GPL-2.0-or-later |
| Keycloak | Apache-2.0 |
| SeaweedFS | Apache-2.0 |
| Ollama | MIT |
| Caddy | Apache-2.0 |
| Карта OpenStreetMap (тайлы) | ODbL, атрибуция «© OpenStreetMap contributors» показана на карте |
| ONNX Runtime, OpenCV | MIT, Apache-2.0 |

Полные списки версий — `backend/requirements.lock`, `backend/requirements-models.lock`, `frontend/package-lock.json`; образы контейнеров закреплены по digest в `compose.yaml`.

## Сертификаты

`infra/ca/russian_trusted_ca_bundle.pem` — набор общедоступных корневых сертификатов для проверки TLS GigaChat. Закрытых ключей в файле нет. Включён в репозиторий намеренно, чтобы внешний провайдер заработал на свежем клоне.
