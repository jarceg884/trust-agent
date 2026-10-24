"use client";

/**
 * Departments (spec 009 REQ-AC-012): lista real de /api/budgets/overview,
 * edición de presupuesto por depto (PUT /api/budgets/dept/{id}) y políticas
 * por departamento. "Add department" deshabilitado: sin API de directorio
 * (D-AC-06) — estado honesto, no simulado.
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

export function DepartmentsPage() {
  const overview = useApi<BudgetOverview>("/api/budgets/overview");
  const { push } = useToast();

  const [editId, setEditId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [amount, setAmount] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [policiesFor, setPoliciesFor] = useState<{ id: string; name: string } | null>(null);

  const policies = useApi<PolicyList>(
    policiesFor ? `/api/policies?level=dept&department_id=${policiesFor.id}` : null,
  );

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
      push("error", cause instanceof ApiError ? cause.message : "Update failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Departments"
          description="Monthly budgets and policies per department. Department budgets must sum within the organization budget."
          actions={
            <Button variant="primary" size="sm" onClick={() => setAddOpen(true)}>
              <IconPlus size={13} /> Add department
            </Button>
          }
        />
        {overview.loading ? (
          <TableSkeleton rows={4} cols={5} />
        ) : overview.error ? (
          <ErrorState error={overview.error} onRetry={overview.refetch} context="GET /api/budgets/overview" />
        ) : (overview.data?.departments.length ?? 0) === 0 ? (
          <EmptyState
            icon={<IconDepartments size={18} />}
            title="No departments with budgets"
            description="Departments created during provisioning appear here once they have a monthly budget."
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
                <Th className="w-44" />
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
                          onClick={() => setPoliciesFor({ id: d.department_id, name: d.name })}
                        >
                          Policies
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

      {/* Editar presupuesto de depto */}
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
        ) : (policies.data?.policies.length ?? 0) === 0 ? (
          <EmptyState
            title="No department policies"
            description="This department inherits the organization baseline. Department policies are managed with the policies API (PUT /api/policies/dept/{id})."
          />
        ) : (
          <ul className="divide-y divide-border-subtle">
            {policies.data!.policies.map((p) => (
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
      </Dialog>

      {/* Add department: API pendiente (D-AC-06) */}
      <Dialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title="Add department"
        description="Department creation requires the directory API."
      >
        <p className="text-sm leading-relaxed text-muted">
          The control plane does not expose a department creation endpoint yet
          (<code className="font-mono text-xs">POST /api/departments</code> —
          contract in the platform spec). Departments are currently created
          during tenant provisioning. This console will activate the form as
          soon as the endpoint is available — no simulated data is shown.
        </p>
      </Dialog>
    </div>
  );
}
