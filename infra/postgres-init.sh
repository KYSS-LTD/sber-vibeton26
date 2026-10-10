#!/bin/sh
set -eu
APP_PASSWORD=$(cat /bootstrap/db_app)
ANALYTICS_PASSWORD=$(cat /bootstrap/db_analytics)
KC_PASSWORD=$(cat /bootstrap/db_keycloak)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_password="$APP_PASSWORD" -v analytics_password="$ANALYTICS_PASSWORD" -v kc_password="$KC_PASSWORD" <<'SQL'
CREATE ROLE prohod_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE prohod_analytics LOGIN PASSWORD :'analytics_password' NOSUPERUSER NOBYPASSRLS;
ALTER ROLE prohod_analytics SET default_transaction_read_only=on;
ALTER ROLE prohod_analytics SET statement_timeout='5s';
CREATE ROLE keycloak LOGIN PASSWORD :'kc_password' NOSUPERUSER NOBYPASSRLS;
CREATE DATABASE keycloak OWNER keycloak;
SQL
