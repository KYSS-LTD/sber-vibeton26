#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
source_path=$(realpath "${1:?Specify backup directory}")
project=${2:?Specify a NEW project name}
[[ "$project" =~ ^[a-z][a-z0-9-]+$ && "$project" != prohod ]] || { echo 'Use a new project name' >&2; exit 1; }
existing=$(docker volume ls --format '{{.Name}}')
if printf '%s\n' "$existing" | grep -q "^${project}_"; then
  echo 'Target project already has volumes; refusing to overwrite' >&2; exit 1
fi
for file in manifest.json checksums.sha256 prohod.dump keycloak.dump objects.tar.gz bootstrap_secrets.tar.gz runtime_secrets.tar.gz certificates.tar.gz; do
  [[ -f "$source_path/$file" ]] || { echo "Missing backup: $file" >&2; exit 1; }
done
(cd "$source_path" && sha256sum -c checksums.sha256)
image='python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e'
for kind in objects bootstrap_secrets runtime_secrets certificates; do
  docker volume create "${project}_${kind}" >/dev/null
  docker run --rm --mount "type=volume,source=${project}_${kind},target=/target" \
    --mount "type=bind,source=$source_path,target=/backup,readonly" "$image" \
    tar xzf "/backup/$kind.tar.gz" -C /target
done
compose=(docker compose -p "$project")
"${compose[@]}" up -d postgres
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if "${compose[@]}" exec -T postgres pg_isready -h 127.0.0.1 -U prohod_owner -d prohod >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
[[ "$ready" == 1 ]] || { echo 'PostgreSQL did not become ready' >&2; exit 1; }
# The PostGIS image precreates tiger/topology in its initial database. Restore
# into empty template0 databases in this NEW project to avoid schema collisions.
"${compose[@]}" exec -T postgres sh -c 'PGPASSWORD=$(cat /bootstrap/db_owner) psql -X -U prohod_owner -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE prohod" -c "CREATE DATABASE prohod OWNER prohod_owner TEMPLATE template0" -c "DROP DATABASE keycloak" -c "CREATE DATABASE keycloak OWNER keycloak TEMPLATE template0"'
for database in prohod keycloak; do
  "${compose[@]}" cp "$source_path/$database.dump" "postgres:/tmp/$database.dump"
  "${compose[@]}" exec -T postgres sh -c "PGPASSWORD=\$(cat /bootstrap/db_owner) pg_restore --exit-on-error -U prohod_owner -d $database /tmp/$database.dump"
done
echo "Restored into separate project $project. Start services after choosing unused proxy ports."
