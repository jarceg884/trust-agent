"""Migración 0036: audit action para token de enrolamiento (spec 044).

Registra la acción ``device.enrollment_token_issued`` que emite el nuevo
flujo de enrolamiento iniciado por el admin (estilo Helios): el admin elige
a la persona en la consola y el control plane crea una ranura de dispositivo
ya autorizada + su token de un solo uso.

Sin tablas nuevas: reutiliza ``devices`` (``enrollment_token_hash``) y el
catálogo de acciones de auditoría (I10, data-driven).
"""
from alembic import op

revision = "0036_enrollment_token_audit"
down_revision = "0035_tenant_byok_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO catalog.audit_actions (action, description) VALUES "
        "  ('device.enrollment_token_issued', "
        "   'Admin issued a one-time device enrollment token.') "
        "ON CONFLICT (action) DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM catalog.audit_actions "
        "WHERE action = 'device.enrollment_token_issued'"
    )
