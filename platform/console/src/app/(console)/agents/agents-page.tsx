"use client";

/**
 * Shared Agents — rediseño UX spec 037 (repro Miller: "no está lógico aún").
 *
 * Dos pestañas de primera intención (D-37-01):
 *  - "Default profiles": tarjetas del catálogo del producto con Adopt de un
 *    clic y la policy EFECTIVA del caller visible inline (REQ-037-002/003).
 *  - "My agents": lista con dueño por NOMBRE (no UUID), acceso, grants y
 *    estado (REQ-037-004).
 *
 * Cero pasteos de UUID (D-37-02): "Share" usa un selector de usuario real
 * (GET /api/users) y "Manage access" lista los grants reales con Revoke por
 * fila (GET /api/agents/{id}/grants + DELETE). El dialog Invoke mantiene el
 * gate real de gobernanza con veredicto en lenguaje llano (D-37-07).
 */
import { useEffect, useMemo, useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type {
  AgentGrantRow,
  AgentList,
  AgentProfilesResponse,
  GrantResult,
  InvokeResult,
  DirectoryList,
} from "@/lib/types";
import { shortId, dateTime } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardBody,
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
import { IconAgents, IconCheck, IconPlus, IconSearch, IconShield } from "@/components/icons";

const ACCESS_LABEL: Record<string, { label: string; tone: "accent" | "success" | "info" | "neutral" }> = {
  own: { label: "You own it", tone: "accent" },
  manage: { label: "You manage it", tone: "success" },
  invoke: { label: "Can use", tone: "info" },
  view: { label: "Can view", tone: "neutral" },
};

const PERMISSION_LABEL: Record<string, string> = {
  invoke: "Can use it (run the agent)",
  view: "Can view it (see it, not run it)",
};

export function AgentsPage() {
  const agents = useApi<AgentList>("/api/agents");
  const profiles = useApi<AgentProfilesResponse>("/api/agent-profiles");
  const users = useApi<DirectoryList>("/api/users");
  const { push } = useToast();

  // Pestaña activa: "profiles" por defecto para un usuario nuevo (D-37-01).
  const [tab, setTab] = useState<"profiles" | "mine">("mine");

  // Publicar
  const [publishOpen, setPublishOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [scope, setScope] = useState("dept");
  const [submitting, setSubmitting] = useState(false);

  // Share (grant) — selector de usuario real, sin UUID (REQ-037-005)
  const [shareFor, setShareFor] = useState<string | null>(null);
  const [shareAgentName, setShareAgentName] = useState("");
  const [granteeId, setGranteeId] = useState("");
  const [userFilter, setUserFilter] = useState("");
  const [permission, setPermission] = useState("invoke");

  // Manage access — lista real de grants con Revoke por fila (REQ-037-006)
  const [manageFor, setManageFor] = useState<string | null>(null);
  const [manageAgentName, setManageAgentName] = useState("");
  const [grantsByAgent, setGrantsByAgent] = useState<Record<string, AgentGrantRow[]>>({});
  const [grantsLoading, setGrantsLoading] = useState(false);

  // Invoke — gate real con veredicto llano (REQ-037-007)
  const [invokeFor, setInvokeFor] = useState<string | null>(null);
  const [invokeAgentName, setInvokeAgentName] = useState("");
  const [invokeModel, setInvokeModel] = useState("");
  const [invokeResult, setInvokeResult] = useState<
    { ok: true; result: InvokeResult } | { ok: false; error: ApiError } | null
  >(null);

  const effective = profiles.data?.effective_policy ?? null;
  const agentList = useMemo(() => agents.data?.agents ?? [], [agents.data]);
  const profileList = useMemo(() => profiles.data?.profiles ?? [], [profiles.data]);

  // Mapa user_id → nombre legible (dueños y grantees por nombre, no UUID).
  const userById = useMemo(() => {
    const map = new Map<string, string>();
    for (const u of users.data?.users ?? []) {
      map.set(u.user_id, u.display_name || u.email);
    }
    return map;
  }, [users.data]);

  const displayName = (id: string | null) =>
    !id ? "—" : userById.get(id) ?? shortId(id);

  // Grants de los agentes que el caller administra (own/manage): alimenta la
  // columna "Shared with" y el dialog Manage access. setState SOLO en .then
  // (regla react-hooks/set-state-in-effect).
  const manageableIds = useMemo(
    () =>
      agentList
        .filter((a) => a.access === "own" || a.access === "manage")
        .map((a) => a.id),
    [agentList],
  );
  useEffect(() => {
    if (manageableIds.length === 0) return;
    let cancelled = false;
    Promise.allSettled(
      manageableIds.map((id) => api<{ grants: AgentGrantRow[] }>(`/api/agents/${id}/grants`)),
    ).then((results) => {
      if (cancelled) return;
      const next: Record<string, AgentGrantRow[]> = {};
      results.forEach((r, i) => {
        if (r.status === "fulfilled") next[manageableIds[i]] = r.value.grants;
      });
      setGrantsByAgent(next);
    });
    return () => {
      cancelled = true;
    };
  }, [manageableIds]);

  async function refreshGrants(agentId: string) {
    setGrantsLoading(true);
    try {
      const result = await api<{ grants: AgentGrantRow[] }>(`/api/agents/${agentId}/grants`);
      setGrantsByAgent((g) => ({ ...g, [agentId]: result.grants }));
    } catch {
      // El listado de la tabla sigue válido; el dialog muestra el estado real.
    } finally {
      setGrantsLoading(false);
    }
  }

  async function publish() {
    setSubmitting(true);
    try {
      await api("/api/agents", {
        method: "POST",
        body: { name, description: description || null, scope },
      });
      push("success", `"${name}" is live. Nobody can use it yet — share it with the people you choose.`);
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
      await api<{ agent_id: string }>(`/api/agent-profiles/${key}/adopt`, {
        method: "POST",
      });
      push(
        "success",
        `"${profileName}" is now your agent. Find it under "My agents" — share it with people, and your organization's rules apply automatically.`,
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
    if (!shareFor || !granteeId) return;
    setSubmitting(true);
    try {
      await api<GrantResult>(`/api/agents/${shareFor}/grants`, {
        method: "POST",
        body: { user_id: granteeId, permission },
      });
      push(
        "success",
        `${displayName(granteeId)} now ${permission === "invoke" ? "can use" : "can view"} "${shareAgentName}".`,
      );
      setShareFor(null);
      setGranteeId("");
      setUserFilter("");
      refreshGrants(shareFor);
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Sharing failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function revokeGrant(agentId: string, grantId: string) {
    setSubmitting(true);
    try {
      await api(`/api/agents/${agentId}/grants/${grantId}`, { method: "DELETE" });
      setGrantsByAgent((g) => ({
        ...g,
        [agentId]: (g[agentId] ?? []).filter((x) => x.id !== grantId),
      }));
      push("success", "Access revoked — it stops working immediately.");
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
      // 403 model_not_approved / budget_cutoff: veredicto REAL del gate.
      setInvokeResult({
        ok: false,
        error: cause instanceof ApiError ? cause : new ApiError(0, "unknown", "Invoke failed.", null),
      });
    } finally {
      setSubmitting(false);
    }
  }

  const availableUsers = (users.data?.users ?? []).filter(
    (u) =>
      u.status === "active" &&
      u.user_id !== agentList.find((a) => a.id === shareFor)?.owner_user_id &&
      (userFilter.trim() === "" ||
        `${u.display_name ?? ""} ${u.email}`.toLowerCase().includes(userFilter.trim().toLowerCase())),
  );

  const manageGrants = manageFor ? grantsByAgent[manageFor] ?? [] : [];

  return (
    <div className="space-y-6">
      {/* Un solo panel: los agentes se crean aquí y se incluyen en una política. */}

      {/* --- Pestaña: Default profiles (primera intención, REQ-037-001/002/003) --- */}
      {tab === "profiles" ? (
        <>
          {/* Policy efectiva INLINE — visible sin abrir ningún dialog */}
          <Card>
            <CardBody className="flex flex-wrap items-center gap-x-6 gap-y-3">
              <div className="flex items-center gap-2 text-sm">
                <IconShield size={15} className="text-accent" />
                <span className="font-medium text-foreground">Your rules</span>
                <span className="text-muted">— applied to every agent you adopt or run:</span>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-xs text-faint">Approved models:</span>
                {effective && effective.allowed_models.length > 0 ? (
                  effective.allowed_models.map((m) => (
                    <Badge key={m} tone="info">
                      {m}
                    </Badge>
                  ))
                ) : (
                  <Badge tone="neutral">none yet — ask an admin</Badge>
                )}
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-faint">Budget:</span>
                {effective ? (
                  <Badge tone={effective.budget_cutoff ? "danger" : "success"}>
                    {effective.budget_cutoff ? "cut off — blocked" : "within budget"}
                  </Badge>
                ) : (
                  <Badge tone="neutral">…</Badge>
                )}
              </div>
            </CardBody>
          </Card>

          {profiles.loading ? (
            <TableSkeleton rows={3} cols={3} />
          ) : profiles.error ? (
            <ErrorState error={profiles.error} onRetry={profiles.refetch} context="GET /api/agent-profiles" />
          ) : profileList.length === 0 ? (
            <EmptyState
              icon={<IconAgents size={18} />}
              title="No default profiles"
              description="The product catalog is empty — contact the provider."
            />
          ) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {profileList.map((p, i) => (
                <Card key={p.key} className="ta-rise flex flex-col" style={{ animationDelay: `${i * 60}ms` }}>
                  <CardBody className="flex flex-1 flex-col gap-3">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="text-sm font-semibold text-foreground">{p.name}</p>
                        <p className="mt-1 text-xs leading-relaxed text-muted">{p.description}</p>
                      </div>
                      {p.adopted_agent_id ? (
                        <Badge tone="success">
                          <IconCheck size={11} /> Adopted
                        </Badge>
                      ) : (
                        <Badge tone="neutral">Available</Badge>
                      )}
                    </div>

                    {p.skills.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {p.skills.map((s) => (
                          <span
                            key={s}
                            className="rounded-full bg-surface-alt px-2 py-0.5 text-[10px] text-muted"
                          >
                            {s}
                          </span>
                        ))}
                      </div>
                    ) : null}

                    <div className="text-[11px] leading-relaxed text-faint">
                      {p.suggested_models.length > 0 ? (
                        <p>
                          <span className="text-muted">Works with models:</span> {p.suggested_models.join(", ")}
                        </p>
                      ) : null}
                      {p.suggested_connectors.length > 0 ? (
                        <p>
                          <span className="text-muted">Can connect to:</span> {p.suggested_connectors.join(", ")}
                        </p>
                      ) : null}
                    </div>

                    <div className="mt-auto pt-2">
                      {p.adopted_agent_id ? (
                        <Button
                          variant="secondary"
                          size="sm"
                          className="w-full"
                          onClick={() => setTab("mine")}
                        >
                          Manage in “My agents”
                        </Button>
                      ) : (
                        <Button
                          variant="primary"
                          size="sm"
                          className="w-full"
                          loading={submitting}
                          onClick={() => adoptProfile(p.key, p.name)}
                        >
                          Adopt — make it mine
                        </Button>
                      )}
                    </div>
                  </CardBody>
                </Card>
              ))}
            </div>
          )}
          <p className="text-xs text-faint">
            New here? Adopt a ready-made agent — it becomes yours in one click, your organization&apos;s rules
            apply automatically, and you choose exactly who can use it.
          </p>
        </>
      ) : (
        /* --- Pestaña: My agents (REQ-037-004) ---------------------------------- */
        <Card>
          <CardHeader
            title="Agentes"
            description="Agents you own or have access to. Access is never implicit — every person needs an explicit grant from the owner."
            actions={
              <Button variant="primary" size="sm" onClick={() => setPublishOpen(true)}>
                <IconPlus size={13} /> Crear agente
              </Button>
            }
          />
          {agents.loading ? (
            <TableSkeleton rows={4} cols={5} />
          ) : agents.error ? (
            <ErrorState error={agents.error} onRetry={agents.refetch} context="GET /api/agents" />
          ) : agentList.length === 0 ? (
            <EmptyState
              icon={<IconAgents size={18} />}
              title="You have no agents yet"
              description="Crea tu primer agente y luego inclúyelo en una política."
              action={
                <Button variant="primary" size="sm" onClick={() => setPublishOpen(true)}>
                  Crear agente
                </Button>
              }
            />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Agent</Th>
                  <Th>Owner</Th>
                  <Th>Your access</Th>
                  <Th>Shared with</Th>
                  <Th className="w-64" />
                </tr>
              </thead>
              <tbody>
                {agentList.map((a) => {
                  const isMine = a.access === "own" || a.access === "manage";
                  const grants = grantsByAgent[a.id];
                  return (
                    <tr key={a.id} className="hover:bg-surface-alt/50">
                      <Td>
                        <p className="font-medium">{a.name}</p>
                        {a.description ? (
                          <p className="max-w-md text-[11px] text-muted">{a.description}</p>
                        ) : null}
                      </Td>
                      <Td className="text-xs text-muted">
                        {a.access === "own" ? "You" : displayName(a.owner_user_id)}
                      </Td>
                      <Td>
                        <Badge tone={ACCESS_LABEL[a.access ?? "view"]?.tone ?? "neutral"}>
                          {ACCESS_LABEL[a.access ?? "view"]?.label ?? a.access}
                        </Badge>
                      </Td>
                      <Td className="text-xs text-muted">
                        {!isMine ? (
                          "—"
                        ) : grants === undefined ? (
                          "…"
                        ) : grants.length === 0 ? (
                          "Nobody yet"
                        ) : (
                          `${grants.length} ${grants.length === 1 ? "person" : "people"}`
                        )}
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
                            Try it
                          </Button>
                          {isMine ? (
                            <>
                              <Button
                                variant="secondary"
                                size="sm"
                                onClick={() => {
                                  setShareFor(a.id);
                                  setShareAgentName(a.name);
                                  setGranteeId("");
                                  setUserFilter("");
                                  setPermission("invoke");
                                }}
                              >
                                Share
                              </Button>
                              <Button
                                variant="secondary"
                                size="sm"
                                onClick={() => {
                                  setManageFor(a.id);
                                  setManageAgentName(a.name);
                                  refreshGrants(a.id);
                                }}
                              >
                                Manage access
                              </Button>
                            </>
                          ) : null}
                        </div>
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          )}
        </Card>
      )}

      {/* Try it / Invoke — gate real con veredicto llano (REQ-037-007) */}
      <Dialog
        open={invokeFor !== null}
        onClose={() => setInvokeFor(null)}
        title={`Try "${invokeAgentName}"`}
        description="Runs a test invocation. Your organization's policy decides on the spot: approved model and budget → it runs; anything else → it tells you why not."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setInvokeFor(null)}>
              Close
            </Button>
            <Button variant="primary" size="sm" loading={submitting} onClick={invoke}>
              Run test
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field
            label="Model"
            htmlFor="invoke-model"
            hint={
              effective
                ? `Models your organization approved: ${effective.allowed_models.join(", ") || "none"}`
                : undefined
            }
          >
            <Select
              id="invoke-model"
              value={invokeModel}
              onChange={(e) => setInvokeModel(e.target.value)}
              className="font-mono text-xs"
            >
              <option value="">Don&apos;t name a model</option>
              {(effective?.allowed_models ?? []).map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </Select>
          </Field>
          {invokeResult ? (
            invokeResult.ok ? (
              <div className="rounded-md border border-success/30 bg-success/10 p-3 text-xs">
                <p className="font-medium text-success">It runs — authorized.</p>
                <p className="text-muted">
                  Billed to you ({shortId(invokeResult.result.billed_to_user_id)})
                  {invokeResult.result.model_name ? ` · model ${invokeResult.result.model_name}` : ""}
                </p>
              </div>
            ) : (
              <div className="rounded-md border border-danger/30 bg-danger/10 p-3 text-xs">
                <p className="font-medium text-danger">
                  {invokeResult.error.reason === "budget_cutoff"
                    ? "Blocked — your budget is used up."
                    : invokeResult.error.reason === "model_not_approved"
                      ? "Blocked — that model isn't approved for you."
                      : `Blocked (${invokeResult.error.reason}).`}
                </p>
                <p className="text-muted">{invokeResult.error.message}</p>
              </div>
            )
          ) : null}
        </div>
      </Dialog>

      {/* Share — selector de usuario real (REQ-037-005) */}
      <Dialog
        open={shareFor !== null}
        onClose={() => setShareFor(null)}
        title={`Share "${shareAgentName}"`}
        description="Pick a person and what they can do. They get access immediately — and you can take it back anytime."
        width="max-w-lg"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setShareFor(null)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" loading={submitting} disabled={!granteeId} onClick={grant}>
              Give access
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Who" htmlFor="share-user-filter">
            <div className="relative">
              <IconSearch
                size={14}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"
              />
              <Input
                id="share-user-filter"
                value={userFilter}
                onChange={(e) => setUserFilter(e.target.value)}
                placeholder="Search by name or email…"
                className="pl-8"
              />
            </div>
          </Field>
          <div className="max-h-56 space-y-1.5 overflow-y-auto rounded-lg border border-border-subtle p-1.5">
            {users.loading ? (
              <p className="px-2 py-3 text-xs text-muted">Loading people…</p>
            ) : availableUsers.length === 0 ? (
              <p className="px-2 py-3 text-xs text-muted">
                Nobody matches. Your directory shows the people in your scope — an admin can share with anyone.
              </p>
            ) : (
              availableUsers.map((u) => (
                <button
                  key={u.user_id}
                  type="button"
                  onClick={() => setGranteeId(u.user_id)}
                  className={`flex w-full items-center justify-between gap-3 rounded-md border px-3 py-2 text-left transition-all duration-150 ease-out active:scale-[0.99] ${
                    granteeId === u.user_id
                      ? "border-accent/50 bg-accent/10"
                      : "border-transparent hover:bg-surface-alt/60"
                  }`}
                >
                  <span>
                    <span className="block text-sm font-medium text-foreground">
                      {u.display_name || u.email}
                    </span>
                    <span className="block text-[11px] text-muted">{u.email}</span>
                  </span>
                  {granteeId === u.user_id ? (
                    <IconCheck size={14} className="text-accent" />
                  ) : null}
                </button>
              ))
            )}
          </div>
          <Field label="What can they do" htmlFor="share-permission">
            <Select id="share-permission" value={permission} onChange={(e) => setPermission(e.target.value)}>
              <option value="invoke">{PERMISSION_LABEL.invoke}</option>
              <option value="view">{PERMISSION_LABEL.view}</option>
            </Select>
          </Field>
        </div>
      </Dialog>

      {/* Manage access — grants reales con Revoke por fila (REQ-037-006) */}
      <Dialog
        open={manageFor !== null}
        onClose={() => setManageFor(null)}
        title={`Who has access to "${manageAgentName}"`}
        description="Every person with explicit access. Revoke takes effect immediately and is audited."
        width="max-w-lg"
        footer={
          <Button variant="ghost" size="sm" onClick={() => setManageFor(null)}>
            Close
          </Button>
        }
      >
        {grantsLoading ? (
          <p className="py-3 text-xs text-muted">Loading…</p>
        ) : manageGrants.length === 0 ? (
          <p className="py-3 text-xs text-muted">
            Nobody has access yet — use “Share” to give someone access.
          </p>
        ) : (
          <ul className="divide-y divide-border-subtle rounded-lg border border-border-subtle">
            {manageGrants.map((g) => (
              <li key={g.id} className="flex items-center justify-between gap-3 px-3.5 py-2.5">
                <div>
                  <p className="text-sm font-medium text-foreground">
                    {userById.get(g.grantee_user_id) ?? shortId(g.grantee_user_id)}
                  </p>
                  <p className="text-[11px] text-muted">
                    {g.permission === "invoke" ? "Can use it" : "Can view it"}
                    {g.granted_at ? ` · since ${dateTime(g.granted_at)}` : ""}
                  </p>
                </div>
                <Button
                  variant="danger"
                  size="sm"
                  loading={submitting}
                  onClick={() => manageFor && revokeGrant(manageFor, g.id)}
                >
                  Revoke
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Dialog>

      {/* Publicar */}
      <Dialog
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
        title="Crear agente"
        description="A published agent starts with zero access — you choose exactly who can use it."
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
          <Field label="Who can potentially see it" htmlFor="agent-scope" hint="Access is still explicit — visibility scope only decides who you can share it with.">
            <Select id="agent-scope" value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="dept">My department</option>
              <option value="org">The whole organization</option>
            </Select>
          </Field>
        </div>
      </Dialog>
    </div>
  );
}
