/**
 * Tipos derivados de los contratos del control plane (spec 014 §4, spec 004 §4).
 * Sin generación desde OpenAPI: la superficie provider es pequeña y estable;
 * cualquier divergencia la atrapan los estados de error reales de la UI.
 */

/** Item de `GET /api/admin/tenants` (REQ-PC-001). */
export interface TenantListItem {
  id: string;
  slug: string;
  name: string;
  status: "active" | "suspended" | "pending_activation";
  oidc_realm: string;
  oidc_issuer_url: string;
  created_at: string;
  counts: {
    users: number;
    departments: number;
  };
  org_budget_cents: number;
  spent_cents_period: number;
}

export interface TenantListResponse {
  tenants: TenantListItem[];
}

/** Estado canónico del tenant (spec 004 tenant_state). */
export interface TenantState {
  id: string;
  slug: string;
  name: string;
  status: "active" | "suspended" | "pending_activation";
  oidc_realm: string;
  oidc_issuer_url: string;
  branding: Record<string, unknown> | null;
  created_at: string;
}

/** Respuesta de suspend/reactivate (REQ-PC-002/003) y de activate
 * (spec 026 REQ-SS-003: pending_activation → active + budget inicial). */
export interface TenantStatusResponse {
  tenant: TenantState;
  notified_org_admins?: number;
}

/** Credenciales one-time del admin inicial (spec 004 D-ONB-05). */
export interface OneTimeActivation {
  issuer_url: string;
  client_id: string;
  username: string;
  one_time_password: string;
  instructions: string;
}

export interface HealthcheckCheck {
  name: string;
  status: string;
  detail: string | null;
}

export interface HealthcheckResult {
  status: string;
  checks: HealthcheckCheck[];
}

/** Respuesta 201/200 de `POST /api/admin/tenants` (spec 004 §4).
 * OJO: la re-ejecución idempotente (200, created=false) NO incluye `admin`
 * ni `provisioned` (D-ONB-04) — ambos son opcionales. */
export interface ProvisionResult {
  created: boolean;
  tenant: TenantState;
  admin?: {
    email: string;
    display_name: string | null;
    department_id: string | null;
  };
  one_time_activation?: OneTimeActivation | null;
  provisioned?: {
    departments: { id: string; name: string; profile_template: string }[];
    models: string[];
    budget_cents: number;
    policies: string[];
  };
  healthcheck: HealthcheckResult;
}

/** Body del formulario de provisioning (spec 004 §4). */
export interface ProvisionBody {
  organization_name: string;
  admin_email: string;
  initial_budget_usd: number;
  model_plan: string | string[];
  branding?: Record<string, unknown>;
  departments?: string[];
  slug?: string;
  admin_display_name?: string;
}

/* ============================================================================
 * Spec 036 — Model Providers (repro Miller t_64e645c1): proveedores de
 * modelos operados por el provider + habilitación por tenant.
 * ========================================================================== */

/** GET /api/admin/model-providers — item (NUNCA material de key). */
export interface ModelProviderItem {
  id: string;
  name: string;
  display_name: string | null;
  base_url: string;
  status: "active" | "disabled";
  input_price_cents_per_m: number | null;
  output_price_cents_per_m: number | null;
  enabled_tenants: number;
  available_models: number;
}

export interface ModelProviderListResponse {
  providers: ModelProviderItem[];
}

/** GET /api/admin/model-providers/{id} — detalle con modelos y tenants. */
export interface ModelProviderDetail extends ModelProviderItem {
  models: {
    model_name: string;
    display_name: string;
    status: string;
    tenant_id: string | null;
    last_seen_at: string | null;
  }[];
  tenants: {
    tenant_id: string;
    slug: string | null;
    enabled: boolean;
    enabled_at: string;
    disabled_at: string | null;
  }[];
}

/** POST /api/admin/model-providers/{id}/refresh — resultado del fetch real. */
export interface ModelProviderRefreshResult {
  provider_id: string;
  name: string;
  upstream_models: number;
  models: string[];
  tenants: {
    discovered: number;
    stale: number;
    approved: number;
    models: string[];
  }[];
}

/** POST /api/admin/model-providers/{id}/tenants — habilitación para un
 * cliente: discovery inmediato + aprobaciones org (D-036-03). */
export interface ModelProviderEnableResult {
  tenant: { id: string; slug: string; name: string };
  provider_id: string;
  provider: string;
  discovered: number;
  stale: number;
  approved: number;
  models: string[];
}

/** POST /api/admin/model-providers/{id}/test-connection — test real (spec 043). */
export interface TestConnectionResult {
  ok: boolean;
  models?: string[];
  count?: number;
  latency_ms: number;
  error?: string;
}

/** GET /api/admin/model-providers/byok-policy — gating BYOK por tenant (spec 043). */
export interface ByokPolicyEntry {
  tenant_id: string;
  slug: string;
  name: string;
  status: string;
  byok_allowed: boolean;
  set_at: string | null;
  set_by: string | null;
}
