"""Migración 0045: audit action para el refresh de device tokens expirados.

Spec 022 v1.2.0 (D-HRB-03 amend 2): un escritorio activado por token
one-time (spec 044) que estuvo cerrado más que el TTL canjea su device token
expirado —auténtico y emitido dentro de la ventana máxima— por uno fresco en
``POST /api/devices/token/refresh``. Cada refresh queda auditado.
"""
from alembic import op

revision = "0045_device_token_refresh_audit"
down_revision = "0044_user_department_required"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO catalog.audit_actions (action, description) VALUES "
        "  ('device.token_refreshed', "
        "   'Expired device token of an active device refreshed by the device itself.') "
        "ON CONFLICT (action) DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM catalog.audit_actions WHERE action = 'device.token_refreshed'"
    )
