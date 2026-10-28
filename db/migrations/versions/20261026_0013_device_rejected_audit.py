"""F4 (QA t_877b0cb5, docs/qa-console-repro.md §F4): distinguish reject from
revoke in the audit trail.

``devices.reject()`` used to emit ``device.revoked`` with payload
``{"outcome": "rejected"}``, so filtering the audit log by ``device.rejected``
returned zero rows and the governance trail could not tell an enrollment
rejection apart from an active-device revocation. The action emitted by
reject is now ``device.rejected``; ``device.revoked`` keeps its meaning of
revoke-with-immediate-session-cut and its catalog description is narrowed
accordingly.

Data-only extension of the versioned enum (spec 002 §3.5): the new action is
seeded in ``catalog.audit_actions`` — never a bypass of the FK on
``audit_log.action``. Canonical DDL: db/schema.sql §3 (catalog.audit_actions),
parity required by db/tests/test_schema_parity.py.
"""
from alembic import op
import sqlalchemy as sa


revision = "0013_device_rejected_audit"
down_revision = "0012_custom_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        INSERT INTO catalog.audit_actions (action, description) VALUES
          ('device.rejected', 'Device enrollment rejected (pending -> rejected).')
        ON CONFLICT (action) DO NOTHING;

        UPDATE catalog.audit_actions
           SET description = 'Device revoked (sessions cut immediately).'
         WHERE action = 'device.revoked';
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM catalog.audit_actions WHERE action = 'device.rejected'"
    ))
    op.execute(sa.text(
        "UPDATE catalog.audit_actions "
        "SET description = 'Device rejected/revoked.' "
        "WHERE action = 'device.revoked'"
    ))
