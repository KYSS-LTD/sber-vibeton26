# Эксплуатация

Публикуется только HTTPS-прокси. Контейнеры БД/S3/IAM/Ollama остаются в Compose-сети. Не публикуйте их порты при обычном запуске.

## Обновление

Сохранить резервную копию, затем `docker compose up --build -d`. Не использовать `down -v`. Realm import применяется при создании realm, не обновляет существующий автоматически; изменение публичного issuer требует согласованной настройки клиентов Keycloak и записей principals.

Миграции применяет owner-пользователь отдельного init-контейнера; API подключается `prohod_app`, аналитика — `prohod_analytics`. В SQL есть `schema_migrations`; добавляйте новые версии с транзакционным применением. Роли runtime не получают schema ownership.

## Резервная копия

Копировать БД и S3/секреты согласованно, остановив запись:

```powershell
docker compose stop backend worker storage-worker frontend
docker compose exec postgres sh -c 'PGPASSWORD=$(cat /bootstrap/db_owner) pg_dump -U prohod_owner -d prohod -Fc -f /tmp/prohod.dump'
docker compose exec postgres sh -c 'PGPASSWORD=$(cat /bootstrap/db_owner) pg_dump -U prohod_owner -d keycloak -Fc -f /tmp/keycloak.dump'
New-Item -ItemType Directory -Force backups
docker compose cp postgres:/tmp/prohod.dump ./backups/prohod.dump
docker compose cp postgres:/tmp/keycloak.dump ./backups/keycloak.dump
docker compose stop s3 keycloak
```

Сохранить содержимое named volumes `prohod_objects`, `prohod_bootstrap_secrets`, `prohod_runtime_secrets`, `prohod_certificates`; их имена посмотреть через `docker volume ls`. Для файловой копии использовать временный контейнер, подключая исходный том read-only и локальную папку backup. Секретные тома необходимы для расшифровки QR/S3 scopes и существующих устройств; резервную копию защищать как биометрические данные.

Восстанавливать в **отдельный Compose project** и новые тома. Сначала секреты, затем PostgreSQL с теми же ролями, `pg_restore` БД и Keycloak, затем S3 data/IAM и сертификаты. Не запускать автоматический seed до восстановления БД. Запустить компоненты и проверить `/api/v1/health`, вход, чтение эталонов, открытые смены и S3-политики. Не проверять восстановление поверх единственной рабочей копии.

## Наблюдение

`docker compose ps` и `logs backend worker storage-worker model-init llm-init`. Отказы прохода хранятся в журнале, изменения прав — в аудите. Не журналировать Authorization, подписи, тела загрузок и координаты отклонённых закрытых смен. При сбое LLM отображается ошибка провайдера; при пропаже GPS — неизвестная позиция.

Для отката оставить старые образы и проверенную резервную копию. При компрометации аккаунта блокировать principal, сбросить браузерный ключ и назначения; при компрометации storage-ключа отозвать его в IAM и заново создать соответствующий scope.
