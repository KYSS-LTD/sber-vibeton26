#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
project=${2:-prohod}
[[ "$project" =~ ^[a-z][a-z0-9-]+$ ]] || { echo 'Invalid project name' >&2; exit 1; }
destination=${1:-"backups/$(date -u +%Y%m%dT%H%M%SZ)"}
[[ ! -e "$destination" ]] || { echo 'Backup destination already exists' >&2; exit 1; }
mkdir -p "$destination"
destination=$(realpath "$destination")
compose=(docker compose -p "$project")
image='python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e'
resume() { "${compose[@]}" start postgres s3 keycloak backend worker storage-worker frontend; }
trap resume EXIT
"${compose[@]}" stop backend worker storage-worker frontend keycloak
for database in prohod keycloak; do
  "${compose[@]}" exec -T postgres sh -c "PGPASSWORD=\$(cat /bootstrap/db_owner) pg_dump -U prohod_owner -d $database -Fc -f /tmp/$database.dump"
  "${compose[@]}" cp "postgres:/tmp/$database.dump" "$destination/$database.dump"
done
"${compose[@]}" stop s3
for kind in objects bootstrap_secrets runtime_secrets certificates; do
  docker volume inspect "${project}_${kind}" >/dev/null
  docker run --rm --mount "type=volume,source=${project}_${kind},target=/source,readonly" \
    --mount "type=bind,source=$destination,target=/backup" "$image" \
    tar czf "/backup/$kind.tar.gz" -C /source .
done
printf '{"format":1,"project":"%s","created_at":"%s"}\n' "$project" "$(date -u +%FT%TZ)" > "$destination/manifest.json"
(cd "$destination" && sha256sum *.dump *.tar.gz manifest.json > checksums.sha256)
chmod 600 "$destination"/*
echo "Backup saved: $destination (contains credentials and biometric data)"
