/**
 * Tipos derivados del control plane REAL (spec 009 D-AC-03).
 *
 * Cada interfaz cita el endpoint y el archivo fuente del que deriva
 * (platform/app/…). Las respuestas del control plane son dicts sin
 * response_model, por lo que la derivación es manual y se verifica contra el
 * código en el converge. Snapshot de referencia: openapi.json (57 rutas,
 * volcado con scripts/dump_openapi.py). Montos en centavos USD (D-AC-08).
 */

// --- /api/me (auth/router.py whoami) ----------------------------------------

export interface RoleAssignment {
  role_key: string;
  department_id: string | null;
}

export interface WhoAmI {
  user_id: string;
  email: string;
  display_name: string | null;
  department_id: string | null;
  tenant_id: string;
  tenant_slug: string;
  /** Additive (t_b14cbefb): human-facing org name (tenants.name) —
   *  the topbar shows it with the slug degraded to tooltip/secondary. */
  tenant_name: string;
  roles: RoleAssignment[];
  permissions: string[];
  mfa_verified: boolean;
  via: "session" | "jwt";
}

// --- /api/auth/org-config (spec 015 D-AUTH-08) --------------------------------

/** GET /api/auth/org-config?org=<slug> — público, pre-auth. */
export interface OrgConfig {
  org: string;
  require_mfa: boolean;
  allow_self_registration: boolean;
  google_login_enabled: boolean;
}

/** POST /api/auth/register — público (spec 015 D-AUTH-04). */
export interface RegisterResult {
  user_id: string;
  org: string;
}

// --- Presupuestos (governance/budgets.py) ------------------------------------

export interface BudgetLevel {
  level: "user" | "dept" | "org";
  budget_cents: number | null;
  spent_cents: number;
  remaining_cents: number | null;
  period: string;
  cutoff: boolean;
}

/** GET /api/budgets/me y /api/me/budget (budgets.status_for_user). */
export interface BudgetStatus {
  user: BudgetLevel;
  department: BudgetLevel;
  organization: BudgetLevel;
  cutoff: boolean;
  cutoff_level: string | null;
}

/** GET /api/budgets/overview (budgets.overview). */
export interface BudgetOverview {
  organization: BudgetLevel;
  departments: DepartmentBudget[];
  dept_budgets_total_cents: number;
  unassigned_cents: number | null;
}

export interface DepartmentBudget {
  department_id: string;
  name: string;
  /** null until PUT /api/budgets/dept/{id} assigns one (spec 013 D-013-07). */
  budget_cents: number | null;
  spent_cents: number;
  members: number;
  /** Per-user budgets nested under the department (t_6b40c758, QA F3):
   *  the read path for PUT /api/budgets/user/{id} editing in the console. */
  users: DepartmentUserBudget[];
  /** Σ active user budgets of the department (REQ-BUD-003). */
  user_budgets_total_cents: number;
}

export interface DepartmentUserBudget {
  user_id: string;
  email: string;
  display_name: string | null;
  /** null until PUT /api/budgets/user/{id} assigns one. */
  budget_cents: number | null;
  spent_cents: number;
}

/** GET /api/budgets/history (budgets.history, spec 013 D-013-03):
 *  CLOSED windows only — budget vs spent per level + per-model breakdown. */
export interface BudgetHistory {
  periods: BudgetHistoryPeriod[];
}

export interface BudgetHistoryPeriod {
  period_start: string;
  period_end: string;
  organization: { budget_cents: number | null; spent_cents: number } | null;
  departments: {
    department_id: string;
    name: string | null;
    budget_cents: number | null;
    spent_cents: number;
  }[];
  users: {
    user_id: string;
    display_name: string | null;
    department_id: string | null;
    budget_cents: number | null;
    spent_cents: number;
  }[];
  by_model: { model_name: string; spent_cents: number }[];
}

/** PUT /api/budgets/{org|dept/{id}|user/{id}} (budgets.set_*_budget). */
export interface BudgetUpsertResult {
  level: "org" | "dept" | "user";
  department_id?: string;
  amount_cents: number;
  period: string;
  soft_threshold_pct?: number;
  budget_id?: string;
}

// --- Políticas (governance/policies.py list_level / router PolicyBody) -------

export interface PolicyEntry {
  key: string;
  value: unknown;
  version: number;
  updated_at: string | null;
}

