"use client";

/**
 * Credentials & Connections — rediseño UX spec 037 (repro Miller: "muy poco
 * user friendly").
 *
 * Separación visual de dos mundos (D-37-05):
 *  1. "Catalog connections": integraciones listas para usar (VMware, email…)
 *     en tarjetas, con el estado de policy visible por app (REQ-037-011) y
 *     Connect/Disconnect.
 *  2. "My custom connections": conexiones propias de la organización (bases
 *     de datos, API keys) creadas con un WIZARD de 3 pasos: tipo → datos →
 *     secreto one-time (REQ-037-012, D-37-04).
 *  3. "My secrets": el vault personal con labels llanos (REQ-037-013).
 *
 * El estado de policy (`allowed_for_caller`) lo computa el backend con el
 * mismo engine deny-overrides del runtime (D-37-03); la UI jamás lo deriva.
 * Secretos: cifrados, mostrados EXACTAMENTE una vez (constitution IV).
 */
import { useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, ApiError } from "@/lib/api-client";
import type {
  ConnectorInstance,
  ConnectorList,
  CredentialList,
  CredentialMeta,
  CredentialSecretResult,
  CustomConnectionList,
  CustomConnectionMeta,
  CustomConnectionSecretResult,
  InstanceList,
} from "@/lib/types";
import { dateTime, timeAgo } from "@/lib/format";
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
  OneTimeSecret,
  Select,
  Table,
  TableSkeleton,
  Td,
  Textarea,
  Th,
  useToast,
} from "@/components/ui";
import { IconCredentials, IconPlus } from "@/components/icons";
import { Database, KeyRound, Package, ShieldCheck, ShieldX, Zap } from "lucide-react";

/** Badge de policy por conexión (REQ-037-011) — dato del backend, no derivado. */
function PolicyBadge({ allowed }: { allowed: boolean }) {
  return allowed ? (
    <Badge tone="success">
      <ShieldCheck size={11} /> Allowed by your organization
    </Badge>
  ) : (
    <Badge tone="danger">
      <ShieldX size={11} /> Blocked by policy
    </Badge>
  );
}

const KIND_LABEL: Record<CustomConnectionMeta["kind"], string> = {
  database: "Database",
  api_key: "API key",
  custom: "Other",
  llm: "LLM (BYOK)",
};

/** Resumen legible de los detalles no-secreto de una conexión (REQ-037-013). */
function detailsSummary(c: CustomConnectionMeta): string {
  const m = c.metadata ?? {};
  if (c.kind === "database") {
    const parts = [m.type, m.host, m.database ? `/${m.database}` : null].filter(
      (x): x is string => Boolean(x),
    );
    return parts.join(" · ") || "—";
  }
  if (c.kind === "api_key") {
    const parts = [m.service, m.auth === "header" ? `header ${m.header_name ?? ""}` : "bearer"].filter(
      (x): x is string => Boolean(x),
    );
    return parts.join(" · ") || "—";
  }
  const keys = Object.keys(m);
  return keys.length > 0 ? keys.join(", ") : "—";
}

