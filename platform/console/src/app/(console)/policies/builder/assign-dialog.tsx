"use client";

/**
 * Diálogo "Assign to…" (spec 041, REQ-041-013 / D-41-04).
 *
 * Flujo: elegir destinatario (Organization / Department / User del alcance)
 * → cargar el preview dry-run (GET /{id}/preview: keys a escribir con
 * reemplazos, omitidas por org-only, would_relax) → confirmar → POST assign
 * → resultado legible (aplicadas/omitidas) o el 422/409 REAL inline con el
 * mensaje del control plane (patrón REQ-036-004: sin toast genérico, el
 * diálogo conserva su estado).
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { ApiError, api } from "@/lib/api-client";
import type { UseApiState } from "@/hooks/use-api";
import type { DirectoryList, PolicyMatrix, WhoAmI } from "@/lib/types";
import {
  Badge,
  Button,
  Dialog,
  ErrorState,
  Field,
  Select,
  Skeleton,
} from "@/components/ui";
import { AlertTriangle, CheckCircle2, SkipForward } from "lucide-react";
import type { AssignResult, NamedPolicy, NamedPolicyPreview } from "./types";
import {
  keyDisplayName,
  readableValue,
  userLabel,
  type DynamicSources,
} from "./present";

type ScopeLevel = "org" | "dept" | "user";

export function AssignDialog({
  open,
  onClose,
  policy,
  me,
  matrix,
  directory,
  sources,
  onAssigned,
}: {
  open: boolean;
  onClose: () => void;
  policy: NamedPolicy;
  me: WhoAmI;
  matrix: UseApiState<PolicyMatrix>;
  directory: UseApiState<DirectoryList>;
  sources: DynamicSources;
  /** Refresca lista + matrix (lo materializado cambió). */
  onAssigned: () => void;
}) {
  const [level, setLevel] = useState<ScopeLevel>("org");
  const [deptId, setDeptId] = useState("");
  const [userId, setUserId] = useState("");
  const [assigning, setAssigning] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<AssignResult | null>(null);

  const recipientReady =
    level === "org" || (level === "dept" && Boolean(deptId)) || (level === "user" && Boolean(userId));

  const previewPath =
    open && recipientReady
      ? `/api/named-policies/${policy.id}/preview?level=${level}` +
        (level === "dept" ? `&department_id=${encodeURIComponent(deptId)}` : "") +
        (level === "user" ? `&user_id=${encodeURIComponent(userId)}` : "")
      : null;
  const preview = useApi<NamedPolicyPreview>(previewPath);

  // Un dept_admin solo puede asignar a SU alcance (D-41-09); el selector
  // ofrece los deptos/usuarios que la API ya filtra por scope.
  const canOrg = me.permissions.includes("policies.org.manage");

  function reset() {
    setError(null);
    setResult(null);
  }

  async function confirm() {
    setAssigning(true);
    setError(null);
    try {
      const res = await api<AssignResult>(
        `/api/named-policies/${policy.id}/assign`,
        {
          method: "POST",
          body: {
            level,
            department_id: level === "dept" ? deptId : undefined,
            user_id: level === "user" ? userId : undefined,
          },
        },
      );
      setResult(res);
      onAssigned();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError(0, "unknown", String(cause), null));
    } finally {
      setAssigning(false);
    }
  }

  const recipientName =
    level === "org"
      ? "the organization"
      : level === "dept"
        ? `department “${matrix.data?.departments.find((d) => d.department_id === deptId)?.name ?? ""}”`
        : `user “${directory.data?.users.find((u) => u.user_id === userId) ? userLabel(directory.data!.users.find((u) => u.user_id === userId)!) : ""}”`;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      width="max-w-2xl"
      title={result ? "Policy assigned" : `Assign “${policy.name}”`}
      description={
        result
          ? undefined
          : "Preview exactly what will change before it changes — nothing is written until you confirm."
      }
      footer={
        result ? (
          <Button variant="primary" size="sm" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <Button variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={assigning}
              disabled={!recipientReady || preview.loading}
              onClick={confirm}
            >
              Confirm assignment
            </Button>
          </>
        )
      }
    >
      {result ? (
        <div className="space-y-3">
          <div className="flex items-start gap-2.5">
            <CheckCircle2 size={18} className="mt-0.5 shrink-0 text-success" aria-hidden="true" />
            <div>
              <p className="text-sm font-medium text-foreground">
                Applied to {recipientName}.
              </p>
              <p className="mt-0.5 text-xs text-muted">
                {Object.keys(result.applied_keys).length}{" "}
                {Object.keys(result.applied_keys).length === 1 ? "policy key" : "policy keys"}{" "}
                written with the existing audit and deny-overrides guarantees.
              </p>
            </div>
          </div>
          {result.skipped.length ? (
            <div className="rounded-lg border border-info/25 bg-info-soft px-3.5 py-2.5">
              <p className="text-xs font-medium text-info">
                Skipped — organization-level only ({result.skipped.length})
              </p>
              <p className="mt-0.5 text-xs text-info/80">
                {result.skipped
                  .map((k) => (matrix.data ? keyDisplayName(matrix.data, k) : k))
                  .join(", ")}{" "}
                can only be set for the organization, so they were not written at this level.
              </p>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Assign to" htmlFor="assign-level">
              <Select
                id="assign-level"
                value={level}
                onChange={(e) => {
                  setLevel(e.target.value as ScopeLevel);
                  setDeptId("");
                  setUserId("");
                  reset();
                }}
                className="w-44"
              >
                {canOrg ? <option value="org">Organization</option> : null}
                <option value="dept">Department</option>
                <option value="user">User</option>
              </Select>
            </Field>
            {level === "dept" ? (
              <Field label="Department" htmlFor="assign-dept">
                <Select
                  id="assign-dept"
                  value={deptId}
                  onChange={(e) => {
                    setDeptId(e.target.value);
                    reset();
                  }}
                  className="w-56"
                >
                  <option value="">Select a department…</option>
                  {(matrix.data?.departments ?? []).map((d) => (
                    <option key={d.department_id} value={d.department_id}>
                      {d.name}
                    </option>
                  ))}
                </Select>
              </Field>
            ) : null}
            {level === "user" ? (
              <Field label="User" htmlFor="assign-user">
                <Select
                  id="assign-user"
                  value={userId}
                  onChange={(e) => {
                    setUserId(e.target.value);
                    reset();
                  }}
                  className="w-72"
                >
                  <option value="">Select a user…</option>
                  {(directory.data?.users ?? []).map((u) => (
                    <option key={u.user_id} value={u.user_id}>
                      {userLabel(u)}
                    </option>
                  ))}
                </Select>
              </Field>
            ) : null}
          </div>

          {/* 422/409 real del control plane, inline (REQ-036-004). */}
          {error ? (
            <div role="alert" className="rounded-lg border border-danger/30 bg-danger/10 px-3.5 py-2.5">
              <p className="text-xs font-medium text-danger">{error.message}</p>
              {error.status === 409 ? (
                <p className="mt-0.5 text-xs text-danger/80">
                  This policy is already assigned to that recipient — unassign it first if you want to re-apply it.
                </p>
              ) : null}
            </div>
          ) : null}

          {!recipientReady ? (
            <p className="text-xs text-muted">Choose a recipient to preview the changes.</p>
          ) : preview.loading ? (
            <div className="space-y-2" role="status" aria-label="Loading preview">
              <Skeleton className="h-16 rounded-lg" />
              <Skeleton className="h-16 rounded-lg" />
            </div>
          ) : preview.error ? (
            <ErrorState
              error={preview.error}
              onRetry={preview.refetch}
              context={`GET /api/named-policies/${policy.id}/preview`}
            />
          ) : preview.data && matrix.data ? (
            <PreviewDiff preview={preview.data} matrix={matrix.data} sources={sources} />
          ) : null}
        </div>
      )}
    </Dialog>
  );
}

