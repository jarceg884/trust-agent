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
  ('desktop.use',            'Use the desktop and agents.'),
  -- t_4f4b0eb1 (deprovisioning): permiso MÁS restrictivo que provision —
  -- borrar es irreversible. Migración 0031 (paridad pg_dump).
  ('tenants.delete',         'Deprovision/delete tenants and all their data (provider, irreversible).')
ON CONFLICT (key) DO NOTHING;

-- Matriz RBAC exacta de spec 001 §2.1
INSERT INTO catalog.role_permissions (role_key, permission_key) VALUES
  ('platform_admin', 'tenants.provision'),
  ('platform_admin', 'tenants.delete'),
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
  -- spec 015 (migración 0014): require_mfa pasa a policy variable (default
  -- false = MFA opcional con banner que sugiere enrolarlo); los tenants
  -- existentes conservan su fila explícita en policies.
  ('require_mfa',                 'boolean', false, 'false', 'MFA mandatory for every authenticated operation (org-tunable; when false the app always suggests enrolling).'),
  ('allow_self_registration',     'boolean', false, 'true',  'Users can create their own account (password self-registration or first Google login).'),
  ('approved_models_only',        'boolean', true,  'true',  'Only models approved by the organization can be used.'),
  ('explicit_shared_agent_grants','boolean', true,  'true',  'Shared agents require explicit grants; no open sharing.'),
  ('scoped_credentials',          'boolean', true,  'true',  'Connector credentials must use the minimum declared scope.'),
  -- t_f0d3122d (migración 0019): default null = sin restricción adicional
  -- (heredar la intersección de aprobaciones); [] = deny-all explícito.
  ('allowed_models',              'list',    false, 'null',  'Model names allowed beyond the approved set (null = no additional restriction; [] = explicit deny-all).'),
  ('allowed_connectors',          'list',    false, '[]',    'Connector slugs the level allows.'),
  ('shared_agent_grants_mode',    'string',  false, '"explicit"', 'Grant mode for shared agents.'),
  ('vault_allowed_credential_types','list',  false, '["api_key","oauth2"]', 'Credential types users may register.'),
  ('session_timeout_minutes',     'integer', false, '60',    'Server-side session timeout.'),
  ('artifact_sharing',            'string',  false, '"private"', 'Artifact sharing mode.'),
  ('messaging_permissions',       'map',     false, '{}',    'Per messaging platform: allowed + agent mode (read_only|reply|full).'),
  ('max_daily_sessions',          'integer', false, '10',    'Maximum agent sessions per user per day.'),
  -- spec 019 (governance sobre Hermes, D-GOH-08; migración 0011): keys de
  -- gobernanza del agente. allowed_tools: default del proveedor = tools
  -- conversacionales básicas SIN terminal/browser (governance_status es la
  -- tool interna del plugin, siempre permitida). offline_grace_minutes tiene
  -- piso 15 (upsert + compilación del bundle). unbind_mode enum en
  -- combinator.STRING_RANKS (lock > readonly).
  ('allowed_tools',               'list',    false, '["web_search", "read_file", "write_file", "governance_status"]', 'Allowlist of Hermes agent tool ids (deny-overrides across levels).'),
  ('offline_grace_minutes',       'integer', false, '60',    'Offline grace for the signed governance bundle (provider floor 15).'),
  ('unbind_mode',                 'string',  false, '"readonly"', 'Restricted-mode degree when unbound/revoked: readonly | lock.'),
  ('attestation_overdue_hours',   'integer', false, '24',    'Hours without attestation before an active device is flagged overdue.'),
  -- spec 024 (conexiones personalizadas, D-24-02/03; migración 0012): tokens
  -- '*' (todo el catálogo, sujeto a allowed_connectors), slugs del catálogo,
  -- o ids de conexiones personalizadas activas del tenant. Default '*' =
  -- sin cambio de comportamiento para tenants existentes.
  ('allowed_connections',         'list',    false, '["*"]', 'Connection tokens the level allows: ''*'' (whole catalog), catalog slugs, or custom connection ids (deny-overrides across levels).'),
  -- spec 029 (catálogo de policies v2, t_fb62560f; migración 0022): las 29 keys
  -- NUEVAS de la matriz de capacidades (docs/hermes-capability-matrix.md §3,
  -- P-1..P-29) — deny-by-default. Combinación key-aware (combinator.py):
  -- approval_mode por STRING_RANKS (manual>smart>off), denied_command_patterns
  -- por UNIÓN, require_write_sandbox/require_checkpoints por OR (true gana),
  -- max_delegation_depth/session_retention_days por mínimo con pisos en
  -- policies.py::INTEGER_FLOORS, web_domain_policy por deny-overrides por modo.
  ('approval_mode',               'string',  false, '"smart"', 'Command approval mode floor: off|smart|manual (STRING_RANKS manual>smart>off; vetoes approvals.mode:off, --yolo, /yolo, HERMES_YOLO_MODE).'),
  ('allowed_terminal_backends',   'list',    false, '["docker"]', 'Terminal backends the level allows (local,ssh,docker,modal,daytona,vercel_sandbox,singularity,*); intersection across levels.'),
  ('denied_command_patterns',     'list',    false, '[]',    'fnmatch globs of commands denied unconditionally; UNION across levels (deny-overrides: more denials = more restrictive).'),
  ('allowed_command_patterns',    'list',    false, '[]',    'Ceiling for the agent''s permanent command_allowlist; intersection across levels.'),
  ('require_write_sandbox',       'boolean', false, 'true',  'File writes confined to the write sandbox (HERMES_WRITE_SAFE_ROOT); OR across levels (true wins).'),
  ('allowed_mcp_servers',         'list',    false, '[]',    'MCP server names the level allows (*); intersection across levels.'),
  ('allowed_plugins',             'list',    false, '[]',    'Plugin names the level allows (*); intersection across levels.'),
  ('allowed_skills',              'list',    false, '["*"]', 'Skill ids the level allows (*); intersection across levels (spec 001 REQ-PRF-005 — was referenced but missing from the catalog).'),
  ('allow_scheduled_tasks',       'boolean', false, 'false', 'Agent may create cron jobs / scheduled tasks; AND across levels.'),
  ('allow_recurring_prompts',     'boolean', false, 'false', 'Recurring prompts in-session (/loop, session heartbeat); AND across levels.'),
  ('allow_goal_mode',             'boolean', false, 'false', 'Goal mode (LLM-judged goal loop); AND across levels.'),
  ('max_delegation_depth',        'integer', false, '1',     'Max subagent spawn depth (provider floor 0); minimum across levels.'),
  ('allowed_memory_providers',    'list',    false, '["builtin"]', 'Memory providers the level allows (builtin + names, *); intersection.'),
  ('allow_custom_personality',    'boolean', false, 'false', 'User may set a custom personality (SOUL.md / agent.personalities); AND.'),
  ('allowed_secret_sources',      'list',    false, '["env"]', 'Secret sources the level allows (env + sources, *); intersection.'),
  ('allow_credential_vault',      'boolean', false, 'false', 'Browser credential vault (logins/cards) and external login adoption; AND.'),
  ('allow_payment_autofill',      'boolean', false, 'false', 'Payment/card autofill prompts; AND across levels.'),
  ('allowed_env_passthrough',     'list',    false, '[]',    'Environment variable names that may pass through to sandboxes (*); intersection across levels.'),
  ('web_domain_policy',           'map',     false, '{"mode": "open", "domains": []}', 'Web domain policy {mode: open|allowlist|blocklist, domains: []}; deny-overrides per mode (any blocklist applies; allowlist = intersection).'),
  ('allow_private_network_access','boolean', false, 'false', 'Agent may reach private networks/metadata endpoints (SSRF opt-out); AND.'),
  ('allow_voice_mode',            'boolean', false, 'false', 'Voice mode (STT+TTS / full-duplex) and wake word; AND across levels.'),
  ('allow_bot_mode',              'boolean', false, 'false', 'Bot mode (bot roster, message_agent, bot_peers); AND across levels.'),
  ('allow_gateway_hooks',         'boolean', false, 'false', 'Gateway event hooks (~/.hermes/hooks/, trusted-by-placement); AND.'),
  ('allow_shell_hooks',           'boolean', false, 'false', 'Shell hooks (hooks: in config.yaml); AND across levels.'),
  ('allow_profile_distributions', 'boolean', false, 'false', 'Profile distributions (hermes profile install) and agent imports; AND.'),
  ('allowed_providers',           'list',    false, '["litellm_gateway"]', 'Model providers the level allows (litellm_gateway + providers, *); intersection across levels (routing/fallback cannot escape the list).'),
  ('session_retention_days',      'integer', false, '30',    'Session retention window in days (provider floor 1); minimum across levels.'),
  ('require_checkpoints',         'boolean', false, 'true',  'Checkpoints (shadow git store / rollback) must be enabled; OR across levels (true wins).'),
  ('allow_self_updates',          'boolean', false, 'false', 'Self-updates (hermes update, lazy installs, catalog installs); AND.')
