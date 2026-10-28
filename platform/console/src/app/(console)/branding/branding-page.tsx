"use client";

/**
 * Branding (spec 001 REQ-BRD-001…005 + spec 028 REQ-BRD-004): white-label
 * con flujo draft/publish versionado.
 *
 * Base: fix de la lane t_6d3f5016 (@ui) — inputs controlados, PUT real con
 * solo claves cambiadas, toasts honestos y validación espejo de
 * app/org/branding.py (constitution I: un toast de éxito sin request es un
 * éxito falso). Esta lane (spec 028, t_d2b65bcf) extiende sobre esa base:
 *
 * - PUT /api/branding edita el DRAFT del servidor (D-028-04): los cambios
 *   no son visibles para usuarios hasta publicar (REQ-BRD-004).
 * - POST /api/branding/assets sube logo light/dark + favicon (PNG/SVG,
 *   ≤ 2 MB) con preview; POST /api/branding/publish publica atómico como
 *   versión N+1 (audit branding.published); POST /api/branding/rollback
 *   restaura una versión anterior re-publicándola.
 */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useApi, useMutation } from "@/hooks/use-api";
import { notifyBrandingUpdated } from "@/lib/branding-events";
import { api, apiUpload, ApiError, proxyUrl } from "@/lib/api-client";
import type {
  BrandingDraft,
  BrandingPublishResponse,
  BrandingUploadResponse,
  BrandingVersions,
  TenantBranding,
} from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  ErrorState,
  Field,
  Input,
  Skeleton,
  useToast,
} from "@/components/ui";

/** Default del proveedor (REQ-BRD-005) — constante de cliente, no dato del
 * API. Debe espejar PROVIDER_DEFAULT de platform/app/org/branding.py
 * (spec 004): #4F46E5 indigo (corregido en t_3cf085b9 — decía #c90104,
 * que nunca fue el default real del API). */
const PROVIDER_DEFAULT: TenantBranding = {
  product_name: "Trust Agent",
  tagline: "AI governance for your organization",
  accent_color: "#4F46E5",
  source: "provider",
};

/** Claves canónicas editables (spec 012 D-ORG-04; los logos viven en assets). */
type BrandingField = "product_name" | "tagline" | "accent_color";
const FIELDS: BrandingField[] = ["product_name", "tagline", "accent_color"];

