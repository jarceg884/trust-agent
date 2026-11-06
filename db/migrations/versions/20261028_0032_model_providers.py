"""Migración 0032: model_providers (spec 036, repro Miller t_64e645c1).

El provider registra proveedores de modelos (endpoint OpenAI-compatible +
key en envelope del vault) y decide por tenant cuáles quedan habilitados.
Los modelos descubiertos viven en catalog.model_catalog como
source='provider' (una fila por tenant habilitado, namespaced
prov:{provider_id}:{upstream_id}) y se aprueban a nivel org al habilitar
(mismo sentido que el model_plan del provisioning).

Piezas:

  1. ``ops.model_providers`` — registro del provider (name, base_url,
     status, pricing declarado, key SOLO como envelope ciphertext; el
     descifrado requiere el KEK server-side, jamás en respuestas/logs).
  2. ``ops.model_provider_tenants`` — habilitación por cliente (la
     "política de accesos" del proveedor: sin fila o enabled=false el
     cliente no ve los modelos del proveedor).
  3. ``catalog.model_catalog`` — ``provider_id`` + source 'provider'
     (scope: tenant_id NOT NULL AND provider_id NOT NULL AND connection_id
     IS NULL) + UNIQUE parcial (tenant_id, model_name) por fuente.
  4. ``model_access.mode`` += 'provider' (resolve_mode lo estampa en el
     ledger) Y ``spend_ledger.mode`` += 'provider' (espejo D-GOV-06 — el
     ingest del spend-worker revienta sin esto); el gate compuesto es
     espejo del byok D-032-04.
  5. Permiso ``providers.manage`` (data-driven, D-ONB-02) + grant a
     platform_admin; acciones de audit ``model.provider_enabled`` /
     ``model.provider_disabled`` (los eventos del operador viven en
     ops.provider_audit, spec 025 D-PS-07).

Paridad schema.sql: sección 3.x/§18 (exigida por db/tests/test_schema_parity.py).
"""
from alembic import op

