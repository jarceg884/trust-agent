"""spec 024 (mandato Miller): conexiones personalizadas del vault gobernadas por policy.

DDL canónico: db/schema.sql sección 14 (embebida verbatim aquí para que la
vía Alembic produzca el mismo estado — paridad exigida por
db/tests/test_schema_parity.py). Cuatro piezas:

- Tabla ``public.vault_connections``: conexión definida por el ADMIN de la org
  (más allá del catálogo fijo de conectores) — base de datos, API key genérica
  u otra metadata libre. Secreto cifrado con el MISMO envelope crypto del vault
  (KEK/DEK AES-256-GCM, columnas espejo de vault_credentials), tenant-scoped
  con RLS fail-closed (patrón §7/9.6) y trigger set_updated_at.
- Policy key ``allowed_connections`` (list, default ``["*"]``): tokens ``"*"``
  (todo el catálogo), slugs del catálogo, o ids de conexiones personalizadas
  activas del tenant. Deny-overrides en los 3 niveles con la maquinaria
  existente (REQ-POL-006/007); el default ``["*"]`` mantiene a los tenants
  actuales sin cambio de comportamiento (D-24-03).
- Permiso ``connections.manage`` otorgado ÚNICAMENTE a org_admin (data-driven
  RBAC, mismo patrón que roles.assign de la spec 017): el admin decide qué
  conexiones existen; el resto solo las consume si la policy se lo permite.
- Audit actions ``connection.created/updated/revoked/used`` (enum versionado).
"""
from alembic import op
import sqlalchemy as sa


revision = "0012_custom_connections"
down_revision = "0011_gov_agent_policy_keys"
branch_labels = None
depends_on = None

SCHEMA_SQL = r"""
-- ============================================================================
-- 14. Evolución spec 024 (conexiones personalizadas): vault_connections +
--     policy key allowed_connections + permiso connections.manage
--     Misma migración: db/migrations/versions/20261026_0012_custom_connections.py
--     (paridad pg_dump exigida por db/tests/test_schema_parity.py)
-- ============================================================================

-- 14.1 Nuevas acciones de audit (enum versionado, extensión por datos)
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('connection.created',  'Custom connection created by the org admin (secret shown one-time).'),
  ('connection.updated',  'Custom connection metadata/secret updated (secret shown one-time).'),
  ('connection.revoked',  'Custom connection revoked (irreversible, use cuts off with 410).'),
  ('connection.used',     'Custom connection secret injected at runtime (policy-gated, never logged).')
ON CONFLICT (action) DO NOTHING;

-- 14.2 Permiso nuevo + grant solo a org_admin (D-24-04)
INSERT INTO catalog.permissions (key, description) VALUES
  ('connections.manage', 'Create/update/revoke tenant custom connections (org_admin only).')
ON CONFLICT (key) DO NOTHING;

INSERT INTO catalog.role_permissions (role_key, permission_key) VALUES
  ('org_admin', 'connections.manage')
ON CONFLICT (role_key, permission_key) DO NOTHING;

-- 14.3 Policy key tipada: allowed_connections (D-24-02/03)
--      Tokens: '*' = todos los conectores del catálogo (sujetos a
--      allowed_connectors), slug del catálogo, o id (UUID) de una conexión
--      personalizada ACTIVA del tenant. Default '*' = sin cambio de
--      comportamiento para tenants existentes.
INSERT INTO catalog.policy_keys (key, value_type, invariable, default_value, description) VALUES
  ('allowed_connections', 'list', false, '["*"]',
   'Connection tokens the level allows: ''*'' (whole catalog), catalog slugs, or custom connection ids (deny-overrides across levels).')
ON CONFLICT (key) DO NOTHING;

-- 14.4 vault_connections — conexiones personalizadas del tenant (spec 024).
--      SOLO material cifrado en las columnas de secreto (mismo contrato que
--      vault_credentials §5.10); la metadata NO-secreta vive en metadata jsonb.
CREATE TABLE IF NOT EXISTS vault_connections (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id),
  created_by        uuid NOT NULL,
  name              text NOT NULL,
  kind              text NOT NULL CHECK (kind IN ('database','api_key','custom')),
  metadata          jsonb NOT NULL DEFAULT '{}',
  status            text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
  ciphertext_secret bytea NOT NULL,   -- AES-256-GCM(secret, DEK)
  ciphertext_dek    bytea NOT NULL,   -- DEK cifrada con la KEK (envelope)
  nonce             bytea NOT NULL,   -- IV de AES-GCM
  key_id            text NOT NULL,    -- versión/alias de la KEK usada
  last_rotated_at   timestamptz,
  last_used_at      timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, name),
  FOREIGN KEY (tenant_id, created_by) REFERENCES users (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS vault_connections_tenant_idx
  ON vault_connections (tenant_id, created_at DESC);

-- 14.5 Trigger updated_at (la función set_updated_at() existe desde §6.1)
DROP TRIGGER IF EXISTS vault_connections_set_updated_at ON vault_connections;
CREATE TRIGGER vault_connections_set_updated_at
  BEFORE UPDATE ON vault_connections FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 14.6 RLS: mismo patrón fail-closed + FORCE que la sección 7
DO $$
DECLARE
  t text;
  tables text[] := ARRAY['vault_connections'];
BEGIN
  FOREACH t IN ARRAY tables
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($p$
      CREATE POLICY tenant_isolation ON public.%I
        FOR ALL TO trust_app
        USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    $p$, t);
  END LOOP;
END $$;

-- 14.7 Grants explícitos a la app (idénticos a los de la sección 8)
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE vault_connections TO trust_app;
"""


def upgrade() -> None:
    op.execute(sa.text(SCHEMA_SQL))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS vault_connections"))
    op.execute(sa.text(
        "DELETE FROM catalog.role_permissions WHERE permission_key = 'connections.manage'"
    ))
    op.execute(sa.text(
        "DELETE FROM catalog.permissions WHERE key = 'connections.manage'"
    ))
    op.execute(sa.text(
        "DELETE FROM catalog.policy_keys WHERE key = 'allowed_connections'"
    ))
    op.execute(sa.text(
        "DELETE FROM catalog.audit_actions WHERE action IN "
        "('connection.created','connection.updated','connection.revoked','connection.used')"
    ))
