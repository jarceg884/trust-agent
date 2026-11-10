"use client";

/**
 * Policy Studio — crea una política global (bundle) y la asigna a un
 * departamento. Réplica de las secciones de Settings de Hermes Desktop:
 * el administrador decide qué puede usar el usuario. El prompt global viaja
 * en el bundle firmado y prevalece sobre SOUL.md/perfiles (no editable en
 * el desktop).
 */
import { useState } from "react";
import { api, ApiError } from "@/lib/api-client";
import { useApi } from "@/hooks/use-api";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Input,
  Select,
  Textarea,
  useToast,
} from "@/components/ui";

interface DepartmentList {
  departments: { id: string; name: string }[];
}

interface BundleList {
  bundles: { id: string; name: string; description: string; department_id: string | null }[];
}

const DESKTOP_OPTIONS: { key: string; label: string; hint: string; def: boolean }[] = [
  { key: "model_main", label: "Model · Main model", hint: "Página Modelo principal: el usuario elige entre los modelos aprobados para sesiones nuevas; el proveedor siempre es la organización", def: false },
  { key: "model_fallback", label: "Model · Fallback models", hint: "Cadena de modelos de respaldo, solo con modelos aprobados", def: false },
  { key: "model_moa", label: "Model · Mixture of Agents", hint: "Preset corporativo MoA (selector de modelos y /moa)", def: false },
  { key: "chat_model_switch", label: "Chat · Cambio de modelo", hint: "Hot-swap desde el compositor, selector de modelos y /model", def: false },
  { key: "providers", label: "Providers", hint: "Proveedores, claves de modelo, modelos auxiliares por tarea, proveedor/modelo de subagentes y generación de mascotas", def: false },
  { key: "gateways", label: "Gateways", hint: "Conexiones de gateway", def: false },
  { key: "keys", label: "Tools & Keys", hint: "API keys de herramientas y variables de servidor", def: false },
  { key: "billing", label: "Billing & suscripciones", hint: "Planes, suscripciones, facturación y uso", def: false },
  { key: "web_access", label: "Web access", hint: "Servir la webapp desde este equipo", def: false },
  { key: "browser", label: "Browser", hint: "Navegador integrado", def: false },
  { key: "vault", label: "Passwords & Logins", hint: "Vault y autofill", def: false },
  { key: "voice", label: "Voice", hint: "Modo voz", def: false },
  { key: "terminal", label: "Advanced · Terminal", hint: "Ejecución de comandos y modo de aprobación (/approvals)", def: false },
  { key: "plugins", label: "Plugins & MCP", hint: "Extensiones, temas del Marketplace e instalación por enlace", def: false },
  { key: "capabilities", label: "Capabilities", hint: "Skills, plugins, conectores, herramientas y comandos / de skills", def: false },
  { key: "messaging", label: "Messaging", hint: "Canales de mensajería (/handoff)", def: false },
  { key: "artifacts", label: "Artifacts", hint: "Artefactos generados", def: false },
  { key: "cron", label: "Scheduled jobs", hint: "Tareas programadas (/goal, /loop, /heartbeat)", def: false },
  { key: "agents", label: "Agents", hint: "Agentes compartidos y subagentes (/agents, /review, /bg) y sus límites de iteraciones y concurrencia", def: false },
  { key: "profiles", label: "Profiles", hint: "Perfiles del agente", def: false },
  { key: "memory", label: "Memory & Context", hint: "Memoria del agente (/memory, /journey, /refine)", def: true },
  { key: "sessions", label: "Sessions", hint: "Borrado permanente de chats", def: true },
  { key: "workspace", label: "Workspace", hint: "Archivos y proyectos", def: true },
];

