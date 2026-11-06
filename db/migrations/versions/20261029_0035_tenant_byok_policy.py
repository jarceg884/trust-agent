"""Migración 0035: tenant_byok_policy (spec 043, mandato Miller).

El provider decide por cliente si puede usar BYOK propio (Bring Your Own
Key). Si byok_allowed=false, el Admin Console del cliente NO MUESTRA la
opción BYOK en ninguna parte — no un botón deshabilitado ni un candado:
LA OPCIÓN NO EXISTE para ese cliente (ocultación total, D-043-03).

Piezas:

  1. ``ops.tenant_byok_policy`` — una fila por tenant (PK tenant_id), flag
     ``byok_allowed`` (default true = backwards-compatible: los tenants
     existentes siguen permitiendo BYOK), ``set_at`` + ``set_by`` (auditoría
     del operador). Sin RLS — acceso solo vía API del provider (grants
     trust_app SELECT/INSERT/UPDATE, trust_migrator ALL).

Paridad schema.sql: sección ops (exigida por db/tests/test_schema_parity.py).
"""
from alembic import op

revision = "0035_tenant_byok_policy"
down_revision = "0034_named_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ops.tenant_byok_policy (
          tenant_id     uuid PRIMARY KEY
                        REFERENCES tenants(id) ON DELETE CASCADE,
          byok_allowed  boolean NOT NULL DEFAULT true,
          set_at        timestamptz NOT NULL DEFAULT now(),
          set_by        text
        )
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON ops.tenant_byok_policy TO trust_app")
    op.execute("GRANT ALL ON ops.tenant_byok_policy TO trust_migrator")
    # Audit action for the BYOK policy toggle (ops.provider_audit has FK →
    # catalog.audit_actions, data-driven I10).
    op.execute(
        "INSERT INTO catalog.audit_actions (action, description) VALUES "
        "  ('provider.byok_policy_set', "
        "   'BYOK policy toggled for a tenant by a provider operator.') "
        "ON CONFLICT (action) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ops.tenant_byok_policy")
    op.execute(
        "DELETE FROM catalog.audit_actions WHERE action = 'provider.byok_policy_set'"
    )
