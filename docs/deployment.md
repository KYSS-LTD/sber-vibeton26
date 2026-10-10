# Сервер Linux

Рабочий каталог установки: `/home/ubuntu/prohod`. Публичный адрес — **https://kyssltd.ru**. В `.env` заданы `APP_HOST=kyssltd.ru` и `PUBLIC_ORIGIN=https://kyssltd.ru`; параметры GigaChat остаются пустыми до получения ключа.

```bash
cd /home/ubuntu/prohod
sudo docker compose up --build -d
sudo systemctl status caddy --no-pager
sudo docker compose ps -a
sudo docker compose logs --tail=50 backend model-init llm-init ollama
sudo docker compose --profile test run --rm --no-deps tests
sudo docker compose --profile test run --rm --no-deps browser-tests
```

Первый GPU-запрос Ollama может компилировать CUDA-ядра около 44 секунд. Init после проверки digest прогревает модель через общий `ModelGateway`; дождитесь завершения `llm-init`. CUDA cache сохраняется в томе Ollama.

Override автоматически применяется обычной командой Compose. GPU выделена только API биометрии, его тестам и Ollama; остальным сервисам GPU не нужна. ONNX Runtime CUDA 12.8/cuDNN 9 использует YOLO и MiniFASNet, OpenCV SFace/YuNet остаются на CPU. Настройка GPU соответствует [Docker](https://docs.docker.com/compose/how-tos/gpu-support/), библиотеки и загрузка provider — [ONNX Runtime](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).

Снаружи 80/443 обслуживает системный **Caddy 2.10.2**, SSH остаётся на 22. Docker-прокси опубликован только на `127.0.0.1:8080`; PostgreSQL, Keycloak, S3 и Ollama доступны внутри сети Docker. В `compose.override.yaml` объединены настройки GPU и `compose.host-caddy.yaml`. Не заменять этот файл одной GPU-конфигурацией: это вернёт публикацию 80/443 контейнером и создаст конфликт с системной службой.

Caddy использует `/etc/caddy/Caddyfile`, сертификаты и ACME-аккаунт сохраняет в `/var/lib/caddy/.local/share/caddy`. Включён автозапуск `caddy.service`; HTTP перенаправляется в HTTPS, сертификат Let's Encrypt обновляется автоматически. Камере и геолокации на телефоне больше не требуется установка собственного CA. Настройка соответствует [службе Caddy](https://caddyserver.com/docs/running#linux-service) и [Automatic HTTPS](https://caddyserver.com/docs/automatic-https).

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo journalctl -u caddy --no-pager -n 30
```

Внутренний HTTP-прокси принудительно сообщает Keycloak HTTPS-схему; его порт доступен только с локального хоста. `KC_HOSTNAME` содержит полный `https://kyssltd.ru/auth`, а клиент `prohod-web` разрешает redirect/web origins только нового домена. При переносе сохранены пользователи, пароли, назначения и смены. Старые привязки браузерных ключей отозваны, поскольку WebCrypto/IndexedDB привязаны к origin; сотрудник входит на новом сайте и автоматически регистрирует новый ключ.

У всех начальных аккаунтов случайные пароли, доступные только владельцу Docker:

```bash
sudo docker compose run --rm --no-deps --entrypoint cat secrets /bootstrap/accounts.json
```

## Установка системного Caddy на новом сервере

Перед первым запуском настроить A-запись `kyssltd.ru` на сервер и публичный origin в `.env`. Для NVIDIA первоначально скопировать `compose.gpu.yaml` в `compose.override.yaml` и запустить Compose. Затем выполнить один раз:

```bash
sudo bash scripts/install-host-caddy.sh
sed '1,/^services:/d' compose.host-caddy.yaml >> compose.override.yaml
sudo docker compose config -q
sudo docker compose stop proxy
sudo docker compose up -d --no-deps proxy
sudo systemctl enable --now caddy
```

Установщик получает Linux-бинарник из уже закреплённого образа Caddy 2.10.2, создаёт непривилегированного пользователя и systemd-службу. При обнаружении другой установленной версии или существующего Caddyfile он отказывается перезаписывать их. Настройки домена хранятся в `infra/Caddyfile.production`, локального маршрутизатора — в `infra/Caddyfile.internal`. Другой проект на портах 80/443 следует перенастроить отдельно.

Для существующей БД смену origin выполнять при остановленных API, frontend и обработчиках. `scripts/migrate-origin.py` запускается в сетевом пространстве Keycloak из образа `prohod-secrets` с read-only томом `prohod_bootstrap_secrets` в `/bootstrap`: параметры — старый и новый HTTPS-origin, флаг `--apply` включает изменения. Без флага выполняется проверка. Скрипт проверяет коллизии principals, обновляет клиент Keycloak и issuer, отзывает ключи/QR/сессии старого origin, отменяет незавершённые проходы и оставляет смены открытыми. Затем обновить `.env` и пересоздать сервисы через Compose. Realm import сам по себе существующий клиент не обновляет; правила hostname описаны в [документации Keycloak](https://www.keycloak.org/server/hostname).

Стандартный Compose без override по-прежнему запускает Caddy в контейнере с `tls internal` для локального IP/localhost; в этом режиме доверие собственному CA необходимо, как описано в README.

## Резервное копирование и восстановление

```bash
sudo bash scripts/backup.sh backups/acceptance
sudo bash scripts/restore.sh backups/acceptance prohod-restore-test
```

Backup кратковременно останавливает запись API, worker, Keycloak и S3, сохраняет обе БД, S3/IAM, исходные и runtime секреты, сертификаты и SHA-256. Если системный Caddy работает, он также кратковременно останавливается: его конфигурация, ACME-аккаунт и сертификаты сохраняются в `host-caddy.tar.gz`. `.env` и Compose-конфигурация сохраняются в `deployment-config.tar.gz`. При ошибке сервисы запускаются обратно. Не перезаписывает существующую папку backup. Restore проверяет контрольные суммы и создаёт новый Compose project; существующие тома и проект `prohod` защищены от перезаписи.

Restore автоматически не перезаписывает системный Caddy и `.env`: эти архивы извлекаются только при восстановлении на отдельном сервере после установки пользователя/службы Caddy. Сохранить владельца `caddy` для каталога сертификатов и закрытые права `.env`. Предыдущая копия до смены домена находится в `backups/before-domain-kyssltd-20261010`.

Секреты отдельных сервисов пересоздаются из исходного тома. Веса моделей и Ollama не являются пользовательскими данными и воспроизводятся init-контейнерами. Перед запуском proxy восстановленного проекта выбрать свободные порты. Не использовать `down -v` для рабочей установки. Резервные копии содержат пароли, ключи шифрования и биометрические данные, поэтому каталог закрывается от других пользователей.

Restore создаёт базы из `template0` внутри нового проекта: образ PostGIS предварительно создаёт собственные схемы, поэтому импорт поверх них конфликтует. Настройки read-only/timeout аналитической роли восстанавливаются из PostgreSQL init; приложение также явно начинает аналитические транзакции в read-only.

Для повторения проверки сохранности на изолированной копии используется `backend/tests/verify_persistence.py`. Команды `seed`/`check` требуют `ALLOW_ACCEPTANCE_FIXTURES=isolated-restore` и том `/acceptance` для состояния проверки. Seed создаёт синтетические TEST-записи напрямую в тестовой БД; это проверка хранения смены, а не биометрического прохода. Не запускать seed на рабочем проекте.
