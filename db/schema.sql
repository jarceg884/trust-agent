-- ============================================================================
-- schema.sql — Trust Agent: capa de datos multi-tenant (PostgreSQL 17)
-- Spec: specs/002-data-layer/spec.md | Contratos: docs/arquitectura.md §5
--
-- DDL CANÓNICO. La migración inicial de Alembic (db/migrations/) ejecuta este
-- mismo SQL embebido; un test de paridad (db/tests/test_schema_parity.py)
-- exige que pg_dump de ambas vías sea idéntico.
--
-- Planos:
--   catalog : referencia global del proveedor (sin tenant_id, sin RLS)
--   public  : tablas de negocio (tenant_id + RLS en TODAS)
--   ops     : estado operativo cross-tenant del proveedor (spend_cursor,
--             login_throttle)
--
-- Ejecutar como superusuario (dev) o como trust_migrator tras scripts/bootstrap.sql.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Roles (idempotente; cluster-level)
-- ----------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trust_app') THEN
    CREATE ROLE trust_app LOGIN PASSWORD 'trust_app_dev';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trust_migrator') THEN
    CREATE ROLE trust_migrator LOGIN BYPASSRLS PASSWORD 'trust_migrator_dev';
  END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 2. Schemas
-- ----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS ops;

COMMENT ON SCHEMA catalog IS 'Global provider reference data (RBAC matrix, policy keys, model/connector catalogs, audit actions). No tenant data, no RLS.';
COMMENT ON SCHEMA ops IS 'Cross-tenant provider operational state (spend polling cursor, login throttle counters). No tenant data, no RLS.';

-- ============================================================================
-- 3. SCHEMA catalog — catálogos globales del proveedor (datos, no código)
-- ============================================================================

-- 3.1 Matriz RBAC (spec 001 §2.1, vinculante)
CREATE TABLE IF NOT EXISTS catalog.roles (
  key         text PRIMARY KEY,
  name        text NOT NULL,
  description text NOT NULL,
  is_tenant_role boolean NOT NULL DEFAULT true  -- false = provider-side (platform_admin)
);