/** GET /api/policies?level=… → { policies: PolicyEntry[] } */
export interface PolicyList {
  policies: PolicyEntry[];
}

/** PUT /api/policies/* (governance/router.py PolicyBody). */
export interface PolicyBody {
  key: string;
  value: unknown;
}

// --- Policies: matriz unificada + diff efectivo (spec 033) --------------------

/** Entrada del catálogo tipado (catalog.policy_keys) con los enums del
 *  contrato (STRING_RANKS / WEB_DOMAIN_MODES) para renderizar selects. */
export interface PolicyCatalogEntry {
  key: string;
  value_type: "boolean" | "integer" | "list" | "string" | "map";
  invariable: boolean;
  /** Spec 015 D-AUTH-07/08: gates org-wide behavior; the enforcement gate
   *  reads only the org level — no dept/user overrides (422 backend-side). */
  org_only: boolean;
  default_value: unknown;
  choices: string[] | null;
}

export interface PolicyMatrixDepartment {
  department_id: string;
  name: string;
  policies: PolicyEntry[];
}

/** Solo usuarios CON overrides activos a nivel usuario (el picker de la
 *  página usa el directorio completo GET /api/users). */
export interface PolicyMatrixUser {
  user_id: string;
  email: string;
  display_name: string | null;
  department_id: string | null;
  department_name: string | null;
  policies: PolicyEntry[];
}

/** GET /api/policies/matrix (spec 033 REQ-033-001). */
export interface PolicyMatrix {
  catalog: PolicyCatalogEntry[];
  org: PolicyEntry[];
  departments: PolicyMatrixDepartment[];
  users: PolicyMatrixUser[];
  scope: "org" | "dept";
}

/** GET /api/policies/effective?user_id=… — provenance por key computada
 *  con el combinator deny-overrides del backend (REQ-033-002). */
export interface PolicyDiffKey {
  key: string;
  value_type: PolicyCatalogEntry["value_type"];
  invariable: boolean;
  org_only: boolean;
  default_value: unknown;
  org_value: unknown;
  dept_value: unknown;
  user_value: unknown;
  org_effective: unknown;
  dept_effective: unknown;
  effective: unknown;
  source: "default" | "org" | "dept" | "user";
  tightened_by_dept: boolean;
  tightened_by_user: boolean;
  denied: boolean;
}

export interface PolicyEffectiveDiff {
  user: {
    user_id: string;
    email: string;
    display_name: string | null;
    department_id: string | null;
    department_name: string | null;
    status: string;
  };
  keys: PolicyDiffKey[];
}

// --- Modelos (models/router.py + models/access.py) ---------------------------

export interface ModelCatalogEntry {
  model_name: string;
  display_name: string;
  provider: string;
  context_tokens: number | null; // MaaS Model List; null = not documented
  max_output_tokens: number | null;
  input_price_cents_per_m: number;
  output_price_cents_per_m: number;
  status: string;
}

/** GET /api/models → { models: ModelCatalogEntry[] } */
export interface ModelList {
  models: ModelCatalogEntry[];
}

export interface ModelApproval {
  level: "org" | "dept" | "user";
  department_id: string | null;
  user_id: string | null;
  mode: string; // "central" | "byok"
}

/** Governance policy section of GET /api/models/access (spec 018). */
export interface ModelAccessPolicy {
  allowed_models: string[] | null; // null = no additional restriction
  approved_models_only: boolean; // invariable floor (catalog.policy_keys)
}

/** GET /api/models/access (access.access_overview). */
export interface AccessOverview {
  models: AccessModelRow[];
  policy: ModelAccessPolicy;
}

export interface AccessModelRow {
  model_name: string;
  display_name: string;
  provider: string;
  context_tokens: number | null; // MaaS Model List; null = not documented
  max_output_tokens: number | null;
  input_price_cents_per_m: number;
  output_price_cents_per_m: number;
  catalog_status: string; // available | deprecated | stale | removed (spec 032)
  approvals: ModelApproval[];
  // Spec 032 (D-032-06): every connected API contributes its models —
  // rows are grouped by source in the UI (central MaaS / each BYOK
  // connection).
  source: "central" | "byok";
  connection_id: string | null; // byok only
  connection_name: string | null; // byok only: vault connection name
  connection_status: string | null; // active | revoked
  last_seen_at: string | null; // last refresh that saw the model upstream
}

