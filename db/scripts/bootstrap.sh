#!/bin/sh
# bootstrap.sh — Roles canónicos de la DB con contraseñas del entorno
# (t_b62edff2, portabilidad "desplegable donde sea").
#
# El servicio `db` del compose monta este script en
# /docker-entrypoint-initdb.d/01-roles.sh: el entrypoint oficial de la
# imagen postgres lo ejecuta con psql EN LA PRIMERA inicialización del
# volumen (volúmenes ya inicializados no lo re-corren — idempotente de
# todos modos por los IF NOT EXISTS).
#
# Equivalente 1:1 a db/scripts/bootstrap.sql (referencia bare-metal),
# pero con las contraseñas de TRUST_APP_PASSWORD / TRUST_MIGRATOR_PASSWORD
# (defaults de dev: trust_app_dev / trust_migrator_dev).
#
# NOTA: las contraseñas se interpolan en SQL con comillas simples —
# no uses comillas simples en los valores del .env.
set -eu

APP_PASSWORD="${TRUST_APP_PASSWORD:-trust_app_dev}"
MIGRATOR_PASSWORD="${TRUST_MIGRATOR_PASSWORD:-trust_migrator_dev}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
-- Rol de APLICACIÓN: sin BYPASSRLS, NO dueño de objetos. La app SIEMPRE conecta aquí.
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trust_app') THEN
    CREATE ROLE trust_app LOGIN PASSWORD '${APP_PASSWORD}';
  END IF;
END \$\$;

-- Rol de MIGRACIONES/OPS: dueño de los objetos, con BYPASSRLS para
-- operaciones cross-tenant auditadas (nunca para la aplicación).
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trust_migrator') THEN
    CREATE ROLE trust_migrator LOGIN BYPASSRLS PASSWORD '${MIGRATOR_PASSWORD}';
  END IF;
END \$\$;
EOSQL
