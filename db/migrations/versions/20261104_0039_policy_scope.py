"""Migración 0039: política única (spec 044 v2, mandato Miller).

Una política se asigna a UN departamento o a UNA persona (nunca a la
organización entera, nunca a ambos). Además del mapa de Desktop UI y el prompt
organizacional, la política define:
  • ``models``        — modelos a los que esa política da acceso.
  • ``default_model`` — modelo por defecto de la política.
  • ``agents``        — agentes compartidos incluidos en la política.
"""
from alembic import op

revision = "0039_policy_scope"
down_revision = "0038_delete_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.policy_bundles
          ADD COLUMN IF NOT EXISTS user_id uuid,
          ADD COLUMN IF NOT EXISTS models jsonb NOT NULL DEFAULT '[]'::jsonb,
          ADD COLUMN IF NOT EXISTS default_model text,
          ADD COLUMN IF NOT EXISTS agents jsonb NOT NULL DEFAULT '[]'::jsonb
        """
    )
    op.execute(
        """
        ALTER TABLE public.policy_bundles
          ADD CONSTRAINT policy_bundles_single_scope
          CHECK (department_id IS NULL OR user_id IS NULL)
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS policy_bundles_tenant_user_idx "
        "ON public.policy_bundles (tenant_id, user_id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.policy_bundles DROP CONSTRAINT IF EXISTS policy_bundles_single_scope")
    op.execute("DROP INDEX IF EXISTS public.policy_bundles_tenant_user_idx")
    op.execute(
        "ALTER TABLE public.policy_bundles "
        "DROP COLUMN IF EXISTS agents, DROP COLUMN IF EXISTS default_model, "
        "DROP COLUMN IF EXISTS models, DROP COLUMN IF EXISTS user_id"
    )
