-- bootstrap.sql — Roles de base de datos para Trust Agent (idempotente).
-- Ejecutar UNA vez como superusuario (postgres) antes de las migraciones.
--
-- VÍA CANÓNICA DEL COMPOSE (t_b62edff2, portabilidad): el servicio `db`
-- monta db/scripts/bootstrap.sh, que genera este mismo SQL con las
-- contraseñas de TRUST_APP_PASSWORD / TRUST_MIGRATOR_PASSWORD del .env
-- (defaults de dev: trust_app_dev / trust_migrator_dev). Este archivo
-- queda como referencia para instalaciones bare-metal SIN Docker:
--   psql -U postgres -d postgres -f db/scripts/bootstrap.sql
-- ...y en ese caso cámbialas a mano (o edítalas después con ALTER ROLE).

-- Rol de APLICACIÓN: sin BYPASSRLS, NO dueño de objetos. La app SIEMPRE conecta aquí.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trust_app') THEN
    CREATE ROLE trust_app LOGIN PASSWORD 'trust_app_dev';
  END IF;
END $$;

-- Rol de MIGRACIONES/OPS: dueño de los objetos, con BYPASSRLS para operaciones
-- cross-tenant auditadas (nunca para la aplicación). Ver skill postgresql-rls-multitenant.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trust_migrator') THEN
    CREATE ROLE trust_migrator LOGIN BYPASSRLS PASSWORD 'trust_migrator_dev';
  END IF;
END $$;
