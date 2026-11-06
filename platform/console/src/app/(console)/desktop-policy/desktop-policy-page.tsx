"use client";

/**
 * Desktop Policy — la vista "espejo" de Settings de Hermes Desktop.
 *
 * El administrador ve exactamente las mismas secciones que el usuario final
 * tiene en la app (Model, Chat, Appearance, Workspace, Safety, Browser,
 * Passwords & Logins, Memory & Context, Voice, Advanced, Notifications,
 * Providers, Gateways, Keyboard Shortcuts, Tools & Keys, Sessions, Plugins,
 * About) y decide qué puede usar su organización.
 *
 * Los toggles booleanos escriben de verdad en PUT /api/policies/org; las
 * claves de lista/rango muestran su valor vigente y enlazan al constructor
 * completo de Policies (donde se editan con su control específico).
 */
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api-client";
import { useApi } from "@/hooks/use-api";
import type { PolicyMatrix } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ErrorState,
  Skeleton,
  useToast,
} from "@/components/ui";

interface Section {
  id: string;
  label: string;
  hint: string;
  keys: string[];
}

const SECTIONS: Section[] = [
  { id: "model", label: "Model", hint: "Modelo principal, fallback y MoA", keys: ["allowed_models", "allowed_providers", "approved_models_only"] },
  { id: "chat", label: "Chat", hint: "Comportamiento del chat", keys: ["approval_mode", "max_daily_sessions", "allow_goal_mode"] },
  { id: "appearance", label: "Appearance", hint: "Tema y presentación (siempre local)", keys: [] },
  { id: "workspace", label: "Workspace", hint: "Archivos y entorno de trabajo", keys: ["require_write_sandbox", "allowed_env_passthrough"] },
  { id: "safety", label: "Safety", hint: "Sesión, MFA, atestación y actualizaciones", keys: ["require_mfa", "session_timeout_minutes", "offline_grace_minutes", "attestation_overdue_hours", "unbind_mode", "require_checkpoints", "allow_self_updates"] },
  { id: "browser", label: "Browser", hint: "Navegador y acceso a red", keys: ["web_domain_policy", "allow_private_network_access"] },
  { id: "vault", label: "Passwords & Logins", hint: "Credenciales y autofill", keys: ["allow_credential_vault", "vault_allowed_credential_types", "allowed_secret_sources", "allow_payment_autofill"] },
  { id: "memory", label: "Memory & Context", hint: "Memoria y personalidad", keys: ["allowed_memory_providers", "allow_custom_personality"] },
  { id: "voice", label: "Voice", hint: "Modo voz", keys: ["allow_voice_mode"] },
  { id: "advanced", label: "Advanced", hint: "Herramientas, terminal y hooks", keys: ["allowed_tools", "allowed_skills", "allow_shell_hooks", "allow_gateway_hooks", "allowed_terminal_backends", "allowed_command_patterns", "denied_command_patterns"] },
  { id: "notifications", label: "Notifications", hint: "Mensajería y avisos", keys: ["messaging_permissions"] },
  { id: "providers", label: "Providers", hint: "Proveedores de modelos", keys: ["allowed_providers"] },
  { id: "gateways", label: "Gateways", hint: "Conexiones de gateway", keys: ["allowed_connections"] },
  { id: "shortcuts", label: "Keyboard Shortcuts", hint: "Atajos (siempre locales)", keys: [] },
  { id: "tools", label: "Tools & Keys", hint: "Herramientas, MCP y plugins", keys: ["allowed_tools", "allowed_mcp_servers", "allowed_plugins", "allowed_secret_sources"] },
  { id: "sessions", label: "Sessions", hint: "Retención y tareas programadas", keys: ["session_retention_days", "artifact_sharing", "allow_scheduled_tasks"] },
  { id: "plugins", label: "Plugins", hint: "Extensiones permitidas", keys: ["allowed_plugins", "allowed_mcp_servers", "allowed_skills"] },
  { id: "about", label: "About", hint: "Actualizaciones", keys: ["allow_self_updates"] },
];

