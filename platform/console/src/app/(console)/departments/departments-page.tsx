"use client";

/**
 * Departments (spec 009 REQ-AC-012 + spec 016 CRUD completo): lista real de
 * /api/budgets/overview (departamentos con y sin presupuesto, D-013-07) y
 * ciclo de vida completo contra el control plane:
 * - POST /api/departments — alta con nombre + budget mensual inicial
 *   opcional, atómica (D-16-01);
 * - PUT /api/departments/{id} — rename (D-16-03);
 * - DELETE /api/departments/{id} — solo deptos vacíos; el 409 del API se
 *   muestra al operador con conteos y salida (D-16-02);
 * - PUT /api/budgets/dept/{id} — edición de presupuesto (spec 009).
 * El aviso anterior ("sin API de directorio") quedó obsoleto con las
 * specs 012/016 y fue retirado.
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type { BudgetOverview, PolicyList } from "@/lib/types";
import { centsToInput, usd, usdToCents, usagePct } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Progress,
  Table,
  TableSkeleton,
  Td,
  Th,
  useToast,
} from "@/components/ui";
import { IconDepartments, IconPlus } from "@/components/icons";
import { AllowedConnectionsSelector } from "@/components/connection-policy";
import { StructureWizard } from "@/components/structure-wizard";

export function DepartmentsPage() {
  const overview = useApi<BudgetOverview>("/api/budgets/overview");
  const { push } = useToast();

  // Spec 038 (REQ-DO-023): the structure wizard re-opens from here.
  const [wizardOpen, setWizardOpen] = useState(false);

  // Editar presupuesto de un depto (spec 009)
  const [editId, setEditId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [amount, setAmount] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Alta de departamento (spec 016 D-16-01)
  const [addOpen, setAddOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newBudget, setNewBudget] = useState("");
  const [creating, setCreating] = useState(false);

  // Rename (spec 016 D-16-03)
  const [renameFor, setRenameFor] = useState<{ id: string; name: string } | null>(null);
  const [renameName, setRenameName] = useState("");
  const [renaming, setRenaming] = useState(false);

  // Delete (spec 016 D-16-02)
  const [deleteFor, setDeleteFor] = useState<{ id: string; name: string } | null>(null);
  const [deleting, setDeleting] = useState(false);

  const [policiesFor, setPoliciesFor] = useState<{ id: string; name: string } | null>(null);

  const policies = useApi<PolicyList>(
    policiesFor ? `/api/policies?level=dept&department_id=${policiesFor.id}` : null,
  );

  // spec 024: allowed_connections a nivel depto (selector catálogo + custom)
  const [connTokens, setConnTokens] = useState<string[] | null>(null);
  const [savingConn, setSavingConn] = useState(false);

  function currentConnTokens(): string[] {
    const entry = policies.data?.policies.find((p) => p.key === "allowed_connections");
    return Array.isArray(entry?.value) ? (entry!.value as string[]) : ["*"];
  }

  async function saveDeptConnections() {
    if (!policiesFor || connTokens === null) return;
    setSavingConn(true);
    try {
      await api(`/api/policies/dept/${policiesFor.id}`, {
        method: "PUT",
        body: { key: "allowed_connections", value: connTokens },
      });
      push("success", `allowed_connections saved for "${policiesFor.name}". The policy sweep cut off any instance the new effective policy no longer allows.`);
      setConnTokens(null);
      policies.refetch();
    } catch (cause) {
      push("error", apiError(cause, "Save failed."));
    } finally {
      setSavingConn(false);
    }
  }

  async function removeDeptConnections() {
    if (!policiesFor) return;
    setSavingConn(true);
    try {
      await api(
        `/api/policies/dept/allowed_connections?department_id=${policiesFor.id}`,
        { method: "DELETE" },
      );
      push("success", "Department-level allowed_connections removed (inherits the organization baseline).");
      setConnTokens(null);
      policies.refetch();
    } catch (cause) {
      push("error", apiError(cause, "Remove failed."));
    } finally {
      setSavingConn(false);
    }
  }

  function apiError(cause: unknown, fallback: string): string {
    return cause instanceof ApiError ? cause.message : fallback;
  }

  async function saveBudget() {
    if (!editId) return;
    const cents = usdToCents(amount);
    if (cents === null) {
      push("error", "Enter a valid USD amount (max 2 decimals).");
      return;
    }
    setSubmitting(true);
    try {
      await api(`/api/budgets/dept/${editId}`, {
        method: "PUT",
        body: { amount_cents: cents, period: "monthly" },
      });
      push("success", `${editName} budget updated.`);
      setEditId(null);
      overview.refetch();
    } catch (cause) {
      push("error", apiError(cause, "Update failed."));
    } finally {
      setSubmitting(false);
    }
  }

  async function createDepartment() {
    const name = newName.trim();
    if (!name) {
      push("error", "Enter a department name.");
      return;
    }
    let initialBudgetCents: number | null = null;
    if (newBudget.trim() !== "") {
      initialBudgetCents = usdToCents(newBudget);
      if (initialBudgetCents === null) {
        push("error", "Enter a valid USD amount (max 2 decimals).");
        return;
      }
    }
    setCreating(true);
    try {
      await api("/api/departments", {
        method: "POST",
        body: {
          name,
          ...(initialBudgetCents !== null
            ? { initial_budget_cents: initialBudgetCents }
            : {}),
        },
      });
      push(
        "success",
        initialBudgetCents !== null
          ? `Department “${name}” created with a ${usd(initialBudgetCents)} monthly budget.`
          : `Department “${name}” created. Set a monthly budget from its row when needed.`,
      );
      setAddOpen(false);
      setNewName("");
      setNewBudget("");
      overview.refetch();
    } catch (cause) {
      // 422 del API: nombre duplicado o presupuesto inválido (Σ dept > org,
      // sin org budget) — mensaje real del control plane.
      push("error", apiError(cause, "Creation failed."));
    } finally {
      setCreating(false);
    }
  }

  async function renameDepartment() {
    if (!renameFor) return;
    const name = renameName.trim();
    if (!name) {
      push("error", "Enter a department name.");
      return;
    }
    setRenaming(true);
    try {
      await api(`/api/departments/${renameFor.id}`, {
        method: "PUT",
        body: { name },
      });
      push("success", `Department renamed to “${name}”.`);
      setRenameFor(null);
      overview.refetch();
    } catch (cause) {
      push("error", apiError(cause, "Rename failed."));
    } finally {
      setRenaming(false);
    }
  }

  async function deleteDepartment() {
    if (!deleteFor) return;
    setDeleting(true);
    try {
      await api(`/api/departments/${deleteFor.id}`, { method: "DELETE" });
      push("success", `Department “${deleteFor.name}” deleted.`);
      setDeleteFor(null);
      overview.refetch();
    } catch (cause) {
      // 409 del API: usuarios/roles/gasto/agentes — mensaje real con conteos.
      push("error", apiError(cause, "Delete failed."));
      setDeleteFor(null);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Departments"
          description="Monthly budgets and policies per department. Department budgets must sum within the organization budget."
          actions={
            <div className="flex items-center gap-1.5">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setWizardOpen(true)}
              >
                Set up structure
              </Button>
              <Button variant="primary" size="sm" onClick={() => setAddOpen(true)}>
                <IconPlus size={13} /> Add department
              </Button>
            </div>
          }
        />
        {overview.loading ? (
          <TableSkeleton rows={4} cols={5} />
        ) : overview.error ? (
          <ErrorState error={overview.error} onRetry={overview.refetch} context="GET /api/budgets/overview" />
        ) : (overview.data?.departments.length ?? 0) === 0 ? (
          <EmptyState
            icon={<IconDepartments size={18} />}
            title="No departments yet"
            description="Declare the real structure of your organization — suggested templates (editable names) or your own custom departments."
            action={
              <Button variant="primary" size="sm" onClick={() => setWizardOpen(true)}>
                Set up structure
              </Button>
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Department</Th>
                <Th>Monthly limit</Th>
                <Th>Used</Th>
                <Th>Members</Th>
                <Th className="w-32">Utilization</Th>
                <Th className="w-56" />
              </tr>
            </thead>
            <tbody>
              {overview.data!.departments.map((d) => {
                const pct = usagePct(d.spent_cents, d.budget_cents);
                return (
                  <tr key={d.department_id} className="hover:bg-surface-alt/50">
                    <Td className="font-medium">{d.name}</Td>
                    <Td>{usd(d.budget_cents)}</Td>
                    <Td>{usd(d.spent_cents)}</Td>
                    <Td>{d.members}</Td>
                    <Td>
                      <div className="flex items-center gap-2">
                        <Progress pct={pct} tone={pct >= 100 ? "danger" : pct >= 80 ? "warning" : "accent"} />
                        <span className="w-8 text-right text-xs text-muted">{pct}%</span>
                      </div>
                    </Td>
                    <Td>
                      <div className="flex justify-end gap-1.5">
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => {
                            setEditId(d.department_id);
                            setEditName(d.name);
                            setAmount(centsToInput(d.budget_cents));
                          }}
                        >
                          Budget
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => {
                            setRenameFor({ id: d.department_id, name: d.name });
                            setRenameName(d.name);
                          }}
                        >
                          Rename
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => setPoliciesFor({ id: d.department_id, name: d.name })}
                        >
                          Policies
                        </Button>
                        <Button
                          variant="danger"
                          size="sm"
                          onClick={() => setDeleteFor({ id: d.department_id, name: d.name })}
                        >
                          Delete
                        </Button>
                      </div>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
        {overview.data?.unassigned_cents != null ? (
          <p className="border-t border-border-subtle px-5 py-3 text-xs text-muted">
            {usd(overview.data.unassigned_cents)} of the organization budget remains unallocated.
          </p>
        ) : null}
      </Card>

      {/* Editar presupuesto de depto (spec 009) */}
      <Dialog
        open={editId !== null}
        onClose={() => setEditId(null)}
        title={`Edit budget — ${editName}`}
        description="Monthly limit in USD. The control plane rejects budgets that would push department totals above the organization budget."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setEditId(null)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" loading={submitting} onClick={saveBudget}>
              Save budget
            </Button>
          </>
        }
      >
        <Field label="Monthly limit (USD)" htmlFor="dept-budget">
          <Input
            id="dept-budget"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            inputMode="decimal"
            placeholder="4000.00"
          />
        </Field>
      </Dialog>

      {/* Alta de departamento (spec 016 D-16-01) */}
      <Dialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title="Add department"
        description="The department and its optional monthly budget are created atomically — if the budget is invalid the department is not created."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setAddOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" loading={creating} onClick={createDepartment}>
              Create department
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Name" htmlFor="dept-name">
            <Input
              id="dept-name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Engineering"
              maxLength={80}
            />
          </Field>
          <Field label="Initial monthly budget (USD, optional)" htmlFor="dept-new-budget">
            <Input
              id="dept-new-budget"
              value={newBudget}
              onChange={(e) => setNewBudget(e.target.value)}
              inputMode="decimal"
              placeholder="4000.00"
            />
          </Field>
          <p className="text-xs leading-relaxed text-muted">
            Names must be unique in the organization. The budget is validated against the
            organization budget (the sum of department budgets cannot exceed it).
          </p>
        </div>
      </Dialog>

      {/* Rename (spec 016 D-16-03) */}
      <Dialog
        open={renameFor !== null}
        onClose={() => setRenameFor(null)}
        title={`Rename — ${renameFor?.name ?? ""}`}
        description="The new name must be unique in the organization (case-insensitive)."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setRenameFor(null)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" loading={renaming} onClick={renameDepartment}>
              Save name
            </Button>
          </>
        }
      >
        <Field label="Name" htmlFor="dept-rename">
          <Input
            id="dept-rename"
            value={renameName}
            onChange={(e) => setRenameName(e.target.value)}
            maxLength={80}
          />
        </Field>
      </Dialog>

      {/* Delete (spec 016 D-16-02) */}
      <ConfirmDialog
        open={deleteFor !== null}
        onClose={() => setDeleteFor(null)}
        onConfirm={deleteDepartment}
        loading={deleting}
        title={`Delete department — ${deleteFor?.name ?? ""}`}
        message="Only empty departments can be deleted. If users, department-scoped roles, spend history or agents still reference it, the control plane will reject the delete and explain what needs to be moved first. The department's budgets, policies and model access entries are removed with it."
        confirmLabel="Delete department"
      />

      {/* Políticas del depto */}
      <Dialog
        open={policiesFor !== null}
        onClose={() => setPoliciesFor(null)}
        title={`Policies — ${policiesFor?.name ?? ""}`}
        description="Active department-level policies (they can only tighten the organization baseline)."
        width="max-w-2xl"
      >
        {policies.loading ? (
          <p className="py-6 text-center text-xs text-muted">Loading…</p>
        ) : policies.error ? (
          <ErrorState error={policies.error} context="GET /api/policies?level=dept" />
        ) : (
          <div className="space-y-5">
            {/* spec 024: allowed_connections por depto (catálogo + custom) */}
            <div className="rounded-lg border border-border-subtle p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-foreground">Allowed connections</p>
                  <p className="text-[11px] text-muted">
                    <span className="font-mono">allowed_connections</span> — catalog connectors and
                    custom connections this department permits (deny-overrides: it can only tighten
                    the organization baseline).
                  </p>
                </div>
                <div className="flex gap-1.5">
                  {connTokens === null ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setConnTokens(currentConnTokens())}
                    >
                      Edit
                    </Button>
                  ) : (
                    <>
                      <Button variant="ghost" size="sm" onClick={() => setConnTokens(null)}>
                        Cancel
                      </Button>
                      <Button
                        variant="primary"
                        size="sm"
                        loading={savingConn}
                        onClick={saveDeptConnections}
                      >
                        Save
                      </Button>
                    </>
                  )}
                  {policies.data?.policies.some((p) => p.key === "allowed_connections") ? (
                    <Button variant="danger" size="sm" loading={savingConn} onClick={removeDeptConnections}>
                      Remove
                    </Button>
                  ) : null}
                </div>
              </div>
              {connTokens !== null ? (
                <AllowedConnectionsSelector value={connTokens} onChange={setConnTokens} />
              ) : (
                <p className="font-mono text-xs text-muted">
                  {JSON.stringify(currentConnTokens())}
                  {!policies.data?.policies.some((p) => p.key === "allowed_connections")
                    ? " — inherited default (no department-level policy)"
                    : ""}
                </p>
              )}
            </div>

            <div>
              <p className="mb-2 text-xs font-semibold text-foreground">Other department policies</p>
              {(policies.data?.policies.filter((p) => p.key !== "allowed_connections").length ?? 0) === 0 ? (
                <p className="rounded-lg border border-dashed border-border-subtle px-3 py-2.5 text-[11px] text-muted">
                  No other department policies — this department inherits the organization baseline
                  for everything except the connections selected above.
                </p>
              ) : (
                <ul className="divide-y divide-border-subtle">
                  {policies.data!.policies
                    .filter((p) => p.key !== "allowed_connections")
                    .map((p) => (
                      <li key={p.key} className="flex items-center justify-between gap-3 py-2.5">
                        <span className="font-mono text-xs">{p.key}</span>
                        <div className="flex items-center gap-2">
                          <Badge tone="info">{JSON.stringify(p.value)}</Badge>
                          <span className="text-[10px] text-faint">v{p.version}</span>
                        </div>
                      </li>
                    ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </Dialog>
      {/* Wizard de estructura (spec 038): re-abrible desde esta página */}
      <StructureWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onCompleted={() => overview.refetch()}
      />
    </div>
  );
}
