"use client";

/**
 * Credentials (spec 009 REQ-AC-018): vault con metadata únicamente (los
 * secretos se muestran EXACTAMENTE una vez al crear/rotar — constitution IV),
 * revocación con corte inmediato, y conectores (catálogo + instancias).
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

export function CredentialsPage() {
  const credentials = useApi<CredentialList>("/api/vault/credentials");
  const connectors = useApi<ConnectorList>("/api/connectors");
  const instances = useApi<InstanceList>("/api/connectors/instances");
  const customConnections = useApi<CustomConnectionList>("/api/vault/connections");
  const { push } = useToast();

  // Crear
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [credType, setCredType] = useState("api_key");
  const [secret, setSecret] = useState("");
  const [connectorSlug, setConnectorSlug] = useState("");
  const [scope, setScope] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [oneTime, setOneTime] = useState<{ value: string; label: string } | null>(null);

  // Rotar / revocar
  const [rotateFor, setRotateFor] = useState<CredentialMeta | null>(null);
  const [revokeFor, setRevokeFor] = useState<CredentialMeta | null>(null);

  // Conectar conector
  const [connectFor, setConnectFor] = useState<string | null>(null);
  const [disconnectFor, setDisconnectFor] = useState<ConnectorInstance | null>(null);

  // Conexiones personalizadas (spec 024): CRUD del org_admin
  const [connCreateOpen, setConnCreateOpen] = useState(false);
  const [connName, setConnName] = useState("");
  const [connKind, setConnKind] = useState<"database" | "api_key" | "custom">("database");
  const [connSecret, setConnSecret] = useState("");
  // metadata database
  const [dbType, setDbType] = useState("postgresql");
  const [dbHost, setDbHost] = useState("");
  const [dbPort, setDbPort] = useState("5432");
  const [dbName, setDbName] = useState("");
  const [dbUser, setDbUser] = useState("");
  // metadata api_key
  const [akService, setAkService] = useState("");
  const [akAuth, setAkAuth] = useState<"bearer" | "header">("bearer");
  const [akHeader, setAkHeader] = useState("");
  // metadata custom (JSON libre NO-secreto)
  const [customJson, setCustomJson] = useState("{}");
  const [connSubmitting, setConnSubmitting] = useState(false);
  const [connOneTime, setConnOneTime] = useState<{ value: string; label: string } | null>(null);
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

  async function createConnection() {
    setConnSubmitting(true);
    try {
      let metadata: Record<string, unknown>;
      try {
        metadata = connMetadata();
      } catch {
        push("error", "Custom metadata must be valid JSON.");
        return;
      }
      const result = await api<CustomConnectionSecretResult>("/api/vault/connections", {
        method: "POST",
        body: { name: connName, kind: connKind, metadata, secret_value: connSecret },
      });
      setConnOneTime({ value: result.secret_value ?? "", label: `Secret for connection "${connName}"` });
      setConnCreateOpen(false);
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
        push("error", "Custom metadata must be valid JSON.");
        return;
      }
      const body: Record<string, unknown> = { name: connName, kind: connKind, metadata };
      if (rotate) body.new_secret_value = connSecret;
      const result = await api<CustomConnectionSecretResult>(
        `/api/vault/connections/${target.id}`,
        { method: "PUT", body },
      );
      if (result.secret_value) {
        setConnOneTime({ value: result.secret_value, label: `New secret for "${target.name}"` });
      } else {
        push("success", `Connection "${connName}" updated.`);
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
      push("success", `Connection "${connRevokeFor.name}" revoked. Its uses are cut off (410).`);
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
          scope: scope || null,
        },
      });
      setOneTime({ value: result.secret_value, label: `Secret for "${name}"` });
      setCreateOpen(false);
      setName("");
      setSecret("");
      setConnectorSlug("");
      setScope("");
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
      setOneTime({ value: result.secret_value, label: `New secret for "${rotateFor.name}"` });
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
      push("success", `Credential "${revokeFor.name}" revoked. Dependent connectors were cut off.`);
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
      push("success", "Connector connected.");
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
      push("success", "Connector disconnected.");
      setDisconnectFor(null);
      instances.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Disconnect failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Credential vault"
          description="Encrypted credentials (envelope encryption). Values are shown only once at creation/rotation — reads return metadata only."
          actions={
            <Button variant="primary" size="sm" onClick={() => setCreateOpen(true)}>
              <IconPlus size={13} /> Add credential
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
            title="No credentials in the vault"
            description="Add an API key or token to connect agents to external applications. Secrets are encrypted at rest and delivered only once."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Type</Th>
                <Th>Connector</Th>
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
                  <Td className="font-mono text-xs text-muted">{c.connector_slug ?? "—"}</Td>
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

      <Card>
        <CardHeader
          title="Application connectors"
          description="Catalog of connectable applications and the instances connected in this organization."
        />
        {connectors.loading || instances.loading ? (
          <TableSkeleton rows={3} cols={3} />
        ) : connectors.error ? (
          <ErrorState error={connectors.error} onRetry={connectors.refetch} context="GET /api/connectors" />
        ) : (
          <CardBody className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {(connectors.data?.connectors ?? []).map((c) => {
                const instance = instances.data?.instances.find(
                  (i) => i.connector_slug === c.slug && i.status === "active",
                );
                return (
                  <div key={c.slug} className="rounded-lg border border-border-subtle p-4">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="text-sm font-medium text-foreground">{c.name}</p>
                        <p className="text-[11px] text-muted">{c.category}</p>
                      </div>
                      {instance ? (
                        <Badge tone="success">Connected</Badge>
                      ) : (
                        <Badge>Available</Badge>
                      )}
                    </div>
                    <div className="mt-3">
                      {instance ? (
                        <Button variant="danger" size="sm" onClick={() => setDisconnectFor(instance)}>
                          Disconnect
                        </Button>
                      ) : (
                        <Button variant="secondary" size="sm" onClick={() => setConnectFor(c.slug)}>
                          Connect
                        </Button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
            {(instances.data?.instances.length ?? 0) > 0 ? (
              <div>
                <p className="mb-2 text-xs font-semibold text-foreground">Connected instances</p>
                <ul className="divide-y divide-border-subtle rounded-lg border border-border-subtle">
                  {instances.data!.instances.map((i) => (
                    <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
                      <div>
                        <p className="font-mono text-xs text-foreground">{i.connector_slug}</p>
                        <p className="text-[11px] text-muted">connected {dateTime(i.created_at)}</p>
                      </div>
                      <Badge tone={i.status === "active" ? "success" : "neutral"}>{i.status}</Badge>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </CardBody>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Custom connections"
          description="Organization-defined connections beyond the catalog (spec 024): databases, generic API keys or free-form metadata. Secrets are encrypted and shown exactly once; usage is governed by the allowed_connections policy."
          actions={
            <Button variant="primary" size="sm" onClick={() => setConnCreateOpen(true)}>
              <IconPlus size={13} /> Add connection
            </Button>
          }
        />
        {customConnections.loading ? (
          <TableSkeleton rows={3} cols={5} />
        ) : customConnections.error ? (
          customConnections.error.status === 403 ? (
            <EmptyState
              title="Only organization admins manage custom connections"
              description="Creating, updating and revoking connections requires the connections.manage permission (org_admin)."
            />
          ) : (
            <ErrorState error={customConnections.error} onRetry={customConnections.refetch} context="GET /api/vault/connections" />
          )
        ) : (customConnections.data?.connections.length ?? 0) === 0 ? (
          <EmptyState
            title="No custom connections"
            description="Define a database, a generic API key or any other connection for your organization. Allowed users and agents consume it through the policy."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Kind</Th>
                <Th>Metadata</Th>
                <Th>Status</Th>
                <Th>Rotated</Th>
                <Th>Used</Th>
                <Th className="w-44" />
              </tr>
            </thead>
            <tbody>
              {customConnections.data!.connections.map((c) => (
                <tr key={c.id} className="hover:bg-surface-alt/50">
                  <Td>
                    <p className="font-medium">{c.name}</p>
                    <p className="font-mono text-[10px] text-faint">{c.id}</p>
                  </Td>
                  <Td>
                    <Badge tone={c.kind === "database" ? "info" : c.kind === "api_key" ? "accent" : "neutral"}>
                      {c.kind}
                    </Badge>
                  </Td>
                  <Td className="max-w-56">
                    <p className="truncate font-mono text-[11px] text-muted" title={JSON.stringify(c.metadata)}>
                      {JSON.stringify(c.metadata)}
                    </p>
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
                          <Button variant="secondary" size="sm" onClick={() => openEdit(c, false)}>
                            Edit
                          </Button>
                          <Button variant="secondary" size="sm" onClick={() => openEdit(c, true)}>
                            Rotate
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

      {/* Crear credencial */}
      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Add credential"
        description="The secret is encrypted with the platform KEK and shown back exactly once."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={submitting}
              disabled={!name.trim() || !secret}
              onClick={createCredential}
            >
              Save credential
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Name" htmlFor="cred-name">
            <Input id="cred-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Production API key" />
          </Field>
          <Field label="Type" htmlFor="cred-type">
            <Select id="cred-type" value={credType} onChange={(e) => setCredType(e.target.value)}>
              <option value="api_key">API key</option>
              <option value="token">Token</option>
              <option value="password">Password</option>
              <option value="secret">Secret</option>
            </Select>
          </Field>
          <Field label="Secret value" htmlFor="cred-secret">
            <Input
              id="cred-secret"
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder="••••••••"
              autoComplete="new-password"
            />
          </Field>
          <Field label="Connector (optional)" htmlFor="cred-connector">
            <Select id="cred-connector" value={connectorSlug} onChange={(e) => setConnectorSlug(e.target.value)}>
              <option value="">Not linked</option>
              {(connectors.data?.connectors ?? []).map((c) => (
                <option key={c.slug} value={c.slug}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Scope (optional)" htmlFor="cred-scope" hint="Free-form scope note (e.g. 'machine identity: ci-runner').">
            <Input id="cred-scope" value={scope} onChange={(e) => setScope(e.target.value)} />
          </Field>
        </div>
      </Dialog>

      {/* Secreto one-time */}
      <Dialog
        open={oneTime !== null}
        onClose={() => setOneTime(null)}
        title="Credential saved"
        description={oneTime?.label}
        footer={
          <Button variant="primary" size="sm" onClick={() => setOneTime(null)}>
            I stored it safely
          </Button>
        }
      >
        {oneTime ? <OneTimeSecret value={oneTime.value} /> : null}
      </Dialog>

      {/* Rotar */}
      <ConfirmDialog
        open={rotateFor !== null}
        onClose={() => setRotateFor(null)}
        onConfirm={rotateCredential}
        loading={submitting}
        title={`Rotate "${rotateFor?.name ?? ""}"`}
        message="A new secret will be generated (or you can provide one). The previous value dies immediately and the new one is shown exactly once. Dependent connectors keep working with the new value."
        confirmLabel="Rotate now"
        danger={false}
      />

      {/* Revocar */}
      <ConfirmDialog
        open={revokeFor !== null}
        onClose={() => setRevokeFor(null)}
        onConfirm={revokeCredential}
        loading={submitting}
        title={`Revoke "${revokeFor?.name ?? ""}"`}
        message="The credential is revoked permanently and any connector instance using it is cut off immediately. This action is audited."
        confirmLabel="Revoke credential"
      />

      {/* Conectar / desconectar */}
      <ConfirmDialog
        open={connectFor !== null}
        onClose={() => setConnectFor(null)}
        onConfirm={connectInstance}
        loading={submitting}
        title={`Connect ${connectFor ?? ""}`}
        message="The connector instance is created for your user, governed by the organization's connector policies."
        confirmLabel="Connect"
        danger={false}
      />
      <ConfirmDialog
        open={disconnectFor !== null}
        onClose={() => setDisconnectFor(null)}
        onConfirm={disconnectInstance}
        loading={submitting}
        title={`Disconnect ${disconnectFor?.connector_slug ?? ""}`}
        message="Agents using this connector lose access to the application immediately."
        confirmLabel="Disconnect"
      />

      {/* Conexión personalizada: crear (spec 024) */}
      <Dialog
        open={connCreateOpen}
        onClose={() => setConnCreateOpen(false)}
        title="Add custom connection"
        description="Organization-level connection (database, API key or custom). The secret is encrypted with the platform KEK and shown back exactly once."
        width="max-w-xl"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setConnCreateOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={connSubmitting}
              disabled={!connName.trim() || !connSecret}
              onClick={createConnection}
            >
              Save connection
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Name" htmlFor="conn-name">
            <Input id="conn-name" value={connName} onChange={(e) => setConnName(e.target.value)} placeholder="Analytics database" />
          </Field>
          <Field label="Kind" htmlFor="conn-kind">
            <Select
              id="conn-kind"
              value={connKind}
              onChange={(e) => setConnKind(e.target.value as "database" | "api_key" | "custom")}
            >
              <option value="database">Database</option>
              <option value="api_key">API key</option>
              <option value="custom">Custom</option>
            </Select>
          </Field>
          {connKind === "database" ? (
            <>
              <Field label="Type" htmlFor="conn-db-type">
                <Select id="conn-db-type" value={dbType} onChange={(e) => setDbType(e.target.value)}>
                  <option value="postgresql">postgresql</option>
                  <option value="mysql">mysql</option>
                  <option value="sqlserver">sqlserver</option>
                  <option value="oracle">oracle</option>
                  <option value="mongodb">mongodb</option>
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
              <Field label="Username" htmlFor="conn-db-user">
                <Input id="conn-db-user" value={dbUser} onChange={(e) => setDbUser(e.target.value)} placeholder="reader" />
              </Field>
            </>
          ) : connKind === "api_key" ? (
            <>
              <Field label="Service" htmlFor="conn-ak-service">
                <Input id="conn-ak-service" value={akService} onChange={(e) => setAkService(e.target.value)} placeholder="internal-billing" />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Auth" htmlFor="conn-ak-auth">
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
          ) : (
            <Field label="Metadata (JSON, non-secret)" htmlFor="conn-custom-json" hint="Secret-looking keys (password/secret/token/…) are rejected by the control plane.">
              <Textarea
                id="conn-custom-json"
                rows={5}
                value={customJson}
                onChange={(e) => setCustomJson(e.target.value)}
                className="font-mono text-xs"
              />
            </Field>
          )}
          <Field label="Secret value" htmlFor="conn-secret" hint="Password, API key or token — encrypted at rest, displayed exactly once.">
            <Input
              id="conn-secret"
              type="password"
              value={connSecret}
              onChange={(e) => setConnSecret(e.target.value)}
              placeholder="••••••••"
              autoComplete="new-password"
            />
          </Field>
        </div>
      </Dialog>

      {/* Conexión personalizada: editar / rotar secreto */}
      <Dialog
        open={connEditFor !== null}
        onClose={() => {
          setConnEditFor(null);
          setConnRotateFor(null);
        }}
        title={connRotateFor ? `Rotate secret — ${connEditFor?.name ?? ""}` : `Edit connection — ${connEditFor?.name ?? ""}`}
        description="Metadata is non-secret; the secret lives encrypted in its own column and a new value is shown exactly once."
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
          <Field label="Kind" htmlFor="conn-edit-kind">
            <Select
              id="conn-edit-kind"
              value={connKind}
              onChange={(e) => setConnKind(e.target.value as "database" | "api_key" | "custom")}
            >
              <option value="database">Database</option>
              <option value="api_key">API key</option>
              <option value="custom">Custom</option>
            </Select>
          </Field>
          {connKind === "database" ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Type" htmlFor="conn-edit-db-type">
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
              <Field label="Auth" htmlFor="conn-edit-ak-auth">
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
            <Field label="Metadata (JSON, non-secret)" htmlFor="conn-edit-custom-json">
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
            label={connRotateFor ? "New secret value" : "New secret value (optional)"}
            htmlFor="conn-edit-secret"
            hint={connRotateFor ? "The previous ciphertext dies immediately; the new value is shown exactly once." : "Leave empty to keep the current secret."}
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

      {/* Conexión personalizada: secreto one-time */}
      <Dialog
        open={connOneTime !== null}
        onClose={() => setConnOneTime(null)}
        title="Connection saved"
        description={connOneTime?.label}
        footer={
          <Button variant="primary" size="sm" onClick={() => setConnOneTime(null)}>
            I stored it safely
          </Button>
        }
      >
        {connOneTime ? <OneTimeSecret value={connOneTime.value} /> : null}
      </Dialog>

      {/* Conexión personalizada: revocar */}
      <ConfirmDialog
        open={connRevokeFor !== null}
        onClose={() => setConnRevokeFor(null)}
        onConfirm={revokeConnection}
        loading={connSubmitting}
        title={`Revoke connection "${connRevokeFor?.name ?? ""}"`}
        message="The connection is revoked permanently: runtime uses answer 410 and it disappears from the governance bundle at the next sync. This action is audited."
        confirmLabel="Revoke connection"
      />
    </div>
  );
}
