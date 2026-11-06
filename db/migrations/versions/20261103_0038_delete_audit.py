"""Migración 0038: audit actions para borrado de usuarios y dispositivos."""
from alembic import op

revision = "0038_delete_audit"
down_revision = "0037_policy_bundles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO catalog.audit_actions (action, description) VALUES "
        "  ('user.deleted', 'User deleted by an organization administrator.'), "
        "  ('device.deleted', 'Enrolled device deleted by an administrator.') "
        "ON CONFLICT (action) DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM catalog.audit_actions WHERE action IN ('user.deleted','device.deleted')"
    )
