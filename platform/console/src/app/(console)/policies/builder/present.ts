/**
 * Presentación legible para el builder (spec 041 sobre la spec 036).
 *
 * Los nombres/frases de las keys NO se hardcodean: salen del bloque
 * `presentation` de GET /api/policies/matrix (D-36-01) con el fallback
 * genérico de spec 036 (D-36-05). Los ítems dinámicos (models/connectors/
 * connections) usan los mismos endpoints que el panel legible (D-36-05).
 */
import type {
  DirectoryList,
  DynamicItem,
  ModelList,
  PolicyMatrix,
} from "@/lib/types";
import {
  buildStatement,
  humanizeKey,
  itemLabel,
  resolvePresentation,
  t,
  type ResolvedPresentation,
} from "../readable/presentation";

/** GET /api/connectors — catálogo global de conectores (REQ-CON-001). */
export interface ConnectorCatalogList {
  connectors: { slug: string; name: string; category: string }[];
}

/** GET /api/vault/connections — conexiones personalizadas (spec 024). */
export interface VaultConnectionList {
  connections: { id: string; name: string; kind: string }[];
}

/** Las tres fuentes de ítems dinámicos, ya resueltas por useApi. */
export interface DynamicSources {
  models: ModelList | null;
  connectors: ConnectorCatalogList | null;
  connections: VaultConnectionList | null;
}

/** Entrada del catálogo tipado para una key (o null si hay drift). */
export function catalogEntryFor(matrix: PolicyMatrix, key: string) {
  return matrix.catalog.find((e) => e.key === key) ?? null;
}

/** Presentación legible de una key (contrato 036 o fallback D-36-05). */
export function presentationFor(
  matrix: PolicyMatrix,
  key: string,
): ResolvedPresentation | null {
  const entry = catalogEntryFor(matrix, key);
  if (!entry) return null;
  return resolvePresentation(entry, matrix.presentation);
}

/** Nombre legible de una key (para el diff del preview y el resumen). */
export function keyDisplayName(matrix: PolicyMatrix, key: string): string {
  const resolved = presentationFor(matrix, key);
  return resolved ? t(resolved.pres.name) : humanizeKey(key);
}

/** Ítems dinámicos según el item_source del control (D-36-05). */
export function dynamicItemsFor(
  matrix: PolicyMatrix,
  key: string,
  sources: DynamicSources,
): DynamicItem[] | undefined {
  const control = presentationFor(matrix, key)?.pres.control;
  const source = control?.item_source;
  if (source === "models") {
    return sources.models?.models.map((m) => ({
      value: m.model_name,
      label: m.display_name,
    }));
  }
  if (source === "connector_catalog") {
    return sources.connectors?.connectors.map((c) => ({
      value: c.slug,
      label: c.name,
    }));
  }
  if (source === "connector_catalog_messaging") {
    return sources.connectors?.connectors
      .filter((c) => c.category === "messaging")
      .map((c) => ({ value: c.slug, label: c.name }));
  }
  if (source === "connections") {
    return sources.connections?.connections.map((c) => ({
      value: c.id,
      label: c.name,
    }));
  }
  return undefined;
}

/** Resumen legible de un valor de key (frase de spec 036, D-36-04). */
export function readableValue(
  matrix: PolicyMatrix,
  key: string,
  value: unknown,
  sources: DynamicSources,
): string {
  const resolved = presentationFor(matrix, key);
  if (!resolved) {
    return value === null || value === undefined
      ? "—"
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
  }
  const dynamic = dynamicItemsFor(matrix, key, sources);
  const labelFor = (v: string) =>
    itemLabel(v, resolved.pres.control.known_items, dynamic);
  return buildStatement(resolved.pres, value, labelFor);
}

/** Etiqueta de un usuario del directorio ("Nombre — email"). */
export function userLabel(u: {
  display_name: string | null;
  email: string;
}): string {
  return u.display_name && u.display_name !== u.email
    ? `${u.display_name} — ${u.email}`
    : u.email;
}

/** Etiqueta del destinatario de una asignación (org / depto / usuario). */
export function recipientLabel(
  assignment: { level: string; department_id: string | null; user_id: string | null },
  matrix: PolicyMatrix | null,
  directory: DirectoryList | null,
): string {
  if (assignment.level === "org") return "The organization";
  if (assignment.level === "dept") {
    const dept = matrix?.departments.find(
      (d) => d.department_id === assignment.department_id,
    );
    return dept ? `Department “${dept.name}”` : "Department";
  }
  const user = directory?.users.find((u) => u.user_id === assignment.user_id);
  return user ? `User “${userLabel(user)}”` : "User";
}