export function PolicyStudioPage() {
  const { push } = useToast();
  const departments = useApi<DepartmentList>("/api/departments");
  const bundles = useApi<BundleList>("/api/policy-bundles");

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [desktop, setDesktop] = useState<Record<string, boolean>>(
    Object.fromEntries(DESKTOP_OPTIONS.map((o) => [o.key, o.def])),
  );
  const [allowVault, setAllowVault] = useState(false);
  const [allowPayments, setAllowPayments] = useState(false);
  const [globalPrompt, setGlobalPrompt] = useState(
    "Eres el asistente de la organización. Responde en español.\nNo reveles información interna ni credenciales.\nCuando no estés seguro, pregunta antes de actuar.",
  );
  const [profileName, setProfileName] = useState("Operaciones");
  const [profileModel, setProfileModel] = useState("glm-5.2");
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!name.trim()) {
      push("error", "Ponle un nombre a la política.");
      return;
    }
    setSaving(true);
    try {
      await api("/api/policy-bundles", {
        method: "POST",
        body: {
          name,
          description,
          desktop,
          credentials: { allow_credential_vault: allowVault, allow_payment_autofill: allowPayments },
          global_prompt: globalPrompt,
          profiles: [{ name: profileName, model: profileModel }],
          department_id: departmentId || null,
        },
      });
      push("success", "Política global creada.");
      setName("");
      setDescription("");
      bundles.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudo guardar.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Crear política global" description="Se asigna a un departamento; sus usuarios la heredan." />
        <CardBody>
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <label className="text-xs font-medium">Nombre</label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Operaciones · Estándar" />
            </div>
            <div>
              <label className="text-xs font-medium">Departamento</label>
              <Select value={departmentId} onChange={(e) => setDepartmentId(e.target.value)} className="w-full">
                <option value="">Toda la organización</option>
                {(departments.data?.departments ?? []).map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </Select>
            </div>
            <div className="md:col-span-2">
              <label className="text-xs font-medium">Descripción</label>
              <Input value={description} onChange={(e) => setDescription(e.target.value)} />
            </div>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Desktop app UI" description="Réplica de Settings: lo desactivado no estará disponible en la app. Siempre bloqueado: suscripciones upstream, envío de diagnósticos, actualizaciones del runtime, onboarding de proveedores, modelos locales y DevTools." />
        <CardBody>
          <div className="space-y-2">
            {DESKTOP_OPTIONS.map((opt) => (
              <div key={opt.key} className="flex items-center justify-between gap-4 rounded-lg border border-border-subtle px-3 py-2">
                <div>
                  <p className="text-sm font-medium">{opt.label}</p>
                  <p className="text-xs text-muted">{opt.hint}</p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={desktop[opt.key]}
                  onClick={() => setDesktop((prev) => ({ ...prev, [opt.key]: !prev[opt.key] }))}
                  className={`relative h-6 w-11 shrink-0 rounded-full transition ${desktop[opt.key] ? "bg-accent" : "bg-border"}`}
                >
                  <span className={`absolute top-0.5 size-5 rounded-full bg-white transition ${desktop[opt.key] ? "left-[22px]" : "left-0.5"}`} />
                </button>
              </div>
            ))}
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Credenciales" description="Accesos del vault permitidos por esta política." />
        <CardBody>
          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={allowVault} onChange={(e) => setAllowVault(e.target.checked)} />
              Usar credenciales del vault
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={allowPayments} onChange={(e) => setAllowPayments(e.target.checked)} />
              Autofill de pagos (tarjetas / datos de pago)
            </label>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Prompt global"
          description="Se inyecta como instrucciones del sistema. No editable en el desktop; prevalece sobre SOUL.md y perfiles."
        />
        <CardBody>
          <Textarea value={globalPrompt} onChange={(e) => setGlobalPrompt(e.target.value)} rows={5} className="w-full" />
          <p className="mt-2 text-xs text-muted">
            Precedencia: Organización &gt; SOUL.md &gt; perfil del usuario &gt; sesión.
          </p>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Perfil predeterminado" description="Perfil de agente + preset de modelo." />
        <CardBody>
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <label className="text-xs font-medium">Nombre del perfil</label>
              <Input value={profileName} onChange={(e) => setProfileName(e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium">Modelo</label>
              <Input value={profileModel} onChange={(e) => setProfileModel(e.target.value)} />
            </div>
          </div>
        </CardBody>
      </Card>

      <div className="flex justify-end">
        <Button onClick={() => void save()} disabled={saving} size="sm" variant="primary">
          {saving ? "Guardando…" : "Guardar política global"}
        </Button>
      </div>

      <Card>
        <CardHeader title="Políticas existentes" description={`${bundles.data?.bundles.length ?? 0} definidas`} />
        <CardBody>
          <div className="space-y-2">
            {(bundles.data?.bundles ?? []).map((b) => (
              <div key={b.id} className="flex items-center justify-between rounded-lg border border-border-subtle px-3 py-2">
                <div>
                  <p className="text-sm font-medium">{b.name}</p>
                  <p className="text-xs text-muted">{b.description || "Sin descripción"}</p>
                </div>
                <span className="text-[11px] text-faint">
                  {departments.data?.departments.find((d) => d.id === b.department_id)?.name ?? "Toda la organización"}
                </span>
              </div>
            ))}
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
