"use client";

/**
 * Detalle interactivo del tenant (spec 014 REQ-PC-013/014/017):
 * - estado canónico + counts + presupuesto/gasto (datos reales),
 * - healthcheck en vivo (GET /api/admin/tenants/{slug}/healthcheck),
 * - suspend/reactivate con ConfirmDialog (transaccional + auditado),
 * - link a la Admin Console del tenant (D-PC-07).
 *
 * CORRECCIÓN MILLER (t_360a752e, 2026-10-26): el "Hermes download link"
 * fue REMOVIDO de aquí — el proveedor solo crea/gestiona tenants; el link
 * de descarga lo genera y envía el ADMIN DEL CLIENTE desde su Admin
 * Console (platform/console, sección "Hermes Download").
 */
import { useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api-client";
import type {
  HealthcheckResult,
  TenantListItem,
  TenantState,
  TenantStatusResponse,
} from "@/lib/types";
import { adminConsoleLoginUrl } from "@/lib/config";
import { usd, dateTime, usagePct } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  CopyButton,
  Progress,
  useToast,
} from "@/components/ui";
import {
  IconExternal,
  IconPulse,
} from "@/components/icons";

export function TenantDetail({ tenant }: { tenant: TenantListItem }) {
  const { push } = useToast();
  const [status, setStatus] = useState(tenant.status);
  const [orgBudgetCents, setOrgBudgetCents] = useState(tenant.org_budget_cents);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<
    "suspend" | "reactivate" | "activate" | null
  >(null);
  // Spec 026: budget inicial (USD) para la activación de un tenant pending.
  const [budgetUsd, setBudgetUsd] = useState("100");
  const [health, setHealth] = useState<HealthcheckResult | null>(null);
  const [healthBusy, setHealthBusy] = useState(false);
  const [healthError, setHealthError] = useState<string | null>(null);

  async function changeStatus(action: "suspend" | "reactivate") {
    setBusy(true);
    try {
      const res = await api<TenantStatusResponse>(
        `/api/admin/tenants/${tenant.slug}/${action}`,
        { method: "POST" },
      );
      setStatus(res.tenant.status);
      push(
        "success",
        action === "suspend"
          ? `Tenant "${tenant.name}" suspended — access is cut immediately.`
          : `Tenant "${tenant.name}" reactivated.`,
      );
    } catch (cause) {
      const message =
        cause instanceof ApiError
          ? `${cause.message} (${cause.reason})`
          : "The status change failed.";
      push("error", message);
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  // Spec 026 (REQ-SS-003): activar un tenant pending_activation + asignar
  // budget inicial. UNA transacción en el control plane (status + budget +
  // audit tenant.activated/budget.assigned + notificación a los org_admin).
  async function activateTenant() {
    const amount = Number.parseFloat(budgetUsd);
    if (!Number.isFinite(amount) || amount <= 0) {
      push("error", "Enter a positive initial budget in USD.");
      return;
    }
    setBusy(true);
    try {
      const res = await api<
        TenantStatusResponse & {
          tenant: TenantState & { org_budget_cents?: number };
          notified_org_admins?: number;
        }
      >(`/api/admin/tenants/${tenant.slug}/activate`, {
        method: "POST",
        // AC-26-008 (t_d139d01b): `api()` ya hace JSON.stringify(body) —
        // pasar el objeto PLANO. El doble stringify mandaba un string JSON
        // y el control plane respondía 422 validation_failed.
        body: { initial_budget_usd: amount },
      });
      setStatus(res.tenant.status);
      setOrgBudgetCents(
        res.tenant.org_budget_cents ?? Math.round(amount * 100),
      );
      push(
        "success",
        `"${tenant.name}" is now active with an initial budget of USD ${amount.toFixed(2)}.${
          res.notified_org_admins
            ? ` ${res.notified_org_admins} admin(s) notified.`
            : ""
        }`,
      );
    } catch (cause) {
      const message =
        cause instanceof ApiError
          ? `${cause.message} (${cause.reason})`
          : "The activation failed.";
      push("error", message);
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  async function runHealthcheck() {
    setHealthBusy(true);
    setHealthError(null);
    try {
      const res = await api<HealthcheckResult>(
        `/api/admin/tenants/${tenant.slug}/healthcheck`,
      );
      setHealth(res);
    } catch (cause) {
      setHealthError(
        cause instanceof ApiError
          ? `${cause.message} (${cause.reason})`
          : "The healthcheck failed.",
      );
    } finally {
      setHealthBusy(false);
    }
  }

  const pct = usagePct(tenant.spent_cents_period, orgBudgetCents);
  const adminUrl = adminConsoleLoginUrl(tenant.slug);

  return (
    <>
      <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs text-muted">
            <Link href="/tenants" className="hover:text-foreground">
              Tenants
            </Link>{" "}
            / <span className="font-mono">{tenant.slug}</span>
          </p>
          <h1 className="mt-1 flex items-center gap-3 text-xl font-semibold tracking-tight text-foreground">
            {tenant.name}
            {status === "pending_activation" ? (
              <Badge tone="warning">Pending activation</Badge>
            ) : status === "active" ? (
              <Badge tone="success">Active</Badge>
            ) : (
              <Badge tone="danger">Suspended</Badge>
            )}
          </h1>
        </div>
        <div className="flex items-center gap-2">
          {status === "pending_activation" ? (
            <Button
              variant="primary"
              size="sm"
              onClick={() => setConfirm("activate")}
              disabled={busy}
            >
              Activate organization
            </Button>
          ) : status === "active" ? (
            <Button
              variant="danger"
              size="sm"
              onClick={() => setConfirm("suspend")}
              disabled={busy}
            >
              Suspend tenant
            </Button>
          ) : (
            <Button
              variant="primary"
              size="sm"
              onClick={() => setConfirm("reactivate")}
              disabled={busy}
            >
              Reactivate tenant
            </Button>
          )}
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Estado + stats */}
        <Card className="lg:col-span-2">
          <CardHeader
            title="Organization"
            description="Canonical state from the control plane."
          />
          <CardBody>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-4 text-sm md:grid-cols-3">
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">Slug</dt>
                <dd className="mt-1 font-mono text-xs">{tenant.slug}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">
                  Keycloak realm
                </dt>
                <dd className="mt-1 font-mono text-xs">{tenant.oidc_realm}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">
                  Created
                </dt>
                <dd className="mt-1 text-xs">{dateTime(tenant.created_at)}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">Users</dt>
                <dd className="mt-1 tabular-nums">{tenant.counts.users}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">
                  Departments
                </dt>
                <dd className="mt-1 tabular-nums">{tenant.counts.departments}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">
                  OIDC issuer
                </dt>
                <dd className="mt-1 break-all font-mono text-[11px]">
                  {tenant.oidc_issuer_url}
                </dd>
              </div>
            </dl>
          </CardBody>
        </Card>

        {/* Presupuesto */}
        <Card>
          <CardHeader title="Budget (period)" description="Organization level." />
          <CardBody className="space-y-3">
            <div className="flex items-baseline justify-between">
              <span className="text-sm text-muted">Spent</span>
              <span className="text-lg font-semibold tabular-nums">
                {usd(tenant.spent_cents_period)}
              </span>
            </div>
            <Progress
              pct={pct}
              tone={pct >= 90 ? "danger" : pct >= 70 ? "warning" : "accent"}
            />
            <div className="flex items-baseline justify-between text-xs text-muted">
              <span>of {usd(orgBudgetCents)} / month</span>
              <span className="tabular-nums">{pct}%</span>
            </div>
          </CardBody>
        </Card>

        {/* Activación (spec 026 REQ-SS-006): solo tenants pending_activation */}
        {status === "pending_activation" ? (
          <Card>
            <CardHeader
              title="Activate this organization"
              description="Self-signup customer waiting for activation. Assign an initial budget to turn everything on."
            />
            <CardBody className="space-y-4">
              <div>
                <label
                  htmlFor="initial-budget"
                  className="mb-1.5 block text-sm font-medium text-foreground"
                >
                  Initial budget (USD / month)
                </label>
                <input
                  id="initial-budget"
                  type="number"
                  min="1"
                  step="0.01"
                  value={budgetUsd}
                  onChange={(e) => setBudgetUsd(e.target.value)}
                  className="w-full rounded-lg border border-border-subtle bg-surface px-3.5 py-2.5 text-sm text-foreground tabular-nums focus:border-accent focus:outline-none"
                />
                <p className="mt-1.5 text-xs leading-relaxed text-muted">
                  One transaction: status → active, org budget assigned,
                  audited (tenant.activated + budget.assigned) and the
                  organization admins are notified.
                </p>
              </div>
              <Button
                variant="primary"
                size="sm"
                onClick={() => setConfirm("activate")}
                disabled={busy}
              >
                Activate with this budget
              </Button>
            </CardBody>
          </Card>
        ) : null}

        {/* Healthcheck en vivo */}
        <Card className="lg:col-span-2">
          <CardHeader
            title="Healthcheck"
            description="Live verification: database, IdP discovery/JWKS, admin user, budget, policies, departments, models, audit chain."
            actions={
              <Button
                variant="secondary"
                size="sm"
                onClick={runHealthcheck}
                loading={healthBusy}
              >
                <IconPulse size={13} /> Run healthcheck
              </Button>
            }
          />
          <CardBody>
            {healthError ? (
              <p
                role="alert"
                className="rounded-lg border border-danger/25 bg-danger-soft px-3.5 py-2.5 text-xs text-danger"
              >
                {healthError}
              </p>
            ) : health ? (
              <div className="space-y-2">
                <Badge tone={health.status === "ok" ? "success" : "warning"}>
                  status: {health.status}
                </Badge>
                <ul className="divide-y divide-border-subtle text-sm">
                  {health.checks.map((c) => (
                    <li key={c.name} className="flex items-center gap-2 py-1.5">
                      <span
                        aria-hidden="true"
                        className={`size-1.5 shrink-0 rounded-full ${
                          c.status === "ok" ? "bg-success" : "bg-warning"
                        }`}
                      />
                      <span className="font-mono text-xs">{c.name}</span>
                      <span className="ml-auto text-xs text-muted">
                        {c.status}
                        {c.detail ? ` — ${c.detail}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-xs text-muted">
                Run the healthcheck to verify this tenant end-to-end against the
                live environment.
              </p>
            )}
          </CardBody>
        </Card>

        {/* Entradas del tenant */}
        <Card>
          <CardHeader
            title="Entry points"
            description="Links that bind users to this organization."
          />
          <CardBody className="space-y-5">
            <div>
              <p className="text-xs font-medium uppercase tracking-wider text-muted">
                Admin Console
              </p>
              <p className="mt-1 text-xs leading-relaxed text-muted">
                The customer&apos;s admins sign in here (SSO + MFA of their realm).
              </p>
              <div className="mt-2 flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate rounded-lg border border-border-subtle bg-surface-alt px-3 py-2 font-mono text-[11px]">
                  {adminUrl}
                </code>
                <CopyButton value={adminUrl} label="Copy" />
              </div>
              <a href={adminUrl} target="_blank" rel="noreferrer" className="mt-2 inline-block">
                <Button variant="secondary" size="sm">
                  <IconExternal size={13} /> Open Admin Console
                </Button>
              </a>
            </div>
            {/* CORRECCIÓN MILLER (t_360a752e): sin "Hermes download link"
                aquí — el admin del cliente lo envía desde SU Admin Console
                (sección "Hermes Download"). El proveedor solo crea tenants. */}
          </CardBody>
        </Card>
      </div>

      <ConfirmDialog
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        onConfirm={() => {
          if (confirm === "activate") void activateTenant();
          else if (confirm) void changeStatus(confirm);
        }}
        loading={busy}
        danger={confirm === "suspend"}
        title={
          confirm === "activate"
            ? "Activate this organization?"
            : confirm === "suspend"
              ? "Suspend this tenant?"
              : "Reactivate this tenant?"
        }
        confirmLabel={
          confirm === "activate"
            ? "Activate"
            : confirm === "suspend"
              ? "Suspend"
              : "Reactivate"
        }
        message={
          confirm === "activate"
            ? `"${tenant.name}" will become active with an initial budget of USD ${Number.parseFloat(budgetUsd || "0").toFixed(2)}. Its admins will be notified and everything (agents, models, devices, spend) will start working against that budget. This action is audited.`
            : confirm === "suspend"
              ? `Every request from "${tenant.name}" will be denied immediately (403 tenant_suspended). The Keycloak realm stays intact for reactivation. This action is audited.`
              : `"${tenant.name}" will return to active status and its users will regain access. This action is audited.`
        }
      />
    </>
  );
}
