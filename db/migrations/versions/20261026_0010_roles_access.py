"""Roles & access evolution (spec 017): roles.assign permission (org_admin only).

Revision ID: 0010_roles_access
Revises: 12c08d4fb858
Create Date: 2026-10-26

DDL canónico: db/schema.sql sección 13 (embebida verbatim aquí para que la
vía Alembic produzca el mismo estado — paridad exigida por
db/tests/test_schema_parity.py). Solo INSERT de datos (permiso nuevo +
grant): cero DDL nuevo; user_roles, su unique index por scope y las acciones
role.assigned/role.revoked existen desde 0001.
"""
from alembic import op

revision = "0010_roles_access"
down_revision = "12c08d4fb858"
branch_labels = None
depends_on = None

SCHEMA_SQL = r"""
-- ============================================================================
-- 13. Evolución spec 017 (roles & access): permiso roles.assign
--     Misma sección: db/schema.sql §13 (paridad pg_dump exigida por
--     db/tests/test_schema_parity.py)
--     Solo datos: cero DDL nuevo. La puerta de los endpoints de asignación
--     de roles es data-driven (constitution I10): un permiso del catálogo
--     otorgado ÚNICAMENTE a org_admin. dept_admin conserva users.manage
--     (editar depto/nombre/deshabilitar dentro de su scope) pero NO puede
--     tocar roles — sin escalada a org_admin.
-- ============================================================================

-- 13.1 Permiso nuevo + grant solo a org_admin
INSERT INTO catalog.permissions (key, description) VALUES
  ('roles.assign', 'Assign/revoke tenant roles (org_admin only).')
ON CONFLICT (key) DO NOTHING;

INSERT INTO catalog.role_permissions (role_key, permission_key) VALUES
  ('org_admin', 'roles.assign')
ON CONFLICT (role_key, permission_key) DO NOTHING;
"""


def upgrade() -> None:
    op.execute(SCHEMA_SQL)


def downgrade() -> None:
    # Catálogo de datos versionado: el downgrade no elimina el permiso
    # (role_permissions/permissions tienen FK y podría haber sesiones vivas
    # que lo cargaron).
    pass
