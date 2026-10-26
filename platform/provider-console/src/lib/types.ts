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
  status: "active" | "suspended";
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
  status: "active" | "suspended";
  oidc_realm: string;
  oidc_issuer_url: string;
  branding: Record<string, unknown> | null;
  created_at: string;
}

/** Respuesta de suspend/reactivate (REQ-PC-002/003). */
export interface TenantStatusResponse {
  tenant: TenantState;
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
