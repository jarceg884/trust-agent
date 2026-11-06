"use client";

/**
 * Models — proveedores de IA + catálogo.
 *
 * El proveedor del SaaS (Huawei Cloud MaaS) viene por defecto. El admin puede
 * añadir proveedores (endpoints OpenAI-compatible) y sus modelos aparecen
 * automáticamente en el catálogo y en el selector de las políticas.
 * La aprobación/acceso por política se gestiona en Policies, no aquí.
 */
import { useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api-client";
import { useApi } from "@/hooks/use-api";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Dialog,
  EmptyState,
  Field,
  Input,
  Select,
  Table,
  TableSkeleton,
  Td,
  Th,
  useToast,
} from "@/components/ui";

interface ModelRow {
  model_name: string;
  display_name: string;
  provider: string;
  context_tokens: number | null;
  max_output_tokens: number | null;
  input_price_cents_per_m: number | null;
  output_price_cents_per_m: number | null;
  cache_read_price_cents_per_m: number | null;
  status: string;
  source: string;
}

interface ModelList { models: ModelRow[] }
interface ConnectionRow {
  id: string;
  name: string;
  kind: string;
  metadata: Record<string, unknown>;
  status: string;
}
interface ConnectionList { connections: ConnectionRow[] }

const PROVIDER_PRESETS = [
  { key: "openai", label: "OpenAI", base_url: "https://api.openai.com/v1" },
  { key: "anthropic", label: "Anthropic", base_url: "https://api.anthropic.com/v1" },
  { key: "huawei_maas", label: "Huawei Cloud MaaS", base_url: "https://api.modelarts-maas.com/v1" },
  { key: "openrouter", label: "OpenRouter", base_url: "https://openrouter.ai/api/v1" },
  { key: "groq", label: "Groq", base_url: "https://api.groq.com/openai/v1" },
  { key: "deepseek", label: "DeepSeek", base_url: "https://api.deepseek.com/v1" },
  { key: "custom", label: "Otro (OpenAI-compatible)", base_url: "" },
];

function usd(cents: number | null | undefined): string {
  if (cents == null) return "—";
  return `$${(cents / 100).toFixed(2)}`;
}