revision = "0032_model_providers"
down_revision = "0031_tenant_deprovision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- 1. ops.model_providers (dominio del provider, patrón ops) ---------
    op.execute(
        """
        CREATE TABLE ops.model_providers (
          id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          name                     text NOT NULL UNIQUE,
          display_name             text,
          base_url                 text NOT NULL,
          status                   text NOT NULL DEFAULT 'active'
                                     CHECK (status IN ('active','disabled')),
          -- Key en envelope del vault (mismo crypto que vault_connections):
          -- ciphertext + DEK encapsulada + nonce. El descifrado es en
          -- memoria, por uso, server-side (KEK); jamás en respuestas/logs.
          ciphertext_secret        bytea NOT NULL,
          ciphertext_dek           bytea NOT NULL,
          nonce                    bytea NOT NULL,
          key_id                   text NOT NULL,
          -- Pricing declarado por el provider (¢/M USD). NULL = desconocido
          -- (mismo contrato BYOK declarado-0, D-032-01).
          input_price_cents_per_m  numeric(12,3) CHECK (input_price_cents_per_m >= 0),
          output_price_cents_per_m numeric(12,3) CHECK (output_price_cents_per_m >= 0),
          created_at               timestamptz NOT NULL DEFAULT now(),
          updated_at               timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # --- 2. Habilitación por cliente (la política de accesos) --------------
    op.execute(
        """
        CREATE TABLE ops.model_provider_tenants (
          provider_id  uuid NOT NULL REFERENCES ops.model_providers(id)
                         ON DELETE CASCADE,
          tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          enabled      boolean NOT NULL DEFAULT true,
          enabled_at   timestamptz NOT NULL DEFAULT now(),
          disabled_at  timestamptz,
          PRIMARY KEY (provider_id, tenant_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX model_provider_tenants_tenant_idx "
        "  ON ops.model_provider_tenants (tenant_id)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE ops.model_providers TO trust_app"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
        "ops.model_provider_tenants TO trust_app"
    )
    op.execute(
        "GRANT ALL PRIVILEGES ON TABLE ops.model_providers TO trust_migrator"
    )
    op.execute(
        "GRANT ALL PRIVILEGES ON TABLE ops.model_provider_tenants "
        "TO trust_migrator"
    )

    # --- 3. catalog.model_catalog: fuente 'provider' ------------------------
    op.execute(
        "ALTER TABLE catalog.model_catalog "
        "ADD COLUMN provider_id uuid REFERENCES ops.model_providers(id) "
        "  ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog DROP CONSTRAINT "
        "model_catalog_source_check"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog ADD CONSTRAINT "
        "model_catalog_source_check CHECK ("
        "  source IN ('central','byok','provider'))"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog DROP CONSTRAINT "
        "model_catalog_source_scope_check"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog ADD CONSTRAINT "
        "model_catalog_source_scope_check CHECK ("
        "  (source = 'central'   AND tenant_id IS NULL AND connection_id IS NULL"
        "                       AND provider_id IS NULL) OR"
        "  (source = 'byok'      AND tenant_id IS NOT NULL"
        "                       AND connection_id IS NOT NULL) OR"
        "  (source = 'provider'  AND tenant_id IS NOT NULL"
        "                       AND provider_id IS NOT NULL"
        "                       AND connection_id IS NULL))"
    )
    op.execute(
        "CREATE UNIQUE INDEX model_catalog_provider_uidx "
        "  ON catalog.model_catalog (tenant_id, model_name) "
        "  WHERE source = 'provider'"
    )

    # --- 4. model_access.mode += 'provider' --------------------------------
    op.execute(
        "ALTER TABLE public.model_access DROP CONSTRAINT model_access_mode_check"
    )
    op.execute(
        "ALTER TABLE public.model_access ADD CONSTRAINT "
        "model_access_mode_check CHECK (mode IN ('central','byok','provider'))"
    )
    # spend_ledger.mode espeja model_access.mode (D-GOV-06: resolve_mode
    # estampa el ledger en el ingest) — sin esto, el spend-worker revienta
    # con CheckViolation al ingerir una invocación de un modelo provider y
    # congela el cursor para TODOS los tenants (hallazgo del run de
    # evidencia AC-036-08 contra el stack real).
    op.execute(
        "ALTER TABLE public.spend_ledger DROP CONSTRAINT "
        "spend_ledger_mode_check"
    )
    op.execute(
        "ALTER TABLE public.spend_ledger ADD CONSTRAINT "
        "spend_ledger_mode_check CHECK (mode IN ('central','byok','provider'))"
    )

    # --- 5. Permiso + acciones de audit (extensión por datos) --------------
    op.execute(
        "INSERT INTO catalog.permissions (key, description) VALUES "
        "  ('providers.manage', "
        "   'Register/refresh model providers and enable them per tenant "
        "(provider).') "
        "ON CONFLICT (key) DO NOTHING"
    )
    op.execute(
        "INSERT INTO catalog.role_permissions (role_key, permission_key) "
        "VALUES ('platform_admin', 'providers.manage') "
        "ON CONFLICT (role_key, permission_key) DO NOTHING"
    )
    op.execute(
        "INSERT INTO catalog.audit_actions (action, description) VALUES "
        "  ('model.provider_enabled', "
        "   'Provider model source enabled for the tenant by the provider "
        "(models approved at org level).'), "
        "  ('model.provider_disabled', "
        "   'Provider model source disabled for the tenant by the provider "
        "(its models went stale).') "
        "ON CONFLICT (action) DO NOTHING"
    )
    # Operator-plane events (ops.provider_audit has FK → audit_actions,
    # data-driven I10): without these rows every provider action above
    # dies as ForeignKeyViolation.
    op.execute(
        "INSERT INTO catalog.audit_actions (action, description) VALUES "
        "  ('provider.created', "
        "   'Model provider registered by a provider operator (key in vault "
        "envelope, never in the payload).'), "
        "  ('provider.refreshed', "
        "   'Model provider catalog refreshed by a provider operator (real "
        "upstream fetch).'), "
        "  ('provider.tenant_enabled', "
        "   'Model provider enabled for a tenant by a provider operator.'), "
        "  ('provider.tenant_disabled', "
        "   'Model provider disabled for a tenant by a provider operator.'), "
        "  ('provider.suspended', "
        "   'Model provider suspended globally by a provider operator.'), "
        "  ('provider.reactivated', "
        "   'Model provider reactivated globally by a provider operator.') "
        "ON CONFLICT (action) DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM catalog.audit_actions WHERE action IN "
        "  ('model.provider_enabled','model.provider_disabled',"
        "   'provider.created','provider.refreshed',"
        "   'provider.tenant_enabled','provider.tenant_disabled',"
        "   'provider.suspended','provider.reactivated')"
    )
    op.execute(
        "DELETE FROM catalog.role_permissions WHERE permission_key = "
        "  'providers.manage'"
    )
    op.execute(
        "DELETE FROM catalog.permissions WHERE key = 'providers.manage'"
    )
    op.execute(
        "ALTER TABLE public.model_access DROP CONSTRAINT model_access_mode_check"
    )
    op.execute(
        "ALTER TABLE public.model_access ADD CONSTRAINT "
        "model_access_mode_check CHECK (mode IN ('central','byok'))"
    )
    op.execute(
        "ALTER TABLE public.spend_ledger DROP CONSTRAINT "
        "spend_ledger_mode_check"
    )
    op.execute(
        "ALTER TABLE public.spend_ledger ADD CONSTRAINT "
        "spend_ledger_mode_check CHECK (mode IN ('central','byok'))"
    )
    op.execute(
        "DROP INDEX IF EXISTS catalog.model_catalog_provider_uidx"
    )
    op.execute(
        "DELETE FROM catalog.model_catalog WHERE source = 'provider'"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog DROP CONSTRAINT "
        "model_catalog_source_scope_check"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog ADD CONSTRAINT "
        "model_catalog_source_scope_check CHECK ("
        "  (source = 'central' AND tenant_id IS NULL AND connection_id IS NULL) OR"
        "  (source = 'byok'   AND tenant_id IS NOT NULL AND connection_id IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog DROP CONSTRAINT "
        "model_catalog_source_check"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog ADD CONSTRAINT "
        "model_catalog_source_check CHECK (source IN ('central','byok'))"
    )
    op.execute(
        "ALTER TABLE catalog.model_catalog DROP COLUMN provider_id"
    )
    op.execute("DROP TABLE IF EXISTS ops.model_provider_tenants")
    op.execute("DROP TABLE IF EXISTS ops.model_providers")
