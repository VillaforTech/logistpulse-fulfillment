#!/usr/bin/env bash
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres --set=analyst_password="$ANALYTICS_PASSWORD" <<'SQL'
CREATE ROLE analytics LOGIN PASSWORD :'analyst_password';
CREATE DATABASE analytics_db OWNER analytics;
REVOKE CONNECT ON DATABASE inventory_db, logistics_db, fulfillment_db FROM PUBLIC;
REVOKE CONNECT ON DATABASE analytics_db FROM PUBLIC;
GRANT CONNECT ON DATABASE analytics_db TO analytics;
SQL
