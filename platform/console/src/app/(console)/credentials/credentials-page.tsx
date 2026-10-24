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
  Th,
  useToast,
} from "@/components/ui";
import { IconCredentials, IconPlus } from "@/components/icons";

export function CredentialsPage() {
  const credentials = useApi<CredentialList>("/api/vault/credentials");
  const connectors = useApi<ConnectorList>("/api/connectors");
  const instances = useApi<InstanceList>("/api/connectors/instances");
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
    </div>
  );
}
