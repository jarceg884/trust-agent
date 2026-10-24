"use client";

/**
 * Audit Log (spec 009 REQ-AC-019): query filtrable server-side
 * (GET /api/audit/events), paginación sobre total, export CSV/JSON del rango
 * filtrado y verificación de cadena (POST /api/admin/audit/verify, org_admin).
 */
import { useMemo, useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError, downloadBlob } from "@/lib/api-client";
import type { AuditQueryResult, ChainVerifyResult } from "@/lib/types";
import { dateTime, shortId, usd } from "@/lib/format";
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
  Pagination,
  Table,
  TableSkeleton,
  Td,
  Th,
  useToast,
} from "@/components/ui";
import { IconAudit, IconDownload, IconRefresh, IconSearch, IconShield } from "@/components/icons";

const LIMIT = 25;

export function AuditPage() {
  const [action, setAction] = useState("");
  const [actorId, setActorId] = useState("");
  const [q, setQ] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [offset, setOffset] = useState(0);
  const [applied, setApplied] = useState<Record<string, string>>({});
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [verifyResult, setVerifyResult] = useState<ChainVerifyResult | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [exporting, setExporting] = useState(false);
  const { push } = useToast();

  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
    for (const [k, v] of Object.entries(applied)) {
      if (v) params.set(k, v);
    }
    return `/api/audit/events?${params.toString()}`;
  }, [applied, offset]);

  const events = useApi<AuditQueryResult>(query);

  function applyFilters() {
    setOffset(0);
    setApplied({
      action: action.trim(),
      actor_id: actorId.trim(),
      q: q.trim(),
      since: since ? new Date(since).toISOString() : "",
      until: until ? new Date(until).toISOString() : "",
    });
  }

  function resetFilters() {
    setAction("");
    setActorId("");
    setQ("");
    setSince("");
    setUntil("");
    setApplied({});
    setOffset(0);
  }

  async function verifyChain() {
    setVerifying(true);
    try {
      const result = await api<ChainVerifyResult>("/api/admin/audit/verify", {
        method: "POST",
      });
      setVerifyResult(result);
      events.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Verification failed.");
      setVerifyOpen(false);
    } finally {
      setVerifying(false);
    }
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

  return (
    <Card>
      <CardHeader
        title="Audit log"
        description="Append-only, hash-chained record of every governed action. Role-scoped by the control plane."
        actions={
          <>
            <Button
              variant="secondary"
              size="sm"
              loading={exporting}
              onClick={() => exportEvents("csv")}
            >
              <IconDownload size={13} /> CSV
            </Button>
            <Button
              variant="secondary"
              size="sm"
              loading={exporting}
              onClick={() => exportEvents("json")}
            >
              <IconDownload size={13} /> JSON
            </Button>
            <Button variant="primary" size="sm" onClick={() => { setVerifyOpen(true); setVerifyResult(null); verifyChain(); }}>
              <IconShield size={13} /> Verify chain
            </Button>
          </>
        }
      />

      {/* Filtros */}
      <div className="grid gap-3 border-b border-border-subtle px-5 py-4 sm:grid-cols-2 lg:grid-cols-5">
        <Field label="Action" htmlFor="f-action">
          <Input id="f-action" value={action} onChange={(e) => setAction(e.target.value)} placeholder="auth.login" className="font-mono text-xs" />
        </Field>
        <Field label="Actor ID" htmlFor="f-actor">
          <Input id="f-actor" value={actorId} onChange={(e) => setActorId(e.target.value)} placeholder="UUID" className="font-mono text-xs" />
        </Field>
        <Field label="Free text" htmlFor="f-q">
          <Input id="f-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search payloads…" />
        </Field>
        <Field label="From" htmlFor="f-since">
          <Input id="f-since" type="date" value={since} onChange={(e) => setSince(e.target.value)} />
        </Field>
        <Field label="To" htmlFor="f-until">
          <Input id="f-until" type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
        </Field>
        <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-5">
          <Button variant="primary" size="sm" onClick={applyFilters}>
            <IconSearch size={13} /> Apply filters
          </Button>
          <Button variant="ghost" size="sm" onClick={resetFilters}>
            Reset
          </Button>
          <Button variant="ghost" size="sm" onClick={events.refetch} aria-label="Refresh">
            <IconRefresh size={13} />
          </Button>
        </div>
      </div>

      {events.loading ? (
        <TableSkeleton rows={8} cols={6} />
      ) : events.error ? (
        events.error.status === 403 ? (
          <EmptyState
            icon={<IconAudit size={18} />}
            title="Audit access requires the audit.view permission"
            description="Your role does not include audit visibility."
          />
        ) : (
          <ErrorState error={events.error} onRetry={events.refetch} context="GET /api/audit/events" />
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
              </tr>
            </thead>
            <tbody>
              {events.data!.items.map((e) => (
                <tr key={e.seq} className="hover:bg-surface-alt/50">
                  <Td className="font-mono text-xs text-muted">{e.seq}</Td>
                  <Td className="whitespace-nowrap text-xs text-muted">{dateTime(e.ts)}</Td>
                  <Td>
                    <Badge tone={e.action.startsWith("auth.") ? "info" : e.action.includes("cutoff") || e.action.includes("denied") ? "danger" : "neutral"}>
                      {e.action}
                    </Badge>
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

      {/* Verificación de cadena */}
      <Dialog
        open={verifyOpen}
        onClose={() => setVerifyOpen(false)}
        title="Audit chain verification"
        description="Recomputes the hash chain of the tenant's audit log. Every verification is itself audited."
        width="max-w-md"
        footer={
          <Button variant="primary" size="sm" onClick={() => setVerifyOpen(false)}>
            Close
          </Button>
        }
      >
        {verifying ? (
          <p className="py-4 text-center text-sm text-muted">Verifying…</p>
        ) : verifyResult ? (
          <div
            role="status"
            className={`rounded-lg border px-4 py-3 ${
              verifyResult.status === "ok"
                ? "border-success/25 bg-success-soft text-success"
                : "border-danger/25 bg-danger-soft text-danger"
            }`}
          >
            <p className="text-sm font-semibold">
              {verifyResult.status === "ok" ? "Chain intact" : `Chain broken at seq ${verifyResult.seq}`}
            </p>
            <p className="mt-1 text-xs leading-relaxed">
              {verifyResult.status === "ok"
                ? "Every event links to the previous one by hash. The log is complete and untampered."
                : "The first inconsistent event is at this sequence number. Investigate immediately — rows may have been altered or removed."}
            </p>
          </div>
        ) : (
          <p className="py-4 text-center text-sm text-muted">Starting verification…</p>
        )}
      </Dialog>
    </Card>
  );
}
