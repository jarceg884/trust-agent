"use client";

/**
 * Model Access (spec 009 REQ-AC-015, spec 018, spec 032): matriz del
 * catálogo con aprobaciones por nivel (GET /api/models/access), aprobar
 * (PUT /api/models/access) y revocar (POST /api/models/access/revoke).
 * Spec 032 (mandato Miller): TODA API conectada aporta sus modelos — la
 * matriz agrupa por fuente (sección "Central MaaS" + una sección por
 * conexión BYOK con su status y botón Refresh) y el header ofrece
 * "Refresh catalog" global (POST /api/models/catalog/refresh). Los
 * modelos que la API upstream ya no devuelve se muestran con badge
 * "Stale" y nunca son invocables. La aprobación de modelos ES policy de
 * gobernanza (spec 018, D-018-02): el bloque "Governance policy" muestra
 * el floor invariable approved_models_only y el allowed_models vigente.
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type {
  AccessModelRow,
  AccessOverview,
  BudgetOverview,
  CatalogRefreshSummary,
  ModelAccessPolicy,
  TestConnectionResult,
} from "@/lib/types";
import { usdPerMillion } from "@/lib/format";
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
  Select,
  Table,
  TableSkeleton,
  Td,
  Th,
  useToast,
} from "@/components/ui";
import { IconModels, IconPlus, IconRefresh } from "@/components/icons";

function ApprovalBadges({ row }: { row: AccessModelRow }) {
  if (row.approvals.length === 0) {
    return <span className="text-xs text-faint">Not approved</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {row.approvals.map((a, i) => (
        <Badge
          key={i}
          tone={a.level === "org" ? "success" : a.level === "dept" ? "info" : "neutral"}
        >
          {a.level}
          {a.mode === "byok" ? " · BYOK" : ""}
        </Badge>
      ))}
    </div>
  );
}

/** Catalog lifecycle badge (spec 032 D-032-02): stale = gone from the
 *  upstream API on the last refresh — never invocable, shown to the admin
 *  so the approval can be revoked. */
function CatalogStatusBadge({ status }: { status: string }) {
  if (status === "available") return null;
  const tone =
    status === "stale" ? "warning" : status === "removed" ? "danger" : "neutral";
  const label =
    status === "stale"
      ? "Stale — gone from the API"
      : status === "removed"
        ? "Removed"
        : status === "deprecated"
          ? "Deprecated"
          : status;
  return <Badge tone={tone}>{label}</Badge>;
}

