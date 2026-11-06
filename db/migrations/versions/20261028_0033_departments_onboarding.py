"""Migración 0033: departments_onboarding (spec 038, t_4bf95d12).

Piezas:

  1. ``tenants.structure_onboarding_pending`` — flag por tenant que arranca
     PENDIENTE para TODOS los tenants (nuevos y los 10 existentes:
     DEFAULT true en el ALTER cubre ambos — la org de Miller ve el wizard
     "Organization structure" en su próximo login, D-38-02). El wizard
     viaja por el CRUD existente de departamentos (spec 012/016); este flag
     solo decide si se presenta una vez.
  2. Acción de audit ``onboarding.structure_completed`` — INSERT-only en
     ``catalog.audit_actions`` (misma técnica que la migración 0009:
     ``audit_log.action`` tiene FK a ese catálogo y el INSERT de audit la
     exige). Payload {source: "wizard"|"skip", created, renamed, deleted};
     se emite SOLO en la transición pendiente -> completado.

Paridad schema.sql: §24 (la exige db/tests/test_schema_parity.py).
"""
from alembic import op

revision = "0033_departments_onboarding"
down_revision = "0032_model_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # DEFAULT true backfills existing tenants: onboarding pendiente para todos.
    op.execute(
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS "
        " structure_onboarding_pending boolean NOT NULL DEFAULT true"
    )
    op.execute(
        """
        INSERT INTO catalog.audit_actions (action, description) VALUES
          ('onboarding.structure_completed',
           'Organization structure onboarding completed (wizard confirm or skip).')
        ON CONFLICT (action) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM catalog.audit_actions "
        " WHERE action = 'onboarding.structure_completed'"
    )
    op.execute(
        "ALTER TABLE tenants DROP COLUMN IF EXISTS structure_onboarding_pending"
    )