export function CredentialsPage() {
  const credentials = useApi<CredentialList>("/api/vault/credentials");
  const connectors = useApi<ConnectorList>("/api/connectors");
  const instances = useApi<InstanceList>("/api/connectors/instances");
  const customConnections = useApi<CustomConnectionList>("/api/vault/connections");
  // Spec 043: fetch byok_allowed to conditionally show the LLM (BYOK) option.
  const accessOverview = useApi<{ byok_allowed?: boolean }>("/api/models/access");
  const byokAllowed = accessOverview.data?.byok_allowed ?? true;
  const { push } = useToast();

  // ---- My secrets (vault personal) ----------------------------------------
  const [secretOpen, setSecretOpen] = useState(false);
  const [name, setName] = useState("");
  const [credType, setCredType] = useState("api_key");
  const [secret, setSecret] = useState("");
  const [connectorSlug, setConnectorSlug] = useState("");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [oneTime, setOneTime] = useState<{ value: string; label: string } | null>(null);

  const [rotateFor, setRotateFor] = useState<CredentialMeta | null>(null);
  const [revokeFor, setRevokeFor] = useState<CredentialMeta | null>(null);

  // ---- Catalog connections -------------------------------------------------
  const [connectFor, setConnectFor] = useState<string | null>(null);
  const [disconnectFor, setDisconnectFor] = useState<ConnectorInstance | null>(null);

  // ---- Wizard de conexión personalizada (REQ-037-012) ----------------------
  const [wizardOpen, setWizardOpen] = useState(false);
  const [step, setStep] = useState(1);
  const [connName, setConnName] = useState("");
  const [connKind, setConnKind] = useState<"database" | "api_key" | "custom" | "llm">("database");
  const [connSecret, setConnSecret] = useState("");
  // database
  const [dbType, setDbType] = useState("postgresql");
  const [dbHost, setDbHost] = useState("");
  const [dbPort, setDbPort] = useState("5432");
  const [dbName, setDbName] = useState("");
  const [dbUser, setDbUser] = useState("");
  // api_key
  const [akService, setAkService] = useState("");
  const [akAuth, setAkAuth] = useState<"bearer" | "header">("bearer");
  const [akHeader, setAkHeader] = useState("");
  // custom
  const [customJson, setCustomJson] = useState("{}");
  // LLM (BYOK) connection fields ('spec 043)
  const [llmBaseUrl, setLlmBaseUrl] = useState("");
  const [connSubmitting, setConnSubmitting] = useState(false);
  const [connOneTime, setConnOneTime] = useState<{ value: string; label: string } | null>(null);

  // ---- Editar / rotar / revocar conexión existente --------------------------
  const [connEditFor, setConnEditFor] = useState<CustomConnectionMeta | null>(null);
  const [connRotateFor, setConnRotateFor] = useState<CustomConnectionMeta | null>(null);
  const [connRevokeFor, setConnRevokeFor] = useState<CustomConnectionMeta | null>(null);

  function connMetadata(): Record<string, unknown> {
    if (connKind === "database") {
      return {
        type: dbType,
        host: dbHost,
        port: Number(dbPort),
        database: dbName,
        username: dbUser,
      };
    }
    if (connKind === "api_key") {
      const meta: Record<string, unknown> = { service: akService, auth: akAuth };
      if (akAuth === "header") meta.header_name = akHeader;
      return meta;
    }
    if (connKind === "llm") {
      return { base_url: llmBaseUrl };
    }
    return JSON.parse(customJson || "{}") as Record<string, unknown>;
  }

  function resetConnForm() {
    setConnName("");
    setConnKind("database");
    setConnSecret("");
    setDbType("postgresql");
    setDbHost("");
    setDbPort("5432");
    setDbName("");
    setDbUser("");
    setAkService("");
    setAkAuth("bearer");
    setAkHeader("");
    setCustomJson("{}");
  }

  function stepValid(): boolean {
    if (step === 1) return true;
    if (step === 2) {
      if (!connName.trim()) return false;
      if (connKind === "database") return dbHost.trim() !== "" && dbName.trim() !== "";
      if (connKind === "api_key") return akService.trim() !== "";
      if (connKind === "llm") return llmBaseUrl.trim() !== "";
      try {
        JSON.parse(customJson || "{}");
        return true;
      } catch {
        return false;
      }
    }
    return connSecret.length > 0;
  }

  async function createConnection() {
    setConnSubmitting(true);
    try {
      let metadata: Record<string, unknown>;
      try {
        metadata = connMetadata();
      } catch {
        push("error", "The details must be valid JSON.");
        return;
      }
      const result = await api<CustomConnectionSecretResult>("/api/vault/connections", {
        method: "POST",
        body: { name: connName, kind: connKind, metadata, secret_value: connSecret },
      });
      setConnOneTime({
        value: result.secret_value ?? "",
        label: `Secret for "${connName}" — copy it now, it won't be shown again.`,
      });
      setWizardOpen(false);
      setStep(1);
      resetConnForm();
      customConnections.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Creation failed.");
    } finally {
      setConnSubmitting(false);
    }
  }

  async function updateConnection(rotate: boolean) {
    const target = connEditFor;
    if (!target) return;
    setConnSubmitting(true);
    try {
      let metadata: Record<string, unknown>;
      try {
        metadata = connMetadata();
      } catch {
        push("error", "The details must be valid JSON.");
        return;
      }
      const body: Record<string, unknown> = { name: connName, kind: connKind, metadata };
      if (rotate) body.new_secret_value = connSecret;
      const result = await api<CustomConnectionSecretResult>(
        `/api/vault/connections/${target.id}`,
        { method: "PUT", body },
      );
      if (result.secret_value) {
        setConnOneTime({
          value: result.secret_value,
          label: `New secret for "${target.name}" — copy it now, it won't be shown again.`,
        });
      } else {
        push("success", `"${connName}" updated.`);
      }
      setConnEditFor(null);
      setConnRotateFor(null);
      resetConnForm();
      customConnections.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Update failed.");
    } finally {
      setConnSubmitting(false);
    }
  }

  async function revokeConnection() {
    if (!connRevokeFor) return;
    setConnSubmitting(true);
    try {
      await api(`/api/vault/connections/${connRevokeFor.id}/revoke`, { method: "POST" });
      push("success", `"${connRevokeFor.name}" revoked — anything using it stops immediately.`);
      setConnRevokeFor(null);
      customConnections.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Revoke failed.");
    } finally {
      setConnSubmitting(false);
    }
  }

  function openEdit(c: CustomConnectionMeta, rotate: boolean) {
    setConnEditFor(c);
    setConnRotateFor(rotate ? c : null);
    setConnName(c.name);
    setConnKind(c.kind);
    setConnSecret("");
    const m = c.metadata ?? {};
    setDbType(String(m.type ?? "postgresql"));
    setDbHost(String(m.host ?? ""));
    setDbPort(String(m.port ?? 5432));
    setDbName(String(m.database ?? ""));
    setDbUser(String(m.username ?? ""));
    setAkService(String(m.service ?? ""));
    setAkAuth(m.auth === "header" ? "header" : "bearer");
    setAkHeader(String(m.header_name ?? ""));
    setCustomJson(JSON.stringify(m, null, 2));
  }

  async function createCredential() {
    setSubmitting(true);
    try {
      const result = await api<CredentialSecretResult>("/api/vault/credentials", {
        method: "POST",
        body: {
          name,
          credential_type: credType,
          secret_value: secret,
          connector_slug: connectorSlug || null,
          scope: note || null,
        },
      });
      setOneTime({
        value: result.secret_value,
        label: `Secret for "${name}" — copy it now, it won't be shown again.`,
      });
      setSecretOpen(false);
      setName("");
      setSecret("");
      setConnectorSlug("");
      setNote("");
      credentials.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Creation failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function rotateCredential() {
    if (!rotateFor) return;
    setSubmitting(true);
    try {
      const result = await api<CredentialSecretResult>(
        `/api/vault/credentials/${rotateFor.id}/rotate`,
        { method: "POST", body: {} },
      );
      setOneTime({
        value: result.secret_value,
        label: `New secret for "${rotateFor.name}" — the old one stopped working.`,
      });
      setRotateFor(null);
      credentials.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Rotation failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function revokeCredential() {
    if (!revokeFor) return;
    setSubmitting(true);
    try {
      await api(`/api/vault/credentials/${revokeFor.id}/revoke`, { method: "POST" });
      push("success", `"${revokeFor.name}" revoked — apps using it were cut off.`);
      setRevokeFor(null);
      credentials.refetch();
      instances.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Revoke failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function connectInstance() {
    if (!connectFor) return;
    setSubmitting(true);
    try {
      await api("/api/connectors/instances", {
        method: "POST",
        body: { connector_slug: connectFor, config: {} },
      });
      push("success", "Connected — your agents can use this app now.");
      setConnectFor(null);
      instances.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Connection failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function disconnectInstance() {
    if (!disconnectFor) return;
    setSubmitting(true);
    try {
      await api(`/api/connectors/instances/${disconnectFor.id}`, { method: "DELETE" });
      push("success", "Disconnected — agents lose access to this app immediately.");
      setDisconnectFor(null);
      instances.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Disconnect failed.");
    } finally {
      setSubmitting(false);
    }
  }

  const wizardSteps = ["Type", "Details", "Secret"];

  return (
    <div className="space-y-8">
      {/* === 1. Catalog connections (D-37-05/06, REQ-037-008/011) ============ */}
      <Card>
        <CardHeader
          title="Catalog connections"
          description="Ready-to-use apps curated by the platform. Connect one and your agents can work with it — if your organization allows it."
        />
        {connectors.loading || instances.loading ? (
          <TableSkeleton rows={3} cols={3} />
        ) : connectors.error ? (
          <ErrorState error={connectors.error} onRetry={connectors.refetch} context="GET /api/connectors" />
        ) : (
          <CardBody className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {(connectors.data?.connectors ?? []).map((c, i) => {
                const instance = instances.data?.instances.find(
                  (it) => it.connector_slug === c.slug && it.status === "active",
                );
                return (
                  <div
                    key={c.slug}
                    className="ta-rise flex flex-col rounded-lg border border-border-subtle p-4"
                    style={{ animationDelay: `${i * 50}ms` }}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="text-sm font-medium text-foreground">{c.name}</p>
                        <p className="text-[11px] text-muted">{c.category}</p>
                      </div>
                      {instance ? (
                        <Badge tone="success">Connected</Badge>
                      ) : (
                        <Badge tone="neutral">Not connected</Badge>
                      )}
                    </div>
                    <div className="mt-2.5">
                      <PolicyBadge allowed={c.allowed_for_caller} />
                    </div>
                    <div className="mt-auto pt-3.5">
                      {instance ? (
                        <Button variant="danger" size="sm" onClick={() => setDisconnectFor(instance)}>
                          Disconnect
                        </Button>
                      ) : c.allowed_for_caller ? (
                        <Button variant="secondary" size="sm" onClick={() => setConnectFor(c.slug)}>
                          Connect
                        </Button>
                      ) : (
                        <p className="text-[11px] leading-relaxed text-faint">
                          Ask an admin to allow this app for your team.
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
            {(instances.data?.instances.length ?? 0) > 0 ? (
              <div>
                <p className="mb-2 text-xs font-semibold text-foreground">Active connections</p>
                <ul className="divide-y divide-border-subtle rounded-lg border border-border-subtle">
                  {instances.data!.instances.map((it) => (
                    <li key={it.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
                      <div>
                        <p className="text-xs text-foreground">
                          {connectors.data?.connectors.find((c) => c.slug === it.connector_slug)?.name ??
                            it.connector_slug}
                        </p>
                        <p className="text-[11px] text-muted">connected {dateTime(it.created_at)}</p>
                      </div>
                      <Badge tone={it.status === "active" ? "success" : "neutral"}>{it.status}</Badge>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </CardBody>
        )}
      </Card>

      {/* === 2. My custom connections (wizard, REQ-037-012) =================== */}
      <Card>
        <CardHeader
          title="My custom connections"
          description="Your organization's own connections — a database agents can query, an internal API, anything beyond the catalog."
          actions={
            <Button variant="primary" size="sm" onClick={() => { setWizardOpen(true); setStep(1); }}>
              <IconPlus size={13} /> New connection
            </Button>
          }
        />
        {customConnections.loading ? (
          <TableSkeleton rows={3} cols={5} />
        ) : customConnections.error ? (
          customConnections.error.status === 403 ? (
            <EmptyState
              title="Managed by your organization admins"
              description="Creating and maintaining custom connections is an admin task. Ask your org admin if you need a new one."
            />
          ) : (
            <ErrorState error={customConnections.error} onRetry={customConnections.refetch} context="GET /api/vault/connections" />
          )
        ) : (customConnections.data?.connections.length ?? 0) === 0 ? (
          <EmptyState
            icon={<Database size={18} />}
            title="No custom connections yet"
            description='Create your first one with "New connection" — three quick steps: what it is, its details, and its secret.'
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Type</Th>
                <Th>Details</Th>
                <Th>Policy</Th>
                <Th>Status</Th>
                <Th>Updated</Th>
                <Th className="w-44" />
              </tr>
            </thead>
            <tbody>
              {customConnections.data!.connections.map((c) => (
                <tr key={c.id} className="hover:bg-surface-alt/50">
                  <Td className="font-medium">{c.name}</Td>
                  <Td>
                    <Badge tone={c.kind === "database" ? "info" : c.kind === "api_key" ? "accent" : "neutral"}>
                      {KIND_LABEL[c.kind]}
                    </Badge>
                  </Td>
                  <Td className="max-w-56">
                    <p className="truncate text-[11px] text-muted" title={detailsSummary(c)}>
                      {detailsSummary(c)}
                    </p>
                  </Td>
                  <Td>
                    <PolicyBadge allowed={c.allowed_for_caller} />
                  </Td>
                  <Td>
                    <Badge tone={c.status === "active" ? "success" : "neutral"}>{c.status}</Badge>
                  </Td>
                  <Td className="text-xs text-muted">{timeAgo(c.last_rotated_at ?? c.created_at)}</Td>
                  <Td>
                    <div className="flex justify-end gap-1.5">
                      {c.status === "active" ? (
                        <>
                          <Button variant="secondary" size="sm" onClick={() => openEdit(c, false)}>
                            Edit
                          </Button>
                          <Button variant="secondary" size="sm" onClick={() => openEdit(c, true)}>
                            Rotate secret
                          </Button>
                          <Button variant="danger" size="sm" onClick={() => setConnRevokeFor(c)}>
                            Revoke
                          </Button>
                        </>
                      ) : (
                        <span className="text-[11px] text-faint">Revoked</span>
                      )}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {/* === 3. My secrets (vault personal, labels llanos) ==================== */}
      <Card>
        <CardHeader
          title="My secrets"
          description="API keys and passwords for your own tools. Encrypted at rest, shown exactly once when created or rotated."
          actions={
            <Button variant="primary" size="sm" onClick={() => setSecretOpen(true)}>
              <IconPlus size={13} /> Add secret
            </Button>
          }
        />
        {credentials.loading ? (
          <TableSkeleton rows={4} cols={5} />
        ) : credentials.error ? (
          <ErrorState error={credentials.error} onRetry={credentials.refetch} context="GET /api/vault/credentials" />
        ) : (credentials.data?.credentials.length ?? 0) === 0 ? (
          <EmptyState
            icon={<IconCredentials size={18} />}
            title="No secrets yet"
            description="Store an API key or password here and connect it to a catalog app — agents use it without ever seeing the value."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Type</Th>
                <Th>Linked app</Th>
                <Th>Status</Th>
                <Th>Rotated</Th>
                <Th>Used</Th>
                <Th className="w-36" />
              </tr>
            </thead>
            <tbody>
              {credentials.data!.credentials.map((c) => (
                <tr key={c.id} className="hover:bg-surface-alt/50">
                  <Td className="font-medium">{c.name}</Td>
                  <Td>
                    <Badge>{c.credential_type}</Badge>
                  </Td>
                  <Td className="text-xs text-muted">
                    {connectors.data?.connectors.find((x) => x.slug === c.connector_slug)?.name ??
                      c.connector_slug ??
                      "—"}
                  </Td>
                  <Td>
                    <Badge tone={c.status === "active" ? "success" : "neutral"}>{c.status}</Badge>
                  </Td>
                  <Td className="text-xs text-muted">{timeAgo(c.last_rotated_at ?? c.created_at)}</Td>
                  <Td className="text-xs text-muted">{timeAgo(c.last_used_at)}</Td>
                  <Td>
                    <div className="flex justify-end gap-1.5">
                      {c.status === "active" ? (
                        <>
                          <Button variant="secondary" size="sm" onClick={() => setRotateFor(c)}>
                            Rotate
                          </Button>
                          <Button variant="danger" size="sm" onClick={() => setRevokeFor(c)}>
                            Revoke
                          </Button>
                        </>
                      ) : (
                        <span className="text-[11px] text-faint">Revoked</span>
                      )}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {/* --- Wizard: nueva conexión (3 pasos, REQ-037-012) -------------------- */}
      <Dialog
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        title="New connection"
        description="Three quick steps. You can go back at any point."
        width="max-w-xl"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setWizardOpen(false)}>
              Cancel
            </Button>
            {step > 1 ? (
              <Button variant="secondary" size="sm" onClick={() => setStep(step - 1)}>
                Back
              </Button>
            ) : null}
            {step < 3 ? (
              <Button variant="primary" size="sm" disabled={!stepValid()} onClick={() => setStep(step + 1)}>
                Continue
              </Button>
            ) : (
              <Button
                variant="primary"
                size="sm"
                loading={connSubmitting}
                disabled={!stepValid()}
                onClick={createConnection}
              >
                Create connection
              </Button>
            )}
          </>
        }
      >
        {/* Indicador de progreso */}
        <ol className="mb-5 flex items-center gap-2">
          {wizardSteps.map((label, i) => {
            const n = i + 1;
            const done = step > n;
            const current = step === n;
            return (
              <li key={label} className="flex items-center gap-2">
                <span
                  className={`flex size-5 items-center justify-center rounded-full text-[10px] font-semibold ${
                    done
                      ? "bg-accent/15 text-accent"
                      : current
                        ? "bg-accent text-white"
                        : "bg-surface-alt text-faint"
                  }`}
                >
                  {n}
                </span>
                <span className={`text-xs ${current ? "font-medium text-foreground" : "text-faint"}`}>
                  {label}
                </span>
                {n < wizardSteps.length ? <span className="h-px w-6 bg-border-subtle" /> : null}
              </li>
            );
          })}
        </ol>

        {step === 1 ? (
          <div className="space-y-2">
            <p className="mb-3 text-sm text-muted">What kind of connection is it?</p>
            {(
              [
                {
                  kind: "database" as const,
                  icon: <Database size={18} className="text-accent" />,
                  title: "Database",
                  hint: "A database your agents can query (PostgreSQL, MySQL, MongoDB…).",
                },
                {
                  kind: "api_key" as const,
                  icon: <KeyRound size={18} className="text-accent" />,
                  title: "API key",
                  hint: "An internal service your agents call with a key or token.",
                },
                ...(byokAllowed ? [{
                  kind: "llm" as const,
                  icon: <Zap size={18} className="text-accent" />,
                  title: "LLM (BYOK)",
                  hint: "An OpenAI-compatible endpoint with your own API key — models are discovered and added to your catalog.",
                }] : []),
                {
                  kind: "custom" as const,
                  icon: <Package size={18} className="text-accent" />,
                  title: "Other",
                  hint: "Anything else — you describe the details yourself.",
                },
              ]
            ).map((opt) => (
              <button
                key={opt.kind}
                type="button"
                onClick={() => setConnKind(opt.kind)}
                className={`flex w-full items-start gap-3 rounded-lg border px-4 py-3 text-left transition-all duration-150 ease-out active:scale-[0.99] ${
                  connKind === opt.kind
                    ? "border-accent/50 bg-accent/10"
                    : "border-border-subtle hover:bg-surface-alt/50"
                }`}
              >
                {opt.icon}
                <span>
                  <span className="block text-sm font-medium text-foreground">{opt.title}</span>
                  <span className="block text-xs text-muted">{opt.hint}</span>
                </span>
              </button>
            ))}
          </div>
        ) : step === 2 ? (
          <div className="space-y-4">
            <Field label="Name" htmlFor="conn-name" hint="Something your team recognizes, e.g. 'Analytics DB'.">
              <Input
                id="conn-name"
                value={connName}
                onChange={(e) => setConnName(e.target.value)}
                placeholder="Analytics database"
              />
            </Field>
            {connKind === "database" ? (
              <>
                <Field label="Database type" htmlFor="conn-db-type">
                  <Select id="conn-db-type" value={dbType} onChange={(e) => setDbType(e.target.value)}>
                    <option value="postgresql">PostgreSQL</option>
                    <option value="mysql">MySQL</option>
                    <option value="sqlserver">SQL Server</option>
                    <option value="oracle">Oracle</option>
                    <option value="mongodb">MongoDB</option>
                  </Select>
                </Field>
                <div className="grid gap-4 sm:grid-cols-3">
                  <Field label="Host" htmlFor="conn-db-host">
                    <Input id="conn-db-host" value={dbHost} onChange={(e) => setDbHost(e.target.value)} placeholder="db.internal" />
                  </Field>
                  <Field label="Port" htmlFor="conn-db-port">
                    <Input id="conn-db-port" value={dbPort} onChange={(e) => setDbPort(e.target.value)} placeholder="5432" />
                  </Field>
                  <Field label="Database" htmlFor="conn-db-name">
                    <Input id="conn-db-name" value={dbName} onChange={(e) => setDbName(e.target.value)} placeholder="analytics" />
                  </Field>
                </div>
                <Field label="Username" htmlFor="conn-db-user" hint="The account your agents will connect as. Its password goes in the next step.">
                  <Input id="conn-db-user" value={dbUser} onChange={(e) => setDbUser(e.target.value)} placeholder="reader" />
                </Field>
              </>
            ) : connKind === "api_key" ? (
              <>
                <Field label="Service" htmlFor="conn-ak-service" hint="The name of the service or API this key belongs to.">
                  <Input id="conn-ak-service" value={akService} onChange={(e) => setAkService(e.target.value)} placeholder="internal-billing" />
                </Field>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="How is the key sent" htmlFor="conn-ak-auth">
                    <Select id="conn-ak-auth" value={akAuth} onChange={(e) => setAkAuth(e.target.value as "bearer" | "header")}>
                      <option value="bearer">Bearer token</option>
                      <option value="header">Custom header</option>
                    </Select>
                  </Field>
                  {akAuth === "header" ? (
                    <Field label="Header name" htmlFor="conn-ak-header">
                      <Input id="conn-ak-header" value={akHeader} onChange={(e) => setAkHeader(e.target.value)} placeholder="X-Api-Key" />
                    </Field>
                  ) : null}
                </div>
              </>
            ) : connKind === "llm" ? (
              <>
                <Field
                  label="Base URL"
                  htmlFor="conn-llm-url"
                  hint="OpenAI-compatible endpoint, e.g. https://api.openai.com/v1 or https://api-ap-southeast-1.modelarts-maas.com/openai/v1"
                >
                  <Input
                    id="conn-llm-url"
                    value={llmBaseUrl}
                    onChange={(e) => setLlmBaseUrl(e.target.value)}
                    placeholder="https://host/v1"
                    className="font-mono text-xs"
                  />
                </Field>
                <p className="text-xs text-muted">
                  Your API key goes in the next step. Models are discovered via a
                  real GET /models call and added to your catalog automatically.
                </p>
              </>
            ) : (
              <Field
                label="Details"
                htmlFor="conn-custom-json"
                hint="Describe the connection as key/value pairs. Never put the password or key here — that goes in the next step."
              >
                <Textarea
                  id="conn-custom-json"
                  rows={5}
                  value={customJson}
                  onChange={(e) => setCustomJson(e.target.value)}
                  className="font-mono text-xs"
                />
              </Field>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <div className="rounded-md border border-accent/30 bg-accent/5 p-3 text-xs leading-relaxed text-muted">
              <p className="font-medium text-foreground">Last step: the secret.</p>
              <p>
                The password, key or token. It&apos;s encrypted immediately and shown back to you{" "}
                <span className="font-medium text-foreground">exactly once</span> — copy it somewhere safe.
              </p>
            </div>
            <Field label="Secret" htmlFor="conn-secret">
              <Input
                id="conn-secret"
                type="password"
                value={connSecret}
                onChange={(e) => setConnSecret(e.target.value)}
                placeholder="••••••••"
                autoComplete="new-password"
              />
            </Field>
            <p className="text-[11px] text-faint">
              Creating: <span className="font-medium text-foreground">{connName}</span>
              {connKind === "database" ? ` · ${dbType} on ${dbHost}` : ""}
              {connKind === "api_key" ? ` · ${akService}` : ""}
            </p>
          </div>
        )}
      </Dialog>

      {/* --- Wizard: secreto one-time ----------------------------------------- */}
      <Dialog
        open={connOneTime !== null}
        onClose={() => setConnOneTime(null)}
        title="Connection created"
        description={connOneTime?.label}
        footer={
          <Button variant="primary" size="sm" onClick={() => setConnOneTime(null)}>
            I stored it safely
          </Button>
        }
      >
        {connOneTime ? <OneTimeSecret value={connOneTime.value} /> : null}
      </Dialog>

      {/* --- Editar / rotar conexión existente -------------------------------- */}
      <Dialog
        open={connEditFor !== null}
        onClose={() => {
          setConnEditFor(null);
          setConnRotateFor(null);
        }}
        title={connRotateFor ? `Rotate secret — ${connEditFor?.name ?? ""}` : `Edit — ${connEditFor?.name ?? ""}`}
        description="Details are non-secret; the secret lives encrypted on its own and a new value is shown exactly once."
        width="max-w-xl"
        footer={
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setConnEditFor(null);
                setConnRotateFor(null);
              }}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={connSubmitting}
              disabled={!connName.trim() || (connRotateFor !== null && !connSecret)}
              onClick={() => updateConnection(connRotateFor !== null)}
            >
              {connRotateFor ? "Rotate secret" : "Save changes"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Name" htmlFor="conn-edit-name">
            <Input id="conn-edit-name" value={connName} onChange={(e) => setConnName(e.target.value)} />
          </Field>
          {connKind === "database" ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Database type" htmlFor="conn-edit-db-type">
                <Input id="conn-edit-db-type" value={dbType} onChange={(e) => setDbType(e.target.value)} />
              </Field>
              <Field label="Host" htmlFor="conn-edit-db-host">
                <Input id="conn-edit-db-host" value={dbHost} onChange={(e) => setDbHost(e.target.value)} />
              </Field>
              <Field label="Port" htmlFor="conn-edit-db-port">
                <Input id="conn-edit-db-port" value={dbPort} onChange={(e) => setDbPort(e.target.value)} />
              </Field>
              <Field label="Database" htmlFor="conn-edit-db-name">
                <Input id="conn-edit-db-name" value={dbName} onChange={(e) => setDbName(e.target.value)} />
              </Field>
              <Field label="Username" htmlFor="conn-edit-db-user">
                <Input id="conn-edit-db-user" value={dbUser} onChange={(e) => setDbUser(e.target.value)} />
              </Field>
            </div>
          ) : connKind === "api_key" ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Service" htmlFor="conn-edit-ak-service">
                <Input id="conn-edit-ak-service" value={akService} onChange={(e) => setAkService(e.target.value)} />
              </Field>
              <Field label="How is the key sent" htmlFor="conn-edit-ak-auth">
                <Select id="conn-edit-ak-auth" value={akAuth} onChange={(e) => setAkAuth(e.target.value as "bearer" | "header")}>
                  <option value="bearer">Bearer token</option>
                  <option value="header">Custom header</option>
                </Select>
              </Field>
              {akAuth === "header" ? (
                <Field label="Header name" htmlFor="conn-edit-ak-header">
                  <Input id="conn-edit-ak-header" value={akHeader} onChange={(e) => setAkHeader(e.target.value)} />
                </Field>
              ) : null}
            </div>
          ) : (
            <Field label="Details" htmlFor="conn-edit-custom-json">
              <Textarea
                id="conn-edit-custom-json"
                rows={5}
                value={customJson}
                onChange={(e) => setCustomJson(e.target.value)}
                className="font-mono text-xs"
              />
            </Field>
          )}
          <Field
            label={connRotateFor ? "New secret" : "New secret (optional)"}
            htmlFor="conn-edit-secret"
            hint={connRotateFor ? "The previous value stops working immediately; the new one is shown exactly once." : "Leave empty to keep the current secret."}
          >
            <Input
              id="conn-edit-secret"
              type="password"
              value={connSecret}
              onChange={(e) => setConnSecret(e.target.value)}
              placeholder="••••••••"
              autoComplete="new-password"
            />
          </Field>
        </div>
      </Dialog>

      {/* --- Revocar conexión --------------------------------------------------- */}
      <ConfirmDialog
        open={connRevokeFor !== null}
        onClose={() => setConnRevokeFor(null)}
        onConfirm={revokeConnection}
        loading={connSubmitting}
        title={`Revoke "${connRevokeFor?.name ?? ""}"`}
        message="The connection is revoked permanently — anything using it stops immediately. This action is audited."
        confirmLabel="Revoke connection"
      />

      {/* --- Add secret (vault personal) --------------------------------------- */}
      <Dialog
        open={secretOpen}
        onClose={() => setSecretOpen(false)}
        title="Add a secret"
        description="It's encrypted immediately and shown back to you exactly once."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setSecretOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={submitting}
              disabled={!name.trim() || !secret}
              onClick={createCredential}
            >
              Save secret
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Name" htmlFor="cred-name" hint="Something you recognize, e.g. 'Production API key'.">
            <Input id="cred-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Production API key" />
          </Field>
          <Field label="Type" htmlFor="cred-type">
            <Select id="cred-type" value={credType} onChange={(e) => setCredType(e.target.value)}>
              <option value="api_key">API key</option>
              <option value="token">Token</option>
              <option value="password">Password</option>
              <option value="secret">Other secret</option>
            </Select>
          </Field>
          <Field label="Secret" htmlFor="cred-secret">
            <Input
              id="cred-secret"
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder="••••••••"
              autoComplete="new-password"
            />
          </Field>
          <Field label="Linked app (optional)" htmlFor="cred-connector" hint="Connect it to a catalog app so agents can use it there.">
            <Select id="cred-connector" value={connectorSlug} onChange={(e) => setConnectorSlug(e.target.value)}>
              <option value="">Not linked</option>
              {(connectors.data?.connectors ?? []).map((c) => (
                <option key={c.slug} value={c.slug}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Note (optional)" htmlFor="cred-scope" hint="For your own bookkeeping, e.g. 'machine identity: ci-runner'.">
            <Input id="cred-scope" value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
        </div>
      </Dialog>

      {/* --- Secreto one-time ---------------------------------------------------- */}
      <Dialog
        open={oneTime !== null}
        onClose={() => setOneTime(null)}
        title="Secret saved"
        description={oneTime?.label}
        footer={
          <Button variant="primary" size="sm" onClick={() => setOneTime(null)}>
            I stored it safely
          </Button>
        }
      >
        {oneTime ? <OneTimeSecret value={oneTime.value} /> : null}
      </Dialog>

      {/* --- Rotar / revocar secreto ---------------------------------------------- */}
      <ConfirmDialog
        open={rotateFor !== null}
        onClose={() => setRotateFor(null)}
        onConfirm={rotateCredential}
        loading={submitting}
        title={`Rotate "${rotateFor?.name ?? ""}"`}
        message="A new secret is generated. The previous value stops working immediately and the new one is shown exactly once."
        confirmLabel="Rotate now"
        danger={false}
      />
      <ConfirmDialog
        open={revokeFor !== null}
        onClose={() => setRevokeFor(null)}
        onConfirm={revokeCredential}
        loading={submitting}
        title={`Revoke "${revokeFor?.name ?? ""}"`}
        message="The secret is revoked permanently and any app using it is cut off immediately. This action is audited."
        confirmLabel="Revoke secret"
      />

      {/* --- Conectar / desconectar app del catálogo ------------------------------- */}
      <ConfirmDialog
        open={connectFor !== null}
        onClose={() => setConnectFor(null)}
        onConfirm={connectInstance}
        loading={submitting}
        title={`Connect ${connectFor ? connectors.data?.connectors.find((c) => c.slug === connectFor)?.name ?? connectFor : ""}`}
        message="Your agents will be able to work with this app, under your organization's rules."
        confirmLabel="Connect"
        danger={false}
      />
      <ConfirmDialog
        open={disconnectFor !== null}
        onClose={() => setDisconnectFor(null)}
        onConfirm={disconnectInstance}
        loading={submitting}
        title={`Disconnect ${disconnectFor ? connectors.data?.connectors.find((c) => c.slug === disconnectFor.connector_slug)?.name ?? disconnectFor.connector_slug : ""}`}
        message="Agents using this app lose access immediately."
        confirmLabel="Disconnect"
      />
    </div>
  );
}
