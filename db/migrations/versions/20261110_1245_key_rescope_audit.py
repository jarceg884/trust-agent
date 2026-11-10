"""Migración 0046: audit action para el re-alcanceo de claves del gateway.

Spec 044: una clave LiteLLM se emite con los modelos aprobados del momento;
cuando la política retira un modelo, el sync del bundle re-alcancea las
claves activas del usuario (``rescope_user_keys``) para que el gateway deje
de aceptar el modelo desautorizado. Cada re-alcanceo queda auditado.
"""
from alembic import op

revision = "0046_key_rescope_audit"
down_revision = "0045_device_token_refresh_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO catalog.audit_actions (action, description) VALUES "
        "  ('model.key_rescoped', "
        "   'Active gateway key re-scoped to the approved models of the current policy.') "
        "ON CONFLICT (action) DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM catalog.audit_actions WHERE action = 'model.key_rescoped'"
    )