CREATE TABLE IF NOT EXISTS catalog.permissions (
  key         text PRIMARY KEY,
  description text NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog.role_permissions (
  role_key       text NOT NULL REFERENCES catalog.roles(key) ON DELETE CASCADE,
  permission_key text NOT NULL REFERENCES catalog.permissions(key) ON DELETE CASCADE,
  PRIMARY KEY (role_key, permission_key)
);

INSERT INTO catalog.roles (key, name, description, is_tenant_role) VALUES
  ('platform_admin', 'Platform Admin',   'Provider operator: control plane, tenant provisioning, global catalogs.', false),
  ('org_admin',      'Organization Admin','Customer admin: full administration of their own tenant.',             true),
  ('dept_admin',     'Department Admin',  'Department leader: administers users/budgets/policies of their department.', true),
  ('user',           'User',              'End user: desktop + agents within policy.',                            true)
ON CONFLICT (key) DO NOTHING;

INSERT INTO catalog.permissions (key, description) VALUES
  ('tenants.provision',      'Provision/suspend tenants (provider).'),
  ('catalog.manage',         'Manage global catalogs: models, connectors, profile templates.'),
  ('branding.manage',        'Edit tenant branding.'),
  ('departments.manage',     'CRUD departments.'),
  ('budget.org.manage',      'Manage organization budget.'),
  ('budget.dept.manage',     'Manage department budget (own department).'),
  ('budget.user.manage',     'Manage user budgets.'),
  ('users.invite',           'Invite users.'),
  ('users.manage',           'Edit/disable users.'),
  ('policies.org.manage',    'Manage organization policies.'),
  ('policies.dept.manage',   'Manage department policies (never less restrictive than org).'),
  ('models.approve',         'Approve/revoke models for dept/user (subset of org approvals).'),
  ('devices.manage',         'Approve/reject/revoke devices.'),
  ('agents.grant',           'Grant/revoke shared-agent grants.'),
  ('audit.view',             'View audit log (scoped by role).'),
  ('connectors.manage',      'Manage tenant/department connectors.'),
  ('vault.view_metadata',    'View vault credential metadata (never secrets).'),
  ('desktop.use',            'Use the desktop and agents.')
ON CONFLICT (key) DO NOTHING;

-- Matriz RBAC exacta de spec 001 §2.1
INSERT INTO catalog.role_permissions (role_key, permission_key) VALUES
  ('platform_admin', 'tenants.provision'),
  ('platform_admin', 'catalog.manage'),
  ('org_admin',      'branding.manage'),
  ('org_admin',      'departments.manage'),
  ('org_admin',      'budget.org.manage'),
  ('org_admin',      'budget.dept.manage'),
  ('org_admin',      'budget.user.manage'),
  ('org_admin',      'users.invite'),
  ('org_admin',      'users.manage'),
  ('org_admin',      'policies.org.manage'),
  ('org_admin',      'policies.dept.manage'),
  ('org_admin',      'models.approve'),
  ('org_admin',      'devices.manage'),
  ('org_admin',      'agents.grant'),
  ('org_admin',      'audit.view'),
  ('org_admin',      'connectors.manage'),
  ('org_admin',      'vault.view_metadata'),
  ('org_admin',      'desktop.use'),
  ('dept_admin',     'budget.dept.manage'),
  ('dept_admin',     'budget.user.manage'),
  ('dept_admin',     'users.invite'),
  ('dept_admin',     'users.manage'),
  ('dept_admin',     'policies.dept.manage'),
  ('dept_admin',     'models.approve'),
  ('dept_admin',     'devices.manage'),
  ('dept_admin',     'agents.grant'),
  ('dept_admin',     'audit.view'),
  ('dept_admin',     'connectors.manage'),
  ('dept_admin',     'vault.view_metadata'),
  ('dept_admin',     'desktop.use'),
  ('user',           'agents.grant'),
  ('user',           'audit.view'),
  ('user',           'vault.view_metadata'),
  ('user',           'desktop.use')
ON CONFLICT (role_key, permission_key) DO NOTHING;

-- 3.2 Catálogo tipado de claves de política (REQ-POL-003/004; invariables = piso del proveedor)
CREATE TABLE IF NOT EXISTS catalog.policy_keys (
  key           text PRIMARY KEY,
  value_type    text NOT NULL CHECK (value_type IN ('boolean','integer','string','list','map')),
  invariable    boolean NOT NULL DEFAULT false,
  default_value jsonb   NOT NULL,
  description   text NOT NULL
);

INSERT INTO catalog.policy_keys (key, value_type, invariable, default_value, description) VALUES
  ('require_mfa',                 'boolean', true,  'true',  'MFA mandatory for every authenticated operation.'),
  ('approved_models_only',        'boolean', true,  'true',  'Only models approved by the organization can be used.'),
  ('explicit_shared_agent_grants','boolean', true,  'true',  'Shared agents require explicit grants; no open sharing.'),
  ('scoped_credentials',          'boolean', true,  'true',  'Connector credentials must use the minimum declared scope.'),
  ('allowed_models',              'list',    false, '[]',    'Model names allowed beyond the approved set.'),
  ('allowed_connectors',          'list',    false, '[]',    'Connector slugs the level allows.'),
  ('shared_agent_grants_mode',    'string',  false, '"explicit"', 'Grant mode for shared agents.'),
  ('vault_allowed_credential_types','list',  false, '["api_key","oauth2"]', 'Credential types users may register.'),
  ('session_timeout_minutes',     'integer', false, '60',    'Server-side session timeout.'),
  ('artifact_sharing',            'string',  false, '"private"', 'Artifact sharing mode.'),
  ('messaging_permissions',       'map',     false, '{}',    'Per messaging platform: allowed + agent mode (read_only|reply|full).'),
  ('max_daily_sessions',          'integer', false, '10',    'Maximum agent sessions per user per day.')
ON CONFLICT (key) DO NOTHING;

-- 3.3 Catálogo global de modelos (LiteLLM/MaaS; pricing en centavos USD por millón de tokens.
--     Seeds de referencia: el job pricing_sync (REQ-MDL-004) actualiza con el pricing vigente.)
CREATE TABLE IF NOT EXISTS catalog.model_catalog (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  model_name               text NOT NULL UNIQUE,          -- LiteLLM model name
  display_name             text NOT NULL,
  provider                 text NOT NULL,                 -- e.g. huawei_maas
  input_price_cents_per_m  bigint NOT NULL CHECK (input_price_cents_per_m >= 0),
  output_price_cents_per_m bigint NOT NULL CHECK (output_price_cents_per_m >= 0),
  status                   text NOT NULL DEFAULT 'available' CHECK (status IN ('available','deprecated')),
  updated_at               timestamptz NOT NULL DEFAULT now()
);

INSERT INTO catalog.model_catalog (model_name, display_name, provider, input_price_cents_per_m, output_price_cents_per_m) VALUES
  ('qwen2.5-72b-instruct', 'Qwen2.5 72B Instruct', 'huawei_maas', 400, 1200),
  ('deepseek-v3',          'DeepSeek V3',          'huawei_maas', 270, 1090)
ON CONFLICT (model_name) DO NOTHING;

-- 3.4 Catálogo global de conectores (REQ-CON-001: set inicial obligatorio de 6)
CREATE TABLE IF NOT EXISTS catalog.connector_catalog (
  slug            text PRIMARY KEY,
  name            text NOT NULL,
  category        text NOT NULL CHECK (category IN ('infrastructure','email','messaging')),
  capabilities    jsonb NOT NULL DEFAULT '[]',
  required_scopes jsonb NOT NULL DEFAULT '[]',
  status          text NOT NULL DEFAULT 'available' CHECK (status IN ('available','disabled'))
);

INSERT INTO catalog.connector_catalog (slug, name, category, capabilities, required_scopes) VALUES
  ('vmware',   'VMware vSphere/ESXi', 'infrastructure', '["vm_list","host_health","snapshots"]', '["read_only"]'),
  ('email',    'Email (IMAP/SMTP)',   'email',          '["read_mail","send_mail"]',             '["imap","smtp"]'),
  ('whatsapp', 'WhatsApp',            'messaging',      '["read","reply"]',                      '["whatsapp_api"]'),
  ('telegram', 'Telegram',            'messaging',      '["read","reply"]',                      '["bot_token"]'),
  ('slack',    'Slack',               'messaging',      '["read","reply"]',                      '["bot_token"]'),
  ('teams',    'Microsoft Teams',     'messaging',      '["read","reply"]',                      '["bot_token"]')
ON CONFLICT (slug) DO NOTHING;

-- 3.5 Enum versionado de acciones de audit (arq. §5.6; extensible por datos)
CREATE TABLE IF NOT EXISTS catalog.audit_actions (
  action      text PRIMARY KEY,
  description text NOT NULL
);

INSERT INTO catalog.audit_actions (action, description) VALUES
  ('auth.login',                 'Successful login.'),
  ('auth.logout',                'Logout.'),
  ('auth.login_failed',          'Failed login attempt.'),
  ('user.invited',               'User invited (provisioning job created).'),
  ('user.created',               'User provisioned.'),
  ('user.updated',               'User updated.'),
  ('user.disabled',              'User disabled; access revoked.'),
  ('role.assigned',              'Role assigned to user.'),
  ('role.revoked',               'Role revoked from user.'),
  ('department.created',         'Department created.'),
  ('policy.updated',             'Policy created/updated (versioned).'),
  ('budget.updated',             'Budget created/updated.'),
  ('budget.forced_below_spend',  'Budget reduced below current spend with force=true.'),
  ('budget.cutoff',              'Budget cutoff applied (user/dept/org).'),
  ('device.enrolled',            'Device enrolled (pending).'),
  ('device.approved',            'Device approved.'),
  ('device.revoked',             'Device rejected/revoked.'),
  ('connector.connected',        'Connector instance connected.'),
  ('connector.disconnected',     'Connector instance disconnected.'),
  ('vault.credential_created',   'Vault credential registered (ciphertext only).'),
  ('vault.credential_used',      'Vault credential used by an agent.'),
  ('vault.credential_rotated',   'Vault credential rotated.'),
  ('vault.credential_revoked',   'Vault credential revoked.'),
  ('model.approved',             'Model approved for a level.'),
  ('model.revoked',              'Model approval revoked.'),
  ('model.access_denied',        'Model invocation denied (not approved).'),
  ('model.consumed',             'Model invocation billed to a user budget.'),
  ('tenant.provisioned',         'Tenant provisioned by provider.'),
  ('tenant.suspended',           'Tenant suspended by provider.'),
  ('tenant.reactivated',         'Tenant reactivated.'),
  ('audit.verified',             'Audit chain verification run.'),
  ('audit.purged',               'Audit retention purge executed.'),
  ('job.created',                'Async job created.'),
  ('job.completed',              'Async job finished.')
ON CONFLICT (action) DO NOTHING;

-- ============================================================================
-- 4. SCHEMA ops — estado operativo del proveedor (cross-tenant)
-- ============================================================================

-- Cursor del worker spend-aggregator (arq. §5.3: "cursor persistido en DB").
-- Singleton por diseño (id = 1).
CREATE TABLE IF NOT EXISTS ops.spend_cursor (
  id              integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  last_request_id text,
  last_occurred_at timestamptz,
  updated_at      timestamptz NOT NULL DEFAULT now()
);

INSERT INTO ops.spend_cursor (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Contadores de rate limiting de login (REQ-SEC-006; D-CP-12 resuelto como
-- D-DB-14 de spec 002). El conteo es PRE-autenticación y cross-tenant por
-- naturaleza: no existe tenant confiable todavía, por lo que audit_log no
-- sirve y el plano `ops` (sin RLS, estado del proveedor) es la vía correcta.
-- Claves despersonalizadas: 'acct:<tenant_id>:<sha256(sub)>' solo con claims
-- criptográficamente verificados; 'ip:<inet>' para tokens basura/flood.
-- Sin DELETE para trust_app: la expiración es por ventana lógica.
CREATE TABLE IF NOT EXISTS ops.login_throttle (
  key              text PRIMARY KEY,
  fail_count       integer NOT NULL DEFAULT 0,
  window_started_at timestamptz NOT NULL DEFAULT now(),
  last_fail_at     timestamptz NOT NULL DEFAULT now(),
  locked_until     timestamptz,
  updated_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE ops.login_throttle IS 'Login rate-limit counters (REQ-SEC-006): per verified (tenant, subject) and per client IP, progressive lockout, survives API restarts. Provider plane: no tenant_id, no RLS.';

-- ============================================================================
-- 5. SCHEMA public — tablas de negocio (tenant_id + RLS en TODAS)
-- ============================================================================

-- 5.1 tenants — auto-referencial: tenant_id = id (GENERATED) para RLS uniforme
CREATE TABLE IF NOT EXISTS tenants (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid GENERATED ALWAYS AS (id) STORED NOT NULL,
  name             text NOT NULL,
  slug             text NOT NULL UNIQUE,
  status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
  oidc_realm       text,
  oidc_issuer_url  text,
  oidc_client_id   text,
  maas_margin_bps  integer NOT NULL DEFAULT 0 CHECK (maas_margin_bps >= 0 AND maas_margin_bps <= 10000),
  branding         jsonb   NOT NULL DEFAULT '{}',
  retention_days   integer NOT NULL DEFAULT 365 CHECK (retention_days >= 365),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tenants_tenant_id_idx ON tenants (tenant_id);

-- 5.2 departments
CREATE TABLE IF NOT EXISTS departments (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  name            text NOT NULL,
  profile_template text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, name)
);

-- 5.3 users
CREATE TABLE IF NOT EXISTS users (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id),
  department_id  uuid,
  email          text NOT NULL,
  display_name   text NOT NULL,
  status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','disabled')),
  mfa_enrolled   boolean NOT NULL DEFAULT false,
  oidc_subject   text,
  last_login_at  timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, email),
  FOREIGN KEY (tenant_id, department_id) REFERENCES departments (tenant_id, id)
);

-- 5.4 user_roles — solo roles de cliente; dept_admin exige scope de depto
CREATE TABLE IF NOT EXISTS user_roles (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  user_id       uuid NOT NULL,
  role_key      text NOT NULL REFERENCES catalog.roles(key),
  department_id uuid,
  granted_at    timestamptz NOT NULL DEFAULT now(),
  granted_by    uuid,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (role_key IN ('org_admin','dept_admin','user')),
  CHECK (role_key <> 'dept_admin' OR department_id IS NOT NULL),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id),
  FOREIGN KEY (tenant_id, department_id) REFERENCES departments (tenant_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS user_roles_scope_uidx
  ON user_roles (tenant_id, user_id, role_key,
                 COALESCE(department_id, '00000000-0000-0000-0000-000000000000'::uuid));

-- 5.5 budgets — jerárquico org→dept→user (una fila por nivel+scope)
CREATE TABLE IF NOT EXISTS budgets (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL REFERENCES tenants(id),
  level              text NOT NULL CHECK (level IN ('org','dept','user')),
  department_id      uuid,
  user_id            uuid,
  amount_cents       bigint NOT NULL CHECK (amount_cents >= 0),
  period             text NOT NULL DEFAULT 'monthly' CHECK (period IN ('daily','weekly','monthly')),
  soft_threshold_pct smallint NOT NULL DEFAULT 80 CHECK (soft_threshold_pct >= 0 AND soft_threshold_pct <= 100),
  status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT budgets_scope_check CHECK (
    (level = 'org'  AND department_id IS NULL     AND user_id IS NULL) OR
    (level = 'dept' AND department_id IS NOT NULL AND user_id IS NULL) OR
    (level = 'user' AND department_id IS NOT NULL AND user_id IS NOT NULL)
  ),
  FOREIGN KEY (tenant_id, department_id) REFERENCES departments (tenant_id, id),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS budgets_scope_uidx
  ON budgets (tenant_id, level,
              COALESCE(department_id, '00000000-0000-0000-0000-000000000000'::uuid),
              COALESCE(user_id,       '00000000-0000-0000-0000-000000000000'::uuid));

-- 5.6 budget_counters — agregación de gasto por período (arq. §5.3)
CREATE TABLE IF NOT EXISTS budget_counters (
  tenant_id    uuid NOT NULL REFERENCES tenants(id),
  level        text NOT NULL CHECK (level IN ('org','dept','user')),
  entity_id    uuid NOT NULL,   -- tenant_id (org) | department_id (dept) | user_id (user)
  period_start date NOT NULL,
  period_end   date NOT NULL,
  spent_cents  bigint NOT NULL DEFAULT 0 CHECK (spent_cents >= 0),
  budget_cents bigint NOT NULL DEFAULT 0 CHECK (budget_cents >= 0),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, level, entity_id, period_start),
  CHECK (period_end >= period_start)
);

-- 5.7 spend_ledger — una fila por request (arq. §5.3; request_id UNIQUE global)
CREATE TABLE IF NOT EXISTS spend_ledger (
  id                bigserial PRIMARY KEY,
  tenant_id         uuid NOT NULL REFERENCES tenants(id),
  request_id        text NOT NULL UNIQUE,
  user_id           uuid,
  department_id     uuid,
  model_name        text NOT NULL,
  mode              text NOT NULL CHECK (mode IN ('central','byok')),
  cost_base_cents   bigint NOT NULL CHECK (cost_base_cents >= 0),
  cost_billed_cents bigint NOT NULL CHECK (cost_billed_cents >= 0),
  tokens_in         bigint NOT NULL DEFAULT 0 CHECK (tokens_in >= 0),
  tokens_out        bigint NOT NULL DEFAULT 0 CHECK (tokens_out >= 0),
  occurred_at       timestamptz NOT NULL,
  ingested_at       timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id),
  FOREIGN KEY (tenant_id, department_id) REFERENCES departments (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS spend_ledger_tenant_time_idx ON spend_ledger (tenant_id, occurred_at);
CREATE INDEX IF NOT EXISTS spend_ledger_tenant_user_idx ON spend_ledger (tenant_id, user_id, occurred_at);

-- 5.8 policies — 3 niveles exactos, claves del catálogo tipado (arq. §5.7)
CREATE TABLE IF NOT EXISTS policies (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  level         text NOT NULL CHECK (level IN ('org','dept','user')),
  department_id uuid,
  user_id       uuid,
  key           text NOT NULL REFERENCES catalog.policy_keys(key),
  value         jsonb NOT NULL,
  version       integer NOT NULL DEFAULT 1,
  is_active     boolean NOT NULL DEFAULT true,
  created_by    uuid,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT policies_scope_check CHECK (
    (level = 'org'  AND department_id IS NULL     AND user_id IS NULL) OR
    (level = 'dept' AND department_id IS NOT NULL AND user_id IS NULL) OR
    (level = 'user' AND department_id IS NOT NULL AND user_id IS NOT NULL)
  ),
  FOREIGN KEY (tenant_id, department_id) REFERENCES departments (tenant_id, id),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS policies_scope_uidx
  ON policies (tenant_id, key, level,
               COALESCE(department_id, '00000000-0000-0000-0000-000000000000'::uuid),
               COALESCE(user_id,       '00000000-0000-0000-0000-000000000000'::uuid));

-- 5.9 devices — enrolamiento pending→active/revoked (REQ-DEV)
CREATE TABLE IF NOT EXISTS devices (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id),
  user_id      uuid NOT NULL,
  fingerprint  text NOT NULL,
  device_name  text,
  platform     text,
  status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','revoked','rejected')),
  public_key   text,
  enrolled_at  timestamptz NOT NULL DEFAULT now(),
  approved_at  timestamptz,
  approved_by  uuid,
  last_seen_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  -- sha256(secret) del enrollment_token one-time (spec 007, ADR-006);
  -- NULL = consumido o nunca emitido. El secreto jamás se persiste.
  enrollment_token_hash text,
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, fingerprint),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
);

-- 5.10 vault_credentials — SOLO material cifrado (arq. §5.5, ADR-009).
--      KEK fuera de la DB; jamás una columna con secreto en claro.
CREATE TABLE IF NOT EXISTS vault_credentials (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id),
  user_id           uuid NOT NULL,
  name              text NOT NULL,
  credential_type   text NOT NULL CHECK (credential_type IN ('api_key','password','oauth2','token')),
  connector_slug    text REFERENCES catalog.connector_catalog(slug),
  scope             text,
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
  UNIQUE (tenant_id, user_id, name),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
);

-- 5.11 connector_instances — instancias por usuario
CREATE TABLE IF NOT EXISTS connector_instances (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id),
  user_id        uuid NOT NULL,
  connector_slug text NOT NULL REFERENCES catalog.connector_catalog(slug),
  status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disconnected','revoked')),
  mode           text CHECK (mode IN ('read_only','reply','full')),  -- mensajerías (REQ-CON-004)
  credential_id  uuid,
  config         jsonb NOT NULL DEFAULT '{}',   -- config NO secreta
  last_used_at   timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, user_id, connector_slug),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id),
  FOREIGN KEY (tenant_id, credential_id) REFERENCES vault_credentials (tenant_id, id)
);

