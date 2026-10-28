"""Tenant self-signup (spec 026): estado pending_activation + audit actions.

- Extiende el CHECK de tenants.status para admitir 'pending_activation'
  (D-26-01): el tenant se provisiona completo pero nace APAGADO y sin budget.
- Acciones de audit del flujo: tenant.self_registered (signup público),
  tenant.activated + budget.assigned (activación por el proveedor).
"""
from alembic import op

revision = "0015_tenant_self_signup"
down_revision = "0014_auth_ux_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tenants DROP CONSTRAINT tenants_status_check"
    )
    op.execute(
        "ALTER TABLE tenants ADD CONSTRAINT tenants_status_check "
        "CHECK (status IN ('active','suspended','pending_activation'))"
    )
    op.execute(
        """
        INSERT INTO catalog.audit_actions (action, description) VALUES
          ('tenant.self_registered',
           'Organization created via public self-signup (spec 026, pending activation).'),
          ('tenant.activated',
           'Tenant activated by provider from pending_activation (spec 026).'),
          ('budget.assigned',
           'Initial budget assigned by the provider on activation (spec 026).')
        ON CONFLICT (action) DO NOTHING
        """
    )


def downgrade() -> None:
    # Los tenants pending no pueden existir en el estado anterior: volverlos
    # 'suspended' (fail-closed) antes de estrechar el CHECK.
    op.execute(
        "UPDATE tenants SET status = 'suspended' "
        "WHERE status = 'pending_activation'"
    )
    op.execute("ALTER TABLE tenants DROP CONSTRAINT tenants_status_check")
    op.execute(
        "ALTER TABLE tenants ADD CONSTRAINT tenants_status_check "
        "CHECK (status IN ('active','suspended'))"
    )
    op.execute(
        "DELETE FROM catalog.audit_actions WHERE action IN "
        "('tenant.self_registered','tenant.activated','budget.assigned')"
    )
