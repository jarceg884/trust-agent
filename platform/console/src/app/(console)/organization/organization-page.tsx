"use client";

/**
 * Organization (spec 009 REQ-AC-011): identidad de la org (/api/me vía props
 * del shell no aplica aquí — se reconsulta), presupuesto org y CRUD de
 * políticas org (GET/PUT /api/policies, DELETE /api/policies/org/{key}).
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type { PolicyList, WhoAmI } from "@/lib/types";
import { dateTime } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardBody,
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

const VALUE_TYPES: Record<string, "boolean" | "json"> = {
  // Tipos de valor habituales del catálogo (catalog.policy_keys); el control
  // plane valida el tipo real (422 si no coincide).
  require_mfa: "boolean",
  allowed_models_only: "boolean",
  sso_required: "boolean",
};

export function OrganizationPage({ me }: { me: WhoAmI }) {
  const policies = useApi<PolicyList>("/api/policies?level=org");
  const { push } = useToast();

  const [createOpen, setCreateOpen] = useState(false);
  const [key, setKey] = useState("");
  const [valueType, setValueType] = useState<"boolean" | "json">("boolean");
  const [boolValue, setBoolValue] = useState("true");
  const [jsonValue, setJsonValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);

  async function createPolicy() {
    setSubmitting(true);
    try {
      let value: unknown;
      if (valueType === "boolean") {
        value = boolValue === "true";
      } else {
        try {
          value = JSON.parse(jsonValue);
        } catch {
          push("error", "The value must be valid JSON.");
          return;
        }
      }
      await api("/api/policies/org", { method: "PUT", body: { key, value } });
      push("success", `Policy "${key}" saved.`);
      setCreateOpen(false);
      setKey("");
      setJsonValue("");
      policies.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Save failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function deletePolicy(policyKey: string) {
    setSubmitting(true);
    try {
      await api(`/api/policies/org/${encodeURIComponent(policyKey)}`, {
        method: "DELETE",
      });
      push("success", `Policy "${policyKey}" removed.`);
      setDeleting(null);
      policies.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Delete failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Organization identity"
          description="Resolved from your verified session (never from client input)."
        />
        <CardBody>
          <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted">Organization code</dt>
              <dd className="mt-0.5 text-sm font-medium text-foreground">{me.tenant_slug}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Tenant ID</dt>
              <dd className="mt-0.5 font-mono text-xs text-foreground">{me.tenant_id}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Your roles</dt>
              <dd className="mt-1 flex flex-wrap gap-1">
                {me.roles.map((r) => (
                  <Badge key={`${r.role_key}-${r.department_id ?? "org"}`} tone="accent">
                    {r.role_key}
                    {r.department_id ? ` (dept)` : ""}
                  </Badge>
                ))}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Session</dt>
              <dd className="mt-0.5 text-sm text-foreground">
                MFA {me.mfa_verified ? "verified" : "not verified"} · via {me.via}
              </dd>
            </div>
          </dl>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Organization policies"
          description="Baseline policies. Department and user policies can only tighten them (deny-overrides)."
          actions={
            <Button variant="primary" size="sm" onClick={() => setCreateOpen(true)}>
              New policy
            </Button>
          }
        />
        {policies.loading ? (
          <TableSkeleton rows={4} cols={3} />
        ) : policies.error ? (
          policies.error.status === 403 ? (
            <EmptyState
              title="Only organization admins manage org policies"
              description="Your role does not include the policies.org.manage permission."
            />
          ) : (
            <ErrorState error={policies.error} onRetry={policies.refetch} context="GET /api/policies?level=org" />
          )
        ) : (policies.data?.policies.length ?? 0) === 0 ? (
          <EmptyState
            title="No active organization policies"
            description="Create the first organization policy to set the governance baseline."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Key</Th>
                <Th>Value</Th>
                <Th>Version</Th>
                <Th>Updated</Th>
                <Th className="w-20" />
              </tr>
            </thead>
            <tbody>
              {policies.data!.policies.map((p) => (
                <tr key={p.key} className="hover:bg-surface-alt/50">
                  <Td className="font-mono text-xs">{p.key}</Td>
                  <Td>
                    <Badge tone="info">{JSON.stringify(p.value)}</Badge>
                  </Td>
                  <Td className="text-xs text-muted">v{p.version}</Td>
                  <Td className="text-xs text-muted">{dateTime(p.updated_at)}</Td>
                  <Td>
                    <Button variant="danger" size="sm" onClick={() => setDeleting(p.key)}>
                      Remove
                    </Button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="New organization policy"
        description="The control plane validates the key against the policy catalog and its value type."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" loading={submitting} disabled={!key.trim()} onClick={createPolicy}>
              Save policy
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Policy key" htmlFor="policy-key" hint="Must exist in the policy catalog (e.g. require_mfa).">
            <Input
              id="policy-key"
              value={key}
              onChange={(e) => {
                setKey(e.target.value);
                setValueType(VALUE_TYPES[e.target.value] ?? "json");
              }}
              placeholder="require_mfa"
              className="font-mono"
            />
          </Field>
          <Field label="Value type" htmlFor="policy-type">
            <Select id="policy-type" value={valueType} onChange={(e) => setValueType(e.target.value as "boolean" | "json")}>
              <option value="boolean">Boolean</option>
              <option value="json">JSON</option>
            </Select>
          </Field>
          {valueType === "boolean" ? (
            <Field label="Value" htmlFor="policy-bool">
              <Select id="policy-bool" value={boolValue} onChange={(e) => setBoolValue(e.target.value)}>
                <option value="true">true</option>
                <option value="false">false</option>
              </Select>
            </Field>
          ) : (
            <Field label="JSON value" htmlFor="policy-json" hint='e.g. ["qwen-max", "qwen-plus"]'>
              <Input
                id="policy-json"
                value={jsonValue}
                onChange={(e) => setJsonValue(e.target.value)}
                placeholder='["model-a"]'
                className="font-mono"
              />
            </Field>
          )}
        </div>
      </Dialog>

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        onConfirm={() => deleting && deletePolicy(deleting)}
        loading={submitting}
        title="Remove organization policy"
        message={`The policy "${deleting}" will stop applying at the organization level. Departments and users keep their own restrictions. This action is audited.`}
        confirmLabel="Remove policy"
      />
    </div>
  );
}