ON CONFLICT (key) DO NOTHING;

-- 3.3 Catálogo global de modelos (LiteLLM/MaaS; pricing en centavos USD por millón de tokens.
--     Seeds de referencia: el job pricing_sync (REQ-MDL-004) actualiza con el pricing vigente.)
--     Spec 032 (discovery dinámico): filas por fuente — 'central' (MaaS de la
--     plataforma, tenant_id NULL, visibles para todos) y 'byok' (una conexión
--     llm del tenant; model_name namespaced byok:{connection_id}:{upstream_id}).
--     El UNIQUE(model_name) global se reemplaza por índices únicos parciales
--     por fuente (migración 0024).
CREATE TABLE IF NOT EXISTS catalog.model_catalog (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  model_name               text NOT NULL,                 -- central: MaaS deployment name; byok: byok:{conn}:{upstream_id}
  display_name             text NOT NULL,                 -- nombre oficial MaaS (p.ej. GLM-5.2)
  provider                 text NOT NULL,                 -- central: huawei_maas; byok: nombre de la conexión
  -- Precios ¢/M USD (peor caso). NULL = coste desconocido (alta automática
  -- del refresh central spec 018 §5 hasta sellado contra la doc oficial);
  -- el CHECK >= 0 se conserva: NULL no es "gratis".
  input_price_cents_per_m  numeric(12,3) CHECK (input_price_cents_per_m >= 0),
  output_price_cents_per_m numeric(12,3) CHECK (output_price_cents_per_m >= 0),
  status                   text NOT NULL DEFAULT 'available' CHECK (status IN ('available','deprecated','stale','removed')),
  updated_at               timestamptz NOT NULL DEFAULT now(),
  -- Columnas añadidas por la migración 0009 (spec 018) vía ADD COLUMN:
  -- al final de la tabla para preservar la paridad schema.sql ↔ Alembic
  -- (AC-DB-040). NULL = límite no documentado en el Model List intl.
  context_tokens           bigint,                        -- contexto documentado (Model List intl)
  max_output_tokens        bigint,                        -- máx. output documentado
  -- Columnas de la migración 0024 (spec 032, discovery dinámico):
  tenant_id                uuid,                          -- byok/provider: tenant dueño (fila por cliente habilitado)
  connection_id            uuid,                          -- byok: conexión llm (FK se añade en §16)
  source                   text NOT NULL DEFAULT 'central' CHECK (source IN ('central','byok','provider')),
  last_seen_at             timestamptz,                   -- último refresh que vio el modelo en la API
  gateway_model_id         text,                          -- id del modelo en el gateway LiteLLM (/model/delete)
  provider_id              uuid,                          -- provider: ops.model_providers (FK se añade en §22)
  CONSTRAINT model_catalog_source_scope_check CHECK (
    (source = 'central'   AND tenant_id IS NULL AND connection_id IS NULL
                          AND provider_id IS NULL) OR
    (source = 'byok'      AND tenant_id IS NOT NULL AND connection_id IS NOT NULL) OR
    (source = 'provider'  AND tenant_id IS NOT NULL AND provider_id IS NOT NULL
                          AND connection_id IS NULL)
  )
);
CREATE UNIQUE INDEX IF NOT EXISTS model_catalog_central_name_uidx
  ON catalog.model_catalog (model_name) WHERE source = 'central';
CREATE UNIQUE INDEX IF NOT EXISTS model_catalog_byok_uidx
  ON catalog.model_catalog (connection_id, model_name) WHERE source = 'byok';
-- spec 036: el MISMO model_name prov:{provider}:{upstream} se comparte entre
-- los tenants habilitados (una fila por tenant) — unicidad por (tenant, name).
CREATE UNIQUE INDEX IF NOT EXISTS model_catalog_provider_uidx
  ON catalog.model_catalog (tenant_id, model_name) WHERE source = 'provider';
CREATE INDEX IF NOT EXISTS model_catalog_tenant_idx
  ON catalog.model_catalog (tenant_id, source);

