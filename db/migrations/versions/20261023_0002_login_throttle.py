"""ops.login_throttle — login rate limiting persisted in DB (REQ-SEC-006).

Revision ID: 0002_login_throttle
Revises: 0001_initial
Create Date: 2026-10-23

Resuelve D-CP-12 (spec 003) como D-DB-14 (spec 002): el conteo de fallos de
login es PRE-autenticación y cross-tenant, así que vive en el plano `ops`
(estado operativo del proveedor, sin RLS por diseño — D-DB-01) y NO como
función SECURITY DEFINER (superficie de dueño que la spec 002 evita).

Paridad: este DDL es idéntico al de db/schema.sql §4 (test AC-DB-040 compara
pg_dump de ambas vías). El COMMENT ON SCHEMA ops se re-emite para converger
con el schema.sql actualizado.
"""
from alembic import op

revision = "0002_login_throttle"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.login_throttle (
          key              text PRIMARY KEY,
          fail_count       integer NOT NULL DEFAULT 0,
          window_started_at timestamptz NOT NULL DEFAULT now(),
          last_fail_at     timestamptz NOT NULL DEFAULT now(),
          locked_until     timestamptz,
          updated_at       timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE ops.login_throttle IS 'Login rate-limit counters "
        "(REQ-SEC-006): per verified (tenant, subject) and per client IP, "
        "progressive lockout, survives API restarts. Provider plane: no "
        "tenant_id, no RLS.'"
    )
    op.execute(
        "COMMENT ON SCHEMA ops IS 'Cross-tenant provider operational state "
        "(spend polling cursor, login throttle counters). No tenant data, "
        "no RLS.'"
    )
    # INSERT/UPDATE for the app, no DELETE (AC-DB-004): expiry is by window.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE ops.login_throttle TO trust_app"
    )
    # Paridad con el grant blanket de schema.sql (GRANT ALL ... IN SCHEMA ops
    # TO trust_migrator): la tabla nace DESPUÉS del grant de 0001, así que la
    # vía de migración debe otorgarlo explícitamente (AC-DB-040).
    op.execute(
        "GRANT ALL ON TABLE ops.login_throttle TO trust_migrator"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ops.login_throttle")
    op.execute(
        "COMMENT ON SCHEMA ops IS 'Cross-tenant provider operational state "
        "(spend polling cursor). No tenant data, no RLS.'"
    )