/** El diff legible del preview (D-41-04): qué se escribe, qué reemplaza,
 *  qué se omite por org-only y qué fallaría por relajación. */
function PreviewDiff({
  preview,
  matrix,
  sources,
}: {
  preview: NamedPolicyPreview;
  matrix: PolicyMatrix;
  sources: DynamicSources;
}) {
  const relax = preview.would_relax;
  const invalid = preview.invalid;
  return (
    <div className="space-y-3">
      {relax.length ? (
        <div role="alert" className="rounded-lg border border-danger/30 bg-danger/10 px-3.5 py-2.5">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-danger">
            <AlertTriangle size={13} aria-hidden="true" />
            This assignment will be rejected
          </p>
          <ul className="mt-1 space-y-0.5">
            {relax.map((r) => (
              <li key={r.key} className="text-xs text-danger/90">
                <span className="font-medium">{keyDisplayName(matrix, r.key)}</span>: {r.message}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-danger/80">
            A lower level can only tighten what a higher level set — it cannot relax it.
          </p>
        </div>
      ) : null}

      {invalid.length ? (
        <div role="alert" className="rounded-lg border border-danger/30 bg-danger/10 px-3.5 py-2.5">
          <p className="text-xs font-semibold text-danger">Invalid values</p>
          <ul className="mt-1 space-y-0.5">
            {invalid.map((r) => (
              <li key={r.key} className="text-xs text-danger/90">
                <span className="font-medium">{keyDisplayName(matrix, r.key)}</span>: {r.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div>
        <p className="text-xs font-semibold text-foreground">
          Will be written ({preview.keys.length})
        </p>
        {preview.keys.length ? (
          <ul className="mt-1.5 divide-y divide-border-subtle rounded-lg border border-border-subtle">
            {preview.keys.map((row) => (
              <li key={row.key} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 px-3 py-2">
                <span className="text-xs font-medium text-foreground">
                  {keyDisplayName(matrix, row.key)}
                </span>
                <span className="text-xs text-muted">
                  → {readableValue(matrix, row.key, row.new_value, sources)}
                </span>
                {row.overwrites ? (
                  <Badge tone="warning" className="ml-auto">
                    Replaces: {readableValue(matrix, row.key, row.current_value, sources)}
                  </Badge>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-xs text-muted">No keys to write.</p>
        )}
      </div>

      {preview.skipped_org_only.length ? (
        <div className="rounded-lg border border-info/25 bg-info-soft px-3.5 py-2.5">
          <p className="flex items-center gap-1.5 text-xs font-medium text-info">
            <SkipForward size={13} aria-hidden="true" />
            Skipped — organization-level only ({preview.skipped_org_only.length})
          </p>
          <p className="mt-0.5 text-xs text-info/80">
            {preview.skipped_org_only
              .map((k) => keyDisplayName(matrix, k))
              .join(", ")}{" "}
            only apply when assigned to the organization.
          </p>
        </div>
      ) : null}
    </div>
  );
}
