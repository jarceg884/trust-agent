"use client";

/**
 * Agents — marketplace de perfiles de Hermes (spec 045).
 *
 * El admin importa perfiles exportados con `hermes profile export`. La consola muestra lo que
 * el saneamiento conservó (persona, skills, MCP, tareas programadas) y lo que quitó
 * (credenciales, memorias, sesiones…). Los perfiles se asignan en Policies → Agentes con su
 * modelo y un perfil por defecto; en el escritorio, el agente del empleado ES ese perfil.
 * Reimportar crea una versión nueva; nada se borra: los perfiles se archivan (D-045-03).
 */
import Link from "next/link";
import { useMemo, useState } from "react";
import { useApi } from "@/hooks/use-api";
import { api, apiUploadFile, ApiError, downloadBlob } from "@/lib/api-client";
import type {
  MarketplaceImportResult,
  MarketplaceList,
  MarketplaceProfile,
  MarketplaceProfileDetail,
} from "@/lib/types";
import { dateTime } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardBody,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Tabs,
  TableSkeleton,
  Textarea,
  useToast,
} from "@/components/ui";
import {
  IconAgents,
  IconArchive,
  IconDownload,
  IconPackage,
  IconRestore,
  IconSearch,
  IconStar,
  IconUpload,
} from "@/components/icons";
import { PROFILE_SLUG_RE, ProfileChips, formatBytes, strippedLabel } from "./profile-chips";

type Filter = "all" | "factory" | "imported" | "archived";

const BASE = "/api/marketplace/profiles";

function SourceBadge({ profile }: { profile: MarketplaceProfile }) {
  return profile.source === "factory" ? (
    <Badge tone="accent">De fábrica</Badge>
  ) : (
    <Badge tone="info">Importado</Badge>
  );
}

function PolicyUsage({ profile }: { profile: MarketplaceProfile }) {
  const n = profile.policies.length;
  if (n === 0) return <span className="text-faint">Sin asignar a ninguna política</span>;
  const defaults = profile.policies.filter((p) => p.default).length;
  return (
    <span className="text-muted">
      En {n} {n === 1 ? "política" : "políticas"}
      {defaults > 0 ? (
        <>
          {" · "}
          <IconStar size={11} className="inline -mt-0.5 text-accent" /> por defecto en {defaults}
        </>
      ) : null}
    </span>
  );
}

