"use client";

/**
 * Usage (spec 035, mandato Miller): el admin ve los REQUESTS y CONSUMOS
 * por usuario sobre el ledger real (GET /api/usage — agregado por usuario;
 * GET /api/usage/requests — drill-down paginado por request). Nada
 * simulado: cada cifra viene del spend_ledger del control plane.
 *
 * - Período seleccionable: Today / Last 7 days / Last 30 days / Custom.
 * - Filtros por modelo (los observados en la ventana) y modo
 *   (All / Central / BYOK).
 * - Presencia en vivo junto al consumo (GET /api/devices/presence, spec
 *   027): punto verde si el usuario tiene dispositivo con heartbeat
 *   reciente, con el tipo (desktop/cli).
 * - Auto-refresco en vivo (spec 042 REQ-PE-007/AC-007-2, momento A1):
 *   summary, drill-down y presencia se re-fetchean cada 15 s EN SILENCIO
 *   (patrón de la franja de presencia de Devices, spec 027) — el admin ve
 *   los tokens subir sin F5 ni clic, con badge "· live". Pausado con la
 *   pestaña oculta (document.hidden); al volver, refresco inmediato.
 * - Export CSV por usuario y por request (GET /api/usage/export —
 *   auditado server-side como usage.exported).
 */
import { useEffect, useMemo, useState } from "react";
import { useApi } from "@/hooks/use-api";
import { downloadBlob } from "@/lib/api-client";
import { usd } from "@/lib/format";
import type {
  PresenceResponse,
  UsageRequestsPage,
  UsageSummary,
  UsageUserRow,
} from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Dialog,
  EmptyState,
  ErrorState,
  Input,
  Pagination,
  Select,
  Table,
  TableSkeleton,
  Td,
  Th,
  useToast,
} from "@/components/ui";
import { IconDownload, IconRefresh } from "@/components/icons";

type PeriodKind = "today" | "7d" | "30d" | "custom";

const PERIODS: { value: PeriodKind; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "custom", label: "Custom" },
];

const MODES: { value: string; label: string }[] = [
  { value: "", label: "All modes" },
  { value: "central", label: "Central" },
  { value: "byok", label: "BYOK" },
];

const DRILLDOWN_PAGE_SIZE = 25;

/** Ciclo de auto-refresco en vivo (mismo ritmo que la franja Devices). */
const USAGE_POLL_MS = 15_000;

function todayIso(daysAgo = 0): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

/** Indicador de presencia en vivo (patrón del ping de devices-page). */
function PresenceDot({
  online,
  deviceTypes,
}: {
  online: boolean;
  deviceTypes: string[];
}) {
  if (!online) {
    return (
      <span
        title="Offline (no recent heartbeat)"
        className="inline-flex h-2 w-2 rounded-full bg-border-strong"
      />
    );
  }
  const kinds = deviceTypes.length ? deviceTypes.join(", ") : "device";
  return (
    <span
      title={`Online now (${kinds})`}
      className="relative inline-flex h-2 w-2"
    >
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-60 motion-reduce:animate-none" />
      <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
    </span>
  );
}