/** POST /api/models/catalog/refresh (spec 032 D-032-03). */
export interface CatalogRefreshConnection {
  scope: "connection";
  connection_id: string;
  discovered?: number;
  stale?: number;
  models?: string[];
  error?: string; // unreachable upstream never blocks the other connections
}

export interface CatalogRefreshSummary {
  central?: { scope: "central"; stale: number; restored: number; gateway_models: number };
  connections?: Record<string, CatalogRefreshConnection>;
  scope?: string;
  connection_id?: string;
  discovered?: number;
  stale?: number;
  models?: string[];
}

/** PUT /api/models/access y POST /api/models/access/revoke (AccessBody). */
export interface AccessBody {
  model_name: string;
  level: "org" | "dept" | "user";
  department_id?: string | null;
  user_id?: string | null;
  mode?: string;
}

// --- Agentes compartidos (governance/agents.py) ------------------------------

export interface AgentRow {
  id: string;
  name: string;
  description: string | null;
  scope: string; // "dept" | "org"
  owner_user_id: string;
  department_id: string | null;
  access: string | null; // "own" | "invoke" | "view" (list_for_user)
}

/** GET /api/agents → { agents: AgentRow[] } */
export interface AgentList {
  agents: AgentRow[];
}

/** POST /api/agents/{id}/grants (agents.grant). */
export interface GrantResult {
  id: string;
  agent_id: string;
  grantee_user_id: string;
  permission: "invoke" | "view";
}

// --- Perfiles default del producto (governance/agent_profiles.py, spec 020) ---

export interface AgentProfileRow {
  key: string;
  name: string;
  description: string;
  scope: string;
  skills: string[];
  suggested_connectors: string[];
  suggested_models: string[];
  adopted_agent_id: string | null;
  adopted_by: string | null;
}

/** Effective governance context of the caller (deny-overrides, spec 020). */
export interface AgentProfilesResponse {
  profiles: AgentProfileRow[];
  effective_policy: {
    allowed_models: string[];
    allowed_connectors: string[];
    budget_cutoff: boolean;
  };
}

/** POST /api/agent-profiles/{key}/adopt (agent_profiles.adopt). */
export interface AdoptResult {
  agent_id: string;
  profile_key: string;
  name: string;
  scope: string;
  owner_user_id: string;
  department_id: string | null;
}

/** POST /api/agents/{id}/invoke (agents.invoke_gate, spec 020 REQ-020-003). */
export interface InvokeResult {
  agent_id: string;
  billed_to_user_id: string;
  access: string;
  model_name?: string;
}

// --- Dispositivos (devices/service.py _DEVICE_SELECT) ------------------------

export type DeviceStatus = "pending" | "active" | "rejected" | "revoked";

/** Spec 034 (D-034-04): 'cli' = Hermes CLI via curl|bash, 'desktop' = bundle. */
export type DeviceType = "desktop" | "cli";

export interface DeviceRow {
  id: string;
  fingerprint: string;
  device_name: string | null;
  platform: string | null;
  device_type: DeviceType;
  status: DeviceStatus;
  enrolled_at: string | null;
  approved_at: string | null;
  approved_by: string | null;
  last_seen_at: string | null;
  user_id: string;
  email: string;
  display_name: string | null;
  department_id: string | null;
  department_name: string | null;
  active_sessions: number;
}

/** GET /api/devices → { devices: DeviceRow[] } */
export interface DeviceList {
  devices: DeviceRow[];
}

// --- Live presence (spec 027: GET /api/devices/presence) ---------------------

export interface PresenceDevice {
  id: string;
  user_id: string;
  email: string;
  display_name: string | null;
  department_id: string | null;
  department_name: string | null;
  fingerprint: string;
  device_name: string | null;
  platform: string | null;
  device_type: DeviceType;
  status: DeviceStatus;
  online: boolean;
  last_heartbeat_at: string | null;
  applied_policy_version: number | null;
  current_policy_version: number;
  policy_up_to_date: boolean;
  agent_uptime_seconds: number | null;
}

/** GET /api/devices/presence → live connections (spec 027 REQ-027-04) */
export interface PresenceResponse {
  online_count: number;
  active_count: number;
  online_window_seconds: number;
  heartbeat_interval_seconds: number;
  devices: PresenceDevice[];
}

// --- Vault + conectores (vault/service.py _meta, connectors/service.py) ------

