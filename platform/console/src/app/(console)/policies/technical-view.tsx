"use client";

/**
 * Vista técnica de policies (spec 033, t_e03bba6c) — tabla maestra por key +
 * diff de efectivos por usuario. SIN cambios funcionales: queda como
 * "Technical view" conmutable SOLO para org_admin (spec 036 D-36-02); el
 * panel legible es la vista por defecto.
 */
import { useMemo, useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type {
  DirectoryList,
  PolicyCatalogEntry,
  PolicyDiffKey,
  PolicyEffectiveDiff,
  PolicyEntry,
  PolicyMatrix,
  WhoAmI,
} from "@/lib/types";
import type { UseApiState } from "@/hooks/use-api";
import { IconCheck, IconSearch, IconShield, IconUsers } from "@/components/icons";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Select,
  Table,
  TableSkeleton,
  Td,
  Th,
  Textarea,
  useToast,
} from "@/components/ui";

type ScopeLevel = "org" | "dept" | "user";
type Tab = "master" | "effective";

/** ¿El principal puede EDITAR en el nivel dado? (los PUT/DELETE igual lo
 *  gatean en el servidor; esto es UX, no seguridad). */
function canEdit(me: WhoAmI, level: ScopeLevel): boolean {
  if (level === "org") return me.permissions.includes("policies.org.manage");
  return (
    me.permissions.includes("policies.org.manage") ||
    me.permissions.includes("policies.dept.manage")
  );
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "[]";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function valueTone(v: unknown): "neutral" | "success" | "danger" {
  if (v === true) return "success";
  if (v === false) return "danger";
  return "neutral";
}

/** Valor de una key en un nivel (null = el nivel no la fijó). */
function entryAt(rows: PolicyEntry[] | undefined, key: string): PolicyEntry | null {
  return rows?.find((p) => p.key === key) ?? null;
}

// --- Editor inline por tipo -----------------------------------------------------

function initialValueFor(entry: PolicyCatalogEntry, current: unknown): string {
  if (entry.value_type === "list") {
    return Array.isArray(current) ? current.join(", ") : "";
  }
  return current === null || current === undefined
    ? String(entry.default_value ?? "")
    : String(current);
}

function PolicyValueEditor({
  entry,
  current,
  onSaved,
  onCancel,
}: {
  entry: PolicyCatalogEntry;
  /** Valor vigente en el nivel (null = heredado; se prellena con el
   *  heredado para partir de un estado válido). */
  current: unknown;
  onSaved: (value: unknown) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(() =>
    entry.value_type === "map"
      ? JSON.stringify(current ?? entry.default_value ?? {}, null, 0)
      : initialValueFor(entry, current),
  );
  const [error, setError] = useState<string | null>(null);

  function commit() {
    let value: unknown;
    if (entry.value_type === "boolean") {
      value = text === "true";
    } else if (entry.value_type === "integer") {
      const n = Number(text);
      if (!Number.isInteger(n)) {
        setError("The value must be an integer.");
        return;
      }
      value = n;
    } else if (entry.value_type === "list") {
      value = text
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s.length > 0);
    } else if (entry.value_type === "map") {
      try {
        value = JSON.parse(text);
      } catch {
        setError("The value must be valid JSON.");
        return;
      }
      if (typeof value !== "object" || value === null || Array.isArray(value)) {
        setError("The value must be a JSON object.");
        return;
      }
    } else {
      value = text;
    }
    onSaved(value);
  }

  return (
    <div className="flex flex-col gap-2">
      {entry.value_type === "boolean" ? (
        <Select
          aria-label={`Value for ${entry.key}`}
          value={text}
          onChange={(e) => setText(e.target.value)}
        >
          <option value="true">true</option>
          <option value="false">false</option>
        </Select>
      ) : entry.value_type === "string" && entry.choices ? (
        <Select
          aria-label={`Value for ${entry.key}`}
          value={text}
          onChange={(e) => setText(e.target.value)}
        >
          {entry.choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
      ) : entry.value_type === "map" ? (
        <Textarea
          aria-label={`Value for ${entry.key}`}
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="min-w-64"
        />
      ) : (
        <Input
          aria-label={`Value for ${entry.key}`}
          type={entry.value_type === "integer" ? "number" : "text"}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={
            entry.value_type === "list" ? "value-a, value-b" : undefined
          }
          className="min-w-48 font-mono text-xs"
        />
      )}
      {error ? (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button variant="primary" size="sm" onClick={commit}>
          Save
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** Etiqueta de usuario: prefija el display name SOLO si difiere del email
 *  (evita "admin@… — admin@…" cuando el tenant no define display name). */
function userLabel(u: { display_name: string | null; email: string }): string {
  return u.display_name && u.display_name !== u.email
    ? `${u.display_name} — ${u.email}`
    : u.email;
}

// --- Vista técnica ----------------------------------------------------------------

export function TechnicalPoliciesView({
  me,
  matrix,
  directory,
}: {
  me: WhoAmI;
  matrix: UseApiState<PolicyMatrix>;
  directory: UseApiState<DirectoryList>;
}) {
  const { push } = useToast();

  const [tab, setTab] = useState<Tab>("master");
  const [level, setLevel] = useState<ScopeLevel>("org");
  const [deptId, setDeptId] = useState<string>("");
  const [userId, setUserId] = useState<string>("");

  // Editor inline: key en edición + error 422 real del control plane.
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [resetting, setResetting] = useState<string | null>(null);
  // Filtro de key (47+ filas): client-side sobre el catálogo ya cargado.
  const [keyFilter, setKeyFilter] = useState("");

  // Tab Effective by user.
  const [diffUser, setDiffUser] = useState<string>("");
  const diff = useApi<PolicyEffectiveDiff>(
    tab === "effective" && diffUser
      ? `/api/policies/effective?user_id=${encodeURIComponent(diffUser)}`
      : null,
  );

  const selectedDept = matrix.data?.departments.find(
    (d) => d.department_id === deptId,
  );
  const selectedDirectoryUser = directory.data?.users.find(
    (u) => u.user_id === userId,
  );
  const editable = canEdit(me, level);

  const scopeRows: PolicyEntry[] | undefined =
    level === "org"
      ? matrix.data?.org
      : level === "dept"
        ? selectedDept?.policies
        : matrix.data?.users.find((u) => u.user_id === userId)?.policies;

  /** PUT del nivel seleccionado (endpoints existentes, spec 005). */
  async function savePolicy(entry: PolicyCatalogEntry, value: unknown) {
    setSavingKey(entry.key);
    setRowError(null);
    try {
      const path =
        level === "org"
          ? "/api/policies/org"
          : level === "dept"
            ? `/api/policies/dept/${encodeURIComponent(deptId)}`
            : `/api/policies/user/${encodeURIComponent(userId)}`;
      await api(path, { method: "PUT", body: { key: entry.key, value } });
      push("success", `Policy "${entry.key}" saved at ${level} level.`);
      setEditingKey(null);
      matrix.refetch();
    } catch (cause) {
      // 422 de validación/no-relajación: el mensaje real, inline (REQ-033-005).
      setRowError(
        cause instanceof ApiError ? cause.message : "Save failed. Try again.",
      );
    } finally {
      setSavingKey(null);
    }
  }

  /** DELETE del override del nivel seleccionado → hereda del superior. */
  async function resetPolicy(key: string) {
    setSavingKey(key);
    setRowError(null);
    try {
      const params = new URLSearchParams();
      if (level === "dept") params.set("department_id", deptId);
      if (level === "user") {
        params.set("user_id", userId);
        if (selectedDirectoryUser?.department_id) {
          params.set("department_id", selectedDirectoryUser.department_id);
        }
      }
      const qs = params.toString();
      await api(
        `/api/policies/${level}/${encodeURIComponent(key)}${qs ? `?${qs}` : ""}`,
        { method: "DELETE" },
      );
      push("success", `Override "${key}" removed — inherits from above.`);
      setResetting(null);
      matrix.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Delete failed.");
    } finally {
      setSavingKey(null);
    }
  }

  const stats = useMemo(() => {
    if (!matrix.data) return null;
    return {
      keys: matrix.data.catalog.length,
      org: matrix.data.org.length,
      depts: matrix.data.departments.filter((d) => d.policies.length > 0).length,
      users: matrix.data.users.length,
    };
  }, [matrix.data]);

  const diffSummary = useMemo(() => {
    if (!diff.data) return null;
    const s = { inherited: 0, dept: 0, user: 0, denied: 0 };
    for (const k of diff.data.keys) {
      if (k.denied) s.denied += 1;
      if (k.tightened_by_dept) s.dept += 1;
      if (k.tightened_by_user) s.user += 1;
      if (k.source === "default") s.inherited += 1;
    }
    return s;
  }, [diff.data]);

  return (
    <div className="space-y-6">
      {/* Resumen real de la matriz */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardBody className="flex items-center gap-3">
            <IconShield size={18} className="text-accent" />
            <div>
              <p className="text-[11px] uppercase tracking-wide text-muted">
                Catalog keys
              </p>
              <p className="text-lg font-semibold">{stats?.keys ?? "—"}</p>
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardBody className="flex items-center gap-3">
            <IconCheck size={18} className="text-accent" />
            <div>
              <p className="text-[11px] uppercase tracking-wide text-muted">
                Org overrides
              </p>
              <p className="text-lg font-semibold">{stats?.org ?? "—"}</p>
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardBody className="flex items-center gap-3">
            <IconShield size={18} className="text-accent" />
            <div>
              <p className="text-[11px] uppercase tracking-wide text-muted">
                Dept overrides
              </p>
              <p className="text-lg font-semibold">{stats?.depts ?? "—"}</p>
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardBody className="flex items-center gap-3">
            <IconUsers size={18} className="text-accent" />
            <div>
              <p className="text-[11px] uppercase tracking-wide text-muted">
                User overrides
              </p>
              <p className="text-lg font-semibold">{stats?.users ?? "—"}</p>
            </div>
          </CardBody>
        </Card>
      </div>

      {/* Tabs */}
      <div
        role="tablist"
        aria-label="Policy views"
        className="flex gap-1 rounded-lg border border-border-subtle bg-surface-alt p-1"
      >
        {(
          [
            ["master", "Master table"],
            ["effective", "Effective by user"],
          ] as [Tab, string][]
        ).map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              tab === id
                ? "bg-surface text-foreground shadow-sm"
                : "text-muted hover:text-foreground"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "master" ? (
        <Card>
          <CardHeader
            title="Policy values by level"
            description="One row per catalog key. Pick the level to edit; lower levels can only tighten the value above (deny-overrides)."
          />
          <CardBody className="border-b border-border-subtle">
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Level" htmlFor="scope-level">
                <Select
                  id="scope-level"
                  value={level}
                  onChange={(e) => {
                    setLevel(e.target.value as ScopeLevel);
                    setEditingKey(null);
                    setRowError(null);
                  }}
                  className="w-44"
                >
                  <option value="org">Organization</option>
                  <option value="dept">Department</option>
                  <option value="user">User</option>
                </Select>
              </Field>
              {level === "dept" ? (
                <Field label="Department" htmlFor="scope-dept">
                  <Select
                    id="scope-dept"
                    value={deptId}
                    onChange={(e) => {
                      setDeptId(e.target.value);
                      setEditingKey(null);
                    }}
                    className="w-56"
                  >
                    <option value="">Select a department…</option>
                    {matrix.data?.departments.map((d) => (
                      <option key={d.department_id} value={d.department_id}>
                        {d.name}
                      </option>
                    ))}
                  </Select>
                </Field>
              ) : null}
              {level === "user" ? (
                <Field label="User" htmlFor="scope-user">
                  <Select
                    id="scope-user"
                    value={userId}
                    onChange={(e) => {
                      setUserId(e.target.value);
                      setEditingKey(null);
                    }}
                    className="w-64"
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
              <Field label="Filter keys" htmlFor="key-filter">
                <div className="relative">
                  <IconSearch
                    size={14}
                    className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"
                  />
                  <Input
                    id="key-filter"
                    value={keyFilter}
                    onChange={(e) => setKeyFilter(e.target.value)}
                    placeholder="e.g. allow_, mfa"
                    className="w-56 pl-8 font-mono text-xs"
                  />
                </div>
              </Field>
              {rowError ? (
                <p
                  role="alert"
                  className="w-full rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger"
                >
                  {rowError}
                </p>
              ) : null}
            </div>
          </CardBody>
          {level !== "org" && !deptId && !userId ? (
            <EmptyState
              title="Select a scope"
              description={
                level === "dept"
                  ? "Choose a department to view and edit its policy overrides."
                  : "Choose a user to view and edit their policy overrides."
              }
            />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <thead>
                  <tr>
                    <Th>Key</Th>
                    <Th>Type</Th>
                    <Th>Provider default</Th>
                    <Th>Organization</Th>
                    <Th>
                      {level === "org"
                        ? "Value"
                        : level === "dept"
                          ? `Department${selectedDept ? ` · ${selectedDept.name}` : ""}`
                          : "User"}
                    </Th>
                    <Th>Origin</Th>
                    <Th className="w-24" />
                  </tr>
                </thead>
              <tbody>
                {matrix.data!.catalog
                  .filter(
                    (e) =>
                      !keyFilter.trim() ||
                      e.key
                        .toLowerCase()
                        .includes(keyFilter.trim().toLowerCase()),
                  )
                  .map((entry) => {
                  const orgRow = entryAt(matrix.data!.org, entry.key);
                  const scopeRow = entryAt(scopeRows, entry.key);
                  const inherited =
                    level !== "org" ? (orgRow?.value ?? entry.default_value) : null;
                  const isEditing = editingKey === entry.key;
                  const canTouch =
                    editable &&
                    !entry.invariable &&
                    !(entry.org_only && level !== "org");

                  return (
                    <tr
                      key={entry.key}
                      className="align-top hover:bg-surface-alt/50"
                    >
                      <Td className="font-mono text-xs">
                        {entry.key}
                        {entry.invariable ? (
                          <span
                            title="Provider invariable floor — cannot be set at any level (REQ-POL-004)"
                            className="ml-1.5"
                          >
                            🔒
                          </span>
                        ) : null}
                        {entry.org_only ? (
                          <span
                            title="Org-only key (spec 015 D-AUTH-07/08): it gates organization-wide behavior (login UX, self registration) and cannot be overridden at dept/user level."
                            className="ml-1.5"
                          >
                            🏢
                          </span>
                        ) : null}
                      </Td>
                      <Td className="text-xs text-muted">{entry.value_type}</Td>
                      <Td className="font-mono text-xs text-muted">
                        {formatValue(entry.default_value)}
                      </Td>
                      <Td className="font-mono text-xs">
                        {orgRow ? (
                          <Badge tone="accent">
                            {formatValue(orgRow.value)}
                          </Badge>
                        ) : (
                          <span className="text-faint">default</span>
                        )}
                      </Td>
                      <Td>
                        {isEditing ? (
                          <PolicyValueEditor
                            entry={entry}
                            current={scopeRow?.value ?? inherited}
                            onSaved={(value) => savePolicy(entry, value)}
                            onCancel={() => {
                              setEditingKey(null);
                              setRowError(null);
                            }}
                          />
                        ) : scopeRow ? (
                          <span className="font-mono text-xs">
                            {formatValue(scopeRow.value)}
                          </span>
                        ) : level === "org" ? (
                          <span className="text-xs text-faint">
                            not set — default applies
                          </span>
                        ) : (
                          <span className="text-xs text-faint">
                            inherits {formatValue(inherited)}
                          </span>
                        )}
                      </Td>
                      <Td>
                        <div className="flex flex-wrap gap-1">
                          {orgRow ? <Badge tone="accent">Org</Badge> : null}
                          {level === "dept" && scopeRow ? (
                            <Badge tone="warning">Dept override</Badge>
                          ) : null}
                          {level === "user" && scopeRow ? (
                            <Badge tone="warning">User override</Badge>
                          ) : null}
                          {!orgRow && !scopeRow ? (
                            <Badge>Default</Badge>
                          ) : null}
                        </div>
                      </Td>
                      <Td>
                        {canTouch && !isEditing ? (
                          <div className="flex gap-1.5">
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => {
                                setEditingKey(entry.key);
                                setRowError(null);
                              }}
                            >
                              Edit
                            </Button>
                            {scopeRow ? (
                              <Button
                                variant="danger"
                                size="sm"
                                disabled={savingKey === entry.key}
                                onClick={() => setResetting(entry.key)}
                              >
                                Reset
                              </Button>
                            ) : null}
                          </div>
                        ) : null}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
              </Table>
            </div>
          )}
        </Card>
      ) : (
        <Card>
          <CardHeader
            title="Effective policy diff"
            description="The deny-overrides combination this user actually gets — computed by the control plane, never re-derived here."
          />
          <CardBody className="border-b border-border-subtle">
            <div className="flex flex-wrap items-end gap-3">
              <Field label="User" htmlFor="diff-user">
                <Select
                  id="diff-user"
                  value={diffUser}
                  onChange={(e) => setDiffUser(e.target.value)}
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
              {diffSummary ? (
                <div className="flex flex-wrap gap-2">
                  <Badge>{diffSummary.inherited} inherited</Badge>
                  <Badge tone={diffSummary.dept > 0 ? "warning" : "neutral"}>
                    {diffSummary.dept} tightened by department
                  </Badge>
                  <Badge tone={diffSummary.user > 0 ? "warning" : "neutral"}>
                    {diffSummary.user} tightened by user
                  </Badge>
                  <Badge tone={diffSummary.denied > 0 ? "danger" : "neutral"}>
                    {diffSummary.denied} denied
                  </Badge>
                </div>
              ) : null}
            </div>
          </CardBody>
          {!diffUser ? (
            <EmptyState
              title="Select a user"
              description="Pick anyone from the directory to see what they inherit, what each level tightens and what stays denied."
            />
          ) : diff.loading ? (
            <TableSkeleton rows={8} cols={6} />
          ) : diff.error ? (
            <ErrorState
              error={diff.error}
              onRetry={diff.refetch}
              context={`GET /api/policies/effective?user_id=${diffUser}`}
            />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <thead>
                  <tr>
                    <Th>Key</Th>
                    <Th>Provider default</Th>
                    <Th>Organization</Th>
                    <Th>Department</Th>
                    <Th>User</Th>
                    <Th>Effective</Th>
                    <Th>Status</Th>
                  </tr>
                </thead>
              <tbody>
                {diff.data!.keys.map((k: PolicyDiffKey) => (
                  <tr key={k.key} className="hover:bg-surface-alt/50">
                    <Td className="font-mono text-xs">{k.key}</Td>
                    <Td className="font-mono text-xs text-muted">
                      {formatValue(k.default_value)}
                    </Td>
                    <Td className="font-mono text-xs">
                      {k.org_value === null ? (
                        <span className="text-muted">—</span>
                      ) : (
                        formatValue(k.org_value)
                      )}
                    </Td>
                    <Td className="font-mono text-xs">
                      {k.dept_value === null ? (
                        <span className="text-muted">—</span>
                      ) : (
                        formatValue(k.dept_value)
                      )}
                    </Td>
                    <Td className="font-mono text-xs">
                      {k.user_value === null ? (
                        <span className="text-muted">—</span>
                      ) : (
                        formatValue(k.user_value)
                      )}
                    </Td>
                    <Td className="font-mono text-xs font-medium">
                      {typeof k.effective === "boolean" ? (
                        <Badge tone={valueTone(k.effective)}>
                          {String(k.effective)}
                        </Badge>
                      ) : (
                        formatValue(k.effective)
                      )}
                    </Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {k.denied ? <Badge tone="danger">Denied</Badge> : null}
                        {k.tightened_by_dept ? (
                          <Badge tone="warning">Tightened · dept</Badge>
                        ) : null}
                        {k.tightened_by_user ? (
                          <Badge tone="warning">Tightened · user</Badge>
                        ) : null}
                        {k.source === "default" ? (
                          <Badge>Inherited</Badge>
                        ) : k.source === "org" && !k.tightened_by_dept && !k.tightened_by_user ? (
                          <Badge tone="accent">Org</Badge>
                        ) : null}
                      </div>
                    </Td>
                  </tr>
                ))}
              </tbody>
              </Table>
            </div>
          )}
        </Card>
      )}

      <ConfirmDialog
        open={resetting !== null}
        onClose={() => setResetting(null)}
        onConfirm={() => resetting && resetPolicy(resetting)}
        loading={savingKey !== null}
        title="Remove policy override"
        message={`The override "${resetting}" will stop applying at this level and the value will fall back to the level above (or the catalog default). This action is audited.`}
        confirmLabel="Remove override"
      />
    </div>
  );
}