-- Catálogo REAL de MaaS intl (migración 0009 + 0026, spec 018 §1/§5): los
-- deployments verificados en vivo contra api-ap-southeast-1.modelarts-maas.com/openai/v1
-- (última verificación 2026-10-27T20:55Z). Precios ¢/M USD, peor caso (P1 /
-- tier ≥32K / reasoning); fuentes: price-maas/price-maas-0002.html (actualizada
-- 2026-10-27) y price-modelarts/price-modelarts-0047.html (Qwen3-32B, retirado).
-- qwen3-32b: retirado upstream (404 ModelArts.81009 en vivo) → stale (0026);
-- deepseek-v4.1-flash-1017: alta 2026-10-27, límites del Model List intl.
-- (ON CONFLICT sin target: el UNIQUE(model_name) global fue reemplazado por
--  índices únicos parciales por fuente en la migración 0024, spec 032.)
INSERT INTO catalog.model_catalog (model_name, display_name, provider, context_tokens, max_output_tokens, input_price_cents_per_m, output_price_cents_per_m, status) VALUES
  ('glm-5.3',             'GLM-5.3',             'huawei_maas', 1000000, 128000, 140.0, 440.0, 'available'),
  ('glm-5.2',             'GLM-5.2',             'huawei_maas', 1000000, 128000, 140.0, 440.0, 'available'),
  ('glm-5.1',             'GLM-5.1',             'huawei_maas',  198000, 128000, 107.8, 377.4, 'available'),
  ('deepseek-v4.1-flash-1017', 'DeepSeek-V4.1-Flash-1017', 'huawei_maas', 1000000, 384000, 30.0, 120.0, 'available'),
  ('deepseek-v4.1-flash', 'DeepSeek-V4.1-Flash', 'huawei_maas', 1000000, 384000,  30.0, 120.0, 'available'),
  ('deepseek-v4-flash',   'DeepSeek-V4-Flash',   'huawei_maas', 1000000, 384000,  13.5,  27.0, 'available'),
  ('qwen3-32b',           'Qwen3-32B',           'huawei_maas',    NULL,   NULL,  27.0, 269.5, 'stale')
ON CONFLICT DO NOTHING;

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
  ('user.self_registered',       'User created their own account with a password (spec 015 D-AUTH-04).'),
  ('user.auto_provisioned',      'User auto-provisioned on first brokered login (spec 015 D-AUTH-05).'),
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
  ('device.rejected',            'Device enrollment rejected (pending -> rejected).'),
  ('device.revoked',             'Device revoked (sessions cut immediately).'),
  ('device.heartbeat',           'Device heartbeat (sampled: at most one per device per audit interval).'),
  ('policy.propagated',          'Device confirmed it applied a new policy version (heartbeat transition).'),
  ('device.attestation',         'Agent session attestation: bundle hash, applied policy version, operating mode and veto counts.'),
  ('device.attestation_overdue', 'Active device with no attestation beyond attestation_overdue_hours (ungoverned agent detection).'),
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
  ('tenant.self_registered',     'Organization created via public self-signup (spec 026, pending activation).'),
  ('tenant.activated',           'Tenant activated by provider from pending_activation (spec 026).'),
  ('tenant.deleted',             'Tenant deprovisioned by provider: Keycloak realm + every row removed (irreversible). Event lives in ops.provider_audit.'),
  ('budget.assigned',            'Initial budget assigned by the provider on activation (spec 026).'),
  ('audit.verified',             'Audit chain verification run.'),
  ('audit.purged',               'Audit retention purge executed.'),
  ('job.created',                'Async job created.'),
  ('job.completed',              'Async job finished.'),
  ('provider.login',             'Provider operator signed in (username+password[+TOTP]).'),
  ('provider.login_denied',      'Provider operator login denied (bad credentials, disabled or bad TOTP).'),
  ('provider.password_changed',  'Provider operator changed their password (first-login required action or rotation).'),
  ('provider.mfa_enrolled',      'Provider operator enrolled TOTP MFA (verified before activation).'),
  ('provider.mfa_disabled',      'Provider operator disabled TOTP MFA (password+code confirmed).')
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
  status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','pending_activation')),
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
  spent_cents  numeric(12,3) NOT NULL DEFAULT 0 CHECK (spent_cents >= 0),  -- milli-centavo (D-GOV-17, migración 0029)
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
  mode              text NOT NULL CHECK (mode IN ('central','byok','provider')),  -- provider: modelos del provider (spec 036); espeja model_access.mode (D-GOV-06)
  cost_base_cents   numeric(12,3) NOT NULL CHECK (cost_base_cents >= 0),    -- milli-centavo (D-GOV-17, migración 0029)
  cost_billed_cents numeric(12,3) NOT NULL CHECK (cost_billed_cents >= 0),  -- milli-centavo (D-GOV-17, migración 0029)
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
  -- Presencia en vivo (spec 027, D-027-06): señal de agente reportando
  -- heartbeat, distinta del last_seen_at del DeviceGate (throttle 60 s).
  -- Al final de la tabla: la migración 0017 las añade con add_column.
  last_heartbeat_at       timestamptz,   -- último POST /api/me/device-heartbeat
  applied_policy_version  integer,       -- versión de policy que el device reporta aplicar
  agent_uptime_seconds    integer,       -- uptime del agente Hermes reportado
  last_heartbeat_audit_at timestamptz,   -- soporte del muestreo device.heartbeat (D-027-05)
  -- Attestation (spec 019 Inc. 3, REQ-GOH-012/013): señal de agente reportando
  -- gobernanza por sesión; el overdue dedupe usa la ventana de la policy key.
  last_attestation_at         timestamptz,  -- último POST /api/devices/{id}/attestation
  last_attestation_overdue_at timestamptz,  -- dedupe notificación+audit overdue (REQ-GOH-013)
  -- Tipo de dispositivo (spec 034, D-034-04): 'cli' = Hermes CLI instalado
  -- vía curl|bash ligado a la org; 'desktop' = bundle desktop (default
  -- retrocompatible — migración 0025).
  device_type  text NOT NULL DEFAULT 'desktop' CHECK (device_type IN ('desktop','cli')),
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
  mode          text NOT NULL DEFAULT 'central' CHECK (mode IN ('central','byok','provider')),
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
  ('agent.profile_adopted',
   'Default agent profile adopted (tenant instance).'),
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
  -- Añadida por la migración 0009_default_agent_profiles (spec 020) vía
  -- ADD COLUMN: al final de la tabla por paridad schema.sql ↔ Alembic (AC-DB-040).
  profile_key   text,            -- spec 020: perfil default del producto adoptado
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, owner_user_id, name),
  FOREIGN KEY (tenant_id, owner_user_id) REFERENCES users (tenant_id, id),
  FOREIGN KEY (tenant_id, department_id) REFERENCES departments (tenant_id, id)
);
-- spec 020: UNA instancia viva por tenant y perfil default (D-020-02)
CREATE UNIQUE INDEX IF NOT EXISTS agents_tenant_profile_uidx
  ON agents (tenant_id, profile_key)
  WHERE profile_key IS NOT NULL AND status = 'active';

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