/** Token count → "1M" / "198K" / "—" (MaaS Model List notation: K=1,000, M=1,000,000). */
function formatTokens(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toLocaleString("en-US")}M`;
  if (n >= 1_000) return `${(n / 1_000).toLocaleString("en-US")}K`;
  return n.toLocaleString("en-US");
}

/** Governance policy block (spec 018, D-018-02): the approval matrix is
 *  part of the governance policy — show the invariable floor and the live
 *  org-level allowed_models with the deny-overrides intersection. */
function GovernancePolicyBlock({ policy }: { policy: ModelAccessPolicy }) {
  const restricted = policy.allowed_models != null && policy.allowed_models.length > 0;
  return (
    <div className="mb-4 rounded-lg border border-border bg-surface-alt/40 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium">Governance policy</p>
        <Badge tone={policy.approved_models_only ? "success" : "warning"}>
          {policy.approved_models_only
            ? "Approved models only — enforced"
            : "approved_models_only disabled"}
        </Badge>
      </div>
      <p className="mt-2 text-xs text-muted">
        Model approvals are part of the governance policy. The effective set for a
        user is the intersection across levels — organization ∩ department ∩ user —
        further narrowed by <span className="font-mono">allowed_models</span>{" "}
        (deny-overrides: a denial at any level wins). BYOK models additionally
        require their connection to be allowed and active (spec 032).
      </p>
      <p className="mt-2 text-xs">
        <span className="font-medium">allowed_models (org):</span>{" "}
        {restricted ? (
          <span className="font-mono">
            {policy.allowed_models!.join(", ")}
          </span>
        ) : (
          <span className="text-faint">
            No additional restriction — every approved model is eligible.
          </span>
        )}
      </p>
    </div>
  );
}

/** One source group (central MaaS or one BYOK connection) with its own
 *  table (spec 032 D-032-06). */
function ModelGroupTable({
  title,
  subtitle,
  badge,
  rows,
  onApprove,
  onRevoke,
}: {
  title: string;
  subtitle?: string;
  badge?: React.ReactNode;
  rows: AccessModelRow[];
  onApprove: (m: AccessModelRow) => void;
  onRevoke: (m: AccessModelRow) => void;
}) {
  return (
    <section className="mb-6">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        {badge}
        {subtitle ? <span className="text-xs text-muted">{subtitle}</span> : null}
      </div>
      <Table>
        <thead>
          <tr>
            <Th>Model</Th>
            <Th>Provider</Th>
            <Th>Context</Th>
            <Th>Input price</Th>
            <Th>Output price</Th>
            <Th>Cache hit</Th>
            <Th>Approvals</Th>
            <Th className="w-44" />
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.model_name} className="hover:bg-surface-alt/50">
              <Td>
                <div className="flex items-center gap-2">
                  <div>
                    <p className="font-medium">{m.display_name}</p>
                    <p className="font-mono text-[11px] text-muted">{m.model_name}</p>
                  </div>
                  <CatalogStatusBadge status={m.catalog_status} />
                </div>
              </Td>
              <Td className="text-xs text-muted">{m.provider}</Td>
              <Td className="text-xs">
                <span title={m.max_output_tokens != null ? `Max output: ${formatTokens(m.max_output_tokens)} tokens` : "Max output not documented"}>
                  {formatTokens(m.context_tokens)}
                </span>
              </Td>
              <Td className="text-xs">{usdPerMillion(m.input_price_cents_per_m)}</Td>
              <Td className="text-xs">{usdPerMillion(m.output_price_cents_per_m)}</Td>
              <Td className="text-xs">{usdPerMillion(m.cache_read_price_cents_per_m)}</Td>
              <Td>
                <ApprovalBadges row={m} />
              </Td>
              <Td>
                <div className="flex justify-end gap-1.5">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => onApprove(m)}
                  >
                    Approve
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    disabled={!m.approvals.some((a) => a.level === "org")}
                    onClick={() => onRevoke(m)}
                  >
                    Revoke
                  </Button>
                </div>
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </section>
  );
}

export function ModelsPage() {
  const overview = useApi<AccessOverview>("/api/models/access");
  const budgets = useApi<BudgetOverview>("/api/budgets/overview");
  const { push } = useToast();

  const [approveFor, setApproveFor] = useState<AccessModelRow | null>(null);
  const [revokeFor, setRevokeFor] = useState<AccessModelRow | null>(null);
  const [level, setLevel] = useState<"org" | "dept" | "user">("org");
  const [departmentId, setDepartmentId] = useState("");
  const [userId, setUserId] = useState("");
  const [mode, setMode] = useState("central");
  const [submitting, setSubmitting] = useState(false);
  // Refresh target: "global" | connection_id (spec 032 D-032-03).
  const [refreshing, setRefreshing] = useState<string | null>(null);
  // Spec 043: test connection for BYOK connections.
  const [byokTestBusy, setByokTestBusy] = useState<string | null>(null);
  const [byokTestResults, setByokTestResults] = useState<Record<string, TestConnectionResult>>({});

  const departments = budgets.data?.departments ?? [];

  // Spec 032 (D-032-06): group the catalog by source — central MaaS plus
  // one section per BYOK connection (name + status + last refresh).
  // Spec 036 (D-036-06): provider-operated sources get their own sections
  // (platform-included options, marked by provider — NOT client BYOK).
  const models = overview.data?.models ?? [];
  const centralModels = models.filter((m) => m.source === "central");
  const providerGroups = new Map<
    string,
    { name: string; baseUrl: string | null; status: string | null; rows: AccessModelRow[] }
  >();
  for (const m of models) {
    if (m.source !== "provider" || !m.provider_id) continue;
    let group = providerGroups.get(m.provider_id);
    if (!group) {
      group = {
        name: m.provider_display_name ?? m.provider_source_name ?? m.provider_id,
        baseUrl: m.provider_base_url,
        status: m.provider_status,
        rows: [],
      };
      providerGroups.set(m.provider_id, group);
    }
    group.rows.push(m);
  }
  const byokGroups = new Map<
    string,
    { name: string; status: string | null; rows: AccessModelRow[] }
  >();
  for (const m of models) {
    if (m.source !== "byok" || !m.connection_id) continue;
    let group = byokGroups.get(m.connection_id);
    if (!group) {
      group = { name: m.connection_name ?? m.connection_id, status: m.connection_status, rows: [] };
      byokGroups.set(m.connection_id, group);
    }
    group.rows.push(m);
  }

  function openApprove(m: AccessModelRow) {
    setApproveFor(m);
    setLevel("org");
    // The access mode follows the model's source (spec 032/036): BYOK models
    // are approved as byok; provider-operated ones as provider; central as
    // central.
    setMode(m.source === "byok" ? "byok" : m.source === "provider" ? "provider" : "central");
  }

  async function refreshCatalog(target: string) {
    setRefreshing(target);
    try {
      const summary = await api<CatalogRefreshSummary>(
        "/api/models/catalog/refresh",
        {
          method: "POST",
          body: { connection_id: target === "global" ? null : target },
        },
      );
      let msg: string;
      if (summary.connections) {
        const conns = Object.values(summary.connections);
        const ok = conns.filter((c) => c.error === undefined);
        const unreachable = conns.length - ok.length;
        const discovered = ok.reduce((acc, c) => acc + (c.discovered ?? 0), 0);
        const stale = ok.reduce((acc, c) => acc + (c.stale ?? 0), 0);
        const provs = Object.values(summary.providers ?? {});
        const provOk = provs.filter((p) => p.error === undefined);
        const provDiscovered = provOk.reduce((acc, p) => acc + (p.discovered ?? 0), 0);
        const provStale = provOk.reduce((acc, p) => acc + (p.stale ?? 0), 0);
        msg =
          `Catalog refreshed — central: ${summary.central?.stale ?? 0} stale, ` +
          `${summary.central?.restored ?? 0} restored; ${discovered} discovered, ` +
          `${stale} stale across ${ok.length} connection(s)` +
          (unreachable > 0 ? `; ${unreachable} unreachable (skipped)` : "") +
          (provOk.length > 0
            ? `; providers: ${provDiscovered} discovered, ${provStale} stale across ${provOk.length} provider(s)`
            : "") +
          ".";
      } else {
        msg = `Connection refreshed — ${summary.discovered ?? 0} discovered, ${summary.stale ?? 0} stale.`;
      }
      push("success", msg);
      overview.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Refresh failed.");
    } finally {
      setRefreshing(null);
    }
  }

  // Spec 043: test connection for a BYOK LLM connection.
  async function testByokConnection(connectionId: string) {
    setByokTestBusy(connectionId);
    try {
      const result = await api<TestConnectionResult>(
        `/api/vault/connections/${connectionId}/test-connection`,
        { method: "POST", body: {} },
      );
      setByokTestResults((prev) => ({ ...prev, [connectionId]: result }));
      if (result.ok) {
        push("success", `Connection OK — ${result.count} models found (${result.latency_ms}ms).`);
      } else {
        push("error", `Connection failed: ${result.error}`);
      }
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Test failed.");
    } finally {
      setByokTestBusy(null);
    }
  }

  async function approve() {
    if (!approveFor) return;
    setSubmitting(true);
    try {
      await api("/api/models/access", {
        method: "PUT",
        body: {
          model_name: approveFor.model_name,
          level,
          department_id: level === "dept" ? departmentId || null : null,
          user_id: level === "user" ? userId || null : null,
          mode,
        },
      });
      push("success", `${approveFor.display_name} approved at ${level} level.`);
      setApproveFor(null);
      overview.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Approval failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function revoke() {
    if (!revokeFor) return;
    setSubmitting(true);
    try {
      await api("/api/models/access/revoke", {
        method: "POST",
        body: {
          model_name: revokeFor.model_name,
          level: "org",
          department_id: null,
          user_id: null,
        },
      });
      push("success", `Org-level approval for ${revokeFor.display_name} revoked.`);
      setRevokeFor(null);
      overview.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Revoke failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Model catalog & approvals"
        description={
          (overview.data?.byok_allowed ?? true)
            ? "Every connected API contributes its models (central MaaS + your BYOK connections). Only approved models can be used. Pricing in USD per million tokens. Approvals cascade: org → department → user."
            : "Models from the platform providers enabled for your organization. Only approved models can be used. Pricing in USD per million tokens. Approvals cascade: org → department → user."
        }
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              loading={refreshing === "global"}
              disabled={refreshing !== null}
              onClick={() => refreshCatalog("global")}
            >
              <IconRefresh size={13} /> Refresh catalog
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={overview.data?.models.length ? false : true}
              onClick={() => {
                const first = overview.data?.models.find((m) => m.approvals.length === 0);
                if (first) {
                  openApprove(first);
                } else {
                  push("error", "Pick a model from the table to approve it.");
                }
              }}
            >
              <IconPlus size={13} /> Approve model
            </Button>
          </div>
        }
      />
      {overview.loading ? (
        <TableSkeleton rows={6} cols={5} />
      ) : overview.error ? (
        overview.error.status === 403 ? (
          <EmptyState
            icon={<IconModels size={18} />}
            title="Only admins view the Model Access matrix"
            description="Your role does not include permission to manage model approvals."
          />
        ) : (
          <ErrorState error={overview.error} onRetry={overview.refetch} context="GET /api/models/access" />
        )
      ) : models.length === 0 ? (
        <EmptyState
          title="The model catalog is empty"
          description="Refresh the catalog to discover the models of the connected APIs, or add an LLM connection under Credentials."
        />
      ) : (
        <>
          <GovernancePolicyBlock policy={overview.data!.policy} />
          {centralModels.length > 0 ? (
            <ModelGroupTable
              title="Central MaaS"
              subtitle="Platform gateway (Huawei Cloud ModelArts Studio) — shared, provider-operated."
              badge={<Badge tone="accent">central</Badge>}
              rows={centralModels}
              onApprove={openApprove}
              onRevoke={setRevokeFor}
            />
          ) : null}
          {[...providerGroups.entries()].map(([pid, group]) => (
            <ModelGroupTable
              key={pid}
              title={group.name}
              subtitle={
                (group.baseUrl ? `${group.baseUrl} — ` : "") +
                "model provider registered by the platform provider and enabled for your organization (platform-included)."
              }
              badge={
                <div className="flex items-center gap-1.5">
                  <Badge tone="accent">provider</Badge>
                  <Badge tone={group.status === "active" ? "success" : "danger"}>
                    {group.status ?? "unknown"}
                  </Badge>
                </div>
              }
              rows={group.rows}
              onApprove={openApprove}
              onRevoke={setRevokeFor}
            />
          ))}
          {[...byokGroups.entries()].map(([cid, group]) => (
            <ModelGroupTable
              key={cid}
              title={group.name}
              subtitle={
                group.rows[0]?.last_seen_at
                  ? `Last seen upstream: ${new Date(group.rows[0].last_seen_at!).toLocaleString("en-US")}`
                  : undefined
              }
              badge={
                <div className="flex items-center gap-1.5">
                  <Badge tone="info">BYOK connection</Badge>
                  <Badge tone={group.status === "active" ? "success" : "danger"}>
                    {group.status ?? "unknown"}
                  </Badge>
                  <Button
                    variant="primary"
                    size="sm"
                    loading={byokTestBusy === cid}
                    disabled={byokTestBusy !== null}
                    onClick={() => testByokConnection(cid)}
                  >
                    Test connection
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={refreshing === cid}
                    disabled={refreshing !== null}
                    onClick={() => refreshCatalog(cid)}
                  >
                    <IconRefresh size={13} /> Refresh
                  </Button>
                </div>
              }
              rows={group.rows}
              onApprove={openApprove}
              onRevoke={setRevokeFor}
            />
          ))}
        </>
      )}

      {/* Aprobar */}
      <Dialog
        open={approveFor !== null}
        onClose={() => setApproveFor(null)}
        title={`Approve ${approveFor?.display_name ?? ""}`}
        description="Choose the governance level and the access mode."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setApproveFor(null)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" loading={submitting} onClick={approve}>
              Approve model
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Level" htmlFor="approve-level" hint="Org: everyone. Department: members of one department. User: a single user.">
            <Select id="approve-level" value={level} onChange={(e) => setLevel(e.target.value as "org" | "dept" | "user")}>
              <option value="org">Organization</option>
              <option value="dept">Department</option>
              <option value="user">User</option>
            </Select>
          </Field>
          {level === "dept" ? (
            <Field label="Department" htmlFor="approve-dept">
              <Select id="approve-dept" value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
                <option value="">Select a department…</option>
                {departments.map((d) => (
                  <option key={d.department_id} value={d.department_id}>
                    {d.name}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          {level === "user" ? (
            <Field
              label="User ID"
              htmlFor="approve-user"
              hint="Paste the user's UUID (the user directory API is pending — see Users & Roles)."
            >
              <Input
                id="approve-user"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                placeholder="00000000-0000-0000-0000-000000000000"
                className="font-mono text-xs"
              />
            </Field>
          ) : null}
          <Field
            label="Access mode"
            htmlFor="approve-mode"
            hint={
              approveFor?.source === "byok"
                ? "BYOK: this model is served by the connection's own API key — it stays invocable only while the connection is allowed and active."
                : (overview.data?.byok_allowed ?? true)
                  ? "Central: Huawei Cloud MaaS via the platform gateway. BYOK: the user's own key."
                  : "Central: Huawei Cloud MaaS via the platform gateway. Provider: platform-included model sources."
            }
          >
            <Select id="approve-mode" value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="central">Central (platform gateway)</option>
              {(overview.data?.byok_allowed ?? true) && (
                <option value="byok">BYOK (user&apos;s own key)</option>
              )}
            </Select>
          </Field>
        </div>
      </Dialog>

      {/* Revocar */}
      <ConfirmDialog
        open={revokeFor !== null}
        onClose={() => setRevokeFor(null)}
        onConfirm={revoke}
        loading={submitting}
        title={`Revoke org approval — ${revokeFor?.display_name ?? ""}`}
        message="Users will no longer be able to use this model at the organization level (department/user approvals, if any, remain). Usage attempts will be denied and audited."
        confirmLabel="Revoke approval"
      />
    </Card>
  );
}
