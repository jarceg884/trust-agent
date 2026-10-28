"""Migración 0027: usage_export_audit

Revision ID: 0027_usage_export_audit
Revises: 0026_retire_qwen3_32b
Create Date: 20261027

Creada vía db/scripts/migration_guard.py (lock de serialización — ADR 0001).
"""
from alembic import op
import sqlalchemy as sa  # noqa: F401

revision = "0027_usage_export_audit"
down_revision = "0026_retire_qwen3_32b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Spec 035 (usage analytics): el export de usage queda auditado con
    # la acción `usage.exported` (REQ-USG-003). Solo catálogo — sin
    # cambios de esquema (spend_ledger ya tiene todo).
    op.execute(
        "INSERT INTO catalog.audit_actions (action, description) VALUES "
        "('usage.exported', "
        "'Usage analytics CSV exported (per-user or per-request level).') "
        "ON CONFLICT (action) DO NOTHING"
    )


def downgrade() -> None:
    # Reverso exacto: retirar la acción del catálogo. Fall-closed en el
    # sentido inverso — si quedaran filas de audit_log con esta acción,
    # el DELETE del catálogo revienta por el FK (integridad primero).
    op.execute("DELETE FROM catalog.audit_actions "
               "WHERE action = 'usage.exported'")
