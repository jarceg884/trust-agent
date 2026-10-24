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
  roles: RoleAssignment[];
  permissions: string[];
  mfa_verified: boolean;
  via: "session" | "jwt";
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

// --- Modelos (models/router.py + models/access.py) ---------------------------

export interface ModelCatalogEntry {
  model_name: string;
  display_name: string;
  provider: string;
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

/** GET /api/models/access (access.access_overview). */
export interface AccessOverview {
  models: AccessModelRow[];
}

export interface AccessModelRow {
  model_name: string;
  display_name: string;
  provider: string;
  input_price_cents_per_m: number;
  output_price_cents_per_m: number;
  catalog_status: string;
  approvals: ModelApproval[];
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

// --- Dispositivos (devices/service.py _DEVICE_SELECT) ------------------------

export type DeviceStatus = "pending" | "active" | "rejected" | "revoked";

export interface DeviceRow {
  id: string;
  fingerprint: string;
  device_name: string | null;
  platform: string | null;
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
  roles: RoleAssignment[];
}

export interface DirectoryList {
  users: DirectoryUser[];
}

/** GET /api/branding (contrato spec 001 AC-ONB-007 / REQ-BRD-005). */
export interface TenantBranding {
  product_name: string;
  tagline?: string | null;
  accent_color?: string | null;
  logo_url?: string | null;
  source?: "tenant" | "provider";
}
