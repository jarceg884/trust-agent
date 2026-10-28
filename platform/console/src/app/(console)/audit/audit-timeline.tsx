"use client";

/**
 * Timeline de audit por actor/sesión (spec 021, D-21-04 / REQ-21-003).
 *
 * Recibe los eventos del rango filtrado (misma query que la tabla) y los
 * presenta en orden cronológico ASCENDENTE, agrupados por actor y divididos
 * en sesiones delimitadas por auth.login (apertura) / auth.logout (cierre).
 * La sesión es heurística sobre la ventana cargada: audit_log no tiene columna
 * de sesión (limitación documentada en la spec §D-21-04).
 */
import { useMemo } from "react";
import type { AuditEvent } from "@/lib/types";
import type { ApiError } from "@/lib/api-client";
import { timeOnly, shortId, usd } from "@/lib/format";
import { Badge, Button, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { IconAudit } from "@/components/icons";

/** Tono de badge por categoría de acción (D-21-04). */
export function actionTone(action: string): "info" | "danger" | "warning" | "success" | "neutral" {
  if (action === "audit.verified") return "success";
  if (action.startsWith("auth.")) return "info";
  if (/denied|cutoff|failed|revoked|rejected|suspended/.test(action)) return "danger";
  if (action.startsWith("budget.") || action.startsWith("policy.")) return "warning";
  return "neutral";
}

interface SessionGroup {
  label: string;
  openedByLogin: boolean;
  events: AuditEvent[];
}

interface ActorGroup {
  key: string;
  actorId: string | null;
  actorRole: string | null;
  sessions: SessionGroup[];
  count: number;
}

/** Agrupa ascendente por actor y por sesión login→logout (useMemo, sin estado). */
export function groupTimeline(events: AuditEvent[]): ActorGroup[] {
  const asc = [...events].sort((a, b) => a.ts.localeCompare(b.ts));
  const byActor = new Map<string, AuditEvent[]>();
  for (const e of asc) {
    const key = e.actor_id ?? "system";
    const list = byActor.get(key);
    if (list) list.push(e);
    else byActor.set(key, [e]);
  }
  const groups: ActorGroup[] = [];
  for (const [key, actorEvents] of byActor) {
    const sessions: SessionGroup[] = [];
    let current: SessionGroup | null = null;
    for (const e of actorEvents) {
      if (e.action === "auth.login" || current === null) {
        current = { label: "", openedByLogin: e.action === "auth.login", events: [] };
        sessions.push(current);
      }
      current.events.push(e);
      if (e.action === "auth.logout" && current.openedByLogin) {
        current = null;
      }
    }
    sessions.forEach((s, i) => {
      s.label = s.openedByLogin
        ? `Session ${i + 1} — opened by login`
        : i === 0
          ? "Earlier activity"
          : `Activity block ${i + 1}`;
    });
    groups.push({
      key,
      actorId: actorEvents[0].actor_id,
      actorRole: actorEvents[0].actor_role,
      sessions,
      count: actorEvents.length,
    });
  }
  // Actores con más eventos primero; system al final.
  groups.sort((a, b) => (a.key === "system" ? 1 : b.key === "system" ? -1 : b.count - a.count));
  return groups;
}

/** Payload resumido: hasta 3 pares k=v acotados (el backend ya excluye secretos). */
function payloadSummary(payload: Record<string, unknown> | null): string {
  if (!payload) return "";
  const entries = Object.entries(payload).slice(0, 3);
  const parts = entries.map(([k, v]) => {
    const value = typeof v === "object" ? JSON.stringify(v) : String(v);
    const shown = value.length > 40 ? `${value.slice(0, 37)}…` : value;
    return `${k}=${shown}`;
  });
  if (Object.keys(payload).length > 3) parts.push("…");
  return parts.join(" · ");
}

export function AuditTimeline({
  events,
  loading,
  error,
  onRetry,
  onPickActor,
}: {
  events: AuditEvent[];
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
  /** Filtra la página por este actor (desde la propia timeline). */
  onPickActor: (actorId: string) => void;
}) {
  const groups = useMemo(() => groupTimeline(events), [events]);

  if (loading) {
    return (
      <div className="space-y-3 px-5 py-4">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <ErrorState error={error} onRetry={onRetry} context="GET /api/audit/events" />
    );
  }
  if (groups.length === 0) {
    return (
      <EmptyState
        icon={<IconAudit size={18} />}
        title="No activity in this range"
        description="Adjust the filters — the timeline shows the filtered range in chronological order."
      />
    );
  }

  return (
    <div className="divide-y divide-border-subtle">
      {groups.map((group) => (
        <section key={group.key} aria-label={`Activity of ${group.actorRole ?? "system"}`} className="px-5 py-4">
          <header className="mb-3 flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-foreground">
                {group.actorRole ?? "system"}
                {group.actorId ? (
                  <span className="ml-2 font-mono text-[11px] text-muted">{shortId(group.actorId)}</span>
                ) : null}
              </p>
              <p className="text-xs text-muted">
                {group.count} event{group.count === 1 ? "" : "s"} in range · chronological order
              </p>
            </div>
            {group.actorId ? (
              <Button variant="ghost" size="sm" onClick={() => onPickActor(group.actorId!)}>
                Filter to this actor
              </Button>
            ) : null}
          </header>

          {group.sessions.map((session) => (
            <div key={session.label} className="mb-4 last:mb-0">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-faint">
                {session.label}
              </p>
              <ol className="relative ml-2 border-l border-border-subtle">
                {session.events.map((e) => (
                  <li key={e.seq} className="relative py-2 pl-5">
                    {/* Nodo del rail */}
                    <span
                      aria-hidden
                      className={`absolute -left-[5px] top-3.5 h-2.5 w-2.5 rounded-full border-2 ${
                        actionTone(e.action) === "danger"
                          ? "border-danger bg-danger-soft"
                          : actionTone(e.action) === "success"
                            ? "border-success bg-success-soft"
                            : "border-accent bg-surface"
                      }`}
                    />
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <time className="font-mono text-[11px] text-muted" dateTime={e.ts}>
                        {timeOnly(e.ts)}
                      </time>
                      <Badge tone={actionTone(e.action)}>{e.action}</Badge>
                      {e.target_type ? (
                        <span className="font-mono text-[11px] text-muted">
                          {e.target_type}:{shortId(e.target_id)}
                        </span>
                      ) : null}
                      {e.cost_cents != null ? (
                        <span className="text-xs text-foreground">{usd(e.cost_cents)}</span>
                      ) : null}
                      {e.hash ? (
                        <span
                          className="font-mono text-[10px] text-faint"
                          title={`hash ${e.hash} · hash_prev ${e.hash_prev ?? "—"}`}
                        >
                          #{e.hash.slice(0, 8)}
                        </span>
                      ) : null}
                    </div>
                    {payloadSummary(e.payload) ? (
                      <p className="mt-1 font-mono text-[11px] leading-relaxed text-muted">
                        {payloadSummary(e.payload)}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ol>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
