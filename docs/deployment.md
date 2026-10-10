# Сервер Linux

Рабочий каталог установки: `/home/ubuntu/prohod`. Публичный адрес тестовой установки — `https://195.209.221.213`. В `.env` до первого запуска заданы `APP_HOST=195.209.221.213` и `PUBLIC_ORIGIN=https://195.209.221.213`; параметры GigaChat остаются пустыми до получения ключа.

```bash
cd /home/ubuntu/prohod
cp compose.gpu.yaml compose.override.yaml
sudo docker compose up --build -d
sudo docker compose ps -a
sudo docker compose logs --tail=50 backend model-init llm-init ollama
sudo docker compose --profile test run --rm tests
sudo docker compose --profile test run --rm browser-tests
```

Первый GPU-запрос Ollama может компилировать CUDA-ядра около 44 секунд. Init после проверки digest прогревает модель через общий `ModelGateway`; дождитесь завершения `llm-init`. CUDA cache сохраняется в томе Ollama.

Override автоматически применяется обычной командой Compose. GPU выделена только API биометрии, его тестам и Ollama; остальным сервисам GPU не нужна. ONNX Runtime CUDA 12.8/cuDNN 9 использует YOLO и MiniFASNet, OpenCV SFace/YuNet остаются на CPU. Настройка GPU соответствует [Docker](https://docs.docker.com/compose/how-tos/gpu-support/), библиотеки и загрузка provider — [ONNX Runtime](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).

Снаружи опубликованы только 80/443; SSH остаётся на 22. PostgreSQL, S3 и Ollama доступны внутри сети Docker. У всех начальных аккаунтов случайные пароли, доступные только владельцу Docker:

```bash
sudo docker compose run --rm --no-deps --entrypoint cat secrets /bootstrap/accounts.json
sudo docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./prohod-root.crt
```

Сертификат Caddy — собственный корневой CA. Его нужно доверить на устройстве, открывающем сайт для камеры/GPS. Ключ CA остаётся в приватном Docker-томе; распространяется только `root.crt`. Для публичного домена можно заменить `tls internal` на автоматический сертификат Caddy и согласованно перенастроить issuer Keycloak.

## Резервное копирование и восстановление

```bash
sudo bash scripts/backup.sh backups/acceptance
sudo bash scripts/restore.sh backups/acceptance prohod-restore-test
```

Backup кратковременно останавливает запись API, worker, Keycloak и S3, сохраняет обе БД, S3/IAM, исходные и runtime секреты, сертификаты и SHA-256. При ошибке сервисы запускаются обратно. Не перезаписывает существующую папку backup. Restore проверяет контрольные суммы и создаёт новый Compose project; существующие тома и проект `prohod` защищены от перезаписи.

Секреты отдельных сервисов пересоздаются из исходного тома. Веса моделей и Ollama не являются пользовательскими данными и воспроизводятся init-контейнерами. Перед запуском proxy восстановленного проекта выбрать свободные порты. Не использовать `down -v` для рабочей установки. Резервные копии содержат пароли, ключи шифрования и биометрические данные, поэтому каталог закрывается от других пользователей.

Restore создаёт базы из `template0` внутри нового проекта: образ PostGIS предварительно создаёт собственные схемы, поэтому импорт поверх них конфликтует. Настройки read-only/timeout аналитической роли восстанавливаются из PostgreSQL init; приложение также явно начинает аналитические транзакции в read-only.

Для повторения проверки сохранности на изолированной копии используется `backend/tests/verify_persistence.py`. Команды `seed`/`check` требуют `ALLOW_ACCEPTANCE_FIXTURES=isolated-restore` и том `/acceptance` для состояния проверки. Seed создаёт синтетические TEST-записи напрямую в тестовой БД; это проверка хранения смены, а не биометрического прохода. Не запускать seed на рабочем проекте.
