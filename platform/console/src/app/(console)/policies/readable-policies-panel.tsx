"use client";

/**
 * Panel legible de políticas (spec 036, REQ-036-002/003/005) — la vista por
 * defecto de /policies: tarjetas en lenguaje humano agrupadas por las 9
 * categorías del contrato, buscador es+en y SIN keys crudas.
 *
 * - Asignador "Apply to" (D-36-03): Organization / Department / User. La
 *   escritura usa SOLO los PUT/DELETE existentes del nivel elegido (cero
 *   mutaciones nuevas); keys org_only escriben siempre a org; las 3
 *   invariables viven al final en "Provider protections" con candado (D-36-09).
 * - Frases y estados provienen del valor EFECTIVO computado por el backend:
 *   org_effective / departments[].effective (matrix) y GET /api/policies/effective
 *   para usuarios (D-36-04). La UI JAMÁS re-deriva ni hardcodea (D-36-01).
 * - Ítems dinámicos (D-36-05): models → GET /api/models; connectors →
 *   GET /api/connectors; connections → GET /api/vault/connections. Un slug sin
 *   etiqueta conocida se muestra tal cual.
 */
import { useMemo, useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type {
  DirectoryList,
  DynamicItem,
  ModelList,
  PolicyCatalogEntry,
  PolicyEffectiveDiff,
  PolicyMatrix,
  WhoAmI,
} from "@/lib/types";
import type { UseApiState } from "@/hooks/use-api";
import { IconSearch } from "@/components/icons";
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Select,
  Skeleton,
  useToast,
} from "@/components/ui";
import { Lock } from "lucide-react";
import { PolicyCard, type ScopeLevel } from "./readable/policy-card";
import { PolicyIcon } from "./readable/policy-icons";
import {
  resolvePresentation,
  t,
  type PolicySource,
  type ResolvedPresentation,
} from "./readable/presentation";

/** GET /api/connectors — catálogo global de conectores (REQ-CON-001). */
interface ConnectorCatalogList {
  connectors: { slug: string; name: string; category: string }[];
}

/** GET /api/vault/connections — conexiones personalizadas (spec 024). */
interface VaultConnectionList {
  connections: { id: string; name: string; kind: string }[];
}

/** ¿El principal puede EDITAR en el nivel? (los PUT/DELETE igual gatean
 *  en el servidor; esto es UX, no seguridad — espejo de spec 033.) */
function canEditLevel(me: WhoAmI, level: ScopeLevel): boolean {
  if (level === "org") return me.permissions.includes("policies.org.manage");
  return (
    me.permissions.includes("policies.org.manage") ||
    me.permissions.includes("policies.dept.manage")
  );
}

function userLabel(u: { display_name: string | null; email: string }): string {
  return u.display_name && u.display_name !== u.email
    ? `${u.display_name} — ${u.email}`
    : u.email;
}

