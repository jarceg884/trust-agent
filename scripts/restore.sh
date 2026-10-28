#!/usr/bin/env bash
# restore.sh — restaura un respaldo hecho con scripts/backup.sh.
#
# PELIGRO: reemplaza TODOS los datos actuales de la DB (drop + recreate +
# restore). Keycloak (usuarios/realm/MFA) vive en su propio volumen kc_data
# y NO está en este dump — para restaurar la identidad también, recupera
# además el volumen kc_data con tu herramienta de respaldo de volúmenes.
#
# Uso: scripts/restore.sh trust-agent-backup-YYYYMMDD-HHMMSS.dump
set -euo pipefail
cd "$(dirname "$0")/.."

PREFIX="${TRUST_CONTAINER_PREFIX:-trust-agent}"
DUMP="${1:?uso: scripts/restore.sh <archivo.dump>}"
[ -f "$DUMP" ] || { echo "ERROR: no existe $DUMP" >&2; exit 1; }

echo "[restore] PELIGRO: se van a BORRAR los datos actuales de trust_agent."
read -r -p "Escribe BORRAR para continuar: " CONFIRM
[ "$CONFIRM" = "BORRAR" ] || { echo "[restore] cancelado."; exit 1; }

echo "[restore] deteniendo workers y API (dejan de escribir)..."
docker compose stop api jobs-worker spend-worker attestation-worker >/dev/null

echo "[restore] drop + recreate + restore..."
docker exec "$PREFIX-db" psql -U postgres -d postgres -c "DROP DATABASE trust_agent WITH (FORCE);"
docker exec "$PREFIX-db" psql -U postgres -d postgres -c "CREATE DATABASE trust_agent;"
docker exec -i "$PREFIX-db" pg_restore -U postgres -d trust_agent --no-owner < "$DUMP"

echo "[restore] re-creando roles si el volumen es nuevo (idempotente)..."
docker exec "$PREFIX-db" psql -U postgres -d trust_agent <<-EOSQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trust_app') THEN
    CREATE ROLE trust_app LOGIN PASSWORD 'restaurar_con_setup';
  END IF;
END \$\$;
EOSQL
echo "[restore] NOTA: si el volumen de DB es nuevo, ajusta las contraseñas de"
echo "[restore] trust_app/trust_migrator con ALTER ROLE a las de tu .env."

echo "[restore] arrancando de nuevo..."
docker compose up -d
echo "[restore] listo — verifica con scripts/smoke.sh"
