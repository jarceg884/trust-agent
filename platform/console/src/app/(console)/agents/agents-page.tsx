"use client";

/**
 * Shared Agents (spec 009 REQ-AC-016): lista de agentes visibles con columna
 * de acceso, publicar agente y gestión de grants explícitos. El listado de
 * grants existentes por agente es contrato pendiente (D-AC-06): la UI muestra
 * los grants creados en esta sesión y ofrece revocación por grant_id.
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type { AgentList, GrantResult } from "@/lib/types";
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

  const agentById = (id: string) => agents.data?.agents.find((a) => a.id === id);

  return (
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
          description="Publish an agent to share it with your organization, or ask an owner to grant you access."
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Agent</Th>
              <Th>Scope</Th>
              <Th>Your access</Th>
              <Th className="w-52" />
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
    </Card>
  );
}
