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
  /** Spec 036 (D-36-04): efectiva del depto computada con el combinator
   *  del backend — combine(org, dept, ∅). La UI jamás re-deriva. */
  effective: Record<string, unknown>;
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

/** GET /api/policies/matrix (spec 033 REQ-033-001 + spec 036 D-36-01/04). */
export interface PolicyMatrix {
  catalog: PolicyCatalogEntry[];
  org: PolicyEntry[];
  departments: PolicyMatrixDepartment[];
  users: PolicyMatrixUser[];
  scope: "org" | "dept";
  /** Spec 036: catálogo legible es+en por key (contrato canónico, tal cual). */
  presentation: Record<string, PolicyPresentation>;
  /** Spec 036: las 9 categorías legibles. */
  categories: PolicyCategory[];
  /** Spec 036 (D-36-04): efectiva de la org — combine(org, ∅, ∅). */
  org_effective: Record<string, unknown>;
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

// --- Policies: capa de presentación legible (spec 036, D-36-01) ----------------

/** Texto bilingüe del contrato legible (es+en completos, D-36-07). */
export interface LocalizedText {
  es: string;
  en: string;
}

export interface PolicyOption {
  value: string;
  label: LocalizedText;
}

/** Ítem conocido de un multiselect (etiqueta legible para el slug). */
export interface PolicyKnownItem {
  value: string;
  label: LocalizedText;
}

/**
 * Control legible del contrato (spec 036). Kinds:
 * - toggle-allow / toggle-require: boolean con on_label/off_label en palabras;
 * - select: enum con options en palabras;
 * - number: entero con unit legible y min;
 * - multiselect-allow / multiselect-deny: lista como sub-toggles por ítem
 *   (D-36-08) — known_items estáticos + item_source dinámico + custom items;
 * - domains: web_domain_policy (modo + lista de dominios);
 * - messaging: messaging_permissions (por plataforma: allowed + mode);
 * - locked: invariable del proveedor, sin control (D-36-09);
 * - raw: SOLO fallback UI para keys sin presentación (D-36-05), JSON crudo.
 */
export interface PolicyControl {
  kind:
    | "toggle-allow"
    | "toggle-require"
    | "select"
    | "number"
    | "multiselect-allow"
    | "multiselect-deny"
    | "domains"
    | "messaging"
    | "locked"
    | "raw";
  on_label?: LocalizedText;
  off_label?: LocalizedText;
  options?: PolicyOption[];
  unit?: LocalizedText;
  min?: number;
  item_source?:
    | "free"
    | "models"
    | "connector_catalog"
    | "connector_catalog_messaging"
    | "connections";
  allow_custom_items?: boolean;
  known_items?: PolicyKnownItem[];
  wildcard?: { value: string; label: LocalizedText };
  items_hint?: LocalizedText;
  mode_options?: PolicyOption[];
  domains_label?: LocalizedText;
  domains_hint?: LocalizedText;
}

/** Bloque `presentation` por key (contrato canónico, spec 036 §7). */
export interface PolicyPresentation {
  category: string;
  icon: string;
  org_only?: boolean;
  invariable?: boolean;
  name: LocalizedText;
  description: LocalizedText;
  control: PolicyControl;
  /** Frase para booleans (y pisos activos de invariables). */
  statement?: { on?: LocalizedText; off?: LocalizedText };
  /** Frase con {value} para el resto de tipos. */
  statement_template?: LocalizedText;
  /** Etiqueta legible cuando la lista efectiva está vacía. */
  empty_label?: LocalizedText;
}

/** Categoría legible (9, spec 036 §7). */
export interface PolicyCategory {
  id: string;
  icon: string;
  name: LocalizedText;
}

/** Ítem dinámico resuelto desde un endpoint real (models/connectors/…). */
export interface DynamicItem {
  value: string;
  label: string;
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
  cache_read_price_cents_per_m: number | null;
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
  byok_allowed?: boolean; // spec 043: false ⇒ hide all BYOK UI
}

/** POST /api/vault/connections/{id}/test-connection — test real (spec 043). */
export interface TestConnectionResult {
  ok: boolean;
  models?: string[];
  count?: number;
  latency_ms: number;
  error?: string;
}

export interface AccessModelRow {
  model_name: string;
  display_name: string;
  provider: string;
  context_tokens: number | null; // MaaS Model List; null = not documented
  max_output_tokens: number | null;
  input_price_cents_per_m: number;
  output_price_cents_per_m: number;
  cache_read_price_cents_per_m: number | null;
  catalog_status: string; // available | deprecated | stale | removed (spec 032)
  approvals: ModelApproval[];
  // Spec 032 (D-032-06): every connected API contributes its models —
  // rows are grouped by source in the UI (central MaaS / each BYOK
  // connection).
  source: "central" | "byok" | "provider";
  connection_id: string | null; // byok only
  connection_name: string | null; // byok only: vault connection name
  connection_status: string | null; // active | revoked
  // Spec 036 (D-036-06): provider-operated model sources enabled for this
  // tenant by the platform provider — shown as platform-included options
  // grouped per provider (NOT the client's own BYOK key).
  provider_id: string | null; // provider source only
  provider_source_name: string | null; // ops.model_providers.name
  provider_display_name: string | null;
  provider_base_url: string | null;
  provider_status: string | null; // active | disabled
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

export interface CatalogRefreshProvider {
  provider_id?: string;
  discovered?: number;
  stale?: number;
  approved?: number;
  models?: string[];
  error?: string;
}

export interface CatalogRefreshSummary {
  central?: { scope: "central"; stale: number; restored: number; gateway_models: number };
  connections?: Record<string, CatalogRefreshConnection>;
  // Spec 036 (D-036-05): the provider leg of the global refresh — keyed by
  // provider name.
  providers?: Record<string, CatalogRefreshProvider>;
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

/** GET /api/agents/{id}/grants (agents.list_grants, spec 012 REQ-ORG-120). */
export interface AgentGrantRow {
  id: string;
  grantee_user_id: string;
  permission: "invoke" | "view";
  granted_at: string | null;
  granted_by: string | null;
}

/** GET /api/agents/{id}/grants → { grants: AgentGrantRow[] } */
export interface AgentGrantsList {
  grants: AgentGrantRow[];
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
  kind: "database" | "api_key" | "custom" | "llm";
  metadata: Record<string, unknown>;
  status: string; // "active" | "revoked"
  created_by: string;
  last_rotated_at: string | null;
  last_used_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  /** Spec 037 (REQ-037-010): ¿la policy efectiva del caller permite usarla? */
  allowed_for_caller: boolean;
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
  /** Spec 037 (REQ-037-009): ¿la policy efectiva del caller lo permite? */
  allowed_for_caller: boolean;
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

// --- Spec 045: marketplace de perfiles de Hermes -------------------------------------------
//   GET  /api/marketplace/profiles[?include_archived=true]   (org_admin / dept_admin)
//   GET  /api/marketplace/profiles/{id}                       (ficha + versiones)
//   POST /api/marketplace/profiles/import[?slug=]             (org_admin, cuerpo = .tar.gz)
//   PUT  /api/marketplace/profiles/{id}                       (org_admin, nombre/descripción)
//   POST /api/marketplace/profiles/{id}/archive | /restore    (org_admin)
//   GET  /api/marketplace/profiles/{id}/package[?version=]    (org_admin, paquete saneado)

export interface MarketplaceSkill {
  path: string;
  name: string;
  description: string;
  has_scripts: boolean;
}

/** Lo que el saneamiento dejó y quitó (manifest del paquete, sin secretos). */
export interface MarketplaceSummary {
  format: "hermes-export" | "hermes-distribution";
  has_soul: boolean;
  soul_excerpt: string;
  skills: MarketplaceSkill[];
  has_scripts: boolean;
  mcp_servers: string[];
  cron_jobs: { name: string; schedule: string }[];
  env_requires: string[];
  redactions: number;
  stripped: Record<string, number>;
  file_count: number;
  total_size: number;
  distribution: { name: string; version: string; description: string; author: string; license: string } | null;
  config_removed_keys: string[];
  profile_meta: { display_name?: string; description?: string } | null;
}

export interface MarketplacePolicyRef {
  id: string;
  name: string;
  /** true si la política lo marca como perfil por defecto. */
  default: boolean;
}

export interface MarketplaceProfile {
  id: string;
  slug: string;
  name: string;
  description: string;
  source: "factory" | "imported";
  factory_key: string | null;
  status: "active" | "archived";
  created_at: string | null;
  updated_at: string | null;
  latest: {
    version: number;
    sha256: string;
    size: number;
    imported_at: string | null;
    imported_by: string | null;
    summary: MarketplaceSummary;
    manifest?: { files?: { path: string; size: number }[] } & Record<string, unknown>;
  };
  policies: MarketplacePolicyRef[];
}

export interface MarketplaceVersion {
  version: number;
  sha256: string;
  size: number;
  imported_at: string | null;
  imported_by: string | null;
}

export interface MarketplaceProfileDetail extends MarketplaceProfile {
  versions: MarketplaceVersion[];
}

export interface MarketplaceList {
  profiles: MarketplaceProfile[];
}

export interface MarketplaceImportResult {
  profile: MarketplaceProfileDetail;
  created: boolean;
  version_added: boolean;
  unchanged: boolean;
}

/** Perfil asignado en una política (spec 045 D-045-04). */
export interface PolicyProfileEntry {
  profile_id: string;
  model: string;
}