export interface CredentialMeta {
  id: string;
  user_id: string;
  name: string;
  credential_type: string;
  connector_slug: string | null;
  scope: string | null;
  status: string; // "active" | "revoked"
  last_rotated_at: string | null;
  last_used_at: string | null;
  created_at: string | null;
}

/** GET /api/vault/credentials → { credentials: CredentialMeta[] } */
export interface CredentialList {
  credentials: CredentialMeta[];
}

/** POST /api/vault/credentials y …/rotate: el valor se muestra UNA vez. */
export interface CredentialSecretResult extends CredentialMeta {
  secret_value: string;
}

// --- Conexiones personalizadas (vault/connections.py, spec 024) ---------------

/** Metadata projection: NUNCA secreto ni ciphertext (REQ-024-002). */
export interface CustomConnectionMeta {
  id: string;
  name: string;
  kind: "database" | "api_key" | "custom";
  metadata: Record<string, unknown>;
  status: string; // "active" | "revoked"
  created_by: string;
  last_rotated_at: string | null;
  last_used_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** GET /api/vault/connections → { connections: CustomConnectionMeta[] } */
export interface CustomConnectionList {
  connections: CustomConnectionMeta[];
}

/** POST /api/vault/connections y PUT …/{id}: secreto UNA sola vez. */
export interface CustomConnectionSecretResult {
  connection_id: string;
  secret_value?: string; // present only when the secret was (re)encrypted
  metadata: CustomConnectionMeta;
}

export interface ConnectorCatalogEntry {
  slug: string;
  name: string;
  category: string;
  capabilities: string[] | null;
  required_scopes: string[] | null;
  status: string;
}

/** GET /api/connectors → { connectors: ConnectorCatalogEntry[] } */
export interface ConnectorList {
  connectors: ConnectorCatalogEntry[];
}

export interface ConnectorInstance {
  id: string;
  user_id: string;
  connector_slug: string;
  credential_id: string | null;
  mode: string | null;
  status: string;
  created_at: string | null;
}

/** GET /api/connectors/instances → { instances: ConnectorInstance[] } */
export interface InstanceList {
  instances: ConnectorInstance[];
}

// --- Audit (audit/service.py query_events, spec 008 REQ-AU-001) --------------

export interface AuditEvent {
  seq: number;
  ts: string;
  actor_role: string | null;
  actor_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  device_id: string | null;
  cost_cents: number | null;
  payload: Record<string, unknown> | null;
  hash_prev: string | null;
  hash: string | null;
}

export interface AuditQueryResult {
  items: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
}

/** POST /api/admin/audit/verify (spec 008 REQ-AU-011). */
export interface ChainVerifyResult {
  status: "ok" | "broken";
  seq?: number;
}

// --- Notificaciones (governance/notifications.py list_for_user) --------------

export interface GovernanceNotification {
  id: string;
  type: string;
  payload: Record<string, unknown> | null;
  read_at: string | null;
  created_at: string;
}

/** GET /api/me/notifications → { notifications: GovernanceNotification[] } */
export interface NotificationList {
  notifications: GovernanceNotification[];
}

// --- Contratos PENDIENTES (spec 009 §4.2, D-AC-06) ---------------------------
// Sin endpoint hoy. El cliente tipado compila contra el contrato de la spec
// 001; la UI degrada con el estado real del control plane (404). En cuanto la
// tarea dev los implemente, estas páginas se activan sin cambios de tipo.

/** GET /api/users (contrato spec 001 AC-ORG-002 — directorio). */
export interface DirectoryUser {
  user_id: string;
  email: string;
  display_name: string | null;
  department_id: string | null;
  status: "active" | "disabled";
  mfa_enrolled: boolean;
  roles: RoleAssignment[];
}

export interface DirectoryList {
  users: DirectoryUser[];
}

/** GET /api/departments (spec 017 REQ-RA-006) — minimal list for selectors. */
export interface DepartmentsList {
  departments: { department_id: string; name: string }[];
}

/** GET /api/branding (contrato spec 001 AC-ONB-007 / REQ-BRD-005 + spec 028:
 *  versionado draft/publish con assets). */
export interface TenantBranding {
  product_name: string;
  tagline?: string | null;
  accent_color?: string | null;
  logo_url?: string | null;
  logo_light_url?: string | null;
  logo_dark_url?: string | null;
  favicon_url?: string | null;
  source?: "tenant" | "provider";
  /** Versión publicada (0 = nunca publicada, default del proveedor). */
  version?: number;
}

/** Asset de branding referenciado por el draft (spec 028 D-028-05). */
export interface BrandingAsset {
  asset_id: string;
  kind: "logo_light" | "logo_dark" | "favicon";
  mime: string;
  size_bytes: number;
  sha256: string;
  /** Ruta API (se consume vía el proxy BFF: `/api/proxy` + url). */
  url: string;
}

/** GET /api/branding/draft — estado del editor (REQ-BRD-004). */
export interface BrandingDraft {
  product_name: string | null;
  tagline: string | null;
  accent_color: string | null;
  logo_url: string | null;
  logo_light: BrandingAsset | null;
  logo_dark: BrandingAsset | null;
  favicon: BrandingAsset | null;
  version: number;
  has_changes: boolean;
}

/** PUT /api/branding — edita el draft (D-028-04). */
export interface BrandingDraftResponse {
  draft: BrandingDraft;
  published: TenantBranding;
}

/** POST /api/branding/assets — upload PNG/SVG ≤ 2 MB. */
export interface BrandingUploadResponse {
  asset: BrandingAsset;
  draft: BrandingDraft;
}

/** POST /api/branding/publish | /api/branding/rollback. */
export interface BrandingPublishResponse {
  version: number;
  restored_from?: number;
  published: TenantBranding;
  draft: BrandingDraft;
}

/** GET /api/branding/versions — historial append-only. */
export interface BrandingVersions {
  current_version: number;
  versions: {
    version: number;
    published_at: string | null;
    published_by: string | null;
    summary: {
      product_name: string | null;
      tagline: string | null;
      accent_color: string | null;
      has_logo_light: boolean;
      has_logo_dark: boolean;
      has_favicon: boolean;
    };
  }[];
}

// --- Download link (contrato spec 023 — t_526030f3, PENDIENTE) ----------------
// CORRECCIÓN MILLER (t_360a752e): el link de descarga lo genera y envía el
// ADMIN DEL CLIENTE desde esta console (sección "Hermes Download"), no el
// proveedor. El endpoint público `GET /download/{tenant_slug}` y la gestión
// enable/disable son de la spec 023 (dev). Contrato acordado:
//   GET  /api/download-link  (sesión org_admin)
//        → { url: string, enabled: boolean, updated_at: string }
//   PUT  /api/download-link  { enabled: boolean }  (auditado:
//        download.link.enabled / download.link.disabled)
// Sin endpoint hoy: la UI muestra el estado real (404) con banner honesto
// y el link estable (determinista por tenant) sigue siendo copiable/enviable.

/** GET/PUT /api/download-link (contrato spec 023, pendiente). */
export interface DownloadLinkStatus {
  url: string;
  enabled: boolean;
  updated_at: string;
}

// --- Usage analytics (spec 035: GET /api/usage, /api/usage/requests) ---------

/** GET /api/usage — aggregated per-user usage over the real spend ledger. */
export interface UsageUserRow {
  user_id: string | null;
  email: string;
  display_name: string;
  department_id: string | null;
  department_name: string | null;
  requests: number;
  tokens_in: number;
  tokens_out: number;
  cost_base_cents: number;
  cost_billed_cents: number;
  central_requests: number;
  byok_requests: number;
  models: UsageModelBreakdown[];
}

export interface UsageModelBreakdown {
  model_name: string;
  mode: "central" | "byok";
  requests: number;
  cost_billed_cents: number;
}

export interface UsageSummary {
  scope: "org" | "dept";
  period: { kind: "today" | "7d" | "30d" | "custom"; from: string; to: string };
  filters: { user_id: string | null; model: string | null; mode: string | null };
  totals: {
    requests: number;
    tokens_in: number;
    tokens_out: number;
    cost_base_cents: number;
    cost_billed_cents: number;
  };
  users: UsageUserRow[];
}

/** GET /api/usage/requests — per-request drill-down (paginated). */
export interface UsageRequestRow {
  request_id: string;
  occurred_at: string;
  user_id: string | null;
  email: string;
  display_name: string;
  department_id: string | null;
  model_name: string;
  mode: "central" | "byok";
  tokens_in: number;
  tokens_out: number;
  cost_base_cents: number;
  cost_billed_cents: number;
}

export interface UsageRequestsPage {
  total: number;
  limit: number;
  offset: number;
  requests: UsageRequestRow[];
}
