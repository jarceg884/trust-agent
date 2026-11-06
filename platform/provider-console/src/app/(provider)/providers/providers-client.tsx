"use client";

/**
 * Model Providers (spec 036 + 043, mandato Miller): rediseño estilo Dify.
 *
 * Tarjetas de proveedor con nombre, endpoint, estado, catálogo y botón
 * "Test connection" (GET /models real — modelos, latencia, error claro).
 * Panel de gating BYOK por cliente (switch on/off por tenant).
 * Toda mutación pasa por el proxy BFF (el token del operador vive
 * server-side, D-PC-02).
 */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api-client";
import type {
  ByokPolicyEntry,
  ModelProviderDetail,
  ModelProviderEnableResult,
  ModelProviderItem,
  ModelProviderRefreshResult,
  TestConnectionResult,
} from "@/lib/types";
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
  Table,
  Td,
  Th,
  useToast,
} from "@/components/ui";
import { IconModels, IconPlus, IconRefresh } from "@/components/icons";

interface TenantOption {
  id: string;
  slug: string;
  name: string;
  status: string;
}

/** Tarjeta de proveedor estilo Dify (spec 043, D-043-04). */
function ProviderCard({
  provider,
  busy,
  testResult,
  onTest,
  onFetch,
  onDetail,
  onToggleStatus,
}: {
  provider: ModelProviderItem;
  busy: string | null;
  testResult: TestConnectionResult | null;
  onTest: () => void;
  onFetch: () => void;
  onDetail: () => void;
  onToggleStatus: () => void;
}) {
  const isActive = provider.status === "active";
  return (
    <div className="rounded-xl border border-border bg-surface shadow-sm transition-shadow hover:shadow-md">
      {/* Header */}
      <div className="flex items-start justify-between border-b border-border-subtle px-5 py-4">
        <div className="flex items-center gap-3">
          <div
            className={`flex h-10 w-10 items-center justify-center rounded-lg ${
              isActive ? "bg-accent-soft text-accent" : "bg-surface-alt text-muted"
            }`}
          >
            <IconModels size={20} />
          </div>
          <div>
            <h3 className="text-sm font-semibold">
              {provider.display_name ?? provider.name}
            </h3>
            <p className="font-mono text-[11px] text-muted">{provider.name}</p>
          </div>
        </div>
        <Badge tone={isActive ? "success" : "danger"}>{provider.status}</Badge>
      </div>

      {/* Body */}
      <div className="space-y-3 px-5 py-4">
        <div className="flex items-center gap-2 text-xs">
          <span className="text-muted">Endpoint:</span>
          <span className="truncate font-mono text-[11px] text-faint">
            {provider.base_url}
          </span>
        </div>
        <div className="flex items-center gap-4 text-xs">
          <span className="flex items-center gap-1">
            <span className="font-medium">{provider.available_models}</span>
            <span className="text-muted">models</span>
          </span>
          <span className="flex items-center gap-1">
            <span className="font-medium">{provider.enabled_tenants}</span>
            <span className="text-muted">clients</span>
          </span>
        </div>

        {/* Test connection result inline */}
        {testResult && (
          <div
            className={`rounded-lg border p-3 text-xs ${
              testResult.ok
                ? "border-success/20 bg-success-soft/50"
                : "border-danger/20 bg-danger-soft/50"
            }`}
          >
            {testResult.ok ? (
              <div className="space-y-1">
                <div className="flex items-center gap-2 font-medium text-success">
                  ✓ Connected — {testResult.count} models found ({testResult.latency_ms}ms)
                </div>
                {testResult.models && testResult.models.length > 0 && (
                  <div className="max-h-24 overflow-y-auto font-mono text-[10px] text-muted">
                    {testResult.models.slice(0, 15).join(", ")}
                    {testResult.models.length > 15 && ` … +${testResult.models.length - 15} more`}
                  </div>
                )}
              </div>
            ) : (
              <div className="font-medium text-danger">
                ✗ {testResult.error} ({testResult.latency_ms}ms)
              </div>
            )}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle px-5 py-3">
        <Button
          variant="primary"
          size="sm"
          loading={busy === `test-${provider.id}`}
          disabled={busy !== null}
          onClick={onTest}
        >
          Test connection
        </Button>
        <Button
          variant="secondary"
          size="sm"
          loading={busy === `refresh-${provider.id}`}
          disabled={busy !== null}
          onClick={onFetch}
        >
          <IconRefresh size={13} /> Fetch models
        </Button>
        <Button
          variant="secondary"
          size="sm"
          loading={busy === `detail-${provider.id}`}
          disabled={busy !== null}
          onClick={onDetail}
        >
          Clients
        </Button>
        <Button
          variant="danger"
          size="sm"
          loading={busy === `status-${provider.id}`}
          disabled={busy !== null}
          onClick={onToggleStatus}
        >
          {isActive ? "Suspend" : "Reactivate"}
        </Button>
      </div>
    </div>
  );
}

export function ProvidersClient({
  initialProviders,
  tenants,
  byokPolicy: initialByokPolicy,
  error,
}: {
  initialProviders: ModelProviderItem[];
  tenants: TenantOption[];
  byokPolicy: ByokPolicyEntry[];
  error: { status: number; reason: string; message: string } | null;
}) {
  const router = useRouter();
  const { push } = useToast();
  const [providers, setProviders] = useState(initialProviders);
  const [byokPolicy, setByokPolicy] = useState(initialByokPolicy);
  const [createOpen, setCreateOpen] = useState(false);
  const [detail, setDetail] = useState<ModelProviderDetail | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestConnectionResult>>({});
  const [enableFor, setEnableFor] = useState<ModelProviderItem | null>(null);
  const [disableFor, setDisableFor] = useState<{
    provider: ModelProviderItem;
    tenant: { tenant_id: string; slug: string | null };
  } | null>(null);
  const [byokBusy, setByokBusy] = useState<string | null>(null);

  // --- create form -----------------------------------------------------
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [inputPrice, setInputPrice] = useState("");
  const [outputPrice, setOutputPrice] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function reload() {
    try {
      const list = await api<{ providers: ModelProviderItem[] }>(
        "/api/admin/model-providers",
      );
      setProviders(list.providers);
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Reload failed.");
    }
  }

  async function reloadByok() {
    try {
      const res = await api<{ tenants: ByokPolicyEntry[] }>(
        "/api/admin/model-providers/byok-policy",
      );
      setByokPolicy(res.tenants);
    } catch {
      // non-fatal — keep stale
    }
  }

  async function create() {
    setSubmitting(true);
    try {
      await api("/api/admin/model-providers", {
        method: "POST",
        body: {
          name,
          base_url: baseUrl,
          api_key: apiKey,
          display_name: displayName || null,
          input_price_cents_per_m: inputPrice ? Number(inputPrice) : null,
          output_price_cents_per_m: outputPrice ? Number(outputPrice) : null,
        },
      });
      push("success", `Provider '${name}' registered. Fetch its models next.`);
      setCreateOpen(false);
      setName(""); setDisplayName(""); setBaseUrl(""); setApiKey("");
      setInputPrice(""); setOutputPrice("");
      await reload();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Creation failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function testConnection(p: ModelProviderItem) {
    setBusy(`test-${p.id}`);
    try {
      const result = await api<TestConnectionResult>(
        `/api/admin/model-providers/${p.id}/test-connection`,
        { method: "POST", body: {} },
      );
      setTestResults((prev) => ({ ...prev, [p.id]: result }));
      if (result.ok) {
        push("success", `Connection OK — ${result.count} models found (${result.latency_ms}ms).`);
      } else {
        push("error", `Connection failed: ${result.error}`);
      }
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Test failed.");
    } finally {
      setBusy(null);
    }
  }

  async function fetchModels(p: ModelProviderItem) {
    setBusy(`refresh-${p.id}`);
    try {
      const result = await api<ModelProviderRefreshResult>(
        `/api/admin/model-providers/${p.id}/refresh`,
        { method: "POST", body: {} },
      );
      push(
        "success",
        `Fetched ${result.upstream_models} models from ${p.name}` +
          ` (${result.tenants.length} enabled client(s) synced).`,
      );
      await reload();
      if (detail?.id === p.id) await openDetail(p.id);
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Fetch failed.");
    } finally {
      setBusy(null);
    }
  }

  async function openDetail(id: string) {
    setBusy(`detail-${id}`);
    try {
      const d = await api<ModelProviderDetail>(
        `/api/admin/model-providers/${id}`,
      );
      setDetail(d);
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Load failed.");
    } finally {
      setBusy(null);
    }
  }

  async function enable(tenantRef: string) {
    if (!enableFor) return;
    setSubmitting(true);
    try {
      const result = await api<ModelProviderEnableResult>(
        `/api/admin/model-providers/${enableFor.id}/tenants`,
        { method: "POST", body: { tenant: tenantRef } },
      );
      push(
        "success",
        `'${enableFor.name}' enabled for ${result.tenant.slug}: ` +
          `${result.discovered} models discovered, ${result.approved} approved at org level.`,
      );
      setEnableFor(null);
      await reload();
      if (detail?.id === enableFor.id) await openDetail(enableFor.id);
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Enable failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function disable() {
    if (!disableFor) return;
    setSubmitting(true);
    try {
      const result = await api<{ stale: number; gateway_freed: string[] }>(
        `/api/admin/model-providers/${disableFor.provider.id}/tenants/` +
          `${disableFor.tenant.tenant_id}`,
        { method: "DELETE" },
      );
      push(
        "success",
        `'${disableFor.provider.name}' disabled for ${disableFor.tenant.slug}: ` +
          `${result.stale} model(s) went stale for that client.`,
      );
      setDisableFor(null);
      await reload();
      if (detail?.id === disableFor.provider.id)
        await openDetail(disableFor.provider.id);
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Disable failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function toggleStatus(p: ModelProviderItem) {
    setBusy(`status-${p.id}`);
    try {
      await api(`/api/admin/model-providers/${p.id}/status`, {
        method: "POST",
        body: { status: p.status === "active" ? "disabled" : "active" },
      });
      push(
        "success",
        p.status === "active"
          ? `'${p.name}' suspended — its models went stale for every enabled client.`
          : `'${p.name}' reactivated — run "Fetch models" to restore its catalog.`,
      );
      await reload();
      if (detail?.id === p.id) await openDetail(p.id);
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "Status change failed.");
    } finally {
      setBusy(null);
    }
  }

  async function toggleByok(tenantId: string, currentAllowed: boolean) {
    setByokBusy(tenantId);
    try {
      const result = await api<{ byok_allowed: boolean; slug: string }>(
        `/api/admin/model-providers/byok-policy/${tenantId}`,
        { method: "PUT", body: { byok_allowed: !currentAllowed } },
      );
      push(
        "success",
        `BYOK ${result.byok_allowed ? "enabled" : "disabled"} for '${result.slug}'.` +
          (result.byok_allowed
            ? " The BYOK option now appears in their Admin Console."
            : " The BYOK option is now HIDDEN from their Admin Console."),
      );
      await reloadByok();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "BYOK policy update failed.");
    } finally {
      setByokBusy(null);
    }
  }

  if (error) {
    return (
      <Card>
        <CardHeader
          title="Model Providers"
          description="Register model sources (OpenAI-compatible endpoint + key) and enable them per client."
        />
        <ErrorState
          error={error}
          onRetry={() => router.refresh()}
          context="GET /api/admin/model-providers"
        />
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {/* === Provider cards (estilo Dify) === */}
      <Card>
        <CardHeader
          title="Model Providers"
          description="Register model sources (any OpenAI-compatible endpoint + API key, stored encrypted) and decide per client which ones are enabled. Test connection runs a real GET /models against the endpoint."
          actions={
            <Button variant="primary" size="sm" onClick={() => setCreateOpen(true)}>
              <IconPlus size={13} /> Register provider
            </Button>
          }
        />
        {providers.length === 0 ? (
          <EmptyState
            icon={<IconModels size={18} />}
            title="No model providers registered"
            description="Register a provider (e.g. Huawei Cloud MaaS, OpenAI, any OpenAI-compatible endpoint), test the connection and enable it for your clients."
          />
        ) : (
          <CardBody>
            <div className="grid gap-4 md:grid-cols-2">
              {providers.map((p) => (
                <ProviderCard
                  key={p.id}
                  provider={p}
                  busy={busy}
                  testResult={testResults[p.id] ?? null}
                  onTest={() => testConnection(p)}
                  onFetch={() => fetchModels(p)}
                  onDetail={() => openDetail(p.id)}
                  onToggleStatus={() => toggleStatus(p)}
                />
              ))}
            </div>
          </CardBody>
        )}
      </Card>

      {/* === BYOK gating por cliente === */}
      <Card>
        <CardHeader
          title="BYOK gating per client"
          description="Control which clients can use Bring Your Own Key (BYOK). When disabled for a client, the BYOK option is completely hidden from their Admin Console — not grayed out, not locked: it does not exist."
        />
        {byokPolicy.length === 0 ? (
          <EmptyState
            title="No clients to gate"
            description="Active tenants will appear here once provisioned."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Client</Th>
                <Th>Slug</Th>
                <Th>Status</Th>
                <Th>BYOK</Th>
                <Th className="w-32" />
              </tr>
            </thead>
            <tbody>
              {byokPolicy.map((t) => (
                <tr key={t.tenant_id} className="hover:bg-surface-alt/50">
                  <Td className="font-medium">{t.name}</Td>
                  <Td className="font-mono text-[11px] text-muted">{t.slug}</Td>
                  <Td>
                    <Badge tone={t.status === "active" ? "success" : "neutral"}>
                      {t.status}
                    </Badge>
                  </Td>
                  <Td>
                    <Badge tone={t.byok_allowed ? "success" : "danger"}>
                      {t.byok_allowed ? "Allowed" : "Hidden"}
                    </Badge>
                  </Td>
                  <Td>
                    <Button
                      variant={t.byok_allowed ? "danger" : "primary"}
                      size="sm"
                      loading={byokBusy === t.tenant_id}
                      disabled={byokBusy !== null}
                      onClick={() => toggleByok(t.tenant_id, t.byok_allowed)}
                    >
                      {t.byok_allowed ? "Disable BYOK" : "Enable BYOK"}
                    </Button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {/* Alta de proveedor */}
      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Register model provider"
        description="Any OpenAI-compatible endpoint. The API key is encrypted at rest and never displayed again."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" loading={submitting} onClick={create}>
              Register
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Name" htmlFor="mp-name" hint="Unique identifier (e.g. huawei-maas-prod).">
            <Input id="mp-name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Display name (optional)" htmlFor="mp-display">
            <Input
              id="mp-display"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Huawei Cloud MaaS"
            />
          </Field>
          <Field
            label="Base URL"
            htmlFor="mp-url"
            hint="OpenAI-compatible endpoint, e.g. https://api-ap-southeast-1.modelarts-maas.com/openai/v1"
          >
            <Input
              id="mp-url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://host/v1"
              className="font-mono text-xs"
            />
          </Field>
          <Field label="API key" htmlFor="mp-key" hint="Stored encrypted (vault envelope); used server-side only.">
            <Input
              id="mp-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="off"
            />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Input price ¢/M (optional)" htmlFor="mp-in" hint="0 or empty = unknown cost.">
              <Input
                id="mp-in"
                value={inputPrice}
                onChange={(e) => setInputPrice(e.target.value)}
                inputMode="decimal"
              />
            </Field>
            <Field label="Output price ¢/M (optional)" htmlFor="mp-out">
              <Input
                id="mp-out"
                value={outputPrice}
                onChange={(e) => setOutputPrice(e.target.value)}
                inputMode="decimal"
              />
            </Field>
          </div>
        </div>
      </Dialog>

      {/* Habilitar para un cliente */}
      <Dialog
        open={enableFor !== null}
        onClose={() => setEnableFor(null)}
        title={`Enable ${enableFor?.display_name ?? enableFor?.name ?? ""} for a client`}
        description="The provider's models are fetched immediately and appear by default in the client's Model Access, approved at organization level (the client admin can still restrict them per level)."
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setEnableFor(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={submitting}
              onClick={() => enable((document.getElementById("enable-tenant") as HTMLSelectElement)?.value ?? "")}
            >
              Enable
            </Button>
          </>
        }
      >
        <Field label="Client (active tenant)" htmlFor="enable-tenant">
          <select
            id="enable-tenant"
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
          >
            {tenants.map((t) => (
              <option key={t.id} value={t.slug}>
                {t.name} ({t.slug})
              </option>
            ))}
          </select>
        </Field>
      </Dialog>

      {/* Deshabilitar para un cliente */}
      <ConfirmDialog
        open={disableFor !== null}
        onClose={() => setDisableFor(null)}
        onConfirm={disable}
        loading={submitting}
        title={`Disable ${disableFor?.provider.display_name ?? disableFor?.provider.name ?? ""} for ${disableFor?.tenant.slug ?? ""}`}
        message="The client's rows for this provider go stale immediately (fail-closed): the models disappear from the effective set even if the client admin approved them. Gateway routes shared with other clients are kept."
        confirmLabel="Disable for this client"
      />

      {/* Detalle: clientes habilitados + modelos */}
      <Dialog
        open={detail !== null}
        onClose={() => setDetail(null)}
        title={`${detail?.display_name ?? detail?.name ?? ""} — clients & models`}
        description={detail?.base_url ?? ""}
        footer={
          <>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => detail && setEnableFor(providers.find((p) => p.id === detail.id) ?? null)}
            >
              <IconPlus size={13} /> Enable for a client
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setDetail(null)}>
              Close
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <p className="mb-1 text-xs font-medium text-muted">ENABLED CLIENTS</p>
            {detail?.tenants.length ? (
              <ul className="space-y-1">
                {detail.tenants.map((t) => (
                  <li key={t.tenant_id} className="flex items-center justify-between rounded-md border border-border px-3 py-1.5">
                    <span className="text-sm">
                      {t.slug ?? t.tenant_id}
                      {!t.enabled && (
                        <span className="ml-2 text-xs text-faint">disabled</span>
                      )}
                    </span>
                    {t.enabled ? (
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() =>
                          setDisableFor({
                            provider: providers.find((p) => p.id === detail.id)!,
                            tenant: t,
                          })
                        }
                      >
                        Disable
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-faint">
                No clients enabled yet — enable this provider for a client and its
                models appear in their Model Access by default.
              </p>
            )}
          </div>
          <div>
            <p className="mb-1 text-xs font-medium text-muted">DISCOVERED MODELS (per client)</p>
            {detail?.models.length ? (
              <ul className="max-h-48 space-y-0.5 overflow-y-auto font-mono text-[11px]">
                {detail.models.map((m, i) => (
                  <li key={i} className="flex items-center gap-2">
                    <span className="text-muted">{m.model_name}</span>
                    {m.status !== "available" && (
                      <Badge tone="warning">{m.status}</Badge>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-faint">
                No models yet — run "Fetch models" (real GET /models against the
                endpoint).
              </p>
            )}
          </div>
        </div>
      </Dialog>
    </div>
  );
}
