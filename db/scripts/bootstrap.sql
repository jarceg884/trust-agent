-- bootstrap.sql — Roles de base de datos para Trust Agent (idempotente).
-- Ejecutar UNA vez como superusuario (postgres) antes de las migraciones.
-- Contraseñas de desarrollo local: cámbialas en cualquier entorno real.

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
