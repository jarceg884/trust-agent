"""ops.spend_rejections — durable evidence of rejected spend logs (QA H2).

Revision ID: 0007_spend_rejections
Revises: 0006_audit_retention
Create Date: 2026-10-23

NOTE on numbering: this migration was born as 0006 alongside the sibling
lane's 0006_audit_retention (t_884bfcb4, both revising 0005_org_endpoints).
To keep a single linear head it is chained AFTER audit_retention as 0007 —
the DDL is fully independent (ops plane vs audit triggers), so the order
carries no semantic weight.

Robustez del spend worker (QA t_82b2ffaa / H2 de docs/validacion-final.md §5):
un log del gateway que referencia un tenant inexistente en la DB debe
saltarse con evidencia auditada en lugar de tumbar el ciclo completo.
`audit_log` no puede contener ese rechazo: su `tenant_id` es FK a
`tenants` (el tenant NO existe), mismo razonamiento que D-DB-14 para
`ops.login_throttle` — sin tenant confiable, el plano `ops` del proveedor
es la vía correcta.

`tenant_id` aquí es una referencia forense a un tenant inexistente (sin
FK por diseño), NO dato de negocio de un tenant: la tabla es del plano
proveedor, sin RLS, append-only para la app (INSERT/SELECT, sin
UPDATE/DELETE). Dedupe por (tenant_id, request_id, reason): re-procesar
la misma ventana (cursor de granularidad diaria) nunca duplica evidencia.

Paridad: este DDL es idéntico al de db/schema.sql §11 (test AC-DB-040
compara pg_dump de ambas vías).
"""
from alembic import op

revision = "0007_spend_rejections"
down_revision = "0006_audit_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.spend_rejections (
          id          bigserial PRIMARY KEY,
          ts          timestamptz NOT NULL DEFAULT now(),
          tenant_id   uuid NOT NULL,
          request_id  text NOT NULL,
          reason      text NOT NULL,
          occurred_at timestamptz,
          CONSTRAINT spend_rejections_dedupe UNIQUE (tenant_id, request_id, reason)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE ops.spend_rejections IS 'Spend-log rejections "
        "(QA H2, t_82b2ffaa): gateway logs that reference a tenant that no "
        "longer exists in the DB. tenant_id is a forensic reference to a "
        "NON-existent tenant (no FK by design); provider plane: no RLS. "
        "Append-only evidence: the app holds INSERT/SELECT only.'"
    )
    # Append-only para la app (sin UPDATE/DELETE): la evidencia no se muta.
    op.execute(
        "GRANT SELECT, INSERT ON TABLE ops.spend_rejections TO trust_app"
    )
    # Pitfall #1 del skill RLS: grants de secuencias.
    op.execute(
        "GRANT USAGE ON SEQUENCE ops.spend_rejections_id_seq TO trust_app"
    )
    # Paridad con el grant blanket de schema.sql §8 (GRANT ALL ... IN SCHEMA
    # ops TO trust_migrator): la tabla nace DESPUÉS de ese grant, así que la
    # vía de migración debe otorgarlo explícitamente (AC-DB-040).
    op.execute(
        "GRANT ALL PRIVILEGES ON TABLE ops.spend_rejections TO trust_migrator"
    )
    op.execute(
        "GRANT USAGE, SELECT, UPDATE ON SEQUENCE ops.spend_rejections_id_seq "
        "TO trust_migrator"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ops.spend_rejections")
