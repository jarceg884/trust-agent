"use client";

/**
 * Policies — un solo panel (spec 044 v2).
 *
 * Una política se asigna a UN departamento o a UNA persona. Dentro de la
 * política el admin configura: réplica de Settings del Desktop (qué puede usar
 * el usuario), prompt organizacional, modelos permitidos + modelo por defecto,
 * agentes compartidos y acceso al vault. No existen políticas personales ni
 * "organization policies": la asignación es siempre explícita.
 */
import Link from "next/link";
import { useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api-client";
import { useApi } from "@/hooks/use-api";
import type { AccessOverview } from "@/lib/types";
import {
  Button,
  Card,
  CardBody,
  Input,
  Select,
  Textarea,
  useToast,
} from "@/components/ui";

interface Policy {
  id: string;
  name: string;
  description: string;
  desktop: Record<string, boolean>;
  credentials: Record<string, boolean>;
  global_prompt: string;
  profiles: { name?: string; model?: string }[];
  contracts?: { name?: string; text?: string }[];
  models: string[];
  default_model: string | null;
  agents: string[];
  department_id: string | null;
  user_id: string | null;
}

interface PolicyList { bundles: Policy[] }
interface DepartmentsList { departments: { department_id: string; name: string }[] }
interface DirectoryList { users: { user_id: string; email: string; display_name: string | null }[] }
interface ModelList { models: { model_name: string; display_name: string; provider: string; source: string }[] }
interface AgentList { agents: { id: string; name: string; description: string | null }[] }
interface VaultSecret { id: string; name: string; credential_type: string }
interface VaultList { credentials: VaultSecret[] }

const DESKTOP_GROUPS: { group: string; hint: string; options: { key: string; label: string; hint: string; def: boolean }[] }[] = [
  {
    group: "Model",
    hint: "Por defecto el modelo lo define la política. Activar una opción solo permite elegir entre los modelos aprobados; el proveedor siempre es la organización.",
    options: [
      { key: "model_main", label: "Main model selector", hint: "Página Modelo principal: el usuario elige entre los modelos aprobados para sesiones nuevas; el proveedor siempre es la organización", def: false },
      { key: "model_fallback", label: "Fallback models", hint: "Cadena de modelos de respaldo, solo con modelos aprobados", def: false },
      { key: "model_moa", label: "Mixture of Agents", hint: "Preset corporativo MoA (selector de modelos y /moa)", def: false },
      { key: "chat_model_switch", label: "Cambio de modelo en chat", hint: "Hot-swap desde el compositor, selector de modelos y /model", def: false },
    ],
  },
  {
    group: "Chat & Workspace",
    hint: "Superficies de trabajo del agente. Memory también le concede su herramienta de memoria.",
    options: [
      { key: "workspace", label: "Workspace", hint: "Archivos y proyectos", def: true },
      { key: "sessions", label: "Sessions", hint: "Borrado permanente de chats", def: true },
      { key: "memory", label: "Memory & Context", hint: "Memoria del agente (/memory, /journey, /refine)", def: true },
    ],
  },
  {
    group: "CLI & Agente",
    hint: "Herramientas que el agente usa en todas sus sesiones (chat del escritorio, CLI y segundo plano). Activar concede la herramienta y lo técnico que necesita (Terminal habilita el backend local); lo que fije Desktop Policy actúa como techo.",
    options: [
      { key: "cli", label: "Acceso a la CLI", hint: "Comandos hermes en terminal", def: false },
      { key: "cli_terminal", label: "Terminal / shell", hint: "El agente ejecuta comandos en el equipo del usuario (terminal local y procesos)", def: false },
      { key: "cli_files_read", label: "Leer archivos", hint: "Acceso de lectura al filesystem", def: false },
      { key: "cli_files_write", label: "Escribir archivos", hint: "Crear/modificar archivos", def: false },
      { key: "cli_web", label: "Web search / fetch", hint: "Buscar y descargar de internet", def: false },
      { key: "cli_code", label: "Ejecución de código", hint: "Python / scripts", def: false },
    ],
  },
  {
    group: "Superficies de la app",
    hint: "Páginas y canales que el usuario puede abrir. Capabilities, Scheduled jobs y Agents también conceden al agente skills, tareas programadas y subagentes.",
    options: [
      { key: "capabilities", label: "Capabilities", hint: "Skills, plugins, conectores, herramientas y comandos / de skills", def: false },
      { key: "messaging", label: "Messaging", hint: "Canales de mensajería (/handoff)", def: false },
      { key: "artifacts", label: "Artifacts", hint: "Artefactos generados", def: false },
      { key: "cron", label: "Scheduled jobs", hint: "Tareas programadas (/goal, /loop, /heartbeat)", def: false },
      { key: "agents", label: "Agents", hint: "Agentes compartidos y subagentes (/agents, /review, /bg) y sus límites de iteraciones y concurrencia", def: false },
      { key: "profiles", label: "Profiles", hint: "Rail de perfiles del escritorio. Los perfiles se definen en Modelos → Perfiles del agente; con la lista vacía los usuarios solo ven el default", def: false },
    ],
  },
  {
    group: "Privacidad & Seguridad",
    hint: "Controles sensibles que la organización puede apagar. Vault, Browser y Voice también conceden al agente la bóveda, el navegador y la voz.",
    options: [
      { key: "providers", label: "Providers", hint: "Proveedores, claves de modelo, modelos auxiliares por tarea, proveedor/modelo de subagentes y generación de mascotas", def: false },
      { key: "gateways", label: "Gateways", hint: "Conexiones de gateway", def: false },
      { key: "keys", label: "Tools & Keys", hint: "API keys de herramientas y variables de servidor", def: false },
      { key: "billing", label: "Billing & suscripciones", hint: "Planes, suscripciones, facturación y uso", def: false },
      { key: "web_access", label: "Web access", hint: "Servir la webapp desde este equipo", def: false },
      { key: "vault", label: "Vault / Passwords", hint: "Vault y autofill", def: false },
      { key: "browser", label: "Browser", hint: "Navegador integrado", def: false },
      { key: "voice", label: "Voice", hint: "Modo voz", def: false },
      { key: "terminal", label: "Advanced · Terminal", hint: "Paneles de terminal y modo de aprobación (/approvals) del escritorio. Los comandos del agente se conceden en CLI & Agente → Terminal / shell", def: false },
      { key: "plugins", label: "Plugins & MCP", hint: "Extensiones, temas del Marketplace e instalación por enlace", def: false },
    ],
  },
];

const ALL_OPTIONS = DESKTOP_GROUPS.flatMap((g) => g.options);
const SECTION = [
  { key: "general", label: "General", hint: "Nombre y asignación" },
  { key: "desktop", label: "Desktop restrictions", hint: "Réplica de Settings" },
  { key: "prompt", label: "Contrato organizacional", hint: "Cómo debe interactuar el agente" },
  { key: "models", label: "Modelos", hint: "Acceso y modelo por defecto" },
  { key: "access", label: "Accesos y agentes", hint: "Vault y agentes" },
] as const;

type SectionKey = (typeof SECTION)[number]["key"];

const EMPTY_DESKTOP = Object.fromEntries(ALL_OPTIONS.map((o) => [o.key, o.def]));

function defaults() {
  return {
    name: "",
    description: "",
    scope: "department" as "department" | "user",
    departmentId: "",
    userId: "",
    desktop: { ...EMPTY_DESKTOP } as Record<string, boolean>,
    contracts: [
      {
        name: "Contrato general",
        text: "Eres el asistente de la organización. Responde en español.\nNo reveles información interna ni credenciales.\nCuando no estés seguro, pregunta antes de actuar.",
      },
    ] as { name: string; text: string }[],
    models: [] as string[],
    defaultModel: "",
    profiles: [] as { name: string; model: string }[],
    agents: [] as string[],
    vaultSecretIds: [] as string[],
  };
}

export function PolicyManagerPage() {
  const { push } = useToast();
  const policies = useApi<PolicyList>("/api/policy-bundles");
  const departments = useApi<DepartmentsList>("/api/departments");
  // Model Access (la OTRA mitad del interruptor): el escritorio recibe la
  // intersección de esta política con las aprobaciones por nivel. Sin esto,
  // un modelo marcado aquí pero sin aprobar en Model Access desaparece en
  // silencio del escritorio (incidente glm-5.3, 2026-11-10).
  const access = useApi<AccessOverview>("/api/models/access");
  const orgApproved = useMemo(
    () =>
      new Set(
        (access.data?.models ?? [])
          .filter(m => m.approvals.some(a => a.level === "org"))
          .map(m => m.model_name)
      ),
    [access.data]
  );
  const users = useApi<DirectoryList>("/api/users");
  const models = useApi<ModelList>("/api/models");
  const agents = useApi<AgentList>("/api/agents");
  const vault = useApi<VaultList>("/api/vault/credentials");

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Policy | null>(null);
  const [section, setSection] = useState<SectionKey>("general");
  const [form, setForm] = useState(defaults());
  // Modelos marcados en la política que Model Access no tiene aprobados:
  // no llegan al escritorio (intersección). Ver aviso en la sección "models".
  const selectedWithoutAccess = useMemo(
    () => form.models.filter(m => !orgApproved.has(m)),
    [form.models, orgApproved]
  );
  const [saving, setSaving] = useState(false);
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const list = policies.data?.bundles ?? [];
    const q = query.trim().toLowerCase();
    return q ? list.filter((p) => `${p.name} ${p.description}`.toLowerCase().includes(q)) : list;
  }, [policies.data, query]);

  function openCreate() {
    setEditing(null);
    setForm(defaults());
    setSection("general");
    setOpen(true);
  }

  function openEdit(policy: Policy) {
    setEditing(policy);
    setForm({
      name: policy.name,
      description: policy.description,
      scope: policy.user_id ? "user" : "department",
      departmentId: policy.department_id ?? "",
      userId: policy.user_id ?? "",
      desktop: { ...EMPTY_DESKTOP, ...policy.desktop },
      contracts: policy.contracts?.length
        ? policy.contracts.map((c) => ({ name: c.name || "Contrato", text: c.text || "" }))
        : [{ name: "Contrato general", text: policy.global_prompt || "" }],
      models: policy.models ?? [],
      defaultModel: policy.default_model ?? "",
      // El editor debe cargar los perfiles existentes: antes este campo se
      // mandaba vacío en cada PUT y el servidor (que escribe lo que recibe)
      // BORRABA la lista con solo editar la política.
      profiles: (policy.profiles ?? [])
        .map((p) => ({ name: p?.name ?? "", model: p?.model ?? "" }))
        .filter((p) => p.name),
      agents: policy.agents ?? [],
      vaultSecretIds: Array.isArray((policy.credentials as { vault_secret_ids?: string[] })?.vault_secret_ids)
        ? ((policy.credentials as { vault_secret_ids?: string[] }).vault_secret_ids as string[])
        : [],
    });
    setSection("general");
    setOpen(true);
  }

  async function save() {
    if (!form.name.trim()) {
      push("error", "Ponle un nombre a la política.");
      return;
    }
    if (form.scope === "department" && !form.departmentId) {
      push("error", "Selecciona el departamento al que se asigna.");
      return;
    }
    if (form.scope === "user" && !form.userId) {
      push("error", "Selecciona la persona a la que se asigna.");
      return;
    }
    for (const p of form.profiles) {
      if (!p.name.trim() || !p.model) {
        push("error", "Cada perfil necesita nombre y modelo.");
        return;
      }
      if (form.models.length > 0 && !form.models.includes(p.model)) {
        push("error", `El perfil "${p.name}" usa un modelo que no está marcado en la política.`);
        return;
      }
    }
    if (new Set(form.profiles.map((p) => p.name.trim().toLowerCase())).size !== form.profiles.length) {
      push("error", "Los perfiles deben tener nombres distintos.");
      return;
    }
    setSaving(true);
    try {
      const body = {
        name: form.name,
        description: form.description,
        desktop: form.desktop,
        credentials: { vault_secret_ids: form.vaultSecretIds, allow_payment_autofill: false },
        contracts: form.contracts,
        global_prompt: form.contracts.map((c) => `[${c.name}]\n${c.text}`).join("\n\n"),
        profiles: form.profiles,
        models: form.models,
        default_model: form.defaultModel || null,
        agents: form.agents,
        department_id: form.scope === "department" ? form.departmentId : null,
        user_id: form.scope === "user" ? form.userId : null,
      };
      if (editing) {
        await api(`/api/policy-bundles/${editing.id}`, { method: "PUT", body });
        push("success", "Política actualizada.");
      } else {
        await api("/api/policy-bundles", { method: "POST", body });
        push("success", "Política creada.");
      }
      setOpen(false);
      policies.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudo guardar la política.");
    } finally {
      setSaving(false);
    }
  }

  async function remove(policy: Policy) {
    if (!window.confirm(`¿Eliminar la política "${policy.name}"?`)) return;
    try {
      await api(`/api/policy-bundles/${policy.id}`, { method: "DELETE" });
      push("success", "Política eliminada.");
      policies.refetch();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudo eliminar.");
    }
  }

  function assignmentLabel(policy: Policy): string {
    if (policy.user_id) {
      const user = users.data?.users.find((u) => u.user_id === policy.user_id);
      return `Persona · ${user?.display_name || user?.email || policy.user_id}`;
    }
    if (policy.department_id) {
      const dep = departments.data?.departments.find((d) => d.department_id === policy.department_id);
      return `Departamento · ${dep?.name || policy.department_id}`;
    }
    return "Sin asignar";
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardBody>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Buscar política…" className="w-64" />
              <span className="text-xs text-muted">{filtered.length} políticas</span>
            </div>
            <Button onClick={openCreate} size="sm" variant="primary">+ Crear política</Button>
          </div>
        </CardBody>
      </Card>

      {filtered.length === 0 ? (
        <Card>
          <CardBody>
            <p className="text-sm text-muted">No hay políticas. Crea una y asígnala a un departamento o a una persona.</p>
          </CardBody>
        </Card>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {filtered.map((policy) => (
            <Card key={policy.id}>
              <CardBody>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold">{policy.name}</p>
                    <p className="mt-0.5 line-clamp-2 text-xs text-muted">{policy.description || "Sin descripción"}</p>
                  </div>
                  <span className="shrink-0 rounded-full border border-border-subtle px-2 py-0.5 text-[10px] text-muted">
                    {assignmentLabel(policy)}
                  </span>
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5 text-[10px] text-muted">
                  <span className="rounded bg-surface px-1.5 py-0.5">{policy.models.length} modelos</span>
                  <span className="rounded bg-surface px-1.5 py-0.5">{policy.agents.length} agentes</span>
                  {policy.default_model ? <span className="rounded bg-surface px-1.5 py-0.5">default: {policy.default_model}</span> : null}
                  <span className="rounded bg-surface px-1.5 py-0.5">
                    {Object.values(policy.desktop).filter(Boolean).length} opciones desktop
                  </span>
                </div>
                <div className="mt-3 flex justify-end gap-2">
                  <Button onClick={() => openEdit(policy)} size="sm" variant="secondary">Editar</Button>
                  <Button onClick={() => void remove(policy)} size="sm" variant="ghost">Eliminar</Button>
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {open ? (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/30 backdrop-blur-sm">
          <div className="flex h-full w-full max-w-3xl flex-col bg-background shadow-2xl">
            <div className="flex items-center justify-between border-b border-border-subtle px-6 py-4">
              <div>
                <p className="text-sm font-semibold">{editing ? "Editar política" : "Nueva política"}</p>
                <p className="text-xs text-muted">Una política = departamento o persona.</p>
              </div>
              <div className="flex items-center gap-2">
                <Button onClick={() => setOpen(false)} size="sm" variant="ghost">Cancelar</Button>
                <Button onClick={() => void save()} disabled={saving} size="sm" variant="primary">
                  {saving ? "Guardando…" : "Guardar política"}
                </Button>
              </div>
            </div>

            <div className="flex min-h-0 flex-1">
              <nav className="w-52 shrink-0 border-r border-border-subtle p-3">
                {SECTION.map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => setSection(item.key)}
                    className={`mb-1 w-full rounded-lg px-3 py-2 text-left transition ${
                      section === item.key ? "bg-accent/10 text-accent" : "text-muted hover:bg-surface"
                    }`}
                  >
                    <span className="block text-xs font-medium">{item.label}</span>
                    <span className="block text-[10px] text-faint">{item.hint}</span>
                  </button>
                ))}
              </nav>

              <div className="min-h-0 flex-1 overflow-y-auto p-6">
                {section === "general" ? (
                  <div className="space-y-4">
                    <div>
                      <label className="text-xs font-medium">Nombre de la política</label>
                      <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Operaciones · Estándar" />
                    </div>
                    <div>
                      <label className="text-xs font-medium">Descripción</label>
                      <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Qué permite esta política" />
                    </div>
                    <div className="grid gap-3 md:grid-cols-2">
                      <div>
                        <label className="text-xs font-medium">Asignar a</label>
                        <Select
                          value={form.scope}
                          onChange={(e) => setForm({ ...form, scope: e.target.value as "department" | "user", departmentId: "", userId: "" })}
                          className="w-full"
                        >
                          <option value="department">Departamento</option>
                          <option value="user">Persona</option>
                        </Select>
                      </div>
                      <div>
                        <label className="text-xs font-medium">{form.scope === "department" ? "Departamento" : "Persona"}</label>
                        {form.scope === "department" ? (
                          <Select value={form.departmentId} onChange={(e) => setForm({ ...form, departmentId: e.target.value })} className="w-full">
                            <option value="">Selecciona…</option>
                            {(departments.data?.departments ?? []).map((d) => (
                              <option key={d.department_id} value={d.department_id}>{d.name}</option>
                            ))}
                          </Select>
                        ) : (
                          <Select value={form.userId} onChange={(e) => setForm({ ...form, userId: e.target.value })} className="w-full">
                            <option value="">Selecciona…</option>
                            {(users.data?.users ?? []).map((u) => (
                              <option key={u.user_id} value={u.user_id}>{u.display_name || u.email}</option>
                            ))}
                          </Select>
                        )}
                      </div>
                    </div>
                  </div>
                ) : null}

                {section === "desktop" ? (
                  <div className="space-y-4">
                    <div className="flex items-start justify-between gap-3">
                      <p className="text-xs text-muted">
                        Réplica de Settings de Trust Agent Desktop + capacidades CLI. Lo que apagues aquí no estará disponible para el usuario ni para el agente en terminal. Siempre bloqueado en Trust Agent, sin importar la política: suscripciones y créditos del proveedor upstream, envío de diagnósticos, actualizaciones del runtime, onboarding de proveedores, modelos locales y DevTools.
                      </p>
                      <div className="flex shrink-0 items-center gap-2">
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() =>
                            setForm({
                              ...form,
                              desktop: Object.fromEntries(Object.keys(form.desktop).map((key) => [key, true])),
                            })
                          }
                        >
                          Habilitar todo
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() =>
                            setForm({
                              ...form,
                              desktop: Object.fromEntries(Object.keys(form.desktop).map((key) => [key, false])),
                            })
                          }
                        >
                          Deshabilitar todo
                        </Button>
                      </div>
                    </div>
                    {DESKTOP_GROUPS.map((group) => (
                      <div key={group.group} className="rounded-lg border border-border-subtle">
                        <div className="border-b border-border-subtle px-3 py-2">
                          <p className="text-xs font-semibold">{group.group}</p>
                          <p className="text-[10px] text-faint">{group.hint}</p>
                        </div>
                        <div className="divide-y divide-border-subtle">
                          {group.options.map((opt) => {
                            const on = Boolean(form.desktop[opt.key]);
                            return (
                              <div key={opt.key} className="flex items-center justify-between gap-3 px-3 py-2">
                                <div>
                                  <p className="text-xs font-medium">{opt.label}</p>
                                  <p className="text-[10px] text-faint">{opt.hint}</p>
                                </div>
                                <button
                                  type="button"
                                  role="switch"
                                  aria-checked={on}
                                  onClick={() => setForm({ ...form, desktop: { ...form.desktop, [opt.key]: !on } })}
                                  className={`relative h-5 w-9 shrink-0 rounded-full border transition ${on ? "border-accent bg-accent" : "border-border bg-gray-300"} ${on ? "" : "shadow-inner"}`}
                                >
                                  <span className={`absolute top-0.5 size-4 rounded-full bg-white shadow-sm transition ${on ? "left-[18px]" : "left-0.5"}`} />
                                </button>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}

                {section === "prompt" ? (
                  <div className="space-y-4">
                    <div>
                      <label className="text-xs font-medium">Contratos organizacionales</label>
                      <p className="text-[11px] text-muted">
                        Cada contrato define cómo debe interactuar el agente en todos los modelos y políticas: tono, límites, qué puede responder y qué debe rechazar.
                      </p>
                    </div>
                    {form.contracts.map((contract, index) => (
                      <div key={index} className="space-y-2 rounded-lg border border-border-subtle p-3">
                        <div className="flex items-center gap-2">
                          <Input
                            value={contract.name}
                            onChange={(e) => {
                              const next = [...form.contracts];
                              next[index] = { ...next[index], name: e.target.value };
                              setForm({ ...form, contracts: next });
                            }}
                            placeholder="Nombre del contrato"
                          />
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setForm({ ...form, contracts: form.contracts.filter((_, i) => i !== index) })}
                          >
                            Eliminar
                          </Button>
                        </div>
                        <Textarea
                          value={contract.text}
                          onChange={(e) => {
                            const next = [...form.contracts];
                            next[index] = { ...next[index], text: e.target.value };
                            setForm({ ...form, contracts: next });
                          }}
                          rows={6}
                          className="w-full"
                        />
                      </div>
                    ))}
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => setForm({ ...form, contracts: [...form.contracts, { name: `Contrato ${form.contracts.length + 1}`, text: "" }] })}
                    >
                      + Añadir contrato
                    </Button>
                    <p className="text-[11px] text-muted">Precedencia: Organización &gt; SOUL.md &gt; perfil del usuario &gt; sesión. No editable desde el desktop ni la CLI.</p>
                  </div>
                ) : null}

                {section === "models" ? (
                  <div className="space-y-3">
                    <p className="text-xs text-muted">Marca los modelos a los que esta política da acceso.</p>
                    {selectedWithoutAccess.length > 0 ? (
                      <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2.5 text-xs text-amber-300">
                        <span className="font-semibold">Ojo:</span> {selectedWithoutAccess.length === 1 ? "este modelo seleccionado no" : `${selectedWithoutAccess.length} modelos seleccionados no`}{" "}
                        está aprobado en Model Access y <span className="font-semibold">no llegará al escritorio</span>:{" "}
                        <span className="font-mono">{selectedWithoutAccess.join(", ")}</span>. El escritorio recibe la
                        intersección de esta política con Model Access. Apruébalo en{" "}
                        <Link className="underline" href="/models">Models → Model Access</Link> (nivel organización).
                      </div>
                    ) : null}
                    <div className="max-h-72 space-y-1 overflow-y-auto rounded-lg border border-border-subtle p-2">
                      {(models.data?.models ?? []).map((model) => {
                        const checked = form.models.includes(model.model_name);
                        return (
                          <label key={model.model_name} className="flex items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-surface">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() =>
                                setForm({
                                  ...form,
                                  models: checked ? form.models.filter((m) => m !== model.model_name) : [...form.models, model.model_name],
                                  defaultModel: checked && form.defaultModel === model.model_name ? "" : form.defaultModel,
                                })
                              }
                            />
                            <span className="font-medium">{model.display_name || model.model_name}</span>
                            <span className="text-faint">{model.provider}</span>
                            {!orgApproved.has(model.model_name) ? (
                              <span className="ml-auto rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300">
                                sin aprobar en Model Access
                              </span>
                            ) : null}
                          </label>
                        );
                      })}
                    </div>
                    <div>
                      <label className="text-xs font-medium">Modelo por defecto de la política</label>
                      <Select value={form.defaultModel} onChange={(e) => setForm({ ...form, defaultModel: e.target.value })} className="w-full">
                        <option value="">Sin modelo por defecto</option>
                        {form.models.map((m) => <option key={m} value={m}>{m}</option>)}
                      </Select>
                    </div>
                    <div className="space-y-2">
                      <label className="text-xs font-medium">Perfiles del agente</label>
                      <p className="text-[11px] text-muted">
                        Perfiles que esta política ofrece en el escritorio además del default. El
                        escritorio gobernado muestra SOLO estos (con el switch Profiles activado en
                        Características); el modelo debe estar marcado arriba.
                      </p>
                      {form.desktop.profiles && form.profiles.length === 0 ? (
                        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2.5 text-xs text-amber-300">
                          <span className="font-semibold">Ojo:</span> el switch Profiles está activado pero la
                          lista está vacía — los usuarios solo verán el perfil <span className="font-mono">default</span>.
                        </div>
                      ) : null}
                      {form.profiles.map((profile, index) => (
                        <div key={index} className="flex items-center gap-2">
                          <Input
                            value={profile.name}
                            onChange={(e) => {
                              const next = [...form.profiles];
                              next[index] = { ...next[index], name: e.target.value };
                              setForm({ ...form, profiles: next });
                            }}
                            placeholder="Nombre (p.ej. Operaciones)"
                          />
                          <Select
                            value={profile.model}
                            onChange={(e) => {
                              const next = [...form.profiles];
                              next[index] = { ...next[index], model: e.target.value };
                              setForm({ ...form, profiles: next });
                            }}
                            className="w-44"
                          >
                            <option value="">Modelo…</option>
                            {form.models.map((m) => <option key={m} value={m}>{m}</option>)}
                          </Select>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setForm({ ...form, profiles: form.profiles.filter((_, i) => i !== index) })}
                          >
                            Eliminar
                          </Button>
                        </div>
                      ))}
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={form.models.length === 0}
                        onClick={() => setForm({ ...form, profiles: [...form.profiles, { name: "", model: "" }] })}
                      >
                        + Agregar perfil
                      </Button>
                      {form.models.length === 0 ? (
                        <p className="text-[11px] text-faint">Marca modelos en la política para poder definir perfiles.</p>
                      ) : null}
                    </div>
                  </div>
                ) : null}

                {section === "access" ? (
                  <div className="space-y-4">
                    <div>
                      <p className="mb-1 text-xs font-medium">Secretos del vault permitidos</p>
                      <p className="mb-2 text-[11px] text-muted">
                        El acceso es por secreto individual, nunca al vault completo.
                      </p>
                      <div className="max-h-60 space-y-1 overflow-y-auto rounded-lg border border-border-subtle p-2">
                        {(vault.data?.credentials ?? []).length === 0 ? (
                          <p className="px-2 py-1.5 text-xs text-muted">No hay secretos en el vault.</p>
                        ) : (
                          vault.data!.credentials.map((secret) => {
                            const checked = form.vaultSecretIds.includes(secret.id);
                            return (
                              <label key={secret.id} className="flex items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-surface">
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  onChange={() =>
                                    setForm({
                                      ...form,
                                      vaultSecretIds: checked
                                        ? form.vaultSecretIds.filter((id) => id !== secret.id)
                                        : [...form.vaultSecretIds, secret.id],
                                    })
                                  }
                                />
                                <span className="font-medium">{secret.name}</span>
                                <span className="text-faint">{secret.credential_type}</span>
                              </label>
                            );
                          })
                        )}
                      </div>
                    </div>
                    <div>
                      <p className="mb-2 text-xs font-medium">Agentes incluidos en esta política</p>
                      <div className="max-h-72 space-y-1 overflow-y-auto rounded-lg border border-border-subtle p-2">
                        {(agents.data?.agents ?? []).map((agent) => {
                          const checked = form.agents.includes(agent.id);
                          return (
                            <label key={agent.id} className="flex items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-surface">
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() =>
                                  setForm({ ...form, agents: checked ? form.agents.filter((a) => a !== agent.id) : [...form.agents, agent.id] })
                                }
                              />
                              <span className="font-medium">{agent.name}</span>
                              <span className="truncate text-faint">{agent.description}</span>
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
            <div className="flex shrink-0 items-center justify-between gap-3 border-t border-border-subtle bg-background px-6 py-3">
              <span className="text-[11px] text-muted">Los cambios se aplican al guardar.</span>
              <div className="flex gap-2">
                <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
                  Salir
                </Button>
                <Button size="sm" variant="primary" onClick={() => void save()} disabled={saving}>
                  {saving ? "Guardando…" : "Guardar política"}
                </Button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
