"""Migración 0023: device_token_reissue

Revision ID: 0023_device_token_reissue
Revises: 0022_policy_catalog_v2
Create Date: 20261027

Creada vía db/scripts/migration_guard.py (lock de serialización — ADR 0001).

Spec 022 v1.1.0 (amend D-HRB-03 / REQ-HRB-010b, gap t_0b6e8e2d): re-emisión
DIRECTA del device token en ``POST /api/devices/enroll`` para un device
``active`` del mismo usuario con id_token MFA fresco — la recuperación
documentada del token expirado (agente apagado > TTL). Solo datos: la
re-emisión se audita como ``device.token_reissued`` (extensión data-only del
catálogo versionado — nunca bypass del FK de ``audit_log.action``). Cero DDL.

Paridad canónica: db/schema.sql §18 (la exige db/tests/test_schema_parity.py).
"""
from alembic import op
import sqlalchemy as sa  # noqa: F401

revision = "0023_device_token_reissue"
down_revision = "0022_policy_catalog_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        INSERT INTO catalog.audit_actions (action, description) VALUES
          ('device.token_reissued',
           'Device token re-issued directly on enroll: already-active device of the same user re-proved identity with a fresh MFA id_token (expired-token recovery, spec 022 D-HRB-03).')
        ON CONFLICT (action) DO NOTHING;
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM catalog.audit_actions WHERE action = 'device.token_reissued'"
    ))