export function UsagePage() {
  const [period, setPeriod] = useState<PeriodKind>("30d");
  const [from, setFrom] = useState(todayIso(29));
  const [to, setTo] = useState(todayIso());
  const [model, setModel] = useState("");
  const [mode, setMode] = useState("");
  const [drillFor, setDrillFor] = useState<UsageUserRow | null>(null);
  const [drillOffset, setDrillOffset] = useState(0);
  const [exporting, setExporting] = useState(false);
  const { push } = useToast();

  const query = useMemo(() => {
    const params = new URLSearchParams({ period });
    if (period === "custom") {
      params.set("from", from);
      params.set("to", to);
    }
    if (model) params.set("model", model);
    if (mode) params.set("mode", mode);
    return `/api/usage?${params.toString()}`;
  }, [period, from, to, model, mode]);

  const summary = useApi<UsageSummary>(query, { silentRefetch: true });
  const presence = useApi<PresenceResponse>("/api/devices/presence", {
    silentRefetch: true,
  });

  const drillQuery = drillFor
    ? `/api/usage/requests?${(() => {
        const params = new URLSearchParams({ period, limit: String(DRILLDOWN_PAGE_SIZE), offset: String(drillOffset) });
        if (period === "custom") {
          params.set("from", from);
          params.set("to", to);
        }
        if (drillFor.user_id) params.set("user_id", drillFor.user_id);
        if (model) params.set("model", model);
        if (mode) params.set("mode", mode);
        return params.toString();
      })()}`
    : null;
  const drill = useApi<UsageRequestsPage>(drillQuery, { silentRefetch: true });

  // Auto-refresco en vivo (REQ-PE-007/AC-007-2, patrón REQ-027-06 de la
  // franja de Devices): cada 15 s se re-fetchean summary, drill-down y
  // presencia. `silentRefetch` hace que sea un re-fetch EN SILENCIO — la
  // tabla previa queda a la vista (cero skeleton/flicker) y los filtros y
  // la paginación del drill-down viven en estado de la página, así que el
  // ciclo no los toca. Pausado con la pestaña oculta (document.hidden);
  // al volver a ser visible se refresca al momento.
  useEffect(() => {
    const tick = () => {
      if (document.hidden) return;
      summary.refetch();
      drill.refetch();
      presence.refetch();
    };
    const timer = window.setInterval(tick, USAGE_POLL_MS);
    const onVisibility = () => {
      if (!document.hidden) tick();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Presencia por usuario: online si CUALQUIER dispositivo activo tiene
  // heartbeat reciente; se listan los tipos conectados (desktop/cli).
  const presenceByUser = useMemo(() => {
    const map = new Map<string, { online: boolean; types: Set<string> }>();
    for (const d of presence.data?.devices ?? []) {
      if (!d.online) continue;
      const entry = map.get(d.user_id) ?? { online: true, types: new Set<string>() };
      entry.online = true;
      entry.types.add(d.device_type);
      map.set(d.user_id, entry);
    }
    return map;
  }, [presence.data]);

  // Modelos observados en la ventana (opciones del filtro).
  const observedModels = useMemo(() => {
    const names = new Set<string>();
    for (const u of summary.data?.users ?? []) {
      for (const m of u.models) names.add(m.model_name);
    }
    return Array.from(names).sort();
  }, [summary.data]);

  async function exportCsv(level: "users" | "requests") {
    setExporting(true);
    try {
      const params = new URLSearchParams({ period, level });
      if (period === "custom") {
        params.set("from", from);
        params.set("to", to);
      }
      if (model) params.set("model", model);
      if (mode) params.set("mode", mode);
      const range =
        period === "custom"
          ? `${from}_${to}`
          : `${summary.data?.period.from ?? "window"}_${summary.data?.period.to ?? "window"}`;
      await downloadBlob(
        `/api/usage/export?${params.toString()}`,
        `usage-${level}-${range}.csv`,
      );
      push("success", `Usage CSV (${level}) downloaded. The export is audited.`);
    } catch (cause) {
      push("error", cause instanceof Error ? cause.message : "Export failed.");
    } finally {
      setExporting(false);
    }
  }

  function openDrill(user: UsageUserRow) {
    setDrillFor(user);
    setDrillOffset(0);
  }

  const totals = summary.data?.totals;
  // Refresco silencioso fallido: el dato previo sigue servido (useApi lo
  // conserva) junto al error — la tabla no se tira, el badge lo dice.
  const liveStale = Boolean(summary.data && summary.error);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Usage by user"
          description="Every request billed through the gateway, aggregated per user from the spend ledger."
          actions={
            <div className="flex flex-wrap items-center gap-2">
              {/* Badge live — mismo lenguaje visual que la franja de Devices */}
              <div
                className="flex items-center gap-2 rounded-lg border border-border bg-surface-alt/60 px-3 py-1.5"
                title={
                  liveStale
                    ? "Last refresh failed — showing the last good figures while retrying every 15 s."
                    : "Auto-refreshes every 15 s while this tab is visible (pauses when hidden). Figures come from the spend ledger."
                }
              >
                <span className="relative flex h-2 w-2">
                  {liveStale ? null : (
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-60 motion-reduce:animate-none" />
                  )}
                  <span
                    className={`relative inline-flex h-2 w-2 rounded-full ${
                      liveStale ? "bg-amber-500" : "bg-emerald-500"
                    }`}
                  />
                </span>
                <span className="text-xs font-medium">
                  {totals
                    ? `${totals.requests.toLocaleString()} request${totals.requests === 1 ? "" : "s"}`
                    : "Usage"}
                </span>
                <span className="text-[11px] text-faint">
                  · {liveStale ? "stale" : "live"}
                </span>
              </div>
              <Button
                variant="secondary"
                size="sm"
                loading={summary.loading || summary.refreshing}
                onClick={summary.refetch}
              >
                <IconRefresh size={14} /> Refresh
              </Button>
              <Button
                variant="secondary"
                size="sm"
                loading={exporting}
                onClick={() => exportCsv("users")}
              >
                <IconDownload size={14} /> Export by user
              </Button>
              <Button
                variant="secondary"
                size="sm"
                loading={exporting}
                onClick={() => exportCsv("requests")}
              >
                <IconDownload size={14} /> Export by request
              </Button>
            </div>
          }
        />
        <CardBody className="space-y-4">
          {/* Filtros: período + custom + modelo + modo */}
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-muted">Period</span>
              <Select
                value={period}
                onChange={(e) => setPeriod(e.target.value as PeriodKind)}
                className="w-36"
              >
                {PERIODS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </Select>
            </label>
            {period === "custom" ? (
              <>
                <label className="flex flex-col gap-1">
                  <span className="text-xs font-medium text-muted">From</span>
                  <Input
                    type="date"
                    value={from}
                    max={to}
                    onChange={(e) => setFrom(e.target.value)}
                    className="w-40"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-xs font-medium text-muted">To</span>
                  <Input
                    type="date"
                    value={to}
                    min={from}
                    onChange={(e) => setTo(e.target.value)}
                    className="w-40"
                  />
                </label>
              </>
            ) : null}
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-muted">Model</span>
              <Select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                className="w-52"
              >
                <option value="">All models</option>
                {observedModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </Select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-muted">Mode</span>
              <Select
                value={mode}
                onChange={(e) => setMode(e.target.value)}
                className="w-36"
              >
                {MODES.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </Select>
            </label>
          </div>

          {/* Totales de la ventana */}
          {totals ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div className="rounded-lg border border-border-subtle p-3">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">
                  Requests
                </p>
                <p className="text-lg font-semibold text-foreground">
                  {totals.requests.toLocaleString()}
                </p>
              </div>
              <div className="rounded-lg border border-border-subtle p-3">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">
                  Tokens in / out
                </p>
                <p className="text-lg font-semibold text-foreground">
                  {totals.tokens_in.toLocaleString()} /{" "}
                  {totals.tokens_out.toLocaleString()}
                </p>
              </div>
              <div className="rounded-lg border border-border-subtle p-3">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">
                  Billed
                </p>
                <p className="text-lg font-semibold text-foreground">
                  {usd(totals.cost_billed_cents)}
                </p>
              </div>
              <div className="rounded-lg border border-border-subtle p-3">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">
                  Base cost
                </p>
                <p className="text-lg font-semibold text-foreground">
                  {usd(totals.cost_base_cents)}
                </p>
              </div>
            </div>
          ) : null}

          {summary.loading ? (
            <TableSkeleton rows={6} cols={6} />
          ) : summary.error && !summary.data ? (
            <ErrorState
              error={summary.error}
              onRetry={summary.refetch}
              context="GET /api/usage"
            />
          ) : !summary.data?.users.length ? (
            <EmptyState
              title="No usage in this period"
              description="No requests were billed in the selected window. Try a wider period."
            />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>User</Th>
                  <Th>Department</Th>
                  <Th className="text-right">Requests</Th>
                  <Th className="text-right">Tokens in</Th>
                  <Th className="text-right">Tokens out</Th>
                  <Th className="text-right">Billed</Th>
                  <Th>Models</Th>
                  <Th />
                </tr>
              </thead>
              <tbody>
                {summary.data.users.map((u) => {
                  const live = u.user_id
                    ? presenceByUser.get(u.user_id)
                    : undefined;
                  return (
                    <tr key={u.user_id ?? u.email}>
                      <Td>
                        <span className="flex items-center gap-2">
                          <PresenceDot
                            online={live?.online ?? false}
                            deviceTypes={live ? Array.from(live.types) : []}
                          />
                          <span className="flex flex-col">
                            <span className="font-medium text-foreground">
                              {u.display_name}
                            </span>
                            <span className="text-xs text-muted">{u.email}</span>
                          </span>
                        </span>
                      </Td>
                      <Td>{u.department_name ?? "—"}</Td>
                      <Td className="text-right">{u.requests.toLocaleString()}</Td>
                      <Td className="text-right">{u.tokens_in.toLocaleString()}</Td>
                      <Td className="text-right">{u.tokens_out.toLocaleString()}</Td>
                      <Td className="text-right font-medium">
                        {usd(u.cost_billed_cents)}
                      </Td>
                      <Td>
                        <span className="flex flex-wrap gap-1">
                          {u.models.map((m) => (
                            <Badge
                              key={`${m.model_name}-${m.mode}`}
                              tone={m.mode === "byok" ? "accent" : "neutral"}
                            >
                              {m.model_name} · {m.mode} · {m.requests}
                            </Badge>
                          ))}
                        </span>
                      </Td>
                      <Td>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => openDrill(u)}
                        >
                          View requests
                        </Button>
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          )}
        </CardBody>
      </Card>

      {/* Drill-down: requests individuales del usuario (paginado) */}
      <Dialog
        open={drillFor !== null}
        title={`Requests — ${drillFor?.display_name ?? ""}`}
        description={
          drillFor
            ? `${drillFor.requests.toLocaleString()} request(s) in the selected window. ${usd(drillFor.cost_billed_cents)} billed.`
            : ""
        }
        onClose={() => setDrillFor(null)}
        width="max-w-3xl"
      >
        {drill.loading ? (
          <TableSkeleton rows={5} cols={5} />
        ) : drill.error && !drill.data ? (
          <ErrorState
            error={drill.error}
            onRetry={drill.refetch}
            context="GET /api/usage/requests"
          />
        ) : !drill.data?.requests.length ? (
          <EmptyState
            title="No requests"
            description="Nothing billed for this user with the current filters."
          />
        ) : (
          <div className="space-y-3">
            <Table>
              <thead>
                <tr>
                  <Th>Timestamp</Th>
                  <Th>Model</Th>
                  <Th>Mode</Th>
                  <Th className="text-right">Tokens in</Th>
                  <Th className="text-right">Tokens out</Th>
                  <Th className="text-right">Billed</Th>
                </tr>
              </thead>
              <tbody>
                {drill.data.requests.map((r) => (
                  <tr key={r.request_id}>
                    <Td className="whitespace-nowrap">
                      {new Date(r.occurred_at).toLocaleString()}
                    </Td>
                    <Td>{r.model_name}</Td>
                    <Td>
                      <Badge tone={r.mode === "byok" ? "accent" : "neutral"}>
                        {r.mode}
                      </Badge>
                    </Td>
                    <Td className="text-right">{r.tokens_in.toLocaleString()}</Td>
                    <Td className="text-right">{r.tokens_out.toLocaleString()}</Td>
                    <Td className="text-right font-medium">
                      {usd(r.cost_billed_cents)}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <div className="-mx-6 -mb-6">
              <Pagination
                offset={drillOffset}
                limit={DRILLDOWN_PAGE_SIZE}
                total={drill.data.total}
                onPageChange={setDrillOffset}
              />
            </div>
          </div>
        )}
      </Dialog>
    </div>
  );
}
