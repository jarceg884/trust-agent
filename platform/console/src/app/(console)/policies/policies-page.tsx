"use client";

/**
 * Policies — shell de la página (spec 041 D-41-10 sobre las specs 036/033).
 *
 * Tres pestañas:
 * - "Policies" (VISTA POR DEFECTO): el Policy Builder de políticas nombradas
 *   (spec 041) — lista, editor con sidebar de capacidades y asignación con
 *   preview. Todo proviene de GET /api/named-policies (bloque `builder`,
 *   D-41-02) — cero hardcodeo.
 * - "Individual settings": el panel legible por key de la spec 036, INTACTO
 *   (solo re-etiquetado), con los chips de políticas nombradas asignadas
 *   por destinatario (REQ-041-014).
 * - "Technical view": la tabla maestra + diff por usuario de la spec 033,
 *   SOLO para org_admin (dept_admin no ve la pestaña).
 *
 * Estados reales compartidos por pestaña (loading/403/error); cero
 * simulación (REQ-041-016): todo el render proviene de la API.
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
  Tabs,
} from "@/components/ui";
import { ReadablePoliciesPanel } from "./readable-policies-panel";
import { TechnicalPoliciesView } from "./technical-view";
import { PolicyBuilder } from "./builder/policy-builder";
import type { NamedPolicyList } from "./builder/types";

type View = "builder" | "readable" | "technical";

export function PoliciesPage({ me }: { me: WhoAmI }) {
  const namedPolicies = useApi<NamedPolicyList>("/api/named-policies");
  const matrix = useApi<PolicyMatrix>("/api/policies/matrix");
  const directory = useApi<DirectoryList>("/api/users");

  // D-41-10: la vista técnica es un extra de depuración SOLO org_admin.
  const isOrgAdmin = me.roles.some((r) => r.role_key === "org_admin");
  const [view, setView] = useState<View>("builder");

  const tabs: { id: string; label: string }[] = [
    { id: "builder", label: "Policies" },
    { id: "readable", label: "Individual settings" },
    ...(isOrgAdmin ? [{ id: "technical", label: "Technical view" }] : []),
  ];

  function matrixGate(children: React.ReactNode) {
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
    return children;
  }

  return (
    <div className="space-y-4">
      <Tabs
        tabs={tabs}
        active={view}
        onChange={(id) => setView(id as View)}
      />

      {view === "builder" ? (
        <div role="tabpanel" id="panel-builder" aria-labelledby="tab-builder">
          <PolicyBuilder
            me={me}
            namedPolicies={namedPolicies}
            matrix={matrix}
            directory={directory}
          />
        </div>
      ) : view === "technical" && isOrgAdmin ? (
        <div role="tabpanel" id="panel-technical" aria-labelledby="tab-technical">
          {matrixGate(
            <TechnicalPoliciesView me={me} matrix={matrix} directory={directory} />,
          )}
        </div>
      ) : (
        <div role="tabpanel" id="panel-readable" aria-labelledby="tab-readable">
          {matrixGate(
            <ReadablePoliciesPanel me={me} matrix={matrix} directory={directory} />,
          )}
        </div>
      )}
    </div>
  );
}