function valueLabel(value: unknown): string {
  if (value === true) return "Permitido";
  if (value === false) return "Bloqueado";
  if (Array.isArray(value)) return value.length ? `${value.length} elemento(s)` : "Vacío";
  if (value === null || value === undefined || value === "") return "Sin definir";
  return String(value);
}

export function DesktopPolicyPage() {
  const matrix = useApi<PolicyMatrix>("/api/policies/matrix");
  const { push } = useToast();
  const [active, setActive] = useState("model");
  const [saving, setSaving] = useState<string | null>(null);
  const [local, setLocal] = useState<Record<string, unknown>>({});

  useEffect(() => {
    setLocal({});
  }, [matrix.data]);

  const data = matrix.data;
  const section = SECTIONS.find((s) => s.id === active) ?? SECTIONS[0];

  const entries = useMemo(() => {
    if (!data) return [];
    return section.keys
      .map((key) => data.catalog.find((c) => c.key === key))
      .filter((entry): entry is NonNullable<typeof entry> => Boolean(entry));
  }, [data, section]);

  async function toggle(key: string, next: boolean) {
    setSaving(key);
    setLocal((prev) => ({ ...prev, [key]: next }));
    try {
      await api("/api/policies/org", { method: "PUT", body: { key, value: next } });
      push("success", "Policy updated.");
      matrix.refetch();
    } catch (cause) {
      setLocal((prev) => ({ ...prev, [key]: !next }));
      push("error", cause instanceof ApiError ? cause.message : "Could not save the policy.");
    } finally {
      setSaving(null);
    }
  }

  if (matrix.error) {
    return <ErrorState error={matrix.error} onRetry={matrix.refetch} context="GET /api/policies/matrix" />;
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
      <Card>
        <CardHeader title="Desktop app" description="Las mismas secciones que ve el usuario." />
        <nav className="p-2">
          {SECTIONS.map((s) => {
            const isActive = s.id === active;
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => setActive(s.id)}
                className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm transition ${
                  isActive
                    ? "bg-accent-soft font-medium text-accent"
                    : "text-muted hover:bg-surface-alt hover:text-foreground"
                }`}
              >
                <span>{s.label}</span>
                <span className="text-[10px] text-faint">{s.keys.length || "—"}</span>
              </button>
            );
          })}
        </nav>
      </Card>

      <Card>
        <CardHeader
          title={section.label}
          description={section.hint}
          actions={
            <Link href="/policies">
              <Button variant="secondary" size="sm">Open full policies</Button>
            </Link>
          }
        />
        <CardBody>
          {matrix.loading ? (
            <div className="space-y-3">
              <Skeleton className="h-16" />
              <Skeleton className="h-16" />
            </div>
          ) : entries.length === 0 ? (
            <p className="text-sm text-muted">
              Esta sección no tiene controles de gobernanza: siempre está disponible en la app.
            </p>
          ) : (
            <div className="space-y-3">
              {entries.map((entry) => {
                const current =
                  entry.key in local ? local[entry.key] : data?.org_effective[entry.key];
                const isBool = entry.value_type === "boolean";
                return (
                  <div
                    key={entry.key}
                    className="flex items-start justify-between gap-4 rounded-lg border border-border-subtle px-4 py-3"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{entry.key.replace(/_/g, " ")}</p>
                      <p className="mt-0.5 text-xs text-muted">
                        {isBool ? `Valor actual: ${valueLabel(current)}` : `Configuración: ${valueLabel(current)}`}
                      </p>
                    </div>
                    {isBool ? (
                      <button
                        type="button"
                        role="switch"
                        aria-checked={current === true}
                        disabled={saving === entry.key}
                        onClick={() => void toggle(entry.key, current !== true)}
                        className={`relative h-6 w-11 shrink-0 rounded-full transition ${
                          current === true ? "bg-accent" : "bg-border"
                        } ${saving === entry.key ? "opacity-60" : ""}`}
                      >
                        <span
                          className={`absolute top-0.5 size-5 rounded-full bg-white transition ${
                            current === true ? "left-[22px]" : "left-0.5"
                          }`}
                        />
                      </button>
                    ) : (
                      <Badge tone="neutral">{valueLabel(current)}</Badge>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
