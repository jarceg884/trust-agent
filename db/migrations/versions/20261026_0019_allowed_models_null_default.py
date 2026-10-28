"""Fix t_f0d3122d (QA P4e/P4e1): el default de catálogo de ``allowed_models``
pasa de ``'[]'::jsonb`` a JSON ``null``.

El combinator deny-overrides (D-CP-15) resuelve una clave ausente en los
tres niveles al ``default_value`` del catálogo. Con default ``[]`` la
política efectiva de un usuario SIN ninguna fila de ``allowed_models`` era
indistinguible de un deny-all explícito — y el enforcement
(``effective_models_for_user``) no podía separar "heredar" de "denegar
todo" (el bug original usaba esa ambigüedad para IGNORAR el ``[]``
explícito).

Semántica cerrada tras el fix (spec delta 003/005/018):
- ``null`` (default, clave ausente en los 3 niveles) = sin restricción
  adicional: el conjunto efectivo es solo la intersección de aprobaciones
  org ∩ dept ∩ user. Misma convención null de ``access_overview``
  (spec 018 D-018-02: "null = sin restricción adicional").
- lista definida en cualquier nivel (aunque sea ``[]``) = intersección
  deny-overrides; ``[]`` = deny-all explícito que NINGÚN nivel puede
  relajar (el upsert ya lo acepta como valor más restrictivo, REQ-POL-007).

Los tenants con filas explícitas de ``allowed_models`` no cambian; los
tenants sin la clave pasan de mostrar ``[]`` a ``null`` en la política
efectiva (``GET /api/me/policies``) sin cambio de enforcement — ``[]``
era ignorado por el bug y ``null`` significa "sin restricción".

Paridad canónica: db/schema.sql §3 (catalog.policy_keys), requerida por
db/tests/test_schema_parity.py.
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_allowed_models_null_default"
down_revision = "0018_branding_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE catalog.policy_keys
           SET default_value = 'null'::jsonb,
               description = 'Model names allowed beyond the approved set (null = no additional restriction; [] = explicit deny-all).'
         WHERE key = 'allowed_models';
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        UPDATE catalog.policy_keys
           SET default_value = '[]'::jsonb,
               description = 'Model names allowed beyond the approved set.'
         WHERE key = 'allowed_models';
    """))
