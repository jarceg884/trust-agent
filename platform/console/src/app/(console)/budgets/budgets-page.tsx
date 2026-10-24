"use client";

/**
 * Budgets (spec 009 REQ-AC-014): jerarquía org→depto con estados de corte y
 * edición por nivel (PUT /api/budgets/org y /api/budgets/dept/{id}). El 422
 * de suma (Σ dept ≤ org) y el de force-below-spend se muestran accionables.
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type { BudgetOverview, BudgetStatus } from "@/lib/types";
import { centsToInput, usd, usdToCents, usagePct } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Progress,
  Skeleton,
  useToast,
} from "@/components/ui";

interface EditTarget {
  kind: "org" | "dept";
  departmentId?: string;
  name: string;
  currentCents: number | null;
}

export function BudgetsPage() {
  const overview = useApi<BudgetOverview>("/api/budgets/overview");
  const mine = useApi<BudgetStatus>("/api/budgets/me");
  const { push } = useToast();

  const [edit, setEdit] = useState<EditTarget | null>(null);
  const [amount, setAmount] = useState("");
  const [forceOpen, setForceOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  function openEdit(target: EditTarget) {
    setEdit(target);
    setAmount(centsToInput(target.currentCents));
    setForceOpen(false);
  }

  async function submit(force: boolean) {
    if (!edit) return;
    const cents = usdToCents(amount);
    if (cents === null) {
      push("error", "Enter a valid USD amount (max 2 decimals).");
      return;
    }
    setSubmitting(true);
    try {
      const path =
        edit.kind === "org"
          ? "/api/budgets/org"
          : `/api/budgets/dept/${edit.departmentId}`;
      await api(path, {
        method: "PUT",
        body: { amount_cents: cents, period: "monthly", force },
      });
      push("success", `${edit.name} budget updated.`);
      setEdit(null);
      overview.refetch();
      mine.refetch();
    } catch (cause) {
      if (cause instanceof ApiError) {
        if (cause.status === 422 && /below.*spend|force/i.test(cause.message)) {
          setForceOpen(true);
        } else {
          push("error", cause.message);
        }
      } else {
        push("error", "Update failed.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  const org = overview.data?.organization;

  return (
    <div className="space-y-6">
      {/* Jerarquía */}
      <Card>
        <CardHeader
          title="Budget hierarchy"
          description="Organization → departments. A cutoff blocks all model usage at that level."
        />
        {overview.loading ? (
          <CardBody className="space-y-4">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </CardBody>
        ) : overview.error ? (
          <ErrorState error={overview.error} onRetry={overview.refetch} context="GET /api/budgets/overview" />
        ) : (
          <CardBody className="space-y-3">
            {/* Nivel org */}
            <div className="rounded-lg border border-border-subtle p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <p className="text-sm font-semibold text-foreground">Organization</p>
                  {org?.cutoff ? <Badge tone="danger">Cutoff</Badge> : null}
                </div>
                <div className="flex items-center gap-4">
                  <p className="text-sm text-muted">
                    Limit <span className="font-semibold text-foreground">{usd(org?.budget_cents)}</span>
                  </p>
                  <p className="text-sm text-muted">
                    Used <span className="font-semibold text-foreground">{usd(org?.spent_cents)}</span>
                  </p>
                  <Button variant="secondary" size="sm" onClick={() => openEdit({ kind: "org", name: "Organization", currentCents: org?.budget_cents ?? null })}>
                    Edit
                  </Button>
                </div>
              </div>
              <div className="mt-3">
                <Progress
                  pct={usagePct(org?.spent_cents ?? 0, org?.budget_cents)}
                  tone={org?.cutoff ? "danger" : usagePct(org?.spent_cents ?? 0, org?.budget_cents ?? 0) >= 80 ? "warning" : "accent"}
                />
              </div>
            </div>

            {/* Niveles depto */}
            {(overview.data?.departments ?? []).map((d) => {
              const pct = usagePct(d.spent_cents, d.budget_cents);
              const cutoff =
                d.budget_cents != null &&
                d.budget_cents > 0 &&
                d.spent_cents >= d.budget_cents;
              return (
                <div key={d.department_id} className="ml-4 rounded-lg border border-border-subtle p-4 sm:ml-8">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span aria-hidden="true" className="text-faint">└</span>
                      <p className="text-sm font-medium text-foreground">{d.name}</p>
                      {cutoff ? <Badge tone="danger">Cutoff</Badge> : null}
                      <span className="text-xs text-muted">{d.members} members</span>
                    </div>
                    <div className="flex items-center gap-4">
                      <p className="text-sm text-muted">
                        Limit <span className="font-semibold text-foreground">{usd(d.budget_cents)}</span>
                      </p>
                      <p className="text-sm text-muted">
                        Used <span className="font-semibold text-foreground">{usd(d.spent_cents)}</span>
                      </p>
                      <Button variant="secondary" size="sm" onClick={() => openEdit({ kind: "dept", departmentId: d.department_id, name: d.name, currentCents: d.budget_cents })}>
                        Edit
                      </Button>
                    </div>
                  </div>
                  <div className="mt-3">
                    <Progress pct={pct} tone={cutoff ? "danger" : pct >= 80 ? "warning" : "accent"} />
                  </div>
                </div>
              );
            })}

            {(overview.data?.departments.length ?? 0) === 0 ? (
              <EmptyState
                title="No department budgets"
                description="Allocate a monthly budget to each department from the Departments page."
              />
            ) : null}
          </CardBody>
        )}
      </Card>

      {/* Mi presupuesto (contexto del admin como usuario) */}
      <Card>
        <CardHeader
          title="Your own budget"
          description="Your personal limits as a user of this organization (user → department → organization)."
        />
        {mine.loading ? (
          <CardBody className="space-y-3">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
          </CardBody>
        ) : mine.error ? (
          <ErrorState error={mine.error} onRetry={mine.refetch} context="GET /api/budgets/me" />
        ) : mine.data ? (
          <CardBody>
            <div className="grid gap-3 sm:grid-cols-3">
              {(["user", "department", "organization"] as const).map((level) => {
                const s = mine.data![level];
                return (
                  <div key={level} className="rounded-lg border border-border-subtle p-3.5">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted">{level}</p>
                    <p className="mt-1 text-lg font-semibold text-foreground">{usd(s.budget_cents)}</p>
                    <p className="text-xs text-muted">used {usd(s.spent_cents)}</p>
                    {s.cutoff ? <Badge tone="danger" className="mt-1.5">Cutoff</Badge> : null}
                  </div>
                );
              })}
            </div>
            {mine.data.cutoff ? (
              <p className="mt-3 rounded-lg border border-danger/25 bg-danger-soft px-3.5 py-2.5 text-xs text-danger">
                A budget cutoff is active at the {mine.data.cutoff_level} level — model usage is blocked until the budget is raised or the period resets.
              </p>
            ) : null}
          </CardBody>
        ) : null}
      </Card>

      {/* Diálogo de edición */}
      <Dialog
        open={edit !== null}
        onClose={() => setEdit(null)}
        title={`Edit ${edit?.name ?? ""} budget`}
        description="Monthly limit in USD. Department budgets must sum within the organization budget."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setEdit(null)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" loading={submitting} onClick={() => submit(false)}>
              Save budget
            </Button>
          </>
        }
      >
        <Field label="Monthly limit (USD)" htmlFor="budget-amount">
          <Input
            id="budget-amount"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            inputMode="decimal"
            placeholder="4000.00"
          />
        </Field>
      </Dialog>

      {/* Confirmación force (422 real del control plane) */}
      <Dialog
        open={forceOpen}
        onClose={() => setForceOpen(false)}
        title="Set budget below current spend?"
        description="The control plane rejects budgets below the spend already recorded unless you force it."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setForceOpen(false)}>
              Cancel
            </Button>
            <Button variant="danger" size="sm" loading={submitting} onClick={() => submit(true)}>
              Force update
            </Button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-muted">
          Forcing a budget below the current spend can trigger an immediate
          cutoff at this level. This action is recorded in the audit log.
        </p>
      </Dialog>
    </div>
  );
}