-- 5.12 audit_log — INSERT-only + hash chain por tenant (arq. §5.6, REQ-AUD)
CREATE TABLE IF NOT EXISTS audit_log (
  id          bigserial PRIMARY KEY,
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  seq         bigint NOT NULL,            -- por tenant, asignado por trigger
  actor_role  text NOT NULL,             -- 'user' | 'org_admin' | ... | 'system' | 'provider'
  actor_id    uuid,                      -- users.id (NULL para system/provider)
  action      text NOT NULL REFERENCES catalog.audit_actions(action),
  target_type text,
  target_id   text,
  device_id   uuid,
  cost_cents  bigint,
  payload     jsonb NOT NULL DEFAULT '{}',
  ts          timestamptz NOT NULL DEFAULT now(),
  hash_prev   char(64) NOT NULL,         -- asignado por trigger (génesis: 64 ceros)
  hash        char(64) NOT NULL          -- sha256(hash_prev || canonical_payload)
);
CREATE INDEX IF NOT EXISTS audit_log_tenant_id_idx    ON audit_log (tenant_id, id DESC);
CREATE INDEX IF NOT EXISTS audit_log_tenant_time_idx  ON audit_log (tenant_id, ts);
CREATE INDEX IF NOT EXISTS audit_log_tenant_actor_idx ON audit_log (tenant_id, actor_id);

