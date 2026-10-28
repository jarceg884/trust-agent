"use client";

/**
 * Audit Log (spec 009 REQ-AC-019 + spec 021): consulta filtrable server-side
 * (GET /api/audit/events), pill de integridad siempre visible (verificación
 * real POST /api/admin/audit/verify), pestañas Table | Timeline por
 * actor/sesión, y export CSV/JSON del rango filtrado con su total visible.
 */
import { useMemo, useState } from "react";
import { useApi } from "@/hooks/use-api";
import { downloadBlob } from "@/lib/api-client";
import type { AuditQueryResult, WhoAmI } from "@/lib/types";
import { dateTime, shortId, usd } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Pagination,
  Select,
  Table,
  TableSkeleton,
  Td,
  Th,
  useToast,
} from "@/components/ui";
import {
  IconAudit,
  IconDownload,
  IconRefresh,
  IconSearch,
} from "@/components/icons";
import { AuditIntegrity } from "./audit-integrity";
import { AuditTimeline, actionTone } from "./audit-timeline";

const LIMIT = 25;
/** Ventana de la timeline y de los facets: tope de la API (spec 008). */
const WIDE_LIMIT = 200;

type Tab = "table" | "timeline";

export function AuditPage() {
  // Filtros (estado del formulario — se aplican con "Apply filters").
  const [actorId, setActorId] = useState("");
  const [action, setAction] = useState("");
  const [targetType, setTargetType] = useState("");
  const [targetId, setTargetId] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const [q, setQ] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [applied, setApplied] = useState<Record<string, string>>({});
  const [offset, setOffset] = useState(0);
  const [tab, setTab] = useState<Tab>("table");
  const [exporting, setExporting] = useState(false);
  const { push } = useToast();

  const me = useApi<WhoAmI>("/api/me");
  const isAdmin = me.data?.roles.some((r) => r.role_key === "org_admin") ?? false;

  // Facets reales: valores vistos en los últimos 200 eventos del alcance.
  const facets = useApi<AuditQueryResult>(
    `/api/audit/events?limit=${WIDE_LIMIT}&offset=0`,
  );
  const facetOptions = useMemo(() => {
    const items = facets.data?.items ?? [];
    const actors = new Map<string, string>();
    const actions = new Set<string>();
    const targetTypes = new Set<string>();
    for (const e of items) {
      if (e.actor_id) actors.set(e.actor_id, e.actor_role ?? "user");
      actions.add(e.action);
      if (e.target_type) targetTypes.add(e.target_type);
    }
    return {
      actors: [...actors.entries()].map(([id, role]) => ({ id, role })),
      actions: [...actions].sort(),
      targetTypes: [...targetTypes].sort(),
    };
  }, [facets.data]);

  // Query principal: misma para tabla (paginada) y timeline (ventana amplia).
  const isTimeline = tab === "timeline";
  const query = useMemo(() => {
    const limit = isTimeline ? WIDE_LIMIT : LIMIT;
    const params = new URLSearchParams({
      limit: String(limit),
      offset: String(isTimeline ? 0 : offset),
    });
    for (const [k, v] of Object.entries(applied)) {
      if (v) params.set(k, v);
    }
    return `/api/audit/events?${params.toString()}`;
  }, [applied, offset, isTimeline]);

  const events = useApi<AuditQueryResult>(query);

  function applyFilters() {
    setOffset(0);
    setApplied({
      actor_id: actorId.trim(),
      action: action.trim(),
      target_type: targetType,
      target_id: targetId.trim(),
      device_id: deviceId.trim(),
      q: q.trim(),
      since: since ? new Date(since).toISOString() : "",
      until: until
        ? new Date(new Date(until).getTime() + 86_399_000).toISOString()
        : "",
    });
  }

  function resetFilters() {
    setActorId("");
    setAction("");
    setTargetType("");
    setTargetId("");
    setDeviceId("");
    setQ("");
    setSince("");
    setUntil("");
    setApplied({});
    setOffset(0);
  }

  /** "View activity" / "Filter to this actor": filtro por actor + timeline. */
  function viewActorActivity(id: string) {
    setActorId(id);
    setOffset(0);
    setApplied((prev) => ({ ...prev, actor_id: id }));
    setTab("timeline");
  }

  async function exportEvents(format: "csv" | "json") {
    setExporting(true);
    try {
      const params = new URLSearchParams({ format });
      for (const [k, v] of Object.entries(applied)) {
        if (v) params.set(k, v);
      }
      await downloadBlob(
        `/api/audit/events/export?${params.toString()}`,
        `audit-events.${format}`,
      );
      push("success", `Exported ${format.toUpperCase()} of the filtered range.`);
    } catch (cause) {
      push("error", cause instanceof Error ? cause.message : "Export failed.");
    } finally {
      setExporting(false);
    }
  }

  const total = events.data?.total ?? null;
  const activeFilterCount = Object.values(applied).filter(Boolean).length;

  return (
    <Card>
      <CardHeader
        title="Audit log"
        description="Append-only, hash-chained record of every governed action. Role-scoped by the control plane."
        actions={
          <>
            <AuditIntegrity isAdmin={isAdmin} onVerified={events.refetch} />
            <Button
              variant="secondary"
              size="sm"
              loading={exporting}
              onClick={() => exportEvents("csv")}
              title="Exports the active filtered range (CSV, hashes included)."
            >
              <IconDownload size={13} /> CSV{total != null ? ` · ${total}` : ""}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              loading={exporting}
              onClick={() => exportEvents("json")}
              title="Exports the active filtered range (JSON)."
            >
              <IconDownload size={13} /> JSON{total != null ? ` · ${total}` : ""}
            </Button>
          </>
        }
      />

      {/* Filtros completos (spec 021 REQ-21-001/002) */}
      <div className="grid gap-3 border-b border-border-subtle px-5 py-4 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Actor" htmlFor="f-actor">
          <Input
            id="f-actor"
            list="facet-actors"
            value={actorId}
            onChange={(e) => setActorId(e.target.value)}
            placeholder="Any actor"
            className="font-mono text-xs"
          />
          <datalist id="facet-actors">
            {facetOptions.actors.map((a) => (
              <option key={a.id} value={a.id}>
                {a.role}
              </option>
            ))}
          </datalist>
        </Field>
        <Field label="Event type" htmlFor="f-action">
          <Input
            id="f-action"
            list="facet-actions"
            value={action}
            onChange={(e) => setAction(e.target.value)}
            placeholder="auth.login, budget.cutoff…"
            className="font-mono text-xs"
          />
          <datalist id="facet-actions">
            {facetOptions.actions.map((a) => (
              <option key={a} value={a} />
            ))}
          </datalist>
        </Field>
        <Field label="Resource type" htmlFor="f-ttype">
          <Select
            id="f-ttype"
            value={targetType}
            onChange={(e) => setTargetType(e.target.value)}
          >
            <option value="">Any resource</option>
            {facetOptions.targetTypes.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Resource ID" htmlFor="f-tid">
          <Input
            id="f-tid"
            value={targetId}
            onChange={(e) => setTargetId(e.target.value)}
            placeholder="UUID or free text"
            className="font-mono text-xs"
          />
        </Field>
        <Field label="Device" htmlFor="f-device">
          <Input
            id="f-device"
            value={deviceId}
            onChange={(e) => setDeviceId(e.target.value)}
            placeholder="Device UUID"
            className="font-mono text-xs"
          />
        </Field>
        <Field label="Free text" htmlFor="f-q" hint="Searches action, target and payload.">
          <Input
            id="f-q"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search payloads…"
          />
        </Field>
        <Field label="From" htmlFor="f-since">
          <Input
            id="f-since"
            type="date"
            value={since}
            onChange={(e) => setSince(e.target.value)}
          />
        </Field>
        <Field label="To" htmlFor="f-until">
          <Input
            id="f-until"
            type="date"
            value={until}
            onChange={(e) => setUntil(e.target.value)}
          />
        </Field>
        <div className="flex flex-wrap items-end gap-2 sm:col-span-2 lg:col-span-4">
          <Button variant="primary" size="sm" onClick={() => applyFilters()}>
            <IconSearch size={13} /> Apply filters
          </Button>
          <Button variant="ghost" size="sm" onClick={resetFilters}>
            Reset
          </Button>
          <Button variant="ghost" size="sm" onClick={events.refetch} aria-label="Refresh">
            <IconRefresh size={13} />
          </Button>
          {activeFilterCount > 0 ? (
            <span className="text-xs text-muted">
              {activeFilterCount} filter{activeFilterCount === 1 ? "" : "s"} active
              {total != null ? ` · ${total} matching events` : ""}
            </span>
          ) : null}
        </div>
      </div>

      {/* Pestañas Table | Timeline */}
      <div
        role="tablist"
        aria-label="Audit view"
        className="flex items-center gap-1 border-b border-border-subtle px-5 py-2"
      >
        {(["table", "timeline"] as const).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium capitalize transition-colors ${
              tab === t
                ? "bg-accent-soft text-accent"
                : "text-muted hover:bg-surface-alt hover:text-foreground"
            }`}
          >
            {t === "table" ? "Table" : "Timeline by actor / session"}
          </button>
        ))}
        {isTimeline ? (
          <span className="ml-auto text-[11px] text-faint">
            Chronological, newest last · up to {WIDE_LIMIT} events of the filtered range
          </span>
        ) : null}
      </div>

      {isTimeline ? (
        <AuditTimeline
          events={events.data?.items ?? []}
          loading={events.loading}
          error={events.error}
          onRetry={events.refetch}
          onPickActor={viewActorActivity}
        />
      ) : events.loading ? (
        <TableSkeleton rows={8} cols={7} />
      ) : events.error ? (
        events.error.status === 403 ? (
          <EmptyState
            icon={<IconAudit size={18} />}
            title="Audit access requires the audit.view permission"
            description="Your role does not include audit visibility."
          />
        ) : (
          <ErrorState
            error={events.error}
            onRetry={events.refetch}
            context="GET /api/audit/events"
          />
        )
      ) : (events.data?.items.length ?? 0) === 0 ? (
        <EmptyState
          title="No audit events match"
          description="Adjust the filters, or wait — every governed action lands here automatically."
        />
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <Th>Seq</Th>
                <Th>When</Th>
                <Th>Action</Th>
                <Th>Actor</Th>
                <Th>Target</Th>
                <Th>Cost</Th>
                <Th>Activity</Th>
              </tr>
            </thead>
            <tbody>
              {events.data!.items.map((e) => (
                <tr key={e.seq} className="hover:bg-surface-alt/50">
                  <Td className="font-mono text-xs text-muted">{e.seq}</Td>
                  <Td className="whitespace-nowrap text-xs text-muted">{dateTime(e.ts)}</Td>
                  <Td>
                    <Badge tone={actionTone(e.action)}>{e.action}</Badge>
                  </Td>
                  <Td>
                    <p className="text-xs">{e.actor_role ?? "system"}</p>
                    <p className="font-mono text-[10px] text-muted">{shortId(e.actor_id)}</p>
                  </Td>
                  <Td>
                    {e.target_type ? (
                      <p className="font-mono text-[11px] text-muted">
                        {e.target_type}:{shortId(e.target_id)}
                      </p>
                    ) : (
                      <span className="text-faint">—</span>
                    )}
                  </Td>
                  <Td className="text-xs">{e.cost_cents != null ? usd(e.cost_cents) : "—"}</Td>
                  <Td>
                    {e.actor_id ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => viewActorActivity(e.actor_id!)}
                        title="Filter by this actor and open the timeline."
                      >
                        View activity
                      </Button>
                    ) : (
                      <span className="text-faint">—</span>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <Pagination
            offset={events.data!.offset}
            limit={events.data!.limit}
            total={events.data!.total}
            onPageChange={setOffset}
          />
        </>
      )}
    </Card>
  );
}
