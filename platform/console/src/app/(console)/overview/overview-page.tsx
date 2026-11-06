"use client";

/**
 * Overview (spec 009 REQ-AC-010, mockup Admin): presupuesto org usado/restante,
 * tabla de presupuestos por departamento, monto sin asignar y política de
 * organización. Datos reales: /api/budgets/overview + /api/policies?level=org.
 */
import Link from "next/link";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api-client";
import type { BudgetOverview, PolicyList } from "@/lib/types";
import { centsToInput, usd, usagePct } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  ErrorState,
  Progress,
  Skeleton,
  Table,
  TableSkeleton,
  Td,
  Th,
  useToast,
} from "@/components/ui";
import { IconBudgets, IconPlus, IconRefresh } from "@/components/icons";
import { StructureWizard } from "@/components/structure-wizard";
import { useState } from "react";

export function OverviewPage() {
  const overview = useApi<BudgetOverview>("/api/budgets/overview");
  const policies = useApi<PolicyList>("/api/policies?level=org");
  const { push } = useToast();
  const [savingOrg, setSavingOrg] = useState(false);
  const [orgInput, setOrgInput] = useState<string | null>(null);
  // Spec 038 (REQ-DO-023): empty-state con CTA al wizard de estructura.
  const [wizardOpen, setWizardOpen] = useState(false);

  const org = overview.data?.organization;
  const hasOrgBudget = org?.budget_cents != null;

  async function saveOrgBudget() {
    if (orgInput === null) return;
    setSavingOrg(true);
    try {
      await api("/api/budgets/org", {
        method: "PUT",
        body: { amount_cents: Math.round(parseFloat(orgInput || "0") * 100), period: "monthly" },
      });
      push("success", "Organization budget updated.");
      setOrgInput(null);
      overview.refetch();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Update failed.";
      push("error", message);
    } finally {
      setSavingOrg(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* KPIs del presupuesto org */}
      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardBody>
            <p className="text-xs font-medium text-muted">
              Monthly organization budget
            </p>
            {overview.loading ? (
              <Skeleton className="mt-2 h-8 w-28" />
            ) : org ? (
              <p className="mt-1.5 text-2xl font-semibold tracking-tight text-foreground">
                {usd(org.budget_cents)}
              </p>
            ) : null}
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <p className="text-xs font-medium text-muted">Used this month</p>
            {overview.loading ? (
              <Skeleton className="mt-2 h-8 w-28" />
            ) : org ? (
              <>
                <p className="mt-1.5 text-2xl font-semibold tracking-tight text-foreground">
                  {usd(org.spent_cents)}
                </p>
                <div className="mt-3">
                  <Progress
                    pct={usagePct(org.spent_cents, org.budget_cents)}
                    tone={
                      org.cutoff
                        ? "danger"
                        : usagePct(org.spent_cents, org.budget_cents) >= 80
                          ? "warning"
                          : "accent"
                    }
                  />
                </div>
              </>
            ) : null}
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <p className="text-xs font-medium text-muted">Remaining budget</p>
            {overview.loading ? (
              <Skeleton className="mt-2 h-8 w-28" />
            ) : org ? (
              <p
                className={`mt-1.5 text-2xl font-semibold tracking-tight ${
                  org.cutoff ? "text-danger" : "text-foreground"
                }`}
              >
                {usd(org.remaining_cents)}
              </p>
            ) : null}
            {org?.cutoff ? (
              <Badge tone="danger" className="mt-2">
                Cutoff active
              </Badge>
            ) : null}
          </CardBody>
        </Card>
      </div>

      {/* Edición rápida del presupuesto org (org_admin) */}
      {!overview.loading && overview.data ? (
        <Card>
          <CardHeader
            title="Organization budget"
            description="Monthly limit for the whole organization. Department budgets must sum within it."
            actions={
              orgInput === null ? (
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!hasOrgBudget}
                  onClick={() => setOrgInput(centsToInput(org?.budget_cents))}
                >
                  Edit
                </Button>
              ) : (
                <>
                  <Button variant="ghost" size="sm" onClick={() => setOrgInput(null)}>
                    Cancel
                  </Button>
                  <Button variant="primary" size="sm" loading={savingOrg} onClick={saveOrgBudget}>
                    Save
                  </Button>
                </>
              )
            }
          />
          {hasOrgBudget ? (
            <CardBody>
              {orgInput === null ? (
                <p className="text-sm text-muted">
                  Current limit:{" "}
                  <span className="font-semibold text-foreground">
                    {usd(org?.budget_cents)}
                  </span>{" "}
                  per month ({org?.period}).
                </p>
              ) : (
                <div className="flex items-end gap-3">
                  <div className="w-44">
                    <label htmlFor="org-budget" className="mb-1.5 block text-xs font-medium text-foreground">
                      New monthly limit (USD)
                    </label>
                    <input
                      id="org-budget"
                      value={orgInput}
                      onChange={(e) => setOrgInput(e.target.value)}
                      inputMode="decimal"
                      className="w-full rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm transition-all duration-150 hover:border-foreground/25 focus:border-foreground/40 focus:ring-[3px] focus:ring-foreground/[0.08] focus:outline-none"
                    />
                  </div>
                </div>
              )}
            </CardBody>
          ) : (
            <EmptyState
              icon={<IconBudgets size={18} />}
              title="No organization budget set"
              description="Set the monthly organization budget before allocating department budgets."
              action={
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => setOrgInput("0")}
                >
                  Set budget
                </Button>
              }
            />
          )}
        </Card>
      ) : null}

      {/* Departamentos */}
      <Card>
        <CardHeader
          title="Department budgets"
          description="Monthly limits, usage and members per department."
          actions={
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={overview.refetch}
                aria-label="Refresh"
              >
                <IconRefresh size={14} />
              </Button>
              <Link href="/departments">
                <Button variant="secondary" size="sm">
                  <IconPlus size={13} /> Add department
                </Button>
              </Link>
            </>
          }
        />
        {overview.loading ? (
          <TableSkeleton rows={4} cols={4} />
        ) : overview.error ? (
          <ErrorState error={overview.error} onRetry={overview.refetch} context="GET /api/budgets/overview" />
        ) : (overview.data?.departments.length ?? 0) === 0 ? (
          <EmptyState
            title="No departments yet"
            description="Departments appear here once created, with their monthly budget once allocated."
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
                <Th className="w-36">Utilization</Th>
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
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
        {overview.data?.unassigned_cents != null ? (
          <p className="border-t border-border-subtle px-5 py-3 text-xs text-muted">
            {usd(overview.data.unassigned_cents)} remains unallocated
            {overview.data.unassigned_cents < 0 ? " — department budgets exceed the organization budget" : ""}.
          </p>
        ) : null}
      </Card>

      {/* Política de organización */}
      <Card>
        <CardHeader
          title="Organization policy"
          description="Active organization-level policies (deny-overrides combinator)."
          actions={
            <Link href="/organization">
              <Button variant="secondary" size="sm">
                Manage
              </Button>
            </Link>
          }
        />
        {policies.loading ? (
          <TableSkeleton rows={3} cols={2} />
        ) : policies.error ? (
          policies.error.status === 403 ? (
            <EmptyState
              title="Org policies are visible to organization admins"
              description="Your role does not include permission to view raw organization policies."
            />
          ) : (
            <ErrorState error={policies.error} onRetry={policies.refetch} context="GET /api/policies?level=org" />
          )
        ) : (policies.data?.policies.length ?? 0) === 0 ? (
          <EmptyState
            title="No active organization policies"
            description="Organization policies set the baseline that department and user policies can only tighten."
          />
        ) : (
          <ul className="divide-y divide-border-subtle">
            {policies.data!.policies.map((p) => (
              <li key={p.key} className="flex items-center justify-between gap-4 px-5 py-3">
                <p className="font-mono text-xs text-foreground">{p.key}</p>
                <div className="flex items-center gap-2">
                  <Badge tone="info">{JSON.stringify(p.value)}</Badge>
                  <span className="text-[10px] text-faint">v{p.version}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* Wizard de estructura (spec 038): CTA del empty-state */}
      <StructureWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onCompleted={() => overview.refetch()}
      />
    </div>
  );
}
