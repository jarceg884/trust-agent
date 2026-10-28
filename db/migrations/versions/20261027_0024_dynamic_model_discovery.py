"""Migración 0024: dynamic_model_discovery (spec 032).

- catalog.model_catalog: columnas tenant_id/connection_id/source/last_seen_at/
  gateway_model_id; status admite 'stale'/'removed'; UNIQUE(model_name) →
  parciales por fuente (central: model_name; byok: (connection_id, model_name)).
- vault_connections: kind 'llm' (conexiones API/LLM OpenAI-compatible).
- RLS por-tenant en catalog.model_catalog (filas byok son del tenant; las
  centrales tenant_id IS NULL siguen visibles para todos) + grants de
  escritura a trust_app para el discovery.

Paridad: db/schema.sql §3.3 (ampliada) y §14.4/§14.6 (patrón RLS).
"""
from alembic import op

revision = "0024_dynamic_model_discovery"
down_revision = "0023_device_token_reissue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- catalog.model_catalog: fuentes por conexión (spec 032 §1/§2/§7) ----
    op.execute(
        """
        ALTER TABLE catalog.model_catalog
          ADD COLUMN tenant_id uuid,
          ADD COLUMN connection_id uuid
            REFERENCES vault_connections(id) ON DELETE SET NULL,
          ADD COLUMN source text NOT NULL DEFAULT 'central',
          ADD COLUMN last_seen_at timestamptz,
          ADD COLUMN gateway_model_id text
        """
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog DROP CONSTRAINT "
        "model_catalog_status_check"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog ADD CONSTRAINT "
        "model_catalog_status_check CHECK ("
        "  status IN ('available','deprecated','stale','removed'))"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog ADD CONSTRAINT "
        "model_catalog_source_check CHECK (source IN ('central','byok'))"
    )
    # El UNIQUE(model_name) global muere: dos conexiones byok (o un byok y el
    # central) pueden ver el mismo upstream id. Namespacing byok:{conn}:{id}
    # + parciales por fuente (AC-032-07, spec 032 §7).
    op.execute(
        "ALTER TABLE catalog.model_catalog DROP CONSTRAINT "
        "model_catalog_model_name_key"
    )
    op.execute(
        "CREATE UNIQUE INDEX model_catalog_central_name_uidx "
        "  ON catalog.model_catalog (model_name) WHERE source = 'central'"
    )
    op.execute(
        "CREATE UNIQUE INDEX model_catalog_byok_uidx "
        "  ON catalog.model_catalog (connection_id, model_name) "
        "  WHERE source = 'byok'"
    )
    op.execute(
        "CREATE INDEX model_catalog_tenant_idx "
        "  ON catalog.model_catalog (tenant_id, source)"
    )
    # Coherencia fuente↔scope: central = sin tenant/conexión; byok = ambos.
    op.execute(
        "ALTER TABLE catalog.model_catalog ADD CONSTRAINT "
        "model_catalog_source_scope_check CHECK ("
        "  (source = 'central' AND tenant_id IS NULL "
        "                    AND connection_id IS NULL) OR"
        "  (source = 'byok'   AND tenant_id IS NOT NULL "
        "                    AND connection_id IS NOT NULL))"
    )

    # --- vault_connections: kind llm (spec 032 §1) --------------------------
    op.execute(
        "ALTER TABLE public.vault_connections DROP CONSTRAINT "
        "vault_connections_kind_check"
    )
    op.execute(
        "ALTER TABLE public.vault_connections ADD CONSTRAINT "
        "vault_connections_kind_check CHECK ("
        "  kind IN ('database','api_key','custom','llm'))"
    )

    # --- RLS: filas byok son del tenant (spec 032 §7, patrón §14.6) --------
    op.execute("ALTER TABLE catalog.model_catalog ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE catalog.model_catalog FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON catalog.model_catalog
          FOR ALL TO trust_app
          USING      (tenant_id IS NULL OR tenant_id = NULLIF(
                        current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id IS NULL OR tenant_id = NULLIF(
                        current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    # El discovery escribe filas byok como trust_app dentro del scope.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE catalog.model_catalog "
        "TO trust_app"
    )
    # Cross-tenant listing for the jobs worker (D-CP-10 pattern, same as
    # devices): the periodic catalog refresh enumerates tenants with active
    # llm connections via the BYPASSRLS migrator connection. vault_connections
    # (migration 0006) never granted this — the Alembic path only grants to
    # trust_app; schema.sql covers migrator with its global GRANT ALL.
    op.execute(
        "GRANT SELECT ON TABLE public.vault_connections TO trust_migrator"
    )

    # Acción de audit (enum versionado; paridad schema.sql §19.4).
    op.execute(
        """
        INSERT INTO catalog.audit_actions (action, description) VALUES
          ('model.catalog_refreshed',
           'Model catalog refreshed from a connected API (BYOK connection and/or central gateway): models discovered, updated and marked stale (spec 032).')
        ON CONFLICT (action) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM catalog.audit_actions WHERE action = "
        "'model.catalog_refreshed'"
    )
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE catalog.model_catalog "
        "FROM trust_app"
    )
    op.execute(
        "REVOKE SELECT ON TABLE public.vault_connections FROM trust_migrator"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON catalog.model_catalog")
    op.execute("ALTER TABLE catalog.model_catalog NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE catalog.model_catalog DISABLE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE public.vault_connections DROP CONSTRAINT "
        "vault_connections_kind_check"
    )
    op.execute(
        "ALTER TABLE public.vault_connections ADD CONSTRAINT "
        "vault_connections_kind_check CHECK ("
        "  kind IN ('database','api_key','custom'))"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog DROP CONSTRAINT "
        "model_catalog_source_scope_check"
    )
    op.execute("DROP INDEX IF EXISTS catalog.model_catalog_tenant_idx")
    op.execute("DROP INDEX IF EXISTS catalog.model_catalog_byok_uidx")
    op.execute("DROP INDEX IF EXISTS catalog.model_catalog_central_name_uidx")
    # Las filas byok no caben en el UNIQUE global: mueren con el downgrade.
    op.execute(
        "DELETE FROM model_access WHERE model_id IN ("
        "  SELECT id FROM catalog.model_catalog WHERE source = 'byok')"
    )
    op.execute(
        "DELETE FROM catalog.model_catalog WHERE source = 'byok'"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog ADD CONSTRAINT "
        "model_catalog_model_name_key UNIQUE (model_name)"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog DROP CONSTRAINT "
        "model_catalog_source_check"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog DROP CONSTRAINT "
        "model_catalog_status_check"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog ADD CONSTRAINT "
        "model_catalog_status_check CHECK ("
        "  status IN ('available','deprecated'))"
    )
    op.execute(
        """
        ALTER TABLE catalog.model_catalog
          DROP COLUMN gateway_model_id,
          DROP COLUMN last_seen_at,
          DROP COLUMN source,
          DROP COLUMN connection_id,
          DROP COLUMN tenant_id
        """
    )
