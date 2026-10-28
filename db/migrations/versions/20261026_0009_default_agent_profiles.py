"""Perfiles de agente default del producto (spec 020, t_0ed22c94).

Revision ID: 0009_default_agent_profiles
Revises: 0008_model_catalog_glm52
Create Date: 2026-10-26

Adoptar un perfil default crea una INSTANCIA del agente para el tenant
(no global): una fila normal de `agents` con `profile_key` (columna
nueva). El índice único parcial garantiza UNA instancia viva por
tenant y perfil — la segunda adopción del mismo perfil en el mismo
tenant se rechaza en la API con 422.

La acción de audit `agent.profile_adopted` se siembra en
catalog.audit_actions (contrato spec 008 §5): audit_log.action tiene
FK a ese catálogo y el INSERT de audit la exige.

Espejo idéntico de db/schema.sql §3.5/§9.2 (paridad AC-DB-040).
"""

from alembic import op

revision = "0009_default_agent_profiles"
down_revision = "0008_model_catalog_glm52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS profile_key text NULL"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS agents_tenant_profile_uidx
          ON agents (tenant_id, profile_key)
          WHERE profile_key IS NOT NULL AND status = 'active'
        """
    )
    op.execute(
        """
        INSERT INTO catalog.audit_actions (action, description) VALUES
          ('agent.profile_adopted',
           'Default agent profile adopted (tenant instance).')
        ON CONFLICT (action) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM catalog.audit_actions WHERE action = 'agent.profile_adopted'"
    )
    op.execute("DROP INDEX IF EXISTS agents_tenant_profile_uidx")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS profile_key")
