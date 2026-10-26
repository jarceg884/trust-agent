/**
 * Tarjetas del dashboard gobernado (REQ-DTB-004). Presentacional puro:
 * los datos llegan ya fetchados server-side desde la API real.
 */
import type {
  BudgetStatus,
  EffectivePolicy,
  GovernanceNotification,
  MyDevice,
  WhoAmI,
} from "@/lib/control-plane";
import {
  formatDateTime,
  formatRelative,
  formatUsd,
  policyLabel,
  policyValue,
} from "@/lib/format";
import { Badge, Card, EmptyState, LevelTag, ProgressBar, StatusDot } from "./ui";

export function BudgetCard({ budget }: { budget: BudgetStatus }) {
  const levels = [budget.user, budget.department, budget.organization];
  return (
    <Card
      title="Budget"
      icon={<StatusDot tone={budget.cutoff ? "danger" : "accent"} />}
      action={
        <Badge tone={budget.cutoff ? "danger" : "neutral"}>
          {budget.cutoff ? "Cutoff active" : "Managed by your organization"}
        </Badge>
      }
    >
      {budget.cutoff && (
        <p className="mb-4 rounded-lg border border-[#3a1a1f] bg-accent-soft px-3.5 py-2.5 text-sm text-[#ff8a95]">
          Spending is cut off at the {budget.cutoff_level} level. Ask your
          organization admin to raise the budget or wait for the next period.
        </p>
      )}
      <div className="space-y-5">
        {levels.map((level) => (
          <div key={level.level}>
            <div className="mb-1.5 flex items-baseline justify-between gap-3">
              <LevelTag level={level.level} />
              <span className="text-sm text-muted">
                {level.budget_cents === null ? (
                  "No budget set"
                ) : (
                  <>
                    <span className="font-semibold text-foreground">
                      {formatUsd(level.remaining_cents ?? 0)}
                    </span>{" "}
                    left of {formatUsd(level.budget_cents)}
                  </>
                )}
              </span>
            </div>
            <ProgressBar
              spentCents={level.spent_cents}
              budgetCents={level.budget_cents}
              cutoff={level.cutoff}
            />
            <p className="mt-1 text-xs text-muted">
              {formatUsd(level.spent_cents)} spent this {level.period}
              {level.cutoff ? " — cutoff" : ""}
            </p>
          </div>
        ))}
      </div>
    </Card>
  );
}

const INVARIABLE_KEYS = new Set([
  "require_mfa",
  "approved_models_only",
  "explicit_shared_agent_grants",
  "scoped_credentials",
]);

export function PoliciesCard({ policies }: { policies: EffectivePolicy }) {
  const entries = Object.entries(policies);
  return (
    <Card
      title="Organization policies active"
      icon={<StatusDot tone="success" />}
    >
      {entries.length === 0 ? (
        <EmptyState>No policies apply to your account yet.</EmptyState>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {entries.map(([key, value]) => (
            <li key={key} className="flex items-center justify-between gap-3 py-2.5">
              <div className="min-w-0">
                <p className="truncate text-sm text-foreground">
                  {policyLabel(key)}
                </p>
                {INVARIABLE_KEYS.has(key) && (
                  <p className="text-xs text-muted">
                    Provider floor — cannot be relaxed
                  </p>
                )}
              </div>
              <span className="shrink-0 font-mono text-xs text-muted">
                {policyValue(value)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function ModelsCard({ models }: { models: string[] }) {
  return (
    <Card
      title="Approved models"
      icon={<StatusDot tone={models.length > 0 ? "success" : "warning"} />}
      action={<Badge>{models.length} available</Badge>}
    >
      {models.length === 0 ? (
        <EmptyState>
          Your organization has not approved any models for you yet.
        </EmptyState>
      ) : (
        <div className="flex flex-wrap gap-2">
          {models.map((model) => (
            <span
              key={model}
              className="rounded-lg border border-border-subtle bg-surface px-3 py-1.5 font-mono text-xs text-foreground"
            >
              {model}
            </span>
          ))}
        </div>
      )}
      <p className="mt-4 text-xs text-muted">
        Agent sessions can only use models approved by your organization
        (Huawei Cloud MaaS via the organization gateway).
      </p>
    </Card>
  );
}

const deviceTone: Record<MyDevice["status"], "success" | "warning" | "danger" | "neutral"> = {
  active: "success",
  pending: "warning",
  rejected: "danger",
  revoked: "danger",
};

export function DevicesCard({
  devices,
  thisDeviceFingerprint,
}: {
  devices: MyDevice[];
  thisDeviceFingerprint: string | null;
}) {
  return (
    <Card title="Your devices" icon={<StatusDot tone="accent" />}>
      {devices.length === 0 ? (
        <EmptyState>
          No devices enrolled yet. Enroll this device to use governed agent
          sessions.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {devices.map((device) => (
            <li
              key={device.id}
              className="flex items-center justify-between gap-3 py-3"
            >
              <div className="min-w-0">
                <p className="truncate text-sm text-foreground">
                  {device.device_name ?? "Unnamed device"}
                  {device.fingerprint === thisDeviceFingerprint && (
                    <span className="ml-2 rounded bg-panel-hover px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted">
                      This device
                    </span>
                  )}
                </p>
                <p className="text-xs text-muted">
                  {device.platform ?? "unknown"} · last seen{" "}
                  {formatRelative(device.last_seen_at)} · enrolled{" "}
                  {formatDateTime(device.enrolled_at)}
                </p>
              </div>
              <Badge tone={deviceTone[device.status]}>{device.status}</Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function NotificationsCard({
  notifications,
  markReadAction,
}: {
  notifications: GovernanceNotification[];
  markReadAction: (formData: FormData) => Promise<void>;
}) {
  const unread = notifications.filter((n) => !n.read_at);
  return (
    <Card
      title="Governance notifications"
      icon={<StatusDot tone={unread.length > 0 ? "warning" : "neutral"} />}
      action={
        unread.length > 0 ? <Badge tone="warning">{unread.length} new</Badge> : undefined
      }
    >
      {notifications.length === 0 ? (
        <EmptyState>No governance notifications.</EmptyState>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {notifications.slice(0, 6).map((n) => (
            <li key={n.id} className="flex items-start justify-between gap-3 py-3">
              <div className="min-w-0">
                <p className="text-sm text-foreground">
                  {!n.read_at && (
                    <span className="mr-2 inline-block h-1.5 w-1.5 rounded-full bg-accent align-middle" />
                  )}
                  {n.title}
                </p>
                {n.body && <p className="mt-0.5 text-xs text-muted">{n.body}</p>}
                <p className="mt-0.5 text-xs text-muted">
                  {formatRelative(n.created_at)}
                </p>
              </div>
              {!n.read_at && (
                <form action={markReadAction}>
                  <input type="hidden" name="notificationId" value={n.id} />
                  <button
                    type="submit"
                    className="shrink-0 rounded-md border border-border-subtle px-2.5 py-1 text-xs text-muted transition hover:bg-panel-hover hover:text-foreground"
                  >
                    Mark read
                  </button>
                </form>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function IdentityHeader({ me }: { me: WhoAmI }) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold">
          {me.display_name ?? me.email}
        </h1>
        <p className="mt-0.5 text-sm text-muted">
          {me.tenant_slug} · SSO + MFA ·{" "}
          {me.roles.map((r) => r.role_key).join(", ") || "member"}
        </p>
      </div>
      <Badge tone="success">
        <StatusDot tone="success" /> Connected to organization
      </Badge>
    </header>
  );
}
