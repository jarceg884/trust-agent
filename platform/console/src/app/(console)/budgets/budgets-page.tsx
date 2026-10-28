"use client";

/**
 * Budgets (spec 009 REQ-AC-014): jerarquía org→depto→usuario con estados de
 * corte y edición por nivel (PUT /api/budgets/org, /api/budgets/dept/{id} y
 * /api/budgets/user/{id} — t_6b40c758, QA F3/feedback Miller #5).
 *
 * t_aa139606 (repro Miller): el budget de la ORG vive ahora en una card
 * destacada de primera intención ("Organization budget") con botón Edit
 * visible — antes estaba enterrado como la primera fila de la jerarquía y
 * el admin no encontraba cómo auto-incrementarlo. El control plane NO
 * exige aprobación del provider para subir el budget org (solo
 * budget.org.manage, que org_admin tiene por schema.sql); la única
 * validación extra es force-below-spend al BAJAR. Los 422 de suma
 * (Σ dept ≤ org, Σ usuarios ≤ depto) y el de force-below-spend se
 * muestran accionables. Al final, historial de periodos cerrados
 * (GET /api/budgets/history, spec 013 D-013-03).
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type {
  BudgetHistory,
  BudgetOverview,
  BudgetStatus,
  WhoAmI,
} from "@/lib/types";
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
  kind: "org" | "dept" | "user";
  departmentId?: string;
  userId?: string;
  name: string;
  currentCents: number | null;
}

export function BudgetsPage() {
  const overview = useApi<BudgetOverview>("/api/budgets/overview");
  const mine = useApi<BudgetStatus>("/api/budgets/me");
  const history = useApi<BudgetHistory>("/api/budgets/history");
  const me = useApi<WhoAmI>("/api/me");
  const { push } = useToast();

  // El control plane exige budget.org.manage para PUT /api/budgets/org y
  // budget.user.manage para PUT /api/budgets/user/{id}; los botones de
  // edición solo se ofrecen a quien los tiene (D-17-01 data-driven).
  const canEditOrgBudget =
    me.data?.permissions.includes("budget.org.manage") ?? false;
  const canEditUserBudgets =
    me.data?.permissions.includes("budget.user.manage") ?? false;

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
          : edit.kind === "dept"
            ? `/api/budgets/dept/${edit.departmentId}`
            : `/api/budgets/user/${edit.userId}`;
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
  const orgPct = usagePct(org?.spent_cents ?? 0, org?.budget_cents);

  return (
    <div className="space-y-6">
      {/* Organization budget — card destacada de primera intención
          (t_aa139606, repro Miller): el límite de la ORG es editable por el
          propio org_admin aquí, sin salir de la página ni pedirle al
          provider. Subirlo es siempre válido (Σ dept ≤ org se relaja al
          subir el techo); solo bajarlo por debajo del gasto pide force. */}
      <Card className="border-accent-border bg-accent-soft/40">
        <CardHeader
          title="Organization budget"
          description="The monthly spend limit for the entire organization. As an org admin you can raise it yourself — no provider approval required."
          actions={
            overview.loading ? null : canEditOrgBudget ? (
              <Button
                variant="primary"
                size="sm"
                title="PUT /api/budgets/org — requires budget.org.manage"
                onClick={() =>
                  openEdit({
                    kind: "org",
                    name: "Organization",
                    currentCents: org?.budget_cents ?? null,
                  })
                }
              >
                Edit budget
              </Button>
            ) : (
              <p className="text-xs text-muted">Requires budget.org.manage</p>
            )
          }
        />
        {overview.loading ? (
          <CardBody className="space-y-4">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-2 w-full" />
          </CardBody>
        ) : overview.error ? (
          <CardBody>
            <ErrorState error={overview.error} onRetry={overview.refetch} context="GET /api/budgets/overview" />
          </CardBody>
        ) : (
          <CardBody className="space-y-4">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted">
                  Monthly limit
                </p>
                <p className="mt-0.5 text-3xl font-semibold tracking-tight text-foreground">
                  {usd(org?.budget_cents)}
                </p>
              </div>
              <div className="flex items-center gap-6 pb-1">
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted">Used</p>
                  <p className="mt-0.5 text-lg font-semibold text-foreground">
                    {usd(org?.spent_cents)}
                    <span className="ml-1 text-xs font-normal text-muted">
                      ({orgPct}%)
                    </span>
                  </p>
                </div>
                {org?.cutoff ? <Badge tone="danger">Cutoff</Badge> : null}
              </div>
            </div>
            <Progress
              pct={orgPct}
              tone={org?.cutoff ? "danger" : orgPct >= 80 ? "warning" : "accent"}
            />
            <p className="text-xs text-muted">
              {usd(overview.data?.dept_budgets_total_cents ?? 0)} allocated to
              departments
              {overview.data?.unassigned_cents != null
                ? ` · ${usd(overview.data.unassigned_cents)} unallocated`
                : ""}
              . Department budgets must sum within this limit — raise it here
              to make room for more.
            </p>
          </CardBody>
        )}
      </Card>

      {/* Jerarquía por debajo de la org */}
      <Card>
        <CardHeader
          title="Budget hierarchy"
          description={`Organization → departments → users. A cutoff blocks all model usage at that level.`}
        />
        {overview.loading ? (
          <CardBody className="space-y-4">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </CardBody>
        ) : overview.error ? (
          <CardBody>
            <ErrorState error={overview.error} onRetry={overview.refetch} context="GET /api/budgets/overview" />
          </CardBody>
        ) : (
          <CardBody className="space-y-3">
            {/* Niveles depto — el techo org vive en la card destacada */}
            {(overview.data?.departments ?? []).map((d) => {
              const pct = usagePct(d.spent_cents, d.budget_cents);
              // Cutoff mirrors the enforcement/read path (AC-GOV-012,
              // edge amount=0): an explicit budget row of $0 IS a budget —
              // spent >= budget cuts off. No `> 0` guard here.
              const cutoff =
                d.budget_cents != null &&
                d.spent_cents >= d.budget_cents;
              return (
                <div key={d.department_id} className="rounded-lg border border-border-subtle p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
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

                  {/* Presupuestos por usuario (t_6b40c758, QA F3): la overview
                      anida los usuarios del depto; cada fila edita via
                      PUT /api/budgets/user/{id} con Σ usuarios ≤ depto. */}
                  {d.users.map((u) => {
                    const uPct = usagePct(u.spent_cents, u.budget_cents);
                    // Same rule as the dept level above (AC-GOV-012,
                    // edge amount=0): explicit $0 budget can cut off.
                    const uCutoff =
                      u.budget_cents != null &&
                      u.spent_cents >= u.budget_cents;
                    return (
                      <div
                        key={u.user_id}
                        className="mt-2 ml-4 rounded-lg border border-border-subtle bg-surface-alt/30 p-3 sm:ml-8"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex items-center gap-2">
                            <span aria-hidden="true" className="text-faint">└</span>
                            <p className="text-sm text-foreground">{u.display_name || u.email}</p>
                            <span className="text-xs text-muted">{u.email}</span>
                            {uCutoff ? <Badge tone="danger">Cutoff</Badge> : null}
                          </div>
                          <div className="flex items-center gap-4">
                            <p className="text-sm text-muted">
                              Limit <span className="font-semibold text-foreground">{usd(u.budget_cents)}</span>
                            </p>
                            <p className="text-sm text-muted">
                              Used <span className="font-semibold text-foreground">{usd(u.spent_cents)}</span>
                            </p>
                            {canEditUserBudgets ? (
                              <Button
                                variant="secondary"
                                size="sm"
                                title="PUT /api/budgets/user/{id} — requires budget.user.manage"
                                onClick={() =>
                                  openEdit({
                                    kind: "user",
                                    userId: u.user_id,
                                    name: u.display_name || u.email,
                                    currentCents: u.budget_cents,
                                  })
                                }
                              >
                                Edit
                              </Button>
                            ) : null}
                          </div>
                        </div>
                        {u.budget_cents != null ? (
                          <div className="mt-2">
                            <Progress pct={uPct} tone={uCutoff ? "danger" : uPct >= 80 ? "warning" : "accent"} />
                          </div>
                        ) : null}
                      </div>
                    );
                  })}

                  {/* Σ usuarios vs depto — espejo del Σ deptos vs org */}
                  {d.budget_cents != null ? (
                    <p className="mt-2 text-xs text-muted">
                      {usd(d.budget_cents - d.user_budgets_total_cents)} of the
                      department budget remains unallocated —{" "}
                      {usd(d.user_budgets_total_cents)} assigned to users.
                    </p>
                  ) : null}
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

      {/* Historial de periodos cerrados (spec 013 D-013-03, gap menor de la
          tarea: GET /api/budgets/history ahora tiene UI) */}
      <Card>
        <CardHeader
          title="Budget history"
          description="Closed periods only: budget vs spent per level, with the per-model breakdown of the window (GET /api/budgets/history)."
        />
        {history.loading ? (
          <CardBody className="space-y-3">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </CardBody>
        ) : history.error ? (
          history.error.status === 403 ? (
            <CardBody>
              <p className="text-xs text-muted">
                Only admins view the budget history.
              </p>
            </CardBody>
          ) : (
            <ErrorState error={history.error} onRetry={history.refetch} context="GET /api/budgets/history" />
          )
        ) : (history.data?.periods.length ?? 0) === 0 ? (
          <CardBody>
            <EmptyState
              title="No closed periods yet"
              description="History appears once a budget window closes (period end in the past)."
            />
          </CardBody>
        ) : (
          <CardBody className="space-y-4">
            {history.data!.periods.map((p) => (
              <div key={`${p.period_start}:${p.period_end}`} className="rounded-lg border border-border-subtle p-4">
                <p className="text-sm font-semibold text-foreground">
                  {p.period_start} → {p.period_end}
                </p>
                <div className="mt-2 space-y-1 text-xs">
                  {p.organization ? (
                    <p className="text-muted">
                      Organization — budget{" "}
                      <span className="font-semibold text-foreground">{usd(p.organization.budget_cents)}</span>, spent{" "}
                      <span className="font-semibold text-foreground">{usd(p.organization.spent_cents)}</span>
                    </p>
                  ) : null}
                  {p.departments.map((d) => (
                    <p key={d.department_id} className="text-muted">
                      └ {d.name ?? d.department_id} — budget{" "}
                      <span className="font-semibold text-foreground">{usd(d.budget_cents)}</span>, spent{" "}
                      <span className="font-semibold text-foreground">{usd(d.spent_cents)}</span>
                    </p>
                  ))}
                  {p.users.map((u) => (
                    <p key={u.user_id} className="text-muted">
                      └ {u.display_name ?? u.user_id} (user) — budget{" "}
                      <span className="font-semibold text-foreground">{usd(u.budget_cents)}</span>, spent{" "}
                      <span className="font-semibold text-foreground">{usd(u.spent_cents)}</span>
                    </p>
                  ))}
                  {p.by_model.length > 0 ? (
                    <p className="text-muted">
                      Models —{" "}
                      {p.by_model
                        .map((m) => `${m.model_name}: ${usd(m.spent_cents)}`)
                        .join(", ")}
                    </p>
                  ) : null}
                </div>
              </div>
            ))}
          </CardBody>
        )}
      </Card>

      {/* Diálogo de edición */}
      <Dialog
        open={edit !== null}
        onClose={() => setEdit(null)}
        title={`Edit ${edit?.name ?? ""} budget`}
        description={
          edit?.kind === "org"
            ? "Monthly limit in USD. Raising the organization budget is always allowed; lowering it below the spend already recorded asks for confirmation."
            : edit?.kind === "user"
              ? "Monthly limit in USD. The user budgets of a department must sum within the department budget."
              : "Monthly limit in USD. Department budgets must sum within the organization budget."
        }
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
