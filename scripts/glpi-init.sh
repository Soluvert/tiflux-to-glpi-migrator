#!/usr/bin/env bash
# ============================================================================
# glpi-init.sh — Aguarda MySQL + GLPI e configura API para migração
# Roda como entrypoint do serviço "glpi-init" no docker-compose.
# ============================================================================
set -euo pipefail

: "${GLPI_DB_HOST:=db}"
: "${GLPI_DB_USER:=glpi}"
: "${GLPI_DB_PASSWORD:=glpi}"
: "${GLPI_DB_NAME:=glpi}"

echo "[glpi-init] Aguardando MySQL ficar disponível..."
until mysqladmin ping -h"$GLPI_DB_HOST" -u"$GLPI_DB_USER" -p"$GLPI_DB_PASSWORD" --silent 2>/dev/null; do
  sleep 2
done
echo "[glpi-init] MySQL pronto."

echo "[glpi-init] Aguardando tabela glpi_configs existir (GLPI instalado)..."
until mysql -h"$GLPI_DB_HOST" -u"$GLPI_DB_USER" -p"$GLPI_DB_PASSWORD" "$GLPI_DB_NAME" \
      -e "SELECT 1 FROM glpi_configs LIMIT 1" &>/dev/null; do
  sleep 3
done
echo "[glpi-init] GLPI schema detectado."

echo "[glpi-init] Habilitando API REST e expandindo IP range..."
mysql -h"$GLPI_DB_HOST" -u"$GLPI_DB_USER" -p"$GLPI_DB_PASSWORD" "$GLPI_DB_NAME" <<'SQL'
-- Habilitar API
UPDATE glpi_configs SET value = 1 WHERE name = 'enable_api';
UPDATE glpi_configs SET value = 1 WHERE name = 'enable_api_login_credentials';
UPDATE glpi_configs SET value = 1 WHERE name = 'enable_api_login_external_token';

-- Abrir range de IP para API client (0.0.0.0 — 255.255.255.255)
UPDATE glpi_apiclients
   SET ipv4_range_start = 0,
       ipv4_range_end   = 4294967295,
       is_active        = 1
 WHERE id = 1;
SQL

echo "[glpi-init] API configurada com sucesso."
