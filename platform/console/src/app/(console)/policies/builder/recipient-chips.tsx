"use client";

/**
 * Chips de políticas nombradas por destinatario (spec 041, REQ-041-014 /
 * D-41-12): qué políticas nombradas aplican a la org / un depto / un
 * usuario, leído de GET /api/named-policies/recipients — un hecho de
 * asignación, NO un cálculo de combinación (la efectiva por key sigue
 * viniendo de matrix/effective, D-36-04).
 *
 * Componente ADITIVO al panel legible de spec 036: si la API falla o el
 * rol no tiene permiso, los chips simplemente no aparecen — jamás rompen
 * el panel existente.
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { ApiError, api } from "@/lib/api-client";
import { Badge, useToast } from "@/components/ui";
import { FileCheck2, X } from "lucide-react";
import type { RecipientsResponse, UnassignResult } from "./types";

export function RecipientPolicyChips({
  level,
  departmentId,
  userId,
  onUnassigned,
}: {
  level: "org" | "dept" | "user";
  departmentId: string | null;
  userId: string | null;
  /** Refresca matrix/effective (la unassign desactiva keys materializadas). */
  onUnassigned: () => void;
}) {
  const recipients = useApi<RecipientsResponse>("/api/named-policies/recipients");
  const { push } = useToast();
  const [busyId, setBusyId] = useState<string | null>(null);

  if (recipients.loading || recipients.error || !recipients.data) return null;

  // La API de recipients estampa el department_id del usuario en las entradas
  // de nivel user (y el user_id solo en las de nivel user): el match es por
  // destinatario, no por par (dept, user).
  const entry = recipients.data.recipients.find((r) => {
    if (r.level !== level) return false;
    if (level === "user") return (r.user_id ?? null) === userId;
    if (level === "dept") return (r.department_id ?? null) === departmentId;
    return (r.department_id ?? null) === null && (r.user_id ?? null) === null;
  });
  if (!entry || entry.policies.length === 0) return null;

  async function unassign(assignmentId: string, name: string) {
    setBusyId(assignmentId);
    try {
      const res = await api<UnassignResult>(
        `/api/named-policies/assignments/${encodeURIComponent(assignmentId)}`,
        { method: "DELETE" },
      );
      const kept = res.kept_modified.length;
      push(
        "success",
        `Unassigned “${name}” — ${res.deactivated.length} ${
          res.deactivated.length === 1 ? "key" : "keys"
        } removed` +
          (kept
            ? `; ${kept} kept (modified after the assignment).`
            : "."),
      );
      recipients.refetch();
      onUnassigned();
    } catch (cause) {
      push(
        "error",
        cause instanceof ApiError ? cause.message : "Unassign failed. Try again.",
      );
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section
      aria-label="Named policies applied here"
      className="flex flex-wrap items-center gap-2"
    >
      <span className="flex items-center gap-1.5 text-xs font-medium text-muted">
        <FileCheck2 size={14} strokeWidth={1.75} aria-hidden="true" />
        Named policies applied here:
      </span>
      {entry.policies.map((p) => (
        <span
          key={p.assignment_id}
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${
            p.stale
              ? "border-warning/30 bg-warning-soft text-warning"
              : "border-accent-border bg-accent-soft text-accent"
          }`}
        >
          {p.name}
          {p.stale ? <Badge tone="warning">Outdated</Badge> : null}
          <button
            type="button"
            aria-label={`Unassign ${p.name}`}
            disabled={busyId === p.assignment_id}
            onClick={() => unassign(p.assignment_id, p.name)}
            className="rounded-full p-0.5 transition-colors hover:bg-foreground/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <X size={12} strokeWidth={2.25} aria-hidden="true" />
          </button>
        </span>
      ))}
    </section>
  );
}
