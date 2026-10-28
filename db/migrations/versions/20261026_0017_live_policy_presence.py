"""Spec 027 (live policy + presencia, mandato Miller): el control plane
registra presencia por dispositivo y la confirmación de propagación de
policy.

- ``devices`` += 4 columnas de presencia (spec 027 D-027-06):
  ``last_heartbeat_at`` (señal de agente vivo, distinta del ``last_seen_at``
  del DeviceGate), ``applied_policy_version`` (última versión de policy que
  el dispositivo REPORTÓ aplicar), ``agent_uptime_seconds`` y
  ``last_heartbeat_audit_at`` (soporte del muestreo de audit, D-027-05).
- Nuevas acciones de audit ``device.heartbeat`` y ``policy.propagated``
  (extensión data-only del enum versionado, spec 002 §3.5 — nunca bypass
  del FK de ``audit_log.action``).

Paridad canónica: db/schema.sql §5.9 (devices) + §3 (catalog.audit_actions),
requerida por db/tests/test_schema_parity.py.

NOTA de serialización (regla 9): el head se movió mientras esta lane
trabajaba (0015_tenant_self_signup y 0016_provider_superuser de lanes
hermanas) — esta revisión se colgó del head real ``0016_provider_superuser``.
"""
from alembic import op
import sqlalchemy as sa


revision = "0017_live_policy_presence"
down_revision = "0016_provider_superuser"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column("applied_policy_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column("agent_uptime_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column("last_heartbeat_audit_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(sa.text("""
        INSERT INTO catalog.audit_actions (action, description) VALUES
          ('device.heartbeat',
           'Device heartbeat (sampled: at most one per device per audit interval).'),
          ('policy.propagated',
           'Device confirmed it applied a new policy version (heartbeat transition).')
        ON CONFLICT (action) DO NOTHING;
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM catalog.audit_actions "
        "WHERE action IN ('device.heartbeat', 'policy.propagated')"
    ))
    op.drop_column("devices", "last_heartbeat_audit_at")
    op.drop_column("devices", "agent_uptime_seconds")
    op.drop_column("devices", "applied_policy_version")
    op.drop_column("devices", "last_heartbeat_at")