export function ReadablePoliciesPanel({
  me,
  matrix,
  directory,
}: {
  me: WhoAmI;
  matrix: UseApiState<PolicyMatrix>;
  directory: UseApiState<DirectoryList>;
}) {
  const { push } = useToast();
  const data = matrix.data!;

  const [level, setLevel] = useState<ScopeLevel>("org");
  const [deptId, setDeptId] = useState("");
  const [userId, setUserId] = useState("");
  const [query, setQuery] = useState("");
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  // Revisiones de escritura por key: cada PUT/DELETE que termina (éxito o 422)
  // bumpa el contador → el control con borrador local (number/domains/raw) se
  // remonta y vuelve al valor EFECTIVO vigente (REQ-036-004: "el control
  // vuelve al valor vigente"). El scopeKey cubre el cambio de destinatario.
  const [settled, setSettled] = useState<Record<string, number>>({});

  // Efectiva del destinatario (usuarios) — computada por el backend.
  const diff = useApi<PolicyEffectiveDiff>(
    level === "user" && userId
      ? `/api/policies/effective?user_id=${encodeURIComponent(userId)}`
      : null,
  );

  // Ítems dinámicos de los multiselects (endpoints existentes, D-36-05).
  const models = useApi<ModelList>("/api/models");
  const connectors = useApi<ConnectorCatalogList>("/api/connectors");
  const connections = useApi<VaultConnectionList>("/api/vault/connections");

  const selectedDept = data.departments.find((d) => d.department_id === deptId);
  const selectedUser = directory.data?.users.find((u) => u.user_id === userId);

  /** Resuelve la presentación (contrato o fallback D-36-05). */
  const resolvedFor = (entry: PolicyCatalogEntry): ResolvedPresentation =>
    resolvePresentation(entry, data.presentation);

  /** Valor EFECTIVO + origen de una key para el destinatario seleccionado. */
  function stateFor(entry: PolicyCatalogEntry): {
    effective: unknown;
    source: PolicySource;
  } | null {
    const orgKeys = new Set(data.org.map((p) => p.key));
    if (level === "org") {
      return {
        effective: data.org_effective[entry.key],
        source: orgKeys.has(entry.key) ? "org" : "default",
      };
    }
    if (level === "dept") {
      if (!selectedDept) return null;
      const deptKeys = new Set(selectedDept.policies.map((p) => p.key));
      return {
        effective: selectedDept.effective[entry.key],
        source: deptKeys.has(entry.key)
          ? "dept"
          : orgKeys.has(entry.key)
            ? "org"
            : "default",
      };
    }
    const row = diff.data?.keys.find((k) => k.key === entry.key);
    if (!row) return null;
    return { effective: row.effective, source: row.source };
  }

  /** Filas explícitas de un nivel (para el botón Reset). */
  function rowsAt(level: ScopeLevel) {
    if (level === "org") return data.org;
    if (level === "dept") return selectedDept?.policies;
    return data.users.find((u) => u.user_id === userId)?.policies;
  }

  /** Ítems dinámicos según el item_source del control. */
  function dynamicItemsFor(entry: PolicyCatalogEntry): DynamicItem[] | undefined {
    const source = resolvedFor(entry).pres.control.item_source;
    if (source === "models") {
      return models.data?.models.map((m) => ({
        value: m.model_name,
        label: m.display_name,
      }));
    }
    if (source === "connector_catalog") {
      return connectors.data?.connectors.map((c) => ({
        value: c.slug,
        label: c.name,
      }));
    }
    if (source === "connector_catalog_messaging") {
      return connectors.data?.connectors
        .filter((c) => c.category === "messaging")
        .map((c) => ({ value: c.slug, label: c.name }));
    }
    if (source === "connections") {
      return connections.data?.connections.map((c) => ({
        value: c.id,
        label: c.name,
      }));
    }
    return undefined;
  }

  const recipientLabel =
    level === "org"
      ? "the organization"
      : level === "dept"
        ? `department “${selectedDept?.name ?? ""}”`
        : `user “${selectedUser ? userLabel(selectedUser) : ""}”`;

  /** PUT del nivel elegido (endpoints existentes, D-36-03). */
  async function writePolicy(entry: PolicyCatalogEntry, value: unknown) {
    const writeLevel: ScopeLevel = entry.org_only ? "org" : level;
    setSavingKey(entry.key);
    setErrors((prev) => ({ ...prev, [entry.key]: "" }));
    try {
      const path =
        writeLevel === "org"
          ? "/api/policies/org"
          : writeLevel === "dept"
            ? `/api/policies/dept/${encodeURIComponent(deptId)}`
            : `/api/policies/user/${encodeURIComponent(userId)}`;
      await api(path, { method: "PUT", body: { key: entry.key, value } });
      setErrors((prev) => {
        const next = { ...prev };
        delete next[entry.key];
        return next;
      });
      push("success", `Policy saved — it now applies to ${recipientLabel}.`);
      matrix.refetch();
      if (level === "user" && userId) diff.refetch();
    } catch (cause) {
      // 422 de validación/no-relajación: el mensaje REAL del control plane,
      // inline en la tarjeta (REQ-036-004). Sin toast genérico.
      setErrors((prev) => ({
        ...prev,
        [entry.key]:
          cause instanceof ApiError ? cause.message : "Save failed. Try again.",
      }));
    } finally {
      setSavingKey(null);
      setSettled((prev) => ({
        ...prev,
        [entry.key]: (prev[entry.key] ?? 0) + 1,
      }));
    }
  }

  /** DELETE del override → hereda del nivel superior (endpoint existente). */
  async function resetPolicy(entry: PolicyCatalogEntry) {
    const writeLevel: ScopeLevel = entry.org_only ? "org" : level;
    setSavingKey(entry.key);
    try {
      const params = new URLSearchParams();
      if (writeLevel === "dept") params.set("department_id", deptId);
      if (writeLevel === "user") {
        params.set("user_id", userId);
        if (selectedUser?.department_id) {
          params.set("department_id", selectedUser.department_id);
        }
      }
      const qs = params.toString();
      await api(
        `/api/policies/${writeLevel}/${encodeURIComponent(entry.key)}${qs ? `?${qs}` : ""}`,
        { method: "DELETE" },
      );
      push("success", `Override removed — inherits from the level above.`);
      matrix.refetch();
      if (level === "user" && userId) diff.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Delete failed.");
    } finally {
      setSavingKey(null);
    }
  }

  /** Agrupación por las 9 categorías del contrato + sección de invariables. */
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matches = (entry: PolicyCatalogEntry) => {
      if (!q) return true;
      const { pres } = resolvedFor(entry);
      const categoryName = t(
        data.categories.find((c) => c.id === pres.category)?.name,
      );
      return [pres.name.es, pres.name.en, pres.description.es, pres.description.en, categoryName]
        .join(" ")
        .toLowerCase()
        .includes(q);
    };
    const visible = data.catalog.filter(matches);
    const regular = visible.filter((e) => !e.invariable);
    const protections = visible.filter((e) => e.invariable);
    const byCategory = data.categories
      .map((category) => ({
        category,
        entries: regular.filter((e) => resolvedFor(e).pres.category === category.id),
      }))
      .filter((g) => g.entries.length > 0);
    return { byCategory, protections };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, data, level, deptId, userId, diff.data]);

  const needsRecipient =
    (level === "dept" && !deptId) || (level === "user" && !userId);
  const userDiffPending = level === "user" && Boolean(userId) && diff.loading;
  const userDiffFailed = level === "user" && Boolean(userId) && diff.error;

  function renderCard(entry: PolicyCatalogEntry) {
    const resolved = resolvedFor(entry);
    const state = stateFor(entry);
    if (!state) return null;
    const writeLevel: ScopeLevel = entry.org_only ? "org" : level;
    const scopeRows = rowsAt(writeLevel);
    return (
      <PolicyCard
        key={entry.key}
        entry={entry}
        resolved={resolved}
        categories={data.categories}
        effective={state.effective}
        source={state.source}
        viewLevel={level}
        writeLevel={writeLevel}
        canEdit={canEditLevel(me, writeLevel)}
        hasOverride={Boolean(scopeRows?.some((p) => p.key === entry.key))}
        saving={savingKey === entry.key}
        error={errors[entry.key] || null}
        dynamicItems={dynamicItemsFor(entry)}
        scopeKey={`${level}:${deptId}:${userId}`}
        revision={settled[entry.key] ?? 0}
        onWrite={(value) => writePolicy(entry, value)}
        onReset={() => resetPolicy(entry)}
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* Asignador "Apply to" (D-36-03) + buscador es+en (REQ-036-002) */}
      <Card>
        <CardHeader
          title="Apply policies to"
          description="Pick who these policies apply to. Changes are saved at that level with the existing permissions and audit — lower levels can only tighten what a higher level set."
        />
        <CardBody className="flex flex-wrap items-end gap-3">
          <Field label="Apply to" htmlFor="apply-level">
            <Select
              id="apply-level"
              value={level}
              onChange={(e) => {
                setLevel(e.target.value as ScopeLevel);
                setErrors({});
              }}
              className="w-44"
            >
              <option value="org">Organization</option>
              <option value="dept">Department</option>
              <option value="user">User</option>
            </Select>
          </Field>
          {level === "dept" ? (
            <Field label="Department" htmlFor="apply-dept">
              <Select
                id="apply-dept"
                value={deptId}
                onChange={(e) => setDeptId(e.target.value)}
                className="w-56"
              >
                <option value="">Select a department…</option>
                {data.departments.map((d) => (
                  <option key={d.department_id} value={d.department_id}>
                    {d.name}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          {level === "user" ? (
            <Field label="User" htmlFor="apply-user">
              <Select
                id="apply-user"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                className="w-72"
              >
                <option value="">Select a user…</option>
                {directory.data?.users.map((u) => (
                  <option key={u.user_id} value={u.user_id}>
                    {userLabel(u)}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          <Field label="Search policies" htmlFor="policy-search">
            <div className="relative">
              <IconSearch
                size={14}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"
              />
              <Input
                id="policy-search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search by name or description (es/en)…"
                className="w-64 pl-8"
              />
            </div>
          </Field>
          {level !== "org" ? (
            <Badge tone="accent">
              {level === "dept"
                ? selectedDept
                  ? `Department · ${selectedDept.name}`
                  : "No department selected"
                : selectedUser
                  ? `User · ${userLabel(selectedUser)}`
                  : "No user selected"}
            </Badge>
          ) : null}
        </CardBody>
      </Card>

      {/* Estados reales del destinatario (REQ-036-008) */}
      {needsRecipient ? (
        <EmptyState
          title="Select a recipient"
          description={
            level === "dept"
              ? "Choose a department to see and set its policies."
              : "Choose a user to see and set their policies."
          }
        />
      ) : userDiffPending ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-44 rounded-xl" />
          ))}
        </div>
      ) : userDiffFailed ? (
        <ErrorState
          error={diff.error!}
          onRetry={diff.refetch}
          context={`GET /api/policies/effective?user_id=${userId}`}
        />
      ) : (
        <>
          {groups.byCategory.map(({ category, entries }) => (
            <section key={category.id} aria-label={t(category.name)}>
              <div className="mb-3 flex items-center gap-2">
                <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-surface-alt text-accent">
                  <PolicyIcon
                    name={category.icon}
                    category={category.id}
                    categories={data.categories}
                    size={15}
                  />
                </span>
                <h2 className="text-sm font-semibold text-foreground">
                  {t(category.name)}
                </h2>
                <Badge>{entries.length}</Badge>
              </div>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {entries.map((entry) => renderCard(entry))}
              </div>
            </section>
          ))}

          {/* Protecciones del proveedor (D-36-09): piso fijo, con candado */}
          {groups.protections.length ? (
            <section aria-label="Provider protections">
              <div className="mb-3 flex items-center gap-2">
                <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-surface-alt text-accent">
                  <Lock size={15} strokeWidth={1.75} aria-hidden="true" />
                </span>
                <h2 className="text-sm font-semibold text-foreground">
                  Provider protections
                </h2>
                <Badge>{groups.protections.length}</Badge>
              </div>
              <p className="mb-3 text-xs text-muted">
                Fixed security floors enforced by the provider for every
                organization — always on, auditable, never editable.
              </p>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {groups.protections.map((entry) => renderCard(entry))}
              </div>
            </section>
          ) : null}

          {!groups.byCategory.length && !groups.protections.length ? (
            <EmptyState
              title="No policies match your search"
              description="Try another word — the search covers names and descriptions in Spanish and English."
            />
          ) : null}
        </>
      )}
    </div>
  );
}
