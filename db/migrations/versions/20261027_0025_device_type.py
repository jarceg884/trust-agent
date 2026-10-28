"""Migración 0025: device_type

Revision ID: 0025_device_type
Revises: 0024_dynamic_model_discovery
Create Date: 20261027

Creada vía db/scripts/migration_guard.py (lock de serialización — ADR 0001).
"""
from alembic import op
import sqlalchemy as sa  # noqa: F401

revision = "0025_device_type"
down_revision = "0024_dynamic_model_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Spec 034 (cli-governed, D-034-04): tipo de dispositivo — distingue un
    # Hermes CLI (instalador curl|bash ligado a la org) de la app desktop.
    # DEFAULT 'desktop' = retrocompatible: todos los devices existentes son
    # desktop. Paridad con db/schema.sql §5.9 (columna al final de la tabla).
    op.execute(
        "ALTER TABLE devices ADD COLUMN device_type text "
        "NOT NULL DEFAULT 'desktop' "
        "CHECK (device_type IN ('desktop','cli'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE devices DROP COLUMN device_type")