function fmtTokens(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${Math.round(n / 1_000_000)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

export function ModelsOverviewPage() {
  const models = useApi<ModelList>("/api/models");
  const access = useApi<{ byok_allowed: boolean }>("/api/models/access");
  const connections = useApi<ConnectionList>("/api/vault/connections");
  const { push } = useToast();
  const byokAllowed = access.data?.byok_allowed ?? true;
  const [providerOpen, setProviderOpen] = useState(false);
  const [providerKind, setProviderKind] = useState("openai");
  const [providerName, setProviderName] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1");
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [syncingPrices, setSyncingPrices] = useState(false);
  const [refreshingCatalog, setRefreshingCatalog] = useState(false);

  const providers = useMemo(() => {
    const fromModels = new Set((models.data?.models ?? []).map((m) => m.provider));
    return [...fromModels];
  }, [models.data]);

  const llmConnections = (connections.data?.connections ?? []).filter((c) => c.kind === "llm");

  async function addProvider() {
    if (!providerName.trim() || !baseUrl.trim() || !apiKey.trim()) {
      push("error", "Nombre, Base URL y API key son obligatorios.");
      return;
    }
    setSaving(true);
    try {
      const created = await api<{ id: string }>("/api/vault/connections", {
        method: "POST",
        body: {
          name: providerName,
          kind: "llm",
          metadata: {
            base_url: baseUrl,
            input_price_cents_per_m: 0,
            output_price_cents_per_m: 0,
          },
          secret_value: apiKey,
        },
      });
      try {
        const refreshed = await api<{ discovered?: number; models?: string[] }>("/api/models/catalog/refresh", {
          method: "POST",
          body: { connection_id: created.id },
        });
        const count = refreshed.discovered ?? refreshed.models?.length ?? 0;
        push("success", count > 0
          ? `Proveedor añadido. Se consultaron ${count} modelo(s) reales y ya están en el catálogo.`
          : "Proveedor añadido, pero el fetch no devolvió modelos todavía — revisa la API key.");
      } catch (refreshErr) {
        push("error", refreshErr instanceof ApiError
          ? `Proveedor guardado, pero el fetch de modelos falló: ${refreshErr.message}`
          : "Proveedor guardado, pero no se pudo consultar sus modelos ahora.");
      }
      setProviderOpen(false);
      setProviderName("");
      setApiKey("");
      connections.refetch();
      models.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudo añadir el proveedor.");
    } finally {
      setSaving(false);
    }
  }

  async function refreshProvider(connectionId: string, name: string) {
    try {
      const refreshed = await api<{ discovered?: number; models?: string[] }>("/api/models/catalog/refresh", {
        method: "POST",
        body: { connection_id: connectionId },
      });
      const count = refreshed.discovered ?? refreshed.models?.length ?? 0;
      push("success", `${name}: ${count} modelo(s) consultados en vivo.`);
      models.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : `No se pudo actualizar ${name}.`);
    }
  }

  /** Refresca TODO el catálogo desde los proveedores (fetch real al gateway
   * MaaS + cada conexión LLM). Un modelo nuevo de Huawei aparece aquí sin
   * tocar código (mandato Miller: "si MaaS pone un modelo nuevo se agrega
   * automático"). */
  async function refreshCatalog() {
    setRefreshingCatalog(true);
    try {
      const r = await api<{
        added?: string[];
        central?: { gateway_models?: number; added?: string[] };
      }>("/api/models/catalog/refresh", { method: "POST", body: {} });
      const added = r.added ?? r.central?.added ?? [];
      push(
        "success",
        added.length
          ? `Catálogo actualizado. Modelo(s) nuevo(s): ${added.join(", ")}.`
          : `Catálogo actualizado desde el gateway (${r.central?.gateway_models ?? 0} modelos upstream; sin novedades).`,
      );
      models.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudo actualizar el catálogo.");
    } finally {
      setRefreshingCatalog(false);
    }
  }

  /** Fuerza el reload del cost map en LiteLLM y sincroniza los precios del
   * catálogo que muestra esta pantalla — ambos leen la misma tabla real de
   * huawei_pricing (nada hardcodeado en la UI). */
  async function syncPrices() {
    setSyncingPrices(true);
    try {
      const r = await api<{
        reloaded: boolean;
        models: number;
        catalog?: { updated?: number };
      }>("/api/models/pricing/sync", { method: "POST" });
      push(
        "success",
        `Precios MaaS sincronizados: ${r.models} modelos en LiteLLM, ${r.catalog?.updated ?? 0} filas del catálogo actualizadas.`,
      );
      models.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudieron sincronizar los precios.");
    } finally {
      setSyncingPrices(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Proveedores de IA"
          description="El proveedor del SaaS viene configurado por defecto. Añade proveedores propios (OpenAI-compatible) cuando lo necesites."
          actions={
            byokAllowed ? (
              <Button variant="primary" size="sm" onClick={() => setProviderOpen(true)}>
                + Añadir proveedor
              </Button>
            ) : (
              <span className="text-xs text-muted">BYOK deshabilitado por el proveedor del SaaS</span>
            )
          }
        />
        <CardBody>
          <div className="grid gap-3 md:grid-cols-2">
            {providers.map((provider) => (
              <div key={provider} className="rounded-lg border border-border-subtle p-4">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold">{provider}</p>
                  <Badge tone={provider === "huawei_maas" ? "success" : "info"}>
                    {provider === "huawei_maas" ? "Proveedor del SaaS · por defecto" : "Proveedor añadido"}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-muted">
                  {provider === "huawei_maas"
                    ? "Huawei Cloud ModelArts Studio — proveedor central de la plataforma."
                    : "Endpoint OpenAI-compatible añadido por la organización."}
                </p>
              </div>
            ))}
            {llmConnections.map((connection) => (
              <div key={connection.id} className="rounded-lg border border-border-subtle p-4">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold">{connection.name}</p>
                  <Badge tone={connection.status === "active" ? "success" : "neutral"}>{connection.status}</Badge>
                </div>
                <p className="mt-1 break-all text-xs text-muted">
                  {String(connection.metadata.base_url ?? "OpenAI-compatible")}
                </p>
                <Button
                  variant="secondary"
                  size="sm"
                  className="mt-2"
                  onClick={() => void refreshProvider(connection.id, connection.name)}
                >
                  Actualizar modelos (fetch real)
                </Button>
              </div>
            ))}
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Catálogo de modelos"
          description="Modelos disponibles desde los proveedores. El acceso por usuario se define en las políticas."
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                loading={refreshingCatalog}
                onClick={() => void refreshCatalog()}
              >
                Actualizar catálogo
              </Button>
              <Button
                variant="secondary"
                size="sm"
                loading={syncingPrices}
                onClick={() => void syncPrices()}
              >
                Sincronizar precios
              </Button>
            </div>
          }
        />
        {models.loading ? (
          <TableSkeleton rows={6} cols={6} />
        ) : models.error ? (
          <EmptyState title="No se pudo cargar el catálogo" description={models.error.message} />
        ) : (models.data?.models.length ?? 0) === 0 ? (
          <EmptyState title="Sin modelos" description="Añade un proveedor para descubrir sus modelos." />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Modelo</Th>
                <Th>Proveedor</Th>
                <Th>Contexto</Th>
                <Th>Input / M</Th>
                <Th>Output / M</Th>
                <Th>Cache hit / M</Th>
                <Th>Estado</Th>
              </tr>
            </thead>
            <tbody>
              {models.data!.models.map((model) => (
                <tr key={`${model.provider}-${model.model_name}`} className="hover:bg-surface-alt/50">
                  <Td>
                    <p className="text-sm font-medium">{model.display_name || model.model_name}</p>
                    <p className="font-mono text-[11px] text-muted">{model.model_name}</p>
                  </Td>
                  <Td className="text-xs">{model.provider}</Td>
                  <Td className="text-xs">{fmtTokens(model.context_tokens)}</Td>
                  <Td className="text-xs">{usd(model.input_price_cents_per_m)}</Td>
                  <Td className="text-xs">{usd(model.output_price_cents_per_m)}</Td>
                  <Td className="text-xs">{usd(model.cache_read_price_cents_per_m)}</Td>
                  <Td><Badge tone={model.status === "available" ? "success" : "neutral"}>{model.status}</Badge></Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Dialog
        open={providerOpen}
        onClose={() => setProviderOpen(false)}
        title="Añadir proveedor de IA"
        description="Endpoint OpenAI-compatible. La API key se guarda cifrada en el vault."
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setProviderOpen(false)}>Cancelar</Button>
            <Button variant="primary" size="sm" loading={saving} onClick={() => void addProvider()}>Guardar proveedor</Button>
          </div>
        }
      >
        <div className="space-y-4">
          <Field label="Proveedor" htmlFor="provider-preset">
            <Select
              id="provider-preset"
              value={providerKind}
              onChange={(e) => {
                const preset = PROVIDER_PRESETS.find((p) => p.key === e.target.value);
                setProviderKind(e.target.value);
                if (preset) {
                  setBaseUrl(preset.base_url);
                  if (!providerName.trim()) setProviderName(preset.label);
                }
              }}
            >
              {PROVIDER_PRESETS.map((preset) => (
                <option key={preset.key} value={preset.key}>{preset.label}</option>
              ))}
            </Select>
          </Field>
          <Field label="Nombre" htmlFor="provider-name">
            <Input id="provider-name" value={providerName} onChange={(e) => setProviderName(e.target.value)} placeholder="OpenAI" />
          </Field>
          <Field label="Base URL" htmlFor="provider-url">
            <Input id="provider-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.openai.com/v1" />
          </Field>
          <Field label="API key" htmlFor="provider-key">
            <Input id="provider-key" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="new-password" />
          </Field>
          <p className="text-[11px] text-muted">
            Al guardar se consultará <code>GET /models</code> del proveedor y sus modelos aparecerán aquí y en las políticas.
          </p>
        </div>
      </Dialog>
    </div>
  );
}