-- ============================================================================
-- 12. Evolución spec 016 (departments CRUD): rename + delete
--     Misma migración: db/migrations/versions/20261026_0009_departments_crud.py
--     (paridad pg_dump exigida por db/tests/test_schema_parity.py)
--     Solo datos: cero DDL nuevo (departments y todas las FK ya existen
--     desde 0001; las reglas de negocio viven en app/org/departments.py).
-- ============================================================================

-- 12.1 Nuevas acciones de audit (enum versionado, extensión por datos)
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('department.renamed', 'Department renamed (old and new name in payload).'),
  ('department.deleted', 'Department deleted (empty department; config cascaded, payload is self-sufficient).')
ON CONFLICT (action) DO NOTHING;

-- ============================================================================
-- 13. Evolución spec 017 (roles & access): permiso roles.assign
--     Misma migración: db/migrations/versions/20261026_0010_roles_access.py
--     (paridad pg_dump exigida por db/tests/test_schema_parity.py)
--     Solo datos: cero DDL nuevo. La puerta de los endpoints de asignación
--     de roles es data-driven (constitution I10): un permiso del catálogo
--     otorgado ÚNICAMENTE a org_admin. dept_admin conserva users.manage
--     (editar depto/nombre/deshabilitar dentro de su scope) pero NO puede
--     tocar roles — sin escalada a org_admin.
-- ============================================================================

-- 13.1 Permiso nuevo + grant solo a org_admin
INSERT INTO catalog.permissions (key, description) VALUES
  ('roles.assign', 'Assign/revoke tenant roles (org_admin only).')
ON CONFLICT (key) DO NOTHING;

INSERT INTO catalog.role_permissions (role_key, permission_key) VALUES
  ('org_admin', 'roles.assign')
ON CONFLICT (role_key, permission_key) DO NOTHING;

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
  kind              text NOT NULL CHECK (kind IN ('database','api_key','custom','llm')),
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
-- Spec 032 (D-CP-10, patrón devices): listing cross-tenant de conexiones
-- llm activas para el job periódico de refresh del catálogo — el jobs-worker
-- enumera tenants vía la conexión BYPASSRLS de claim (trust_migrator),
-- SOLO lectura (mínimo privilegio). Paridad: migración 0024.
GRANT SELECT ON TABLE vault_connections TO trust_migrator;

-- ============================================================================
-- 15. Evolución spec 025 (super-user del Provider Console, mandato Miller):
--     operador + sesiones + audit del plano proveedor (ops, sin RLS).
--     Migración: db/migrations/versions/20261026_0016_provider_superuser.py
--     (las acciones provider.* del catálogo viven en el INSERT de §3).
-- ============================================================================