export function MarketplacePage({ canManage }: { canManage: boolean }) {
  const list = useApi<MarketplaceList>(`${BASE}?include_archived=true`);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // null = diálogo cerrado; "" = perfil nuevo; "<slug>" = nueva versión de ese perfil.
  const [importSlug, setImportSlug] = useState<string | null>(null);

  const profiles = useMemo(() => list.data?.profiles ?? [], [list.data]);
  const counts = useMemo(() => {
    const active = profiles.filter((p) => p.status === "active");
    return {
      all: active.length,
      factory: active.filter((p) => p.source === "factory").length,
      imported: active.filter((p) => p.source === "imported").length,
      archived: profiles.length - active.length,
    };
  }, [profiles]);
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return profiles
      .filter((p) =>
        filter === "archived"
          ? p.status === "archived"
          : p.status === "active" && (filter === "all" || p.source === filter),
      )
      .filter(
        (p) =>
          !q ||
          `${p.name} ${p.slug} ${p.description} ${p.latest.summary.skills.map((s) => s.name).join(" ")}`
            .toLowerCase()
            .includes(q),
      );
  }, [profiles, filter, query]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs
          tabs={[
            { id: "all", label: "Todos", count: counts.all },
            { id: "factory", label: "De fábrica", count: counts.factory },
            { id: "imported", label: "Importados", count: counts.imported },
            { id: "archived", label: "Archivados", count: counts.archived },
          ]}
          active={filter}
          onChange={(id) => setFilter(id as Filter)}
        />
        <div className="flex items-center gap-2">
          <div className="relative">
            <IconSearch
              size={14}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"
            />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Buscar perfil o skill…"
              aria-label="Buscar perfiles"
              className="w-60 pl-8"
            />
          </div>
          {canManage ? (
            <Button variant="primary" size="sm" onClick={() => setImportSlug("")}>
              <IconUpload size={13} /> Importar perfil
            </Button>
          ) : null}
        </div>
      </div>

      {list.loading ? (
        <TableSkeleton rows={3} cols={3} />
      ) : list.error ? (
        <ErrorState error={list.error} onRetry={list.refetch} context={`GET ${BASE}`} />
      ) : visible.length === 0 ? (
        <EmptyState
          icon={<IconAgents size={18} />}
          title={query ? "Ningún perfil coincide con la búsqueda" : filter === "archived" ? "No hay perfiles archivados" : "No hay perfiles"}
          description={
            canManage && filter !== "archived"
              ? "Exporta un perfil en Hermes con `hermes profile export <nombre>` e impórtalo aquí."
              : undefined
          }
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {visible.map((p, i) => (
            <Card key={p.id} className="ta-rise flex flex-col" style={{ animationDelay: `${i * 50}ms` }}>
              <CardBody className="flex flex-1 flex-col gap-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-foreground">{p.name}</p>
                    <p className="font-mono text-[11px] text-faint">
                      {p.slug} · v{p.latest.version}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    <SourceBadge profile={p} />
                    {p.status === "archived" ? <Badge tone="warning">Archivado</Badge> : null}
                  </div>
                </div>
                {p.description ? (
                  <p className="line-clamp-3 text-xs leading-relaxed text-muted">{p.description}</p>
                ) : p.latest.summary.soul_excerpt ? (
                  <p className="line-clamp-3 text-xs italic leading-relaxed text-muted">
                    {p.latest.summary.soul_excerpt}
                  </p>
                ) : null}
                <ProfileChips summary={p.latest.summary} />
                <div className="mt-auto flex items-center justify-between gap-2 pt-2 text-[11px]">
                  <PolicyUsage profile={p} />
                  <Button size="sm" variant="secondary" onClick={() => setSelectedId(p.id)}>
                    Ver ficha
                  </Button>
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}
      <p className="text-xs text-faint">
        Asigna los perfiles en{" "}
        <Link className="underline" href="/policies">
          Policies → Agentes
        </Link>
        , con su modelo y un perfil por defecto. En el escritorio, el agente del empleado es ese perfil de
        Hermes; sus memorias y sesiones se conservan al actualizar o archivar.
      </p>

      <ProfileDetailDialog
        profileId={selectedId}
        canManage={canManage}
        onClose={() => setSelectedId(null)}
        onChanged={list.refetch}
        onImportVersion={(slug) => {
          setSelectedId(null);
          setImportSlug(slug);
        }}
      />
      <ImportDialog
        slug={importSlug}
        onClose={() => setImportSlug(null)}
        onImported={(result) => {
          list.refetch();
          if (!result.unchanged || result.created) setFilter(result.profile.source === "factory" ? "factory" : "all");
        }}
        onOpenProfile={(id) => {
          setImportSlug(null);
          setSelectedId(id);
        }}
      />
    </div>
  );
}

// --- Ficha ----------------------------------------------------------------------------------
type DetailTab = "summary" | "skills" | "runtime" | "sanitize" | "versions" | "policies";

function ProfileDetailDialog({
  profileId,
  canManage,
  onClose,
  onChanged,
  onImportVersion,
}: {
  profileId: string | null;
  canManage: boolean;
  onClose: () => void;
  onChanged: () => void;
  onImportVersion: (slug: string) => void;
}) {
  const { push } = useToast();
  const detail = useApi<MarketplaceProfileDetail>(profileId ? `${BASE}/${profileId}` : null);
  const [tab, setTab] = useState<DetailTab>("summary");
  const [editing, setEditing] = useState<{ name: string; description: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [archiveBlockers, setArchiveBlockers] = useState<{ name: string; default: boolean }[] | null>(null);

  const p = detail.data;
  const s = p?.latest.summary;

  function close() {
    setTab("summary");
    setEditing(null);
    setArchiveBlockers(null);
    onClose();
  }

  async function saveEdit() {
    if (!p || !editing) return;
    if (!editing.name.trim()) {
      push("error", "El nombre es obligatorio.");
      return;
    }
    setBusy(true);
    try {
      await api(`${BASE}/${p.id}`, { method: "PUT", body: editing });
      push("success", "Perfil actualizado.");
      setEditing(null);
      detail.refetch();
      onChanged();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudo guardar.");
    } finally {
      setBusy(false);
    }
  }

  async function archive(confirm: boolean) {
    if (!p) return;
    setBusy(true);
    try {
      await api(`${BASE}/${p.id}/archive`, { method: "POST", body: { confirm } });
      push("success", "Perfil archivado. Los equipos lo archivarán en su próxima sincronización.");
      setArchiveBlockers(null);
      detail.refetch();
      onChanged();
    } catch (cause) {
      const rule = cause instanceof ApiError ? cause.payload?.rule : null;
      if (rule === "in_use") {
        const policies = (cause as ApiError).payload?.policies as { name: string; default: boolean }[] | undefined;
        setArchiveBlockers(policies ?? []);
      } else {
        push("error", cause instanceof ApiError ? cause.message : "No se pudo archivar.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function restore() {
    if (!p) return;
    setBusy(true);
    try {
      await api(`${BASE}/${p.id}/restore`, { method: "POST" });
      push("success", "Perfil restaurado.");
      detail.refetch();
      onChanged();
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudo restaurar.");
    } finally {
      setBusy(false);
    }
  }

  async function download(version: number) {
    if (!p) return;
    try {
      await downloadBlob(`${BASE}/${p.id}/package?version=${version}`, `${p.slug}-v${version}.tar.gz`);
    } catch (cause) {
      push("error", cause instanceof ApiError ? cause.message : "No se pudo descargar.");
    }
  }

  const strippedEntries = Object.entries(s?.stripped ?? {});

  return (
    <Dialog
      open={profileId !== null}
      onClose={close}
      title={p ? p.name : "Perfil"}
      description={p ? `${p.slug} · versión ${p.latest.version} · ${formatBytes(p.latest.size)}` : undefined}
      width="max-w-3xl"
      footer={
        p && canManage ? (
          <div className="flex w-full flex-wrap items-center justify-between gap-2">
            <div className="flex gap-2">
              {p.status === "active" ? (
                <Button size="sm" variant="ghost" loading={busy} onClick={() => void archive(false)}>
                  <IconArchive size={13} /> Archivar
                </Button>
              ) : (
                <Button size="sm" variant="ghost" loading={busy} onClick={() => void restore()}>
                  <IconRestore size={13} /> Restaurar
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => void download(p.latest.version)}>
                <IconDownload size={13} /> Descargar paquete
              </Button>
            </div>
            <div className="flex gap-2">
              {p.source === "imported" ? (
                <Button size="sm" variant="secondary" onClick={() => onImportVersion(p.slug)}>
                  <IconUpload size={13} /> Importar nueva versión
                </Button>
              ) : null}
              <Button
                size="sm"
                variant="primary"
                onClick={() => setEditing({ name: p.name, description: p.description ?? "" })}
              >
                Editar
              </Button>
            </div>
          </div>
        ) : undefined
      }
    >
      {detail.loading && !p ? (
        <TableSkeleton rows={4} cols={2} />
      ) : detail.error ? (
        <ErrorState error={detail.error} onRetry={detail.refetch} context={`GET ${BASE}/{id}`} />
      ) : p && s ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <SourceBadge profile={p} />
            {p.status === "archived" ? <Badge tone="warning">Archivado</Badge> : <Badge tone="success">Activo</Badge>}
            {s.format === "hermes-distribution" ? <Badge tone="neutral">Distribución de Hermes</Badge> : null}
            <span className="font-mono text-[11px] text-faint" title={p.latest.sha256}>
              sha256 {p.latest.sha256.slice(0, 12)}…
            </span>
          </div>

          {editing ? (
            <div className="space-y-3 rounded-lg border border-border-subtle p-3">
              <Field label="Nombre visible" htmlFor="mk-edit-name">
                <Input
                  id="mk-edit-name"
                  value={editing.name}
                  maxLength={120}
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                />
              </Field>
              <Field label="Descripción" htmlFor="mk-edit-desc" hint="Se muestra en el marketplace y al asignar el perfil.">
                <Textarea
                  id="mk-edit-desc"
                  rows={3}
                  maxLength={1000}
                  value={editing.description}
                  onChange={(e) => setEditing({ ...editing, description: e.target.value })}
                />
              </Field>
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                  Cancelar
                </Button>
                <Button size="sm" variant="primary" loading={busy} onClick={() => void saveEdit()}>
                  Guardar
                </Button>
              </div>
            </div>
          ) : null}

          {archiveBlockers ? (
            <div className="space-y-2 rounded-lg border border-warning/30 bg-warning-soft p-3 text-xs text-warning">
              <p>
                <span className="font-semibold">Este perfil está asignado.</span> Al archivarlo, los equipos de{" "}
                {archiveBlockers.length === 1 ? "esta política" : "estas políticas"} lo archivarán en su próxima
                sincronización (sus memorias y sesiones se conservan):
              </p>
              <ul className="list-disc pl-5">
                {archiveBlockers.map((b) => (
                  <li key={b.name}>
                    {b.name}
                    {b.default ? " — es su perfil por defecto: elige otro en la política" : ""}
                  </li>
                ))}
              </ul>
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="ghost" onClick={() => setArchiveBlockers(null)}>
                  Cancelar
                </Button>
                <Button size="sm" variant="danger" loading={busy} onClick={() => void archive(true)}>
                  Archivar de todos modos
                </Button>
              </div>
            </div>
          ) : null}

          <Tabs
            tabs={[
              { id: "summary", label: "Resumen" },
              { id: "skills", label: "Skills", count: s.skills.length },
              { id: "runtime", label: "MCP y tareas", count: s.mcp_servers.length + s.cron_jobs.length },
              { id: "sanitize", label: "Saneamiento" },
              { id: "versions", label: "Versiones", count: p.versions.length },
              { id: "policies", label: "Políticas", count: p.policies.length },
            ]}
            active={tab}
            onChange={(id) => setTab(id as DetailTab)}
          />

          {tab === "summary" ? (
            <div className="space-y-3 text-xs">
              {p.description ? <p className="leading-relaxed text-muted">{p.description}</p> : null}
              <div>
                <p className="mb-1 font-medium">Persona (SOUL.md)</p>
                {s.has_soul ? (
                  <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg bg-surface-alt p-3 font-mono text-[11px] leading-relaxed text-muted">
                    {s.soul_excerpt}
                    {s.soul_excerpt.length >= 800 ? "\n…" : ""}
                  </pre>
                ) : (
                  <p className="text-faint">El perfil no trae SOUL.md: usará la persona por defecto de Hermes.</p>
                )}
              </div>
              <ProfileChips summary={s} />
              {s.env_requires.length > 0 ? (
                <p className="text-faint">
                  <span className="text-muted">Variables que el perfil espera:</span> {s.env_requires.join(", ")}. Los
                  valores nunca viajan en el paquete.
                </p>
              ) : null}
              {s.distribution ? (
                <p className="text-faint">
                  <span className="text-muted">Distribución:</span> {s.distribution.name} {s.distribution.version}
                  {s.distribution.author ? ` · ${s.distribution.author}` : ""}
                  {s.distribution.license ? ` · ${s.distribution.license}` : ""}
                </p>
              ) : null}
              <p className="text-faint">
                Versión {p.latest.version} importada {dateTime(p.latest.imported_at)}
                {p.latest.imported_by ? ` por ${p.latest.imported_by}` : p.source === "factory" ? " (catálogo de fábrica)" : ""}.
              </p>
            </div>
          ) : null}

          {tab === "skills" ? (
            s.skills.length === 0 ? (
              <p className="text-xs text-faint">El perfil no trae skills propias.</p>
            ) : (
              <ul className="divide-y divide-border-subtle rounded-lg border border-border-subtle">
                {s.skills.map((skill) => (
                  <li key={skill.path} className="flex items-start justify-between gap-3 px-3 py-2 text-xs">
                    <div className="min-w-0">
                      <p className="font-medium">{skill.name}</p>
                      <p className="font-mono text-[10px] text-faint">{skill.path}</p>
                      {skill.description ? <p className="mt-0.5 text-muted">{skill.description}</p> : null}
                    </div>
                    {skill.has_scripts ? <Badge tone="warning">scripts</Badge> : null}
                  </li>
                ))}
              </ul>
            )
          ) : null}

          {tab === "runtime" ? (
            <div className="space-y-3 text-xs">
              <div>
                <p className="mb-1 font-medium">Servidores MCP</p>
                {s.mcp_servers.length === 0 ? (
                  <p className="text-faint">Ninguno.</p>
                ) : (
                  <>
                    <div className="flex flex-wrap gap-1">
                      {s.mcp_servers.map((m) => (
                        <span key={m} className="rounded-full bg-surface-alt px-2 py-0.5 font-mono text-[10px] text-muted">
                          {m}
                        </span>
                      ))}
                    </div>
                    <p className="mt-1 text-faint">
                      Se conservan tal cual. La política decide en el equipo cuáles se permiten; los no permitidos no
                      arrancan.
                    </p>
                  </>
                )}
              </div>
              <div>
                <p className="mb-1 font-medium">Tareas programadas</p>
                {s.cron_jobs.length === 0 ? (
                  <p className="text-faint">Ninguna.</p>
                ) : (
                  <>
                    <ul className="space-y-0.5">
                      {s.cron_jobs.map((job) => (
                        <li key={`${job.name}-${job.schedule}`}>
                          {job.name || "(sin nombre)"} <span className="font-mono text-faint">{job.schedule}</span>
                        </li>
                      ))}
                    </ul>
                    <p className="mt-1 text-faint">Solo corren si la política permite tareas programadas.</p>
                  </>
                )}
              </div>
            </div>
          ) : null}

          {tab === "sanitize" ? (
            <div className="space-y-3 text-xs">
              <p className="text-muted">
                El archivo subido nunca se guarda: se conserva solo lo que define al agente y se re-empaqueta.
              </p>
              <div>
                <p className="mb-1 font-medium">Quitado del export</p>
                {strippedEntries.length === 0 ? (
                  <p className="text-faint">Nada: el export solo traía contenido del perfil.</p>
                ) : (
                  <ul className="space-y-0.5">
                    {strippedEntries.map(([key, n]) => (
                      <li key={key}>
                        {strippedLabel(key)} <span className="text-faint">({n})</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <p>
                <span className="font-medium">Secretos redactados:</span>{" "}
                {s.redactions > 0 ? s.redactions : <span className="text-faint">ninguno</span>}
              </p>
              {s.config_removed_keys.length > 0 ? (
                <p>
                  <span className="font-medium">Ajustes que fija la organización</span> (se ignoran del perfil):{" "}
                  <span className="font-mono">{s.config_removed_keys.join(", ")}</span>
                </p>
              ) : null}
              <details>
                <summary className="cursor-pointer text-muted">
                  Archivos del paquete ({s.file_count}, {formatBytes(s.total_size)})
                </summary>
                <ul className="mt-2 max-h-56 overflow-y-auto font-mono text-[10px] text-faint">
                  {(p.latest.manifest?.files ?? []).map((f) => (
                    <li key={f.path}>
                      {f.path} <span className="text-muted">· {formatBytes(f.size)}</span>
                    </li>
                  ))}
                </ul>
              </details>
            </div>
          ) : null}

          {tab === "versions" ? (
            <ul className="divide-y divide-border-subtle rounded-lg border border-border-subtle text-xs">
              {p.versions.map((v) => (
                <li key={v.version} className="flex items-center justify-between gap-3 px-3 py-2">
                  <div className="min-w-0">
                    <p className="font-medium">
                      v{v.version}
                      {v.version === p.latest.version ? (
                        <Badge tone="success" className="ml-2">
                          vigente
                        </Badge>
                      ) : null}
                    </p>
                    <p className="text-faint">
                      {dateTime(v.imported_at)}
                      {v.imported_by ? ` · ${v.imported_by}` : ""} · {formatBytes(v.size)} ·{" "}
                      <span className="font-mono" title={v.sha256}>
                        {v.sha256.slice(0, 12)}…
                      </span>
                    </p>
                  </div>
                  {canManage ? (
                    <Button size="sm" variant="ghost" onClick={() => void download(v.version)}>
                      <IconPackage size={13} /> .tar.gz
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}

          {tab === "policies" ? (
            <div className="space-y-2 text-xs">
              {p.policies.length === 0 ? (
                <p className="text-faint">Ninguna política entrega este perfil todavía.</p>
              ) : (
                <ul className="space-y-1">
                  {p.policies.map((pol) => (
                    <li key={pol.id} className="flex items-center gap-2">
                      <span className="font-medium">{pol.name}</span>
                      {pol.default ? (
                        <Badge tone="accent">
                          <IconStar size={10} /> por defecto
                        </Badge>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
              <p className="text-faint">
                Asigna o quita el perfil en{" "}
                <Link className="underline" href="/policies">
                  Policies → Agentes
                </Link>
                .
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </Dialog>
  );
}

// --- Importar ---------------------------------------------------------------------------------
const NAME_RULES = new Set(["default_profile", "invalid_name", "factory_slug"]);

function ImportDialog({
  slug,
  onClose,
  onImported,
  onOpenProfile,
}: {
  slug: string | null;
  onClose: () => void;
  onImported: (result: MarketplaceImportResult) => void;
  onOpenProfile: (id: string) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [needsName, setNeedsName] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<MarketplaceImportResult | null>(null);
  const isVersion = Boolean(slug);
  const effectiveName = isVersion ? (slug as string) : name.trim();
  const nameInvalid = effectiveName !== "" && !PROFILE_SLUG_RE.test(effectiveName);

  function reset() {
    setFile(null);
    setName("");
    setNeedsName(false);
    setError(null);
    setResult(null);
  }

  function close() {
    reset();
    onClose();
  }

  async function submit() {
    if (!file) {
      setError("Elige el .tar.gz exportado.");
      return;
    }
    if (nameInvalid) {
      setError("El nombre solo admite minúsculas, números, - y _ (hasta 64 caracteres).");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const query = effectiveName ? `?slug=${encodeURIComponent(effectiveName)}` : "";
      const res = await apiUploadFile<MarketplaceImportResult>(`${BASE}/import${query}`, file);
      setResult(res);
      onImported(res);
    } catch (cause) {
      if (cause instanceof ApiError) {
        setError(cause.message);
        if (NAME_RULES.has(String(cause.payload?.rule ?? ""))) setNeedsName(true);
      } else {
        setError("No se pudo importar el perfil.");
      }
    } finally {
      setBusy(false);
    }
  }

  const outcome = result
    ? result.created
      ? "Perfil importado"
      : result.unchanged
        ? "Sin cambios: el contenido es idéntico a la versión vigente"
        : `Nueva versión v${result.profile.latest.version}: los equipos la recibirán en su próxima sincronización`
    : null;
  const s = result?.profile.latest.summary;

  return (
    <Dialog
      open={slug !== null}
      onClose={close}
      title={isVersion ? `Nueva versión de ${slug}` : "Importar perfil de Hermes"}
      description={
        isVersion
          ? "Sube el export actualizado. Se guarda como versión nueva; las anteriores se conservan."
          : "Sube el archivo que genera `hermes profile export <nombre>`."
      }
      width="max-w-xl"
      footer={
        result ? (
          <div className="flex w-full justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={close}>
              Cerrar
            </Button>
            <Button size="sm" variant="primary" onClick={() => { const id = result.profile.id; reset(); onOpenProfile(id); }}>
              Ver ficha
            </Button>
          </div>
        ) : (
          <div className="flex w-full justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={close}>
              Cancelar
            </Button>
            <Button size="sm" variant="primary" loading={busy} disabled={!file} onClick={() => void submit()}>
              <IconUpload size={13} /> Importar
            </Button>
          </div>
        )
      }
    >
      {result && s ? (
        <div className="space-y-3 text-xs">
          <div className="rounded-lg border border-success/30 bg-success-soft p-3 text-success">
            <p className="font-semibold">{outcome}</p>
            <p className="mt-0.5">
              {result.profile.name} · <span className="font-mono">{result.profile.slug}</span> · v
              {result.profile.latest.version}
            </p>
          </div>
          <ProfileChips summary={s} />
          {Object.keys(s.stripped).length > 0 ? (
            <p className="text-muted">
              <span className="font-medium">Quitado del export:</span>{" "}
              {Object.entries(s.stripped)
                .map(([k, n]) => `${strippedLabel(k)} (${n})`)
                .join(", ")}
              .
            </p>
          ) : null}
          {s.redactions > 0 ? (
            <p className="text-muted">
              <span className="font-medium">Secretos redactados:</span> {s.redactions}.
            </p>
          ) : null}
          {s.config_removed_keys.length > 0 ? (
            <p className="text-muted">
              <span className="font-medium">Ajustes que fija la organización:</span>{" "}
              <span className="font-mono">{s.config_removed_keys.join(", ")}</span>.
            </p>
          ) : null}
        </div>
      ) : (
        <div className="space-y-3">
          <Field
            label="Archivo exportado (.tar.gz)"
            htmlFor="mk-import-file"
            hint="Solo se conserva lo que define al agente: SOUL.md, config, skills, MCP y tareas programadas. Credenciales, memorias y sesiones se descartan."
          >
            <input
              id="mk-import-file"
              type="file"
              accept=".tar.gz,.tgz,application/gzip,application/x-gzip"
              className="block w-full text-xs text-muted file:mr-3 file:rounded-md file:border-0 file:bg-surface-alt file:px-3 file:py-1.5 file:text-xs file:text-foreground"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setError(null);
              }}
            />
          </Field>
          {!isVersion ? (
            <Field
              label={needsName ? "Nombre del perfil (obligatorio)" : "Nombre del perfil (opcional)"}
              htmlFor="mk-import-name"
              hint="Minúsculas, números, - y _. Vacío = el nombre con el que se exportó."
              error={nameInvalid ? "Nombre de perfil no válido." : null}
            >
              <Input
                id="mk-import-name"
                value={name}
                placeholder="p. ej. ventas"
                onChange={(e) => setName(e.target.value.toLowerCase())}
              />
            </Field>
          ) : null}
          {error ? (
            <div className="rounded-lg border border-danger/30 bg-danger-soft p-2.5 text-xs text-danger">{error}</div>
          ) : null}
        </div>
      )}
    </Dialog>
  );
}