/** Espejo cliente de las validaciones de app/org/branding.py (_validate). */
function validateField(field: BrandingField, value: string): string | null {
  if (field === "product_name") {
    const name = value.trim();
    if (name.length < 1 || name.length > 80)
      return "Product name must be 1-80 characters.";
    return null;
  }
  if (field === "tagline") {
    if (value.length > 140) return "Tagline must be at most 140 characters.";
    return null;
  }
  if (!/^#[0-9a-fA-F]{6}$/.test(value.trim()))
    return "Accent color must be a #RRGGBB hex color.";
  return null;
}

const MAX_BYTES = 2 * 1024 * 1024;

type AssetKind = "logo_light" | "logo_dark" | "favicon";

const ASSET_LABELS: Record<AssetKind, string> = {
  logo_light: "Logo (light)",
  logo_dark: "Logo (dark)",
  favicon: "Favicon",
};

function formatBytes(n: number): string {
  return n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`;
}

export function BrandingPage() {
  const published = useApi<TenantBranding>("/api/branding");
  const draftApi = useApi<BrandingDraft>("/api/branding/draft");
  const versions = useApi<BrandingVersions>("/api/branding/versions");
  const router = useRouter();
  const { push } = useToast();
  const saveDraft = useMutation();
  const publish = useMutation();
  const rollback = useMutation();

  // Borrador de edición LOCAL: SOLO las claves tocadas (patrón t_6d3f5016).
  // La base del diff es el DRAFT del servidor (GET /api/branding/draft).
  const [local, setLocal] = useState<Partial<Record<BrandingField, string>>>({});
  const [uploading, setUploading] = useState<AssetKind | null>(null);
  const [rollbackTarget, setRollbackTarget] = useState<number | null>(null);

  const serverDraft = draftApi.data;
  const current: TenantBranding = published.data ?? PROVIDER_DEFAULT;

  /** Valor vigente del DRAFT del servidor para una clave (base del diff). */
  function serverValue(field: BrandingField): string {
    if (serverDraft) {
      const value = serverDraft[field];
      if (value !== null && value !== undefined) return value;
    }
    // Clave sin definir en el draft: hereda lo publicado (effective).
    if (field === "product_name") return current.product_name;
    return current[field] ?? "";
  }

  /** Valor mostrado en cada input: borrador local si fue editado, si no el del servidor. */
  function valueOf(field: BrandingField): string {
    return local[field] !== undefined ? local[field] : serverValue(field);
  }

  /** Payload del PUT: solo las claves cuyo valor difiere del draft del servidor. */
  function changedPayload(): Partial<Record<BrandingField, string>> {
    const payload: Partial<Record<BrandingField, string>> = {};
    for (const field of FIELDS) {
      if (local[field] === undefined) continue;
      if (local[field] === serverValue(field)) continue;
      payload[field] = local[field];
    }
    return payload;
  }

  const payload = changedPayload();
  const dirty = Object.keys(payload).length > 0;
  // Hay cambios publicables si el borrador local está guardado Y el draft
  // del servidor difiere de lo publicado (has_changes, REQ-BRD-004).
  const publishable = (serverDraft?.has_changes ?? false) && !dirty;

  const previewName = valueOf("product_name") || "Trust Agent";
  const previewTagline = valueOf("tagline");
  const accent = valueOf("accent_color") || PROVIDER_DEFAULT.accent_color || "#4F46E5";

  function edit(field: BrandingField, value: string) {
    setLocal((d) => ({ ...d, [field]: value }));
  }

  function refetchAll() {
    published.refetch();
    draftApi.refetch();
    versions.refetch();
  }

  /** Refresh del shell SIN reload (spec 028 D-028-09, REQ-BRD2-008):
   *  el evento re-fetcha el TenantBrand del sidebar (nombre, logo,
   *  favicon, <title>) y router.refresh() re-renderiza el RSC (acento
   *  server-side del layout). Solo tras un publish/rollback real. */
  function refreshShell() {
    notifyBrandingUpdated();
    router.refresh();
  }

  async function save() {
    if (!dirty || saveDraft.submitting) return;
    // Validación cliente (espejo de branding.py) — error honesto sin request.
    for (const field of FIELDS) {
      if (payload[field] === undefined) continue;
      const problem = validateField(field, payload[field]);
      if (problem) {
        push("error", problem);
        return;
      }
    }
    try {
      await api("/api/branding", { method: "PUT", body: payload });
      // Éxito SOLO tras el 200 real del control plane (constitution I).
      push("success", "Draft saved — publish to make it visible to users.");
      setLocal({});
      draftApi.refetch();
      versions.refetch();
    } catch (cause) {
      if (cause instanceof ApiError) {
        push("error", cause.message);
      } else {
        push("error", "Save failed.");
      }
    }
  }

  async function doPublish() {
    try {
      const result = (await publish.mutate("/api/branding/publish")) as
        | BrandingPublishResponse
        | undefined;
      push(
        "success",
        `Published as version ${result?.version ?? "?"} — visible to all users.`,
      );
      refetchAll();
      refreshShell();
    } catch (cause) {
      if (cause instanceof ApiError) push("error", cause.message);
      else push("error", "Publish failed.");
    }
  }

  async function upload(kind: AssetKind, file: File) {
    const isSvg = file.type === "image/svg+xml" || file.name.endsWith(".svg");
    const isPng = file.type === "image/png" || file.name.endsWith(".png");
    if (!isPng && !isSvg) {
      push("error", "Only PNG and SVG files are allowed.");
      return;
    }
    if (file.size > MAX_BYTES) {
      push("error", "File exceeds the 2 MB limit.");
      return;
    }
    setUploading(kind);
    try {
      const form = new FormData();
      form.append("kind", kind);
      form.append("file", file);
      const result = (await apiUpload<BrandingUploadResponse>(
        "/api/branding/assets",
        form,
      )) as BrandingUploadResponse;
      push(
        "success",
        `${ASSET_LABELS[kind]} uploaded to the draft (${formatBytes(result.asset.size_bytes)}).`,
      );
      draftApi.refetch();
      versions.refetch();
    } catch (cause) {
      if (cause instanceof ApiError) push("error", cause.message);
      else push("error", "Upload failed.");
    } finally {
      setUploading(null);
    }
  }

  async function doRollback() {
    if (rollbackTarget === null) return;
    try {
      const result = (await rollback.mutate("/api/branding/rollback", {
        version: rollbackTarget,
      })) as BrandingPublishResponse | undefined;
      push(
        "success",
        `Rolled back to version ${rollbackTarget} (republished as version ${result?.version ?? "?"}).`,
      );
      setRollbackTarget(null);
      refetchAll();
      refreshShell();
    } catch (cause) {
      if (cause instanceof ApiError) push("error", cause.message);
      else push("error", "Rollback failed.");
    }
  }

  if (!draftApi.loading && draftApi.error && !serverDraft) {
    return (
      <ErrorState
        error={draftApi.error}
        onRetry={draftApi.refetch}
        context="GET /api/branding/draft"
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* Estado draft/published (REQ-BRD-004) */}
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={current.source === "tenant" ? "success" : "neutral"}>
          published: v{current.version ?? 0} · source: {current.source ?? "provider"}
        </Badge>
        <Badge tone={publishable ? "warning" : "neutral"}>
          {dirty
            ? "draft: unsaved edits"
            : publishable
              ? "draft: unpublished changes"
              : "draft: in sync"}
        </Badge>
      </div>

      <Card>
        <CardHeader
          title="White-label branding"
          description="Edit the draft, preview it, then publish. Unpublished changes are never visible to users (REQ-BRD-004)."
        />
        {draftApi.loading || !serverDraft ? (
          <CardBody className="space-y-4">
            <Skeleton className="h-10 w-2/3" />
            <Skeleton className="h-10 w-1/2" />
          </CardBody>
        ) : (
          <CardBody className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Product name" htmlFor="brand-name" hint="Shown in the sidebar and page titles of the console.">
                <Input
                  id="brand-name"
                  value={valueOf("product_name")}
                  disabled={saveDraft.submitting}
                  onChange={(e) => edit("product_name", e.target.value)}
                />
              </Field>
              <Field label="Tagline" htmlFor="brand-tagline" hint="Optional — leave empty to clear it.">
                <Input
                  id="brand-tagline"
                  value={valueOf("tagline")}
                  disabled={saveDraft.submitting}
                  onChange={(e) => edit("tagline", e.target.value)}
                />
              </Field>
              <Field label="Accent color" htmlFor="brand-accent" hint="Hex color for buttons, links and highlights.">
                <div className="flex gap-2">
                  <Input
                    id="brand-accent"
                    value={valueOf("accent_color")}
                    disabled={saveDraft.submitting}
                    className="font-mono"
                    onChange={(e) => edit("accent_color", e.target.value)}
                  />
                  <span
                    aria-hidden="true"
                    className="size-9.5 shrink-0 rounded-lg border border-border-subtle"
                    style={{ background: accent }}
                  />
                </div>
              </Field>
            </div>

            {/* Preview reactivo: refleja el borrador en vivo (REQ-BRD-004:
                preview ANTES de publicar), con el logo del draft si existe. */}
            <div>
              <p className="mb-2 text-xs font-semibold text-foreground">Preview</p>
              <div className="rounded-xl border border-border-subtle">
                <div className="flex items-center gap-2.5 border-b border-border-subtle px-5 py-3.5">
                  {serverDraft.logo_light || serverDraft.logo_dark ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={proxyUrl((serverDraft.logo_light ?? serverDraft.logo_dark)!.url)}
                      alt={`${previewName} logo preview`}
                      className="size-7 rounded-lg object-contain"
                    />
                  ) : (
                    <span
                      aria-hidden="true"
                      className="flex size-7 items-center justify-center rounded-lg text-[13px] font-bold text-white"
                      style={{ background: accent }}
                    >
                      {previewName.charAt(0).toUpperCase()}
                    </span>
                  )}
                  <div className="leading-tight">
                    <p className="text-sm font-semibold text-foreground">{previewName}</p>
                    <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-faint">
                      Admin Console
                    </p>
                  </div>
                </div>
                <div className="space-y-2 px-5 py-4">
                  <p className="text-sm text-foreground">{previewTagline}</p>
                  <span
                    className="inline-flex items-center rounded-lg px-3.5 py-2 text-sm font-semibold text-white"
                    style={{ background: accent }}
                  >
                    Primary button
                  </span>
                </div>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                loading={saveDraft.submitting}
                disabled={!dirty}
                title={dirty ? undefined : "Edit a field to enable saving"}
                onClick={save}
              >
                Save draft
              </Button>
              {dirty ? (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={saveDraft.submitting}
                  onClick={() => setLocal({})}
                >
                  Discard changes
                </Button>
              ) : null}
              <Button
                variant="primary"
                size="sm"
                disabled={!publishable}
                loading={publish.submitting}
                title={
                  publishable
                    ? undefined
                    : "Save the draft first; publishing makes it visible to all users"
                }
                onClick={doPublish}
              >
                Publish{serverDraft?.version ? ` (→ v${serverDraft.version + 1})` : ""}
              </Button>
            </div>
          </CardBody>
        )}
      </Card>

      {/* Uploads de assets (REQ-BRD-001: PNG/SVG ≤ 2 MB, light/dark + favicon) */}
      <Card>
        <CardHeader
          title="Logos & favicon"
          description="PNG or SVG, up to 2 MB. Uploads land in the draft — publish to make them live."
        />
        <CardBody>
          <div className="grid gap-4 sm:grid-cols-3">
            {(["logo_light", "logo_dark", "favicon"] as AssetKind[]).map(
              (kind) => {
                const asset = serverDraft?.[kind] ?? null;
                return (
                  <div
                    key={kind}
                    className="rounded-xl border border-border-subtle p-4"
                  >
                    <p className="text-xs font-semibold text-foreground">
                      {ASSET_LABELS[kind]}
                    </p>
                    <div className="mt-3 flex h-16 items-center justify-center rounded-lg bg-surface">
                      {asset ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={proxyUrl(asset.url)}
                          alt={`${ASSET_LABELS[kind]} preview`}
                          className="max-h-14 max-w-full object-contain"
                        />
                      ) : (
                        <span className="text-xs text-faint">Not set</span>
                      )}
                    </div>
                    <p className="mt-2 text-[11px] text-muted">
                      {asset
                        ? `${asset.mime} · ${formatBytes(asset.size_bytes)}`
                        : "PNG/SVG · max 2 MB"}
                    </p>
                    <label className="mt-3 block">
                      <span className="sr-only">Upload {ASSET_LABELS[kind]}</span>
                      <input
                        type="file"
                        accept="image/png,image/svg+xml"
                        className="block w-full text-xs text-muted file:mr-2 file:cursor-pointer file:rounded-lg file:border-0 file:bg-accent file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-white"
                        disabled={uploading !== null}
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          e.target.value = "";
                          if (file) void upload(kind, file);
                        }}
                      />
                    </label>
                    {uploading === kind ? (
                      <p className="mt-2 text-[11px] text-muted">Uploading…</p>
                    ) : null}
                  </div>
                );
              },
            )}
          </div>
        </CardBody>
      </Card>

      {/* Historial de versiones + rollback (spec 028 REQ-BRD2-004) */}
      <Card>
        <CardHeader
          title="Published versions"
          description="Append-only history. Rollback republishes an older version as a new one (audited)."
        />
        <CardBody>
          {versions.loading ? (
            <Skeleton className="h-20 w-full" />
          ) : versions.error ? (
            <ErrorState
              error={versions.error}
              onRetry={versions.refetch}
              context="GET /api/branding/versions"
            />
          ) : !versions.data?.versions.length ? (
            <p className="text-sm text-muted">
              Nothing published yet — the provider default is live.
            </p>
          ) : (
            <ul className="divide-y divide-border-subtle">
              {versions.data.versions.map((v) => (
                <li
                  key={v.version}
                  className="flex flex-wrap items-center justify-between gap-2 py-3"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-foreground">
                      v{v.version} · {v.summary.product_name ?? "—"}
                      {v.version === versions.data?.current_version ? (
                        <Badge tone="success" className="ml-2">live</Badge>
                      ) : null}
                    </p>
                    <p className="truncate text-xs text-muted">
                      {v.published_at
                        ? new Date(v.published_at).toLocaleString()
                        : ""}
                      {v.summary.accent_color ? ` · ${v.summary.accent_color}` : ""}
                      {v.summary.has_logo_light ? " · logo-light" : ""}
                      {v.summary.has_logo_dark ? " · logo-dark" : ""}
                      {v.summary.has_favicon ? " · favicon" : ""}
                    </p>
                  </div>
                  {v.version !== versions.data?.current_version ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setRollbackTarget(v.version)}
                    >
                      Rollback to v{v.version}
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <ConfirmDialog
        open={rollbackTarget !== null}
        onClose={() => setRollbackTarget(null)}
        onConfirm={doRollback}
        loading={rollback.submitting}
        title={`Rollback to version ${rollbackTarget ?? ""}`}
        message={`The branding of version ${rollbackTarget ?? ""} will be republished as a new version, replacing the current live branding (audited as branding.rolled_back).`}
        confirmLabel="Rollback"
        danger={false}
      />
    </div>
  );
}
