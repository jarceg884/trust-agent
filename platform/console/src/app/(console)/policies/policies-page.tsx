"use client";

/**
 * Policies — shell de la página (spec 036 sobre la spec 033).
 *
 * - Vista por DEFECTO: panel legible de tarjetas por categoría (D-36-02) —
 *   sin keys crudas, con asignador "Apply to" y frases del valor efectivo.
 * - "Technical view" (tabla maestra + diff por usuario de la spec 033):
 *   conmutable SOLO para org_admin; dept_admin no ve el conmutador.
 * - Estados reales compartidos: loading (skeleton), 403 (empty state por
 *   rol), error de red (retry). Cero simulación (REQ-036-009): todo el
 *   render proviene de /api/policies/matrix, /api/policies/effective y
 *   /api/users.
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import type { DirectoryList, PolicyMatrix, WhoAmI } from "@/lib/types";
import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from "@/components/ui";
import { ReadablePoliciesPanel } from "./readable-policies-panel";
import { TechnicalPoliciesView } from "./technical-view";

type View = "readable" | "technical";

export function PoliciesPage({ me }: { me: WhoAmI }) {
  const matrix = useApi<PolicyMatrix>("/api/policies/matrix");
  const directory = useApi<DirectoryList>("/api/users");

  // D-36-02: la vista técnica es un extra de depuración SOLO org_admin.
  const isOrgAdmin = me.roles.some((r) => r.role_key === "org_admin");
  const [view, setView] = useState<View>("readable");

  if (matrix.loading) {
    return (
      <Card>
        <CardHeader
          title="Policies"
          description="Loading the policy catalog…"
        />
        <TableSkeleton rows={8} cols={5} />
      </Card>
    );
  }
  if (matrix.error) {
    return (
      <Card>
        <CardHeader title="Policies" />
        {matrix.error.status === 403 ? (
          <EmptyState
            title="Only admins view policies"
            description="Your role does not include the policies.org.manage, policies.dept.manage or audit.view permissions."
          />
        ) : (
          <ErrorState
            error={matrix.error}
            onRetry={matrix.refetch}
            context="GET /api/policies/matrix"
          />
        )}
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {isOrgAdmin ? (
        <div
          role="tablist"
          aria-label="Policy view"
          className="flex w-fit gap-1 rounded-lg border border-border-subtle bg-surface-alt p-1"
        >
          {(
            [
              ["readable", "Policy cards"],
              ["technical", "Technical view"],
            ] as [View, string][]
          ).map(([id, label]) => (
            <button
              key={id}
              role="tab"
              aria-selected={view === id}
              onClick={() => setView(id)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                view === id
                  ? "bg-surface text-foreground shadow-sm"
                  : "text-muted hover:text-foreground"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      ) : null}

      {view === "technical" && isOrgAdmin ? (
        <TechnicalPoliciesView me={me} matrix={matrix} directory={directory} />
      ) : (
        <ReadablePoliciesPanel me={me} matrix={matrix} directory={directory} />
      )}
    </div>
  );
}
