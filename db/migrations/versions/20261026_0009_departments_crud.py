"""Departments CRUD evolution (spec 016): rename + delete audit actions.

Revision ID: 0009_departments_crud
Revises: 0008_model_catalog_glm52
Create Date: 2026-10-26

DDL canónico: db/schema.sql sección 12 (embebida verbatim aquí para que la
vía Alembic produzca el mismo estado — paridad exigida por
db/tests/test_schema_parity.py). Solo INSERT de datos (nuevas acciones de
audit): cero DDL nuevo, la paridad pg_dump no se ve afectada.
"""
from alembic import op

revision = "0009_departments_crud"
down_revision = "0008_model_catalog_glm52"
branch_labels = None
depends_on = None

SCHEMA_SQL = r"""
-- ============================================================================
-- 12. Evolución spec 016 (departments CRUD): rename + delete
--     Misma sección: db/schema.sql §12 (paridad pg_dump exigida por
--     db/tests/test_schema_parity.py)
--     Solo datos: cero DDL nuevo (departments y todas las FK ya existen
--     desde 0001; las reglas de negocio viven en app/org/departments.py).
-- ============================================================================

-- 11.1 Nuevas acciones de audit (enum versionado, extensión por datos)
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('department.renamed', 'Department renamed (old and new name in payload).'),
  ('department.deleted', 'Department deleted (empty department; config cascaded, payload is self-sufficient).')
ON CONFLICT (action) DO NOTHING;
"""


def upgrade() -> None:
    op.execute(SCHEMA_SQL)


def downgrade() -> None:
    # Catálogo de datos versionado: el downgrade no elimina las acciones
    # (audit_log.action tiene FK y podría haber eventos emitidos).
    pass
