"""devices.enrollment_token_hash — spec 007 (enrolamiento de primera clase).

Revision ID: 0003_devices_enrollment_token
Revises: 0002_login_throttle
Create Date: 2026-10-23

ALTER TABLE ADD COLUMN appends the column as the LAST column of the table,
matching its position in db/schema.sql (parity test db/tests/test_schema_parity.py
compares pg_dump of both routes). Apilada sobre 0002_login_throttle (tarea
hermana t_4da60ada) para mantener una única cabeza de migración.
"""
from alembic import op

revision = "0003_devices_enrollment_token"
down_revision = "0002_login_throttle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE devices ADD COLUMN enrollment_token_hash text"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE devices DROP COLUMN enrollment_token_hash"
    )
