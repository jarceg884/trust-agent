"use client";

/**
 * Shared Agents (spec 009 REQ-AC-016 + spec 020): lista de agentes visibles
 * con columna de acceso, publicar agente y gestión de grants explícitos.
 * Sección "Default profiles" (spec 020): perfiles curados del producto que
 * el tenant puede adoptar (instancia del tenant, grants explícitos como
 * cualquier agente) con la policy EFECTIVA del caller y un dialog Invoke
 * que ejercita el gate de gobernanza (modelo aprobado → 200; no aprobado →
 * 403 model_not_approved; budget cortado → 403 budget_cutoff).
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type {
  AgentList,
  AgentProfilesResponse,
  GrantResult,
  InvokeResult,
} from "@/lib/types";
import { shortId } from "@/lib/format";
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
  Select,
  Table,
  TableSkeleton,
  Td,
  Th,
  useToast,
} from "@/components/ui";
import { IconAgents, IconPlus } from "@/components/icons";

const ACCESS_TONES: Record<string, "accent" | "success" | "info" | "neutral"> = {
  own: "accent",
  manage: "success",
  invoke: "info",
  view: "neutral",
};

export function AgentsPage() {
  const agents = useApi<AgentList>("/api/agents");
  const profiles = useApi<AgentProfilesResponse>("/api/agent-profiles");
  const { push } = useToast();

  const [publishOpen, setPublishOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [scope, setScope] = useState("dept");
  const [submitting, setSubmitting] = useState(false);

  const [grantFor, setGrantFor] = useState<string | null>(null);
  const [grantAgentName, setGrantAgentName] = useState("");
  const [granteeId, setGranteeId] = useState("");
  const [permission, setPermission] = useState("invoke");
  const [sessionGrants, setSessionGrants] = useState<GrantResult[]>([]);

  const [revokeGrantId, setRevokeGrantId] = useState("");
  const [revokeForAgent, setRevokeForAgent] = useState<string | null>(null);

  // spec 020: effective-policy dialog + invoke dialog.
  const [policyOpen, setPolicyOpen] = useState(false);
  const [invokeFor, setInvokeFor] = useState<string | null>(null);
  const [invokeAgentName, setInvokeAgentName] = useState("");
  const [invokeModel, setInvokeModel] = useState("");
  const [invokeResult, setInvokeResult] = useState<
    { ok: true; result: InvokeResult } | { ok: false; error: ApiError } | null
  >(null);

  const effective = profiles.data?.effective_policy ?? null;

  async function publish() {
    setSubmitting(true);
    try {
      await api("/api/agents", {
        method: "POST",
        body: { name, description: description || null, scope },
      });
      push("success", `Agent "${name}" published (no grants — access is explicit).`);
      setPublishOpen(false);
      setName("");
      setDescription("");
      agents.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Publish failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function adoptProfile(key: string, profileName: string) {
    setSubmitting(true);
    try {
      const result = await api<{ agent_id: string }>(
        `/api/agent-profiles/${key}/adopt`,
        { method: "POST" },
      );
      push(
        "success",
        `"${profileName}" adopted — agent ${shortId(result.agent_id)} is now yours to grant (explicit access, deny-overrides).`,
      );
      profiles.refetch();
      agents.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Adopt failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function grant() {
    if (!grantFor) return;
    setSubmitting(true);
    try {
      const result = await api<GrantResult>(`/api/agents/${grantFor}/grants`, {
        method: "POST",
        body: { user_id: granteeId, permission },
      });
      setSessionGrants((g) => [...g, result]);
      push("success", `Grant created (${shortId(result.id)}). Share the grant ID with the admin if it needs revoking later.`);
      setGrantFor(null);
      setGranteeId("");
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Grant failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function revokeGrant() {
    if (!revokeForAgent || !revokeGrantId.trim()) return;
    setSubmitting(true);
    try {
      await api(`/api/agents/${revokeForAgent}/grants/${revokeGrantId.trim()}`, {
        method: "DELETE",
      });
      setSessionGrants((g) => g.filter((x) => x.id !== revokeGrantId.trim()));
      push("success", "Grant revoked.");
      setRevokeForAgent(null);
      setRevokeGrantId("");
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Revoke failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function invoke() {
    if (!invokeFor) return;
    setSubmitting(true);
    setInvokeResult(null);
    try {
      const result = await api<InvokeResult>(`/api/agents/${invokeFor}/invoke`, {
        method: "POST",
        body: invokeModel.trim() ? { model_name: invokeModel.trim() } : {},
      });
      setInvokeResult({ ok: true, result });
    } catch (cause) {
      // 403 model_not_approved / budget_cutoff: la policy restringe — se
      // muestra el veredicto real del gate en el dialog (spec 020).
      setInvokeResult({
        ok: false,
        error: cause instanceof ApiError ? cause : new ApiError(0, "unknown", "Invoke failed.", null),
      });
    } finally {
      setSubmitting(false);
    }
  }

  const agentById = (id: string) => agents.data?.agents.find((a) => a.id === id);

  return (
    <>
      {/* --- Default profiles (spec 020) ---------------------------------- */}
      <Card>
        <CardHeader
          title="Default profiles"
          description="Product-curated agent profiles your organization can adopt. Adoption creates a tenant instance you own — explicit grants, tenant policy (deny-overrides) applies like any other agent."
          actions={
            <Button variant="secondary" size="sm" onClick={() => setPolicyOpen(true)}>
              Effective policy
            </Button>
          }
        />
        {profiles.loading ? (
          <TableSkeleton rows={4} cols={4} />
        ) : profiles.error ? (
          <ErrorState error={profiles.error} onRetry={profiles.refetch} context="GET /api/agent-profiles" />
        ) : (profiles.data?.profiles.length ?? 0) === 0 ? (
          <EmptyState
            icon={<IconAgents size={18} />}
            title="No default profiles"
            description="The product catalog is empty — contact the provider."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Profile</Th>
                <Th>Suggested</Th>
                <Th>Status</Th>
                <Th className="w-40" />
              </tr>
            </thead>
            <tbody>
              {profiles.data!.profiles.map((p) => (
                <tr key={p.key} className="hover:bg-surface-alt/50">
                  <Td>
                    <p className="font-medium">{p.name}</p>
                    <p className="max-w-md text-[11px] text-muted">{p.description}</p>
                    {p.skills.length > 0 ? (
                      <p className="mt-0.5 text-[10px] text-faint">{p.skills.join(" · ")}</p>
                    ) : null}
                  </Td>
                  <Td>
                    <p className="text-[11px]">
                      <span className="text-faint">models:</span> {p.suggested_models.join(", ") || "—"}
                    </p>
                    <p className="text-[11px]">
                      <span className="text-faint">connectors:</span> {p.suggested_connectors.join(", ") || "—"}
                    </p>
                  </Td>
                  <Td>
                    {p.adopted_agent_id ? (
                      <Badge tone="success">adopted · {shortId(p.adopted_agent_id)}</Badge>
                    ) : (
                      <Badge tone="neutral">available</Badge>
                    )}
                  </Td>
                  <Td>
                    <div className="flex justify-end">
                      <Button
                        variant="primary"
                        size="sm"
                        loading={submitting}
                        disabled={p.adopted_agent_id !== null}
                        onClick={() => adoptProfile(p.key, p.name)}
                      >
                        Adopt
                      </Button>
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {/* --- Shared agents -------------------------------------------------- */}
      <Card>
        <CardHeader
          title="Shared agents"
          description="Agents published in the organization. Access is never implicit: every user needs an explicit grant."
          actions={
            <Button variant="primary" size="sm" onClick={() => setPublishOpen(true)}>
              <IconPlus size={13} /> Publish agent
            </Button>
          }
        />
        {agents.loading ? (
          <TableSkeleton rows={4} cols={4} />
        ) : agents.error ? (
          <ErrorState error={agents.error} onRetry={agents.refetch} context="GET /api/agents" />
        ) : (agents.data?.agents.length ?? 0) === 0 ? (
          <EmptyState
            icon={<IconAgents size={18} />}
            title="No shared agents visible"
            description="Adopt a default profile above, publish an agent to share it, or ask an owner to grant you access."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Agent</Th>
                <Th>Scope</Th>
                <Th>Your access</Th>
                <Th className="w-64" />
              </tr>
            </thead>
            <tbody>
              {agents.data!.agents.map((a) => {
                const grants = sessionGrants.filter((g) => g.agent_id === a.id);
                return (
                  <tr key={a.id} className="hover:bg-surface-alt/50">
                    <Td>
                      <p className="font-medium">{a.name}</p>
                      {a.description ? (
                        <p className="max-w-md text-[11px] text-muted">{a.description}</p>
                      ) : null}
                    </Td>
                    <Td>
                      <Badge tone={a.scope === "org" ? "info" : "neutral"}>{a.scope}</Badge>
                    </Td>
                    <Td>
                      <Badge tone={ACCESS_TONES[a.access ?? "view"] ?? "neutral"}>
                        {a.access ?? "view"}
                      </Badge>
                    </Td>
                    <Td>
                      <div className="flex justify-end gap-1.5">
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => {
                            setInvokeFor(a.id);
                            setInvokeAgentName(a.name);
                            setInvokeModel(effective?.allowed_models[0] ?? "");
                            setInvokeResult(null);
                          }}
                        >
                          Invoke
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => {
                            setGrantFor(a.id);
                            setGrantAgentName(a.name);
                          }}
                        >
                          Grant access
                        </Button>
                        <Button
                          variant="danger"
                          size="sm"
                          onClick={() => {
                            setRevokeForAgent(a.id);
                            setRevokeGrantId(grants[0]?.id ?? "");
                          }}
                        >
                          Revoke grant
                        </Button>
                      </div>
                      {grants.length > 0 ? (
                        <p className="mt-1 text-right text-[10px] text-faint">
                          {grants.length} grant(s) created this session
                        </p>
                      ) : null}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>

      {/* Effective policy (spec 020) */}
      <Dialog
        open={policyOpen}
        onClose={() => setPolicyOpen(false)}
        title="Your effective policy"
        description="Deny-overrides combination of org → department → user levels, applied to every agent invocation (default profiles included)."
        footer={
          <Button variant="ghost" size="sm" onClick={() => setPolicyOpen(false)}>
            Close
          </Button>
        }
      >
        {effective ? (
          <div className="space-y-3 text-sm">
            <div>
              <p className="text-[11px] uppercase tracking-wide text-faint">Approved models (effective)</p>
              <p className="font-mono text-xs">
                {effective.allowed_models.length > 0
                  ? effective.allowed_models.join(", ")
                  : "none — ask an admin to approve models"}
              </p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wide text-faint">Allowed connectors</p>
              <p className="font-mono text-xs">
                {effective.allowed_connectors.length > 0
                  ? effective.allowed_connectors.join(", ")
                  : "none"}
              </p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wide text-faint">Budget</p>
              <Badge tone={effective.budget_cutoff ? "danger" : "success"}>
                {effective.budget_cutoff ? "cut off — invoke blocked" : "within budget"}
              </Badge>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted">Loading…</p>
        )}
      </Dialog>

      {/* Invoke (spec 020: gate de gobernanza con veredicto visible) */}
      <Dialog
        open={invokeFor !== null}
        onClose={() => setInvokeFor(null)}
        title={`Invoke — ${invokeAgentName}`}
        description="Authorization + policy gate. Name the model you plan to use: the tenant policy decides (approved → 200, otherwise 403)."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setInvokeFor(null)}>
              Close
            </Button>
            <Button variant="primary" size="sm" loading={submitting} onClick={invoke}>
              Invoke
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field
            label="Model name (optional)"
            htmlFor="invoke-model"
            hint={
              effective
                ? `Your approved models: ${effective.allowed_models.join(", ") || "none"}`
                : undefined
            }
          >
            <Input
              id="invoke-model"
              value={invokeModel}
              onChange={(e) => setInvokeModel(e.target.value)}
              placeholder="glm-5.2"
              className="font-mono text-xs"
              list="invoke-model-options"
            />
            <datalist id="invoke-model-options">
              {(effective?.allowed_models ?? []).map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </Field>
          {invokeResult ? (
            invokeResult.ok ? (
              <div className="rounded-md border border-success/30 bg-success/10 p-3 text-xs">
                <p className="font-medium text-success">Authorized (200)</p>
                <p className="text-muted">
                  billed to {shortId(invokeResult.result.billed_to_user_id)} · access{" "}
                  {invokeResult.result.access}
                  {invokeResult.result.model_name
                    ? ` · model ${invokeResult.result.model_name}`
                    : ""}
                </p>
              </div>
            ) : (
              <div className="rounded-md border border-danger/30 bg-danger/10 p-3 text-xs">
                <p className="font-medium text-danger">
                  Denied ({invokeResult.error.status}) — {invokeResult.error.reason}
                </p>
                <p className="text-muted">{invokeResult.error.message}</p>
              </div>
            )
          ) : null}
        </div>
      </Dialog>

      {/* Publicar */}
      <Dialog
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
        title="Publish a shared agent"
        description="Published agents start with zero grants (REQ-AGT-001)."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setPublishOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" loading={submitting} disabled={!name.trim()} onClick={publish}>
              Publish
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Name" htmlFor="agent-name" hint="Unique per owner.">
            <Input id="agent-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Support triage agent" />
          </Field>
          <Field label="Description (optional)" htmlFor="agent-desc">
            <Input id="agent-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
          <Field label="Scope" htmlFor="agent-scope">
            <Select id="agent-scope" value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="dept">Department</option>
              <option value="org">Organization</option>
            </Select>
          </Field>
        </div>
      </Dialog>

      {/* Grant */}
      <Dialog
        open={grantFor !== null}
        onClose={() => setGrantFor(null)}
        title={`Grant access — ${grantAgentName}`}
        description="Explicit grant for a specific user (deny-overrides: no grant, no access)."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setGrantFor(null)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" loading={submitting} disabled={!granteeId.trim()} onClick={grant}>
              Create grant
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field
            label="Grantee user ID"
            htmlFor="grantee-id"
            hint="Paste the user's UUID (the user directory API is pending — see Users & Roles)."
          >
            <Input
              id="grantee-id"
              value={granteeId}
              onChange={(e) => setGranteeId(e.target.value)}
              placeholder="00000000-0000-0000-0000-000000000000"
              className="font-mono text-xs"
            />
          </Field>
          <Field label="Permission" htmlFor="grant-permission">
            <Select id="grant-permission" value={permission} onChange={(e) => setPermission(e.target.value)}>
              <option value="invoke">Invoke (use the agent)</option>
              <option value="view">View (see the agent)</option>
            </Select>
          </Field>
        </div>
      </Dialog>

      {/* Revocar grant por ID */}
      <Dialog
        open={revokeForAgent !== null}
        onClose={() => setRevokeForAgent(null)}
        title={`Revoke grant — ${agentById(revokeForAgent ?? "")?.name ?? ""}`}
        description="Revoking needs the grant ID returned at creation (a grants-listing API is pending)."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setRevokeForAgent(null)}>
              Cancel
            </Button>
            <Button variant="danger" size="sm" loading={submitting} disabled={!revokeGrantId.trim()} onClick={revokeGrant}>
              Revoke grant
            </Button>
          </>
        }
      >
        <Field label="Grant ID" htmlFor="revoke-grant-id">
          <Input
            id="revoke-grant-id"
            value={revokeGrantId}
            onChange={(e) => setRevokeGrantId(e.target.value)}
            placeholder="00000000-0000-0000-0000-000000000000"
            className="font-mono text-xs"
          />
        </Field>
      </Dialog>
    </>
  );
}
