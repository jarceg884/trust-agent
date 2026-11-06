"""Migración 0040: contratos organizacionales múltiples por política."""
from alembic import op

revision = "0040_policy_contracts"
down_revision = "0039_policy_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.policy_bundles "
        "ADD COLUMN IF NOT EXISTS contracts jsonb NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.policy_bundles DROP COLUMN IF EXISTS contracts")
