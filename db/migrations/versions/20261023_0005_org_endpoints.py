"""Org endpoints evolution (spec 012): directory, branding, grants-list, departments.

Revision ID: 0005_org_endpoints
Revises: 0004_governance
Create Date: 2026-10-23

DDL canónico: db/schema.sql sección 10 (embebida verbatim aquí para que la
vía Alembic produzca el mismo estado — paridad exigida por
db/tests/test_schema_parity.py). Solo INSERT de datos (nueva acción de
audit): cero DDL nuevo, la paridad pg_dump no se ve afectada.
"""
from alembic import op

revision = "0005_org_endpoints"
down_revision = "0004_governance"
branch_labels = None
depends_on = None

SCHEMA_SQL = r"""
-- ============================================================================
-- 10. Evolución spec 012 (org endpoints): directorio users, branding,
--     grants-list, departments CRUD
--     Misma sección: db/schema.sql §10 (paridad pg_dump exigida por
--     db/tests/test_schema_parity.py)
--     Solo datos: cero DDL nuevo (users/departments/tenants.branding/
--     agent_grants/jobs ya existen desde 0001/0004).
-- ============================================================================

-- 10.1 Nueva acción de audit (enum versionado, extensión por datos)
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('branding.updated', 'Tenant branding created/updated (white-label).')
ON CONFLICT (action) DO NOTHING;
"""


def upgrade() -> None:
    op.execute(SCHEMA_SQL)


def downgrade() -> None:
    # Catálogo de datos versionado: el downgrade no elimina la acción
    # (audit_log.action tiene FK y podría haber eventos emitidos).
    pass
