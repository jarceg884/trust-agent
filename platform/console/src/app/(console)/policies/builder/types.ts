/**
 * Tipos de políticas nombradas (spec 041) — derivados del control plane REAL.
 *
 * Fuentes: platform/app/governance/named_policies.py (respuestas) y
 * named_policy_builder.py (bloque `builder` = contrato canónico tal cual,
 * D-41-02). La UI JAMÁS hardcodea grupos/capacidades/defaults/plantillas:
 * todo llega en el bloque `builder` de GET /api/named-policies.
 */

/** Texto bilingüe del contrato del builder (es+en completos). */
export interface BuilderLocalized {
  es: string;
  en: string;
}

/** Grupo del sidebar (6, D-41-03). Estructuralmente compatible con
 *  PolicyCategory (id + icon + name es/en) para el fallback de iconos. */
export interface BuilderGroup {
  id: string;
  icon: string;
  name: BuilderLocalized;
  description: BuilderLocalized;
}

/** Capacidad (ítem) del sidebar — 47, mapeadas 1:1 sobre las 44 keys no
 *  invariables del catálogo. Dos tipos (D-41-03):
 *  - "list-item": un ítem de una key lista (p. ej. allowed_tools:web_search);
 *  - "scalar": una key entera con su control legible de spec 036. */
export interface BuilderItem {
  id: string;
  group: string;
  type: "list-item" | "scalar";
  key: string;
  /** Solo list-item: el slug dentro del valor de la key. */
  item?: string;
  icon: string;
  name: BuilderLocalized;
  description: BuilderLocalized;
  /** Solo list-item: estado del hermano al activar el PRIMER ítem de la key. */
  default_on?: boolean;
  /** Solo scalar: valor preseleccionado al activar la capacidad. */
  default_value?: unknown;
  /** Bloquea el guardado hasta que el admin elija valor (p. ej. modelos). */
  requires_choice?: boolean;
  /** Key org_only: se omite al asignar a depto/usuario (D-41-04). */
  org_only?: boolean;
}

/** Plantilla pre-cargada (3, D-41-08) — fila named_policies sembrada. */
export interface BuilderTemplate {
  slug: string;
  name: BuilderLocalized;
  description: BuilderLocalized;
  definition: Record<string, unknown>;
}

/** Bloque `builder` de GET /api/named-policies (contrato canónico). */
export interface BuilderBlock {
  schema_version: string;
  groups: BuilderGroup[];
  items: BuilderItem[];
  templates: BuilderTemplate[];
}

/** Asignación de una política nombrada (con badge stale, D-41-06). */
export interface NamedPolicyAssignment {
  assignment_id: string;
  named_policy_id: string;
  level: "org" | "dept" | "user";
  department_id: string | null;
  user_id: string | null;
  /** Snapshot de ownership (D-41-05): lo efectivamente escrito. */
  applied_keys: Record<string, unknown>;
  applied_at: string;
  stale: boolean;
}

/** Fila de named_policies (GET /api/named-policies, POST, PUT). */
export interface NamedPolicy {
  id: string;
  name: string;
  description: string | null;
  definition: Record<string, unknown>;
  /** 'builtin:<slug>' para las plantillas sembradas (badge, D-41-08). */
  source_template: string | null;
  created_at: string;
  updated_at: string;
  assignments: NamedPolicyAssignment[];
  assignments_count: number;
  stale_assignments: boolean;
}

/** GET /api/named-policies — lista + contrato del builder (D-41-02). */
export interface NamedPolicyList {
  policies: NamedPolicy[];
  builder: BuilderBlock;
}

/** GET /api/named-policies/{id}/preview — dry-run SIN escribir (REQ-041-004). */
export interface NamedPolicyPreview {
  named_policy_id: string;
  name: string;
  level: "org" | "dept" | "user";
  department_id: string | null;
  user_id: string | null;
  keys: {
    key: string;
    new_value: unknown;
    current_value: unknown;
    overwrites: boolean;
  }[];
  /** Keys org_only omitidas porque el destino no es la organización. */
  skipped_org_only: string[];
  /** Keys cuya asignación relajaría un nivel superior → la asignación falla. */
  would_relax: { key: string; message: string }[];
  /** Keys desconocidas/invariables o con valor inválido. */
  invalid: { key: string; message: string }[];
}

/** POST /api/named-policies/{id}/assign (REQ-041-005). */
export interface AssignResult {
  assignment_id: string;
  named_policy_id: string;
  level: "org" | "dept" | "user";
  department_id: string | null;
  user_id: string | null;
  applied_keys: Record<string, unknown>;
  skipped: string[];
  applied_at: string;
}

/** DELETE /api/named-policies/assignments/{id} (D-41-05): solo las keys del
 *  snapshot siguen iguales se desactivan; las modificadas se conservan. */
export interface UnassignResult {
  deactivated: string[];
  kept_modified: { key: string; current_value: unknown; reason: string }[];
}

/** POST /api/named-policies/{id}/reapply (REQ-041-006): best-effort por
 *  destinatario, con el error real de cada falla. */
export interface ReapplyResult {
  results: {
    assignment_id: string;
    level: "org" | "dept" | "user";
    department_id: string | null;
    user_id: string | null;
    stale: boolean;
    status: "applied" | "failed";
    applied_keys?: Record<string, unknown>;
    skipped?: string[];
    errors?: string[];
  }[];
}

/** GET /api/named-policies/recipients (D-41-12): qué políticas nombradas
 *  aplican a cada destinatario del alcance — hecho de asignación, no un
 *  cálculo de combinación. */
export interface RecipientPolicies {
  assignment_id: string;
  named_policy_id: string;
  name: string;
  source_template: string | null;
  stale: boolean;
  applied_at: string;
}

export interface RecipientEntry {
  level: "org" | "dept" | "user";
  department_id: string | null;
  user_id: string | null;
  name?: string;
  policies: RecipientPolicies[];
}

export interface RecipientsResponse {
  recipients: RecipientEntry[];
}
