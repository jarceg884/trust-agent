"""Migración 0020: device_attestation (spec 019 Incremento 3, REQ-GOH-012/013).

Attestation del agente al control plane (D-GOH-06/ADR-018): cada sesión del
agente enrolado reporta el hash del bundle vigente, la policy_version
aplicada, el modo operativo y el conteo de vetos desde la última
attestation; el control plane lo registra en el audit del tenant
(``device.attestation``). Un dispositivo ``active`` sin attestation más
allá de ``attestation_overdue_hours`` (policy key existente, default 24)
genera notificación ``device_attestation_overdue`` a los org_admin y la
fila de audit ``device.attestation_overdue`` (REQ-GOH-013) — detección del
agente corriendo desgobernado (plugin deshabilitado localmente).

- ``devices`` += 2 columnas:
  ``last_attestation_at`` — último POST /api/devices/{id}/attestation
  aceptado (señal de agente reportando gobernanza, distinta del heartbeat
  de presencia de spec 027);
  ``last_attestation_overdue_at`` — soporte del dedupe de la notificación
  (máximo una notificación+audit por dispositivo por ventana de overdue,
  REQ-GOH-013).
- Nuevas acciones de audit ``device.attestation`` y
  ``device.attestation_overdue`` (extensión data-only del catálogo
  versionado — nunca bypass del FK de ``audit_log.action``).

Paridad canónica: db/schema.sql §5.9 (devices) + §3 (catalog.audit_actions),
requerida por db/tests/test_schema_parity.py.

Creada vía db/scripts/migration_guard.py (lock de serialización — ADR 0001).
"""
from alembic import op
import sqlalchemy as sa  # noqa: F401

revision = "0020_device_attestation"
down_revision = "0019_allowed_models_null_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("last_attestation_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column(
            "last_attestation_overdue_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.execute(sa.text("""
        INSERT INTO catalog.audit_actions (action, description) VALUES
          ('device.attestation',
           'Agent session attestation: bundle hash, applied policy version, operating mode and veto counts.'),
          ('device.attestation_overdue',
           'Active device with no attestation beyond attestation_overdue_hours (ungoverned agent detection).')
        ON CONFLICT (action) DO NOTHING;
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM catalog.audit_actions "
        "WHERE action IN ('device.attestation', 'device.attestation_overdue')"
    ))
    op.drop_column("devices", "last_attestation_overdue_at")
    op.drop_column("devices", "last_attestation_at")
