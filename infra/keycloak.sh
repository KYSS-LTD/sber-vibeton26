#!/bin/bash
set -eu
export KC_DB_PASSWORD="$(cat /bootstrap/db_keycloak)"
export KC_BOOTSTRAP_ADMIN_PASSWORD="$(cat /bootstrap/kc_admin)"
mkdir -p /opt/keycloak/data/import
cp /bootstrap/realm.json /opt/keycloak/data/import/prohod.json
exec /opt/keycloak/bin/kc.sh start --import-realm