-- 5.13 model_access — modelos aprobados por nivel (REQ-MDL)
CREATE TABLE IF NOT EXISTS model_access (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  model_id      uuid NOT NULL REFERENCES catalog.model_catalog(id),
  level         text NOT NULL CHECK (level IN ('org','dept','user')),
  department_id uuid,
  user_id       uuid,
  mode          text NOT NULL DEFAULT 'central' CHECK (mode IN ('central','byok')),
  status        text NOT NULL DEFAULT 'approved' CHECK (status IN ('approved','pending','denied')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT model_access_scope_check CHECK (
    (level = 'org'  AND department_id IS NULL     AND user_id IS NULL) OR
    (level = 'dept' AND department_id IS NOT NULL AND user_id IS NULL) OR
    (level = 'user' AND department_id IS NOT NULL AND user_id IS NOT NULL)
  ),
  FOREIGN KEY (tenant_id, department_id) REFERENCES departments (tenant_id, id),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS model_access_scope_uidx
  ON model_access (tenant_id, model_id, level,
                   COALESCE(department_id, '00000000-0000-0000-0000-000000000000'::uuid),
                   COALESCE(user_id,       '00000000-0000-0000-0000-000000000000'::uuid));

-- 5.14 api_keys — virtual keys LiteLLM: SOLO hash, jamás la key (arq. §5.3)
CREATE TABLE IF NOT EXISTS api_keys (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id),
  user_id          uuid NOT NULL,
  device_id        uuid,
  litellm_key_hash text NOT NULL UNIQUE,
  litellm_team_id  text,                 -- team del depto en LiteLLM
  alias            text,
  status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','blocked','revoked')),
  allowed_models   jsonb NOT NULL DEFAULT '[]',
  max_budget_cents bigint,
  budget_duration  text,                 -- p.ej. '30d' (LiteLLM budget_duration)
  created_at       timestamptz NOT NULL DEFAULT now(),
  revoked_at       timestamptz,
  updated_at       timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id),
  FOREIGN KEY (tenant_id, device_id) REFERENCES devices (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS api_keys_tenant_user_idx ON api_keys (tenant_id, user_id);

-- 5.15 sessions — estado de sesión en PostgreSQL, nunca en memoria (C2, REQ-SEC-003)
CREATE TABLE IF NOT EXISTS sessions (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id),
  user_id           uuid NOT NULL,
  device_id         uuid,
  kind              text NOT NULL CHECK (kind IN ('desktop','admin','api')),
  jti               text NOT NULL UNIQUE,
  refresh_token_hash text UNIQUE,
  csrf_token_hash   text UNIQUE,
  status            text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked','expired')),
  mfa_verified      boolean NOT NULL DEFAULT false,
  ip                inet,
  user_agent        text,
  expires_at        timestamptz NOT NULL,
  last_seen_at      timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id),
  FOREIGN KEY (tenant_id, device_id) REFERENCES devices (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS sessions_tenant_user_idx   ON sessions (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS sessions_tenant_status_idx ON sessions (tenant_id, status);

-- 5.16 jobs — jobs asíncronos en DB, idempotentes por (tenant, tipo, key) (C2, REQ-ORG-002)
CREATE TABLE IF NOT EXISTS jobs (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  job_type        text NOT NULL,
  status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','succeeded','failed','cancelled')),
  payload         jsonb NOT NULL DEFAULT '{}',
  result          jsonb,
  error           text,
  attempts        integer NOT NULL DEFAULT 0,
  max_attempts    integer NOT NULL DEFAULT 3,
  idempotency_key text,
  run_after       timestamptz NOT NULL DEFAULT now(),
  started_at      timestamptz,
  finished_at     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency_uidx
  ON jobs (tenant_id, job_type, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs (status, run_after);

-- ----------------------------------------------------------------------------
-- 6. Funciones y triggers
-- ----------------------------------------------------------------------------

-- 6.1 updated_at automático en tablas con esa columna
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

DO $$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT c.table_name
    FROM information_schema.columns c
    JOIN pg_tables t ON t.schemaname = c.table_schema AND t.tablename = c.table_name
    WHERE c.table_schema = 'public' AND c.column_name = 'updated_at'
      AND NOT EXISTS (
        SELECT 1 FROM pg_trigger tg
        JOIN pg_class rel ON rel.oid = tg.tgrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = 'public' AND rel.relname = c.table_name
          AND tg.tgname = c.table_name || '_set_updated_at'
      )
  LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE ON public.%I FOR EACH ROW EXECUTE FUNCTION set_updated_at()',
                   r.table_name || '_set_updated_at', r.table_name);
  END LOOP;
END $$;

-- 6.2 Audit: payload canónico para el hash (orden fijo de columnas; jsonb::text es canónico)
CREATE OR REPLACE FUNCTION audit_canonical_payload(rec audit_log) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT concat_ws('|',
    rec.tenant_id::text,
    rec.seq::text,
    rec.actor_role,
    COALESCE(rec.actor_id::text, ''),
    rec.action,
    COALESCE(rec.target_type, ''),
    COALESCE(rec.target_id, ''),
    COALESCE(rec.device_id::text, ''),
    COALESCE(rec.cost_cents::text, ''),
    rec.payload::text,
    extract(epoch FROM rec.ts)::text
  );
$$;

-- 6.3 Audit: asignar seq + hash_prev + hash por tenant (advisory lock contra forks de cadena)
CREATE OR REPLACE FUNCTION audit_log_before_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_prev_hash text;
  v_prev_seq  bigint;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('audit:' || NEW.tenant_id::text, 0));
  SELECT hash, seq INTO v_prev_hash, v_prev_seq
    FROM audit_log
    WHERE tenant_id = NEW.tenant_id
    ORDER BY id DESC
    LIMIT 1;
  NEW.seq       := COALESCE(v_prev_seq, 0) + 1;
  NEW.hash_prev := COALESCE(v_prev_hash, repeat('0', 64));
  NEW.hash      := encode(sha256(convert_to(NEW.hash_prev || '|' || audit_canonical_payload(NEW), 'utf8')), 'hex');
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS audit_log_chain_trigger ON audit_log;
CREATE TRIGGER audit_log_chain_trigger
  BEFORE INSERT ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_before_insert();

-- 6.4 Audit: inmutabilidad a nivel DB (trigger de rechazo; aplica a TODO rol)
CREATE OR REPLACE FUNCTION audit_log_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only: % is prohibited (id %)', TG_OP, COALESCE(OLD.id, NEW.id)
    USING ERRCODE = 'raise_exception';
END $$;

DROP TRIGGER IF EXISTS audit_log_immutable_trigger ON audit_log;
CREATE TRIGGER audit_log_immutable_trigger
  BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation();

-- 6.5 Audit: verificación de cadena → ('ok', NULL) | ('broken', seq)
CREATE OR REPLACE FUNCTION audit_verify_chain(p_tenant uuid)
RETURNS TABLE (status text, seq bigint)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  r        audit_log%ROWTYPE;
  v_prev   text := repeat('0', 64);
  v_seq    bigint := 0;
BEGIN
  FOR r IN SELECT * FROM audit_log WHERE tenant_id = p_tenant ORDER BY id LOOP
    IF r.seq <> v_seq + 1 OR r.hash_prev <> v_prev
       OR r.hash <> encode(sha256(convert_to(r.hash_prev || '|' || audit_canonical_payload(r), 'utf8')), 'hex') THEN
      RETURN QUERY SELECT 'broken'::text, r.seq;
      RETURN;
    END IF;
    v_prev := r.hash;
    v_seq  := r.seq;
  END LOOP;
  RETURN QUERY SELECT 'ok'::text, NULL::bigint;
END $$;

-- ----------------------------------------------------------------------------
-- 7. Row Level Security: política única en TODAS las tablas de negocio
--    Patrón: current_setting('app.tenant_id') fail-closed + FORCE (skill RLS)
-- ----------------------------------------------------------------------------
DO $$
DECLARE
  t text;
  tables text[] := ARRAY[
    'tenants','departments','users','user_roles','budgets','budget_counters',
    'spend_ledger','policies','devices','vault_credentials','connector_instances',
    'audit_log','model_access','api_keys','sessions','jobs'
  ];
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

-- ----------------------------------------------------------------------------
-- 8. Grants y default privileges
-- ----------------------------------------------------------------------------

-- La app (trust_app): DML en negocio, lectura de catálogos, cursor de spend.
GRANT USAGE ON SCHEMA public, catalog, ops TO trust_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO trust_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO trust_app;
GRANT SELECT ON ALL TABLES IN SCHEMA catalog TO trust_app;
GRANT SELECT, INSERT, UPDATE ON TABLE ops.spend_cursor TO trust_app;
-- Contadores de login: INSERT/UPDATE para la app, sin DELETE (AC-DB-004).
GRANT SELECT, INSERT, UPDATE ON TABLE ops.login_throttle TO trust_app;

-- audit_log es INSERT-only para la app (REQ-AUD-001).
REVOKE UPDATE, DELETE ON TABLE audit_log FROM trust_app;

-- El migrator conserva todo (dueño); sin grants extra necesarios.
GRANT USAGE ON SCHEMA public, catalog, ops TO trust_migrator;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public, catalog, ops TO trust_migrator;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO trust_migrator;

-- Futuros objetos creados por el rol que corre este script heredan los grants.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO trust_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO trust_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA catalog
  GRANT SELECT ON TABLES TO trust_app;

-- ============================================================================
-- 9. Evolución spec 005 (governance): agentes compartidos, grants, notificaciones
--     Misma migración: db/migrations/versions/20261023_0004_governance.py
--     (paridad pg_dump exigida por db/tests/test_schema_parity.py)
-- ============================================================================

-- 9.1 Nuevas acciones de audit (enum versionado, extensión por datos)
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('agent.shared',        'Agent published as shared (no grants by default).'),
  ('agent.unshared',      'Agent archived / unshared.'),
  ('agent.granted',       'Shared-agent grant created (invoke/view).'),
  ('agent.revoked',       'Shared-agent grant revoked.'),
  ('agent.invoked',       'Shared agent invoked (billed to the invoker).'),
  ('agent.invoke_denied', 'Shared-agent invocation denied (no invoke grant).'),
  ('model.key_issued',    'LiteLLM virtual key issued for a user (hash stored only).'),
  ('model.key_blocked',   'LiteLLM virtual key blocked (budget cutoff).'),
  ('model.key_unblocked', 'LiteLLM virtual key unblocked (budget recovered / period reset).'),
  ('budget.threshold',    'Budget soft threshold crossed (notification sent).')
ON CONFLICT (action) DO NOTHING;

-- 9.2 agents — agentes compartidos del tenant (REQ-AGT-001)
CREATE TABLE IF NOT EXISTS agents (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  owner_user_id uuid NOT NULL,
  department_id uuid,
  name          text NOT NULL,
  description   text,
  scope         text NOT NULL DEFAULT 'dept' CHECK (scope IN ('dept','org')),
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, owner_user_id, name),
  FOREIGN KEY (tenant_id, owner_user_id) REFERENCES users (tenant_id, id),
  FOREIGN KEY (tenant_id, department_id) REFERENCES departments (tenant_id, id)
);

-- 9.3 agent_grants — grants explícitos invoke/view (REQ-AGT-002)
CREATE TABLE IF NOT EXISTS agent_grants (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  agent_id        uuid NOT NULL,
  grantee_user_id uuid NOT NULL,
  permission      text NOT NULL CHECK (permission IN ('invoke','view')),
  granted_by      uuid,
  granted_at      timestamptz NOT NULL DEFAULT now(),
  revoked_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, agent_id, grantee_user_id, permission),
  FOREIGN KEY (tenant_id, agent_id) REFERENCES agents (tenant_id, id),
  FOREIGN KEY (tenant_id, grantee_user_id) REFERENCES users (tenant_id, id),
  FOREIGN KEY (tenant_id, granted_by) REFERENCES users (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS agent_grants_grantee_idx ON agent_grants (tenant_id, grantee_user_id);

-- 9.4 notifications — inbox de gobernanza (cortes y umbrales; arq. §2.3)
CREATE TABLE IF NOT EXISTS notifications (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id),
  user_id    uuid NOT NULL,
  type       text NOT NULL,
  payload    jsonb NOT NULL DEFAULT '{}',
  read_at    timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS notifications_user_idx
  ON notifications (tenant_id, user_id, created_at DESC);

-- 9.5 Triggers updated_at para las tablas nuevas (la sección 6.1 corrió antes)
DROP TRIGGER IF EXISTS agents_set_updated_at ON agents;
CREATE TRIGGER agents_set_updated_at
  BEFORE UPDATE ON agents FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 9.6 RLS: mismo patrón fail-closed + FORCE que la sección 7
DO $$
DECLARE
  t text;
  tables text[] := ARRAY['agents','agent_grants','notifications'];
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

-- 9.7 Grants explícitos a la app (idénticos a los de la sección 8)
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE agents, agent_grants, notifications TO trust_app;

-- ============================================================================
-- 10. Evolución spec 012 (org endpoints): directorio users, branding,
--     grants-list, departments CRUD
--     Misma migración: db/migrations/versions/20261023_0005_org_endpoints.py
--     (paridad pg_dump exigida por db/tests/test_schema_parity.py)
--     Solo datos: cero DDL nuevo (users/departments/tenants.branding/
--     agent_grants/jobs ya existen desde 0001/0004).
-- ============================================================================

-- 10.1 Nueva acción de audit (enum versionado, extensión por datos)
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('branding.updated', 'Tenant branding created/updated (white-label).')
ON CONFLICT (action) DO NOTHING;

-- ============================================================================
-- 11. Evolución robustez QA (t_82b2ffaa / H2): rechazo durable de logs de
--     spend que referencian un tenant inexistente.
--     Misma migración: db/migrations/versions/20261023_0007_spend_rejections.py
--     (paridad pg_dump exigida por db/tests/test_schema_parity.py)
--     audit_log no puede contener el rechazo (su tenant_id es FK a tenants
--     y el tenant NO existe): plano `ops` del proveedor, mismo criterio que
--     D-DB-14. Sin RLS; append-only para la app.
-- ============================================================================

CREATE TABLE IF NOT EXISTS ops.spend_rejections (
  id          bigserial PRIMARY KEY,
  ts          timestamptz NOT NULL DEFAULT now(),
  tenant_id   uuid NOT NULL,
  request_id  text NOT NULL,
  reason      text NOT NULL,
  occurred_at timestamptz,
  CONSTRAINT spend_rejections_dedupe UNIQUE (tenant_id, request_id, reason)
);
COMMENT ON TABLE ops.spend_rejections IS 'Spend-log rejections '
  '(QA H2, t_82b2ffaa): gateway logs that reference a tenant that no '
  'longer exists in the DB. tenant_id is a forensic reference to a '
  'NON-existent tenant (no FK by design); provider plane: no RLS. '
  'Append-only evidence: the app holds INSERT/SELECT only.';
-- Append-only para la app (sin UPDATE/DELETE): la evidencia no se muta.
GRANT SELECT, INSERT ON TABLE ops.spend_rejections TO trust_app;
-- Pitfall #1 del skill RLS: grants de secuencias.
GRANT USAGE ON SEQUENCE ops.spend_rejections_id_seq TO trust_app;
-- La tabla nace después del grant blanket de la sección 8, así que el
-- grant del migrator se otorga explícito (paridad con la vía Alembic).
GRANT ALL PRIVILEGES ON TABLE ops.spend_rejections TO trust_migrator;
GRANT USAGE, SELECT, UPDATE ON SEQUENCE ops.spend_rejections_id_seq TO trust_migrator;

-- ============================================================================
-- 11. Evolución REQ-AUD-006 (spec 013 D-013-04): retención/purga de audit
--     Misma migración: db/migrations/versions/20261023_0006_audit_retention.py
--     (paridad pg_dump exigida por db/tests/test_schema_parity.py)
-- ============================================================================

-- 11.1 Acción de audit nueva (enum versionado, extensión por datos;
--      `audit.purged` ya existía en el catálogo desde 0001).
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('audit.retention_updated', 'Tenant audit retention setting updated (minimum 365 days).')
ON CONFLICT (action) DO NOTHING;

-- 11.2 Checkpoint de cadena por tenant: la purga deja constancia del
--      último (seq, hash) purgado para que audit_verify_chain verifique
--      desde el checkpoint y el trigger de INSERT continúe la cadena
--      aunque el tenant quede vacío. Plano de negocio con RLS (mismo
--      patrón fail-closed de la sección 7); la escribe solo el job de
--      purga (trust_migrator), la lee audit_verify_chain (trust_app).
CREATE TABLE IF NOT EXISTS audit_chain_checkpoint (
  tenant_id   uuid PRIMARY KEY REFERENCES tenants(id),
  last_seq    bigint NOT NULL,
  last_hash   char(64) NOT NULL,
  purged_rows bigint NOT NULL DEFAULT 0,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE audit_chain_checkpoint ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_chain_checkpoint FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON audit_chain_checkpoint
  FOR ALL TO trust_app
  USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
GRANT SELECT ON TABLE audit_chain_checkpoint TO trust_app;
-- La tabla nace DESPUÉS del grant blanket de la sección 8, pero los
-- default privileges le dan DML completo: revocar explícito — la app es
-- SELECT-only aquí (las escrituras son del job de purga, migrator).
REVOKE INSERT, UPDATE, DELETE ON TABLE audit_chain_checkpoint FROM trust_app;
GRANT ALL PRIVILEGES ON TABLE audit_chain_checkpoint TO trust_migrator;

-- 11.3 Inmutabilidad con excepción de purga: DELETE permitido SOLO al
--      migrator con el flag de transacción app.audit_purge='on' (el job
--      de purga lo activa); UPDATE siempre prohibido. trust_app no tiene
--      privilegio DELETE de todas formas (sección 8): fail-closed doble.
CREATE OR REPLACE FUNCTION audit_log_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE'
     AND current_user = 'trust_migrator'
     AND COALESCE(current_setting('app.audit_purge', true), '') = 'on' THEN
    RETURN OLD;  -- authorized retention purge (REQ-AUD-006)
  END IF;
  RAISE EXCEPTION 'audit_log is append-only: % is prohibited (id %)', TG_OP, COALESCE(OLD.id, NEW.id)
    USING ERRCODE = 'raise_exception';
END $$;

-- 11.4 Continuidad de cadena a través de una purga total: si el tenant no
--      tiene filas, el INSERT continúa desde el checkpoint.
CREATE OR REPLACE FUNCTION audit_log_before_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_prev_hash text;
  v_prev_seq  bigint;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('audit:' || NEW.tenant_id::text, 0));
  SELECT hash, seq INTO v_prev_hash, v_prev_seq
    FROM audit_log
    WHERE tenant_id = NEW.tenant_id
    ORDER BY id DESC
    LIMIT 1;
  IF NOT FOUND THEN
    SELECT last_hash, last_seq INTO v_prev_hash, v_prev_seq
      FROM audit_chain_checkpoint WHERE tenant_id = NEW.tenant_id;
  END IF;
  NEW.seq       := COALESCE(v_prev_seq, 0) + 1;
  NEW.hash_prev := COALESCE(v_prev_hash, repeat('0', 64));
  NEW.hash      := encode(sha256(convert_to(NEW.hash_prev || '|' || audit_canonical_payload(NEW), 'utf8')), 'hex');
  RETURN NEW;
END $$;

-- 11.5 Verificación desde el checkpoint (si existe) en vez de la génesis.
CREATE OR REPLACE FUNCTION audit_verify_chain(p_tenant uuid)
RETURNS TABLE (status text, seq bigint)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  r        audit_log%ROWTYPE;
  v_prev   text;
  v_seq    bigint;
BEGIN
  SELECT last_hash, last_seq INTO v_prev, v_seq
    FROM audit_chain_checkpoint WHERE tenant_id = p_tenant;
  IF NOT FOUND THEN
    v_prev := repeat('0', 64);
    v_seq  := 0;
  END IF;
  FOR r IN SELECT * FROM audit_log WHERE tenant_id = p_tenant ORDER BY id LOOP
    IF r.seq <> v_seq + 1 OR r.hash_prev <> v_prev
       OR r.hash <> encode(sha256(convert_to(r.hash_prev || '|' || audit_canonical_payload(r), 'utf8')), 'hex') THEN
      RETURN QUERY SELECT 'broken'::text, r.seq;
      RETURN;
    END IF;
    v_prev := r.hash;
    v_seq  := r.seq;
  END LOOP;
  RETURN QUERY SELECT 'ok'::text, NULL::bigint;
END $$;

-- 11.6 Job de purga (REQ-AUD-006): purga eventos vencidos por retención,
--      actualiza el checkpoint y registra audit.purged con conteo — todo
--      en UNA transacción, bajo el advisory lock de la cadena. Solo el
--      rol trust_migrator puede ejecutarla (la app es INSERT-only).
CREATE OR REPLACE FUNCTION audit_purge_expired(p_tenant uuid, p_retention_days integer DEFAULT NULL)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
  v_retention integer;
  v_cutoff    timestamptz;
  v_last      audit_log%ROWTYPE;
  v_count     bigint;
BEGIN
  IF p_retention_days IS NOT NULL AND p_retention_days < 365 THEN
    RAISE EXCEPTION 'audit retention floor is 365 days (REQ-AUD-006)'
      USING ERRCODE = 'check_violation';
  END IF;
  SELECT retention_days INTO v_retention FROM tenants WHERE id = p_tenant;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown tenant %', p_tenant USING ERRCODE = 'foreign_key_violation';
  END IF;
  v_cutoff := now() - (COALESCE(p_retention_days, v_retention) || ' days')::interval;
  PERFORM set_config('app.tenant_id', p_tenant::text, true);
  PERFORM set_config('app.audit_purge', 'on', true);
  PERFORM pg_advisory_xact_lock(hashtextextended('audit:' || p_tenant::text, 0));
  SELECT * INTO v_last FROM audit_log
    WHERE tenant_id = p_tenant AND ts < v_cutoff
    ORDER BY id DESC LIMIT 1;
  IF NOT FOUND THEN
    RETURN 0;
  END IF;
  DELETE FROM audit_log WHERE tenant_id = p_tenant AND ts < v_cutoff;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  INSERT INTO audit_chain_checkpoint (tenant_id, last_seq, last_hash, purged_rows)
    VALUES (p_tenant, v_last.seq, v_last.hash, v_count)
    ON CONFLICT (tenant_id) DO UPDATE SET
      last_seq = EXCLUDED.last_seq,
      last_hash = EXCLUDED.last_hash,
      purged_rows = audit_chain_checkpoint.purged_rows + EXCLUDED.purged_rows,
      updated_at = now();
  INSERT INTO audit_log (tenant_id, actor_role, action, target_type, target_id, payload)
    VALUES (p_tenant, 'system', 'audit.purged', 'tenant', p_tenant::text,
            jsonb_build_object('purged_rows', v_count,
                               'retention_days', COALESCE(p_retention_days, v_retention),
                               'cutoff', v_cutoff));
  RETURN v_count;
END $$;

COMMIT;