-- 15.1 ops.provider_operators — el ÚNICO actor fuera del multi-tenant.
-- Plano proveedor: sin tenant_id, sin RLS (D-DB-01). password_hash = PHC
-- argon2id (nunca plaintext); el secret TOTP vive cifrado con envelope
-- AES-256-GCM (KEK fuera de la BD) y es NULL hasta el enrolamiento;
-- must_change_password = required action del primer login.
CREATE TABLE IF NOT EXISTS ops.provider_operators (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username             text NOT NULL UNIQUE,
  password_hash        text NOT NULL,
  status               text NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active','disabled')),
  must_change_password boolean NOT NULL DEFAULT true,
  totp_secret_enc      bytea,
  totp_dek_enc         bytea,
  totp_nonce           bytea,
  totp_key_id          text,
  totp_active          boolean NOT NULL DEFAULT false,
  totp_enrolled_at     timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE ops.provider_operators IS 'Provider super-users (spec 025, mandate Miller): the ONLY actor outside the multi-tenant scope. Provider plane: no tenant_id, no RLS (D-DB-01). password_hash is a PHC argon2id string; the TOTP secret is stored envelope-encrypted (AES-256-GCM, KEK never in the DB) and is NULL until enrollment.';

-- 15.2 ops.provider_sessions — sesiones del operador en PostgreSQL
-- (spec 025 D-PS-04): token opaco pst1.<id>.<secret>, solo sha256(secret)
-- en BD (jti). Misma infra que public.sessions pero en el plano proveedor.
-- Nada en memoria: las sesiones sobreviven reinicios del API por
-- construcción.
CREATE TABLE IF NOT EXISTS ops.provider_sessions (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operator_id  uuid NOT NULL REFERENCES ops.provider_operators(id),
  jti          text NOT NULL UNIQUE,
  status       text NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active','revoked','expired')),
  mfa_verified boolean NOT NULL DEFAULT false,
  ip           inet,
  user_agent   text,
  expires_at   timestamptz NOT NULL,
  last_seen_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS provider_sessions_operator_idx
  ON ops.provider_sessions (operator_id, status);

COMMENT ON TABLE ops.provider_sessions IS 'Provider operator sessions in PostgreSQL (spec 025 D-PS-04): opaque token pst1.<id>.<secret>, only sha256(secret) stored (jti). Same infrastructure as public.sessions but in the provider plane (no tenant scope). Nothing in process memory: sessions survive API restarts by construction.';

-- 15.3 ops.provider_audit — audit del plano proveedor (spec 025 D-PS-07):
-- eventos de acceso del operador que no pertenecen a ningún tenant
-- (audit_log exige tenant_id NOT NULL + FK). Append-only con hash chain
-- GLOBAL (mismo patrón del trigger de audit_log, secuencia única) +
-- trigger de rechazo de UPDATE/DELETE. action lleva FK al catálogo
-- (data-driven, I10).
CREATE TABLE IF NOT EXISTS ops.provider_audit (
  id          bigserial PRIMARY KEY,
  seq         bigint NOT NULL,
  action      text NOT NULL REFERENCES catalog.audit_actions(action),
  operator_id uuid,
  username    text,
  ip          inet,
  user_agent  text,
  payload     jsonb NOT NULL DEFAULT '{}',
  ts          timestamptz NOT NULL DEFAULT now(),
  hash_prev   char(64) NOT NULL,
  hash        char(64) NOT NULL
);

COMMENT ON TABLE ops.provider_audit IS 'Append-only audit for provider-plane events (spec 025 D-PS-07): operator access events that belong to no tenant. Global hash chain (same trigger pattern as audit_log, single sequence); UPDATE/DELETE rejected by trigger.';

CREATE OR REPLACE FUNCTION ops.provider_audit_canonical_payload(
    rec ops.provider_audit) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT concat_ws('|',
    rec.seq::text,
    rec.action,
    COALESCE(rec.operator_id::text, ''),
    COALESCE(rec.username, ''),
    COALESCE(rec.ip::text, ''),
    COALESCE(rec.user_agent, ''),
    rec.payload::text,
    extract(epoch FROM rec.ts)::text
  );
$$;

CREATE OR REPLACE FUNCTION ops.provider_audit_before_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_prev_hash text;
  v_prev_seq  bigint;
BEGIN
  PERFORM pg_advisory_xact_lock(
    hashtextextended('provider-audit', 0));
  SELECT hash, seq INTO v_prev_hash, v_prev_seq
    FROM ops.provider_audit
    ORDER BY id DESC
    LIMIT 1;
  NEW.seq       := COALESCE(v_prev_seq, 0) + 1;
  NEW.hash_prev := COALESCE(v_prev_hash, repeat('0', 64));
  NEW.hash      := encode(sha256(convert_to(
    NEW.hash_prev || '|' ||
    ops.provider_audit_canonical_payload(NEW), 'utf8')), 'hex');
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS provider_audit_chain_trigger ON ops.provider_audit;
CREATE TRIGGER provider_audit_chain_trigger
  BEFORE INSERT ON ops.provider_audit
  FOR EACH ROW EXECUTE FUNCTION ops.provider_audit_before_insert();

CREATE OR REPLACE FUNCTION ops.provider_audit_reject_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION
    'ops.provider_audit is append-only: % is prohibited (id %)',
    TG_OP, COALESCE(OLD.id, NEW.id)
    USING ERRCODE = 'raise_exception';
END $$;

DROP TRIGGER IF EXISTS provider_audit_immutable_trigger ON ops.provider_audit;
CREATE TRIGGER provider_audit_immutable_trigger
  BEFORE UPDATE OR DELETE ON ops.provider_audit
  FOR EACH ROW EXECUTE FUNCTION ops.provider_audit_reject_mutation();

CREATE OR REPLACE FUNCTION ops.provider_audit_verify_chain()
RETURNS TABLE (status text, seq bigint)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  r ops.provider_audit%ROWTYPE;
  v_prev text := repeat('0', 64);
  v_seq  bigint := 0;
BEGIN
  FOR r IN SELECT * FROM ops.provider_audit ORDER BY id LOOP
    IF r.seq <> v_seq + 1 OR r.hash_prev <> v_prev
       OR r.hash <> encode(sha256(convert_to(
            r.hash_prev || '|' ||
            ops.provider_audit_canonical_payload(r), 'utf8')),
            'hex') THEN
      RETURN QUERY SELECT 'broken'::text, r.seq;
      RETURN;
    END IF;
    v_prev := r.hash;
    v_seq  := r.seq;
  END LOOP;
  RETURN QUERY SELECT 'ok'::text, NULL::bigint;
END $$;

-- 15.4 Triggers updated_at (la función set_updated_at() existe desde §6.1;
-- el DO-loop de §6.1 solo barre public, ops.* se declara explícito).
CREATE TRIGGER provider_operators_set_updated_at
  BEFORE UPDATE ON ops.provider_operators
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER provider_sessions_set_updated_at
  BEFORE UPDATE ON ops.provider_sessions
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 15.5 Grants explícitos (las tablas nacen después del grant blanket de la
-- sección 8, paridad con la vía Alembic). provider_audit es append-only
-- para la app: INSERT/SELECT solamente.
GRANT SELECT, INSERT, UPDATE ON TABLE ops.provider_operators TO trust_app;
GRANT SELECT, INSERT, UPDATE ON TABLE ops.provider_sessions TO trust_app;
GRANT SELECT, INSERT ON TABLE ops.provider_audit TO trust_app;
GRANT USAGE ON SEQUENCE ops.provider_audit_id_seq TO trust_app;
GRANT ALL PRIVILEGES ON TABLE ops.provider_operators TO trust_migrator;
GRANT ALL PRIVILEGES ON TABLE ops.provider_sessions TO trust_migrator;
GRANT ALL PRIVILEGES ON TABLE ops.provider_audit TO trust_migrator;
GRANT USAGE, SELECT, UPDATE ON SEQUENCE ops.provider_audit_id_seq TO trust_migrator;

-- ============================================================================
-- 16. Evolución spec 028 (branding REQ-BRD-004 de la spec 001): logos
--     light/dark + favicon por tenant, versionado draft/publish y rollback.
--     Migración: db/migrations/versions/20261026_0018_branding_versioning.py
-- ============================================================================

-- 16.1 tenants += draft de edición (NUNCA visible para usuarios —
-- REQ-BRD-004) y versión publicada. tenants.branding sigue siendo SOLO lo
-- publicado (D-028-02).
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS branding_draft jsonb NOT NULL DEFAULT '{}';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS branding_version integer NOT NULL DEFAULT 0;

-- 16.2 branding_assets — logos light/dark + favicon por tenant (D-028-01):
-- bytea (NO jsonb gigante: RLS uniforme, backup pg_dump, publish atómico
-- en la misma tx). Append-only por diseño: cada upload inserta una fila
-- nueva y los assets referenciados por versiones publicadas jamás se mutan.
CREATE TABLE IF NOT EXISTS branding_assets (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  kind        text NOT NULL CHECK (kind IN ('logo_light','logo_dark','favicon')),
  mime        text NOT NULL CHECK (mime IN ('image/png','image/svg+xml')),
  size_bytes  integer NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 2097152),
  sha256      text NOT NULL,
  content     bytea NOT NULL,
  created_by  uuid,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS branding_assets_tenant_kind_idx
  ON branding_assets (tenant_id, kind);
CREATE INDEX IF NOT EXISTS branding_assets_tenant_id_idx
  ON branding_assets (tenant_id);

COMMENT ON TABLE branding_assets IS 'Tenant branding assets (spec 028, REQ-BRD-001/004): logo light/dark + favicon, PNG/SVG up to 2 MB, stored as bytea (D-028-01 — never inside tenants.branding jsonb). Append-only: every upload inserts a new row; assets referenced by published versions are never mutated.';

-- 16.3 branding_versions — historial append-only de lo publicado
-- (D-028-03). Publish = INSERT versión N+1 + UPDATE tenants.branding en
-- UNA transacción; rollback = re-publicar una versión anterior como
-- versión nueva.
CREATE TABLE IF NOT EXISTS branding_versions (
  tenant_id    uuid NOT NULL REFERENCES tenants(id),
  version      integer NOT NULL CHECK (version >= 1),
  data         jsonb NOT NULL,
  published_by uuid,
  published_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, version)
);

COMMENT ON TABLE branding_versions IS 'Published branding history (spec 028, D-028-03): exact jsonb published per version. Publish is atomic (INSERT next version + UPDATE tenants.branding in one tx); rollback republishes an older version as a NEW version — the history is append-only.';

-- 16.4 RLS: mismo patrón fail-closed + FORCE que la sección 7.
DO $$
DECLARE
  t text;
  tables text[] := ARRAY['branding_assets','branding_versions'];
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

-- 16.5 Grants explícitos a la app (las tablas nacen después del grant
-- blanket de la sección 8 — paridad con la vía Alembic).
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE branding_assets TO trust_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE branding_versions TO trust_app;

-- 16.6 Nuevas acciones de audit (enum versionado, extensión por datos).
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('branding.draft.updated',
   'Tenant branding draft edited (not yet visible to users — REQ-BRD-004).'),
  ('branding.published',
   'Tenant branding draft published atomically as a new version.'),
  ('branding.rolled_back',
   'Tenant branding rolled back to a previous published version.'),
  ('branding.asset.uploaded',
   'Branding asset uploaded (logo light/dark or favicon, PNG/SVG <= 2 MB).')
ON CONFLICT (action) DO NOTHING;

-- 17. Evolución spec 023 (hermes download — seam link→app, t_526030f3):
--     link público de descarga por tenant gestionado por el ADMIN DEL CLIENTE
--     (corrección Miller 2026-10-26). Migración:
--     db/migrations/versions/20261026_0021_hermes_download.py
--
-- 17.1 tenants += estado del link público (D-023-08): habilitado por defecto
--     (un tenant recién creado es descargable sin tocar nada; el admin lo
--     deshabilita a propósito desde el Admin Console).
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS download_enabled boolean NOT NULL DEFAULT true;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS download_updated_at timestamptz NOT NULL DEFAULT now();

-- 17.2 Permiso de gestión del link (D-023-09): SOLO org_admin — coherente con
--     el gate de rol de la sección "Hermes Download" del Admin Console.
INSERT INTO catalog.permissions (key, description) VALUES
  ('download_link.manage',
   'Enable/disable the public Hermes download link.')
ON CONFLICT (key) DO NOTHING;

INSERT INTO catalog.role_permissions (role_key, permission_key) VALUES
  ('org_admin', 'download_link.manage')
ON CONFLICT (role_key, permission_key) DO NOTHING;

-- 17.3 Acciones de audit (enum versionado, extensión por datos):
--     download.issued por descarga del bundle (REQ-HDL-004); toggles
--     auditados del link (REQ-HDL-005).
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('download.issued',
   'Hermes bundle downloaded from the public link.'),
  ('download.link.enabled',
   'Public download link enabled by the organization admin.'),
  ('download.link.disabled',
   'Public download link disabled by the organization admin.')
ON CONFLICT (action) DO NOTHING;

-- ============================================================================
-- 18. Evolución spec 022 v1.1.0 (amend D-HRB-03 / REQ-HRB-010, gap
--     t_0b6e8e2d): re-emisión directa del device token en enroll para un
--     device active del mismo usuario con id_token MFA fresco (recuperación
--     del token expirado — agente apagado > TTL).
--     Migración: db/migrations/versions/20261027_0023_device_token_reissue.py
-- ============================================================================

-- 18.1 Acción de audit (enum versionado, extensión por datos).
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('device.token_reissued',
   'Device token re-issued directly on enroll: already-active device of the same user re-proved identity with a fresh MFA id_token (expired-token recovery, spec 022 D-HRB-03).')
ON CONFLICT (action) DO NOTHING;

-- ============================================================================
-- 19. Evolución spec 032 (discovery dinámico de modelos, mandato Miller):
--     toda conexión llm (OpenAI-compatible) del vault aporta sus modelos al
--     catálogo (GET {base_url}/v1/models con la key de la conexión), con
--     ciclo de vida stale/removed y gate compuesto conexión∧aprobación.
--     Migración: db/migrations/versions/20261027_0024_dynamic_model_discovery.py
--     (columnas/índices/CHECKs de §3.3 y kind de §14.4 ya reflejados arriba).
-- ============================================================================

-- 19.1 FK byok → conexión (aquí porque vault_connections nace en §14.4,
--      después de §3.3; mismo nombre auto que la migración).
ALTER TABLE catalog.model_catalog
  ADD CONSTRAINT model_catalog_connection_id_fkey
  FOREIGN KEY (connection_id) REFERENCES vault_connections(id) ON DELETE SET NULL;

-- 19.2 RLS por-tenant (patrón §14.6): las filas byok son del tenant; las
--      centrales (tenant_id IS NULL) siguen visibles para todos.
ALTER TABLE catalog.model_catalog ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog.model_catalog FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON catalog.model_catalog
  FOR ALL TO trust_app
  USING      (tenant_id IS NULL OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id IS NULL OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

-- 19.3 El discovery escribe filas byok como trust_app dentro del scope.
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE catalog.model_catalog TO trust_app;

-- 19.4 Acciones de audit (enum versionado, extensión por datos).
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('model.catalog_refreshed',
   'Model catalog refreshed from a connected API (BYOK connection and/or central gateway): models discovered, updated and marked stale (spec 032).')
ON CONFLICT (action) DO NOTHING;

-- ============================================================================
-- 20. Evolución spec 035 (usage analytics): el admin ve requests y consumos
--     por usuario sobre el ledger real (mandato Miller).
--     Misma migración: db/migrations/versions/20261027_0027_usage_export_audit.py
--     (paridad pg_dump exigida por db/tests/test_schema_parity.py)
--     Solo datos: cero DDL nuevo (spend_ledger y sus índices ya existen
--     desde 0001/0004).
-- ============================================================================

-- 20.1 Nueva acción de audit (enum versionado, extensión por datos)
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('usage.exported',
   'Usage analytics CSV exported (per-user or per-request level).')
ON CONFLICT (action) DO NOTHING;

-- ============================================================================
-- 21. Evolución spec 015 v1.3.0 (ADR-019, auth panel — Keycloak nunca
--     visible): estado de los "dances" de autenticación del panel.
--     Misma migración: db/migrations/versions/20261028_0030_auth_panel_dances.py
--     (paridad pg_dump exigida por db/tests/test_schema_parity.py)
-- ============================================================================

-- 21.1 ops.auth_dances — estado intermedio del enrolamiento TOTP (D-AUTH-14)
--     y del proof-handle de UPDATE_PASSWORD (D-AUTH-15) entre requests.
--     Pre-autenticación por naturaleza: plano ops, sin RLS (D-DB-01/D-DB-14).
--     El handle se guarda HASHEADO (sha256, como sessions.jti); passwords
--     JAMÁS se persisten. TTL 10 minutos; las filas consumidas se borran
--     (por eso trust_app sí lleva DELETE aquí, a diferencia de login_throttle).
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
);

CREATE INDEX IF NOT EXISTS ix_auth_dances_expiry ON ops.auth_dances (expires_at);

COMMENT ON TABLE ops.auth_dances IS 'In-flight Keycloak auth dances for the auth panel (ADR-019, D-AUTH-14): TOTP enrollment state between the setup and confirm requests. Handle stored hashed; TTL 10 minutes; NO passwords ever persisted. Provider plane: no RLS.';

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE ops.auth_dances TO trust_app;
-- El blanket de migrator (§ grants) corre ANTES de esta sección en la vía
-- schema.sql: el grant explícito mantiene la paridad pg_dump con la
-- migración (que crea la tabla después del blanket de 0001).
GRANT ALL ON TABLE ops.auth_dances TO trust_migrator;

-- 21.2 Nuevas acciones de audit (enum versionado, extensión por datos).
--     Los eventos NUNCA llevan material de credencial (D-AUTH-16).
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('auth.totp_enrolled', 'TOTP (MFA) enrolled via the auth panel.'),
  ('auth.password_changed', 'Password set or changed via the auth panel.')
ON CONFLICT (action) DO NOTHING;

-- ============================================================================
-- 22. Evolución spec 036 (model providers — repro Miller t_64e645c1):
--     proveedores de modelos operados por el provider + habilitación por
--     tenant. Misma migración: db/migrations/versions/20261028_0032_model_providers.py
--     (paridad pg_dump exigida por db/tests/test_schema_parity.py)
-- ============================================================================

-- 22.1 ops.model_providers — registro del provider (D-036-01). La key vive
--      SOLO como envelope ciphertext (mismo crypto que vault_connections);
--      el descifrado es en memoria, por uso, server-side (KEK). Plano
--      provider: sin RLS (D-DB-01/D-DB-14, patrón ops.provider_operators).
CREATE TABLE IF NOT EXISTS ops.model_providers (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                     text NOT NULL UNIQUE,
  display_name             text,
  base_url                 text NOT NULL,
  status                   text NOT NULL DEFAULT 'active'
                             CHECK (status IN ('active','disabled')),
  ciphertext_secret        bytea NOT NULL,
  ciphertext_dek           bytea NOT NULL,
  nonce                    bytea NOT NULL,
  key_id                   text NOT NULL,
  input_price_cents_per_m  numeric(12,3) CHECK (input_price_cents_per_m >= 0),
  output_price_cents_per_m numeric(12,3) CHECK (output_price_cents_per_m >= 0),
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now()
);

-- 22.2 ops.model_provider_tenants — habilitación por cliente: la "política
--      de accesos" del proveedor. Sin fila o enabled=false, el cliente NO
--      ve los modelos del proveedor (gate compuesto D-036-03).
CREATE TABLE IF NOT EXISTS ops.model_provider_tenants (
  provider_id  uuid NOT NULL REFERENCES ops.model_providers(id)
                 ON DELETE CASCADE,
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  enabled      boolean NOT NULL DEFAULT true,
  enabled_at   timestamptz NOT NULL DEFAULT now(),
  disabled_at  timestamptz,
  PRIMARY KEY (provider_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS model_provider_tenants_tenant_idx
  ON ops.model_provider_tenants (tenant_id);

-- 22.3 FK de catalog.model_catalog.provider_id (la tabla ops se crea aquí;
--      mismo patrón diferido que §16 para connection_id).
ALTER TABLE catalog.model_catalog
  ADD CONSTRAINT model_catalog_provider_id_fkey
  FOREIGN KEY (provider_id) REFERENCES ops.model_providers(id)
  ON DELETE SET NULL;

GRANT SELECT, INSERT, UPDATE ON TABLE ops.model_providers TO trust_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE ops.model_provider_tenants TO trust_app;
GRANT ALL ON TABLE ops.model_providers TO trust_migrator;
GRANT ALL ON TABLE ops.model_provider_tenants TO trust_migrator;

-- 22.4 Permiso nuevo (data-driven, D-ONB-02) + grant a platform_admin: el
--      operador del provider gestiona proveedores de modelos.
INSERT INTO catalog.permissions (key, description) VALUES
  ('providers.manage',
   'Register/refresh model providers and enable them per tenant (provider).')
ON CONFLICT (key) DO NOTHING;

INSERT INTO catalog.role_permissions (role_key, permission_key) VALUES
  ('platform_admin', 'providers.manage')
ON CONFLICT (role_key, permission_key) DO NOTHING;

-- 22.5 Acciones de audit (enum versionado, extensión por datos): los
--      eventos SOBRE el tenant. Los del operador viven en ops.provider_audit
--      (spec 025 D-PS-07). Sin material de key jamás.
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('model.provider_enabled',
   'Provider model source enabled for the tenant by the provider (models approved at org level).'),
  ('model.provider_disabled',
   'Provider model source disabled for the tenant by the provider (its models went stale).'),
  -- Operator-plane events (ops.provider_audit has FK → audit_actions,
  -- data-driven I10). Migración 0032 (paridad pg_dump).
  ('provider.created',
   'Model provider registered by a provider operator (key in vault envelope, never in the payload).'),
  ('provider.refreshed',
   'Model provider catalog refreshed by a provider operator (real upstream fetch).'),
  ('provider.tenant_enabled',
   'Model provider enabled for a tenant by a provider operator.'),
  ('provider.tenant_disabled',
   'Model provider disabled for a tenant by a provider operator.'),
  ('provider.suspended',
   'Model provider suspended globally by a provider operator.'),
  ('provider.reactivated',
   'Model provider reactivated globally by a provider operator.')
ON CONFLICT (action) DO NOTHING;

-- ============================================================================
-- 23. Evolución deprovisioning de tenants (t_4f4b0eb1, migración 0031).
--     Los INSERT de catálogo (tenant.deleted / tenants.delete / grant a
--     platform_admin) viven en las secciones 3.x de arriba. Aquí va el
--     GRANT HEALING: las tablas creadas DESPUÉS del grant blanket de la
--     sección 8 (agents, agent_grants, notifications, vault_connections,
--     branding_assets, branding_versions) nunca recibieron DELETE para
--     trust_migrator — el deprovisioning corre como migrator (BYPASSRLS)
--     y revienta con InsufficientPrivilege (verificado en la DB real).
--     Re-grant blanket idempotente; la migración 0031 ejecuta las MISMAS
--     sentencias (paridad pg_dump, db/tests/test_schema_parity.py).
-- ============================================================================
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public, catalog, ops TO trust_migrator;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO trust_migrator;

-- ============================================================================
-- 24. Evolución spec 038 (departments onboarding — estructura real al nacer):
--     flag por tenant que arranca PENDIENTE para todos (nuevos y existentes)
--     + acción de audit del cierre. Misma migración:
--     db/migrations/versions/20261028_0033_departments_onboarding.py
--     (paridad pg_dump exigida por db/tests/test_schema_parity.py)
-- ============================================================================

-- 24.1 Flag de onboarding de estructura (D-38-02): DEFAULT true backfilla a
--      los tenants existentes — cada org_admin ve el wizard "Organization
--      structure" UNA vez en su próximo login con el tenant active.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS
  structure_onboarding_pending boolean NOT NULL DEFAULT true;

-- 24.2 Acción de audit del cierre (D-38-06, técnica INSERT-only de la
--      migración 0009): payload {source: "wizard"|"skip", created,
--      renamed, deleted}; se emite solo en la transición pendiente ->
--      completado (actor = el org_admin).
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('onboarding.structure_completed',
   'Organization structure onboarding completed (wizard confirm or skip).')
ON CONFLICT (action) DO NOTHING;

-- ============================================================================
-- 25. Evolución spec 041 (políticas nombradas — visión Miller): una política
--     nombrada = preset de policy keys; la asignación MATERIALIZA vía
--     policies.upsert existente (engine intacto, D-41-01). Misma migración:
--     db/migrations/versions/20261029_0034_named_policies.py
--     (paridad pg_dump exigida por db/tests/test_schema_parity.py). El
--     backfill de plantillas para tenants existentes vive SOLO en la
--     migración (schema.sql corre sobre una DB vacía); el provisioning
--     nuevo siembra desde platform/app/governance/named_policies.py.
-- ============================================================================

-- 25.1 named_policies — el preset: name + description + definition (mapa
--      key→value sobre las 44 keys no invariables) + source_template
--      ('builtin:<slug>' para las plantillas sembradas, D-41-08).
CREATE TABLE IF NOT EXISTS named_policies (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id),
  name           text NOT NULL,
  description    text,
  definition     jsonb NOT NULL,
  source_template text,
  created_by     uuid,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS named_policies_tenant_name_uidx
  ON named_policies (tenant_id, name);
-- Seed idempotente POR SLUG (D-41-08): re-correr el seed no duplica; si el
-- admin borra la plantilla, el slug queda libre (son políticas comunes).
CREATE UNIQUE INDEX IF NOT EXISTS named_policies_template_uidx
  ON named_policies (tenant_id, source_template)
  WHERE source_template IS NOT NULL;
CREATE INDEX IF NOT EXISTS named_policies_tenant_idx
  ON named_policies (tenant_id);

-- 25.2 named_policy_assignments — una fila por (política, destinatario):
--      applied_keys = snapshot de ownership (D-41-05), applied_at = marcador
--      de stale vs named_policies.updated_at (D-41-06).
CREATE TABLE IF NOT EXISTS named_policy_assignments (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id),
  named_policy_id  uuid NOT NULL REFERENCES named_policies(id)
                     ON DELETE CASCADE,
  level            text NOT NULL CHECK (level IN ('org','dept','user')),
  department_id    uuid,
  user_id          uuid,
  applied_keys     jsonb NOT NULL,
  applied_at       timestamptz NOT NULL DEFAULT now(),
  created_by       uuid,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT named_policy_assignments_scope_check CHECK (
    (level = 'org'  AND department_id IS NULL     AND user_id IS NULL) OR
    (level = 'dept' AND department_id IS NOT NULL AND user_id IS NULL) OR
    (level = 'user' AND department_id IS NOT NULL AND user_id IS NOT NULL)
  ),
  FOREIGN KEY (tenant_id, department_id) REFERENCES departments (tenant_id, id),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
);
-- UNIQUE por (política, destinatario) — los NULL de org no colisionan
-- (mismo truco que policies_scope_uidx).
CREATE UNIQUE INDEX IF NOT EXISTS named_policy_assignments_scope_uidx
  ON named_policy_assignments (named_policy_id, level,
    COALESCE(department_id, '00000000-0000-0000-0000-000000000000'::uuid),
    COALESCE(user_id,       '00000000-0000-0000-0000-000000000000'::uuid));
CREATE INDEX IF NOT EXISTS named_policy_assignments_tenant_idx
  ON named_policy_assignments (tenant_id);
CREATE INDEX IF NOT EXISTS named_policy_assignments_policy_idx
  ON named_policy_assignments (named_policy_id);

DROP TRIGGER IF EXISTS named_policies_set_updated_at ON named_policies;
CREATE TRIGGER named_policies_set_updated_at
  BEFORE UPDATE ON named_policies
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
DROP TRIGGER IF EXISTS named_policy_assignments_set_updated_at
  ON named_policy_assignments;
CREATE TRIGGER named_policy_assignments_set_updated_at
  BEFORE UPDATE ON named_policy_assignments
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 25.3 RLS: mismo patrón fail-closed + FORCE que la sección 7.
DO $$
DECLARE
  t text;
  tables text[] := ARRAY['named_policies','named_policy_assignments'];
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

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
  named_policies, named_policy_assignments TO trust_app;
-- El deprovisioning (sección 23) borra por tenant_id como trust_migrator.
GRANT ALL PRIVILEGES ON TABLE
  named_policies, named_policy_assignments TO trust_migrator;

-- 25.4 Acciones de audit (enum versionado, extensión por datos — FK de
--      audit_log.action). Migración 0034 (paridad pg_dump).
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('named_policy.created',
   'Named policy created (preset of policy keys, spec 041).'),
  ('named_policy.updated',
   'Named policy edited; existing assignments become stale until reapplied.'),
  ('named_policy.deleted',
   'Named policy deleted (only without active assignments, or after cascade unassign).'),
  ('named_policy.assigned',
   'Named policy assigned to org/dept/user: keys materialized via policies.upsert in one transaction.'),
  ('named_policy.unassigned',
   'Named policy unassigned: only snapshot-owned keys deactivated; manually edited keys kept.'),
  ('named_policy.reapplied',
   'Stale assignment re-materialized (best-effort per recipient).')
ON CONFLICT (action) DO NOTHING;
