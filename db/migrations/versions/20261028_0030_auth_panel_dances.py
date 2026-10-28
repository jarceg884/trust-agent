"""ops.auth_dances + panel audit actions (spec 015 v1.3.0, ADR-019).

Revision ID: 0030_auth_panel_dances
Revises: 0029_sub_cent_spend_precision
Create Date: 2026-10-28

The TOTP enrollment dance (D-AUTH-14) spans two HTTP requests (setup shows
the QR, confirm submits the code): the intermediate Keycloak auth-session
state (cookies, form action, totpSecret, PKCE verifier) must survive
between them. It is PRE-authentication by nature (the user is proving who
they are), cross-request and short-lived — same plane as ops.login_throttle
(D-DB-14): the `ops` schema, no RLS by design (D-DB-01), no SECURITY
DEFINER. The handle is stored HASHED (sha256, like sessions.jti): the row
key is not a bearer credential even if the DB leaks.

Passwords are NEVER stored here (or anywhere) — only the Keycloak-side
dance state. Rows are single-purpose and expire in 10 minutes; consumed
rows are deleted by the confirm path (the app role gets DELETE on this
table — unlike login_throttle, whose counters must accumulate).

New audit actions (catalog FK, D-AU-02): auth.totp_enrolled,
auth.password_changed. The events carry NO credential material (D-AUTH-16).
"""
from alembic import op

revision = "0030_auth_panel_dances"
down_revision = "0029_sub_cent_spend_precision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.auth_dances (
          handle_hash  text PRIMARY KEY,
          kind         text NOT NULL,
          realm        text NOT NULL,
          tenant_id    uuid NOT NULL,
          email        text NOT NULL,
          state        jsonb NOT NULL,
          expires_at   timestamptz NOT NULL,
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_auth_dances_expiry "
        "ON ops.auth_dances (expires_at)"
    )
    op.execute(
        "COMMENT ON TABLE ops.auth_dances IS 'In-flight Keycloak auth "
        "dances for the auth panel (ADR-019, D-AUTH-14): TOTP enrollment "
        "state between the setup and confirm requests. Handle stored "
        "hashed; TTL 10 minutes; NO passwords ever persisted. Provider "
        "plane: no RLS.'"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE ops.auth_dances "
        "TO trust_app"
    )
    op.execute("GRANT ALL ON TABLE ops.auth_dances TO trust_migrator")
    op.execute(
        "INSERT INTO catalog.audit_actions (action, description) VALUES "
        "('auth.totp_enrolled', 'TOTP (MFA) enrolled via the auth panel.'), "
        "('auth.password_changed', 'Password set or changed via the auth panel.') "
        "ON CONFLICT (action) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ops.auth_dances")
    op.execute(
        "DELETE FROM catalog.audit_actions WHERE action IN "
        "('auth.totp_enrolled', 'auth.password_changed')"
    )
