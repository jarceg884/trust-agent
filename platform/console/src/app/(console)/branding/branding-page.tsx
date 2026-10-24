"use client";

/**
 * Branding (spec 009 REQ-AC-020, D-AC-06/07): white-label contra el contrato
 * GET/PUT /api/branding (spec 001 REQ-BRD-001/005). El endpoint NO existe
 * hoy: preview deshabilitado con el default del proveedor y banner honesto
 * de dependencia. Cero datos simulados como guardados (constitution I).
 */
import { useApi } from "@/hooks/use-api";
import type { TenantBranding } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ErrorState,
  Field,
  Input,
  Skeleton,
  useToast,
} from "@/components/ui";

/** Default del proveedor (REQ-BRD-005) — constante de cliente, no dato del API. */
const PROVIDER_DEFAULT: TenantBranding = {
  product_name: "Trust Agent",
  tagline: "AI governance for your organization",
  accent_color: "#c90104",
  source: "provider",
};

export function BrandingPage() {
  const branding = useApi<TenantBranding>("/api/branding");
  const { push } = useToast();

  const pending =
    branding.error !== null &&
    (branding.error.status === 404 || branding.error.status === 405);

  const current: TenantBranding = branding.data ?? PROVIDER_DEFAULT;
  const accent = current.accent_color ?? PROVIDER_DEFAULT.accent_color ?? "#c90104";

  return (
    <div className="space-y-6">
      {pending ? (
        <div
          role="status"
          className="rounded-xl border border-warning/25 bg-warning-soft px-5 py-4"
        >
          <p className="text-sm font-semibold text-warning">
            Branding API not available yet — read-only preview
          </p>
          <p className="mt-1 text-xs leading-relaxed text-warning">
            The control plane does not expose{" "}
            <code className="font-mono">GET/PUT /api/branding</code> in this
            build (contract: platform spec REQ-BRD-001/005, tracked as a
            control-plane development task). The form below previews the
            provider defaults and cannot be saved. It activates automatically
            once the endpoint ships.
          </p>
        </div>
      ) : null}

      <Card>
        <CardHeader
          title="White-label branding"
          description="Product name, tagline and accent color applied to the Admin Console and the desktop app in runtime — no redeploy."
          actions={
            <Badge tone={current.source === "tenant" ? "success" : "neutral"}>
              source: {current.source ?? "provider"}
            </Badge>
          }
        />
        {branding.loading ? (
          <CardBody className="space-y-4">
            <Skeleton className="h-10 w-2/3" />
            <Skeleton className="h-10 w-1/2" />
          </CardBody>
        ) : !pending && branding.error ? (
          <ErrorState error={branding.error} onRetry={branding.refetch} context="GET /api/branding" />
        ) : (
          <CardBody className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Product name" htmlFor="brand-name" hint="Shown in the sidebar, login and page titles.">
                <Input id="brand-name" defaultValue={current.product_name} disabled={pending} />
              </Field>
              <Field label="Tagline" htmlFor="brand-tagline">
                <Input id="brand-tagline" defaultValue={current.tagline ?? ""} disabled={pending} />
              </Field>
              <Field label="Accent color" htmlFor="brand-accent" hint="Hex color for buttons, links and highlights.">
                <div className="flex gap-2">
                  <Input
                    id="brand-accent"
                    defaultValue={current.accent_color ?? PROVIDER_DEFAULT.accent_color!}
                    disabled={pending}
                    className="font-mono"
                  />
                  <span
                    aria-hidden="true"
                    className="size-9.5 shrink-0 rounded-lg border border-border-subtle"
                    style={{ background: accent }}
                  />
                </div>
              </Field>
            </div>

            {/* Preview */}
            <div>
              <p className="mb-2 text-xs font-semibold text-foreground">Preview</p>
              <div className="rounded-xl border border-border-subtle">
                <div className="flex items-center gap-2.5 border-b border-border-subtle px-5 py-3.5">
                  <span
                    aria-hidden="true"
                    className="flex size-7 items-center justify-center rounded-lg text-[13px] font-bold text-white"
                    style={{ background: accent }}
                  >
                    {current.product_name.charAt(0)}
                  </span>
                  <div className="leading-tight">
                    <p className="text-sm font-semibold text-foreground">{current.product_name}</p>
                    <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-faint">
                      Admin Console
                    </p>
                  </div>
                </div>
                <div className="space-y-2 px-5 py-4">
                  <p className="text-sm text-foreground">{current.tagline}</p>
                  <span
                    className="inline-flex items-center rounded-lg px-3.5 py-2 text-sm font-semibold text-white"
                    style={{ background: accent }}
                  >
                    Primary button
                  </span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <Button
                variant="primary"
                size="sm"
                disabled={pending}
                onClick={() =>
                  push(
                    pending ? "error" : "success",
                    pending
                      ? "Saving requires the branding API (pending)."
                      : "Branding saved.",
                  )
                }
              >
                Save branding
              </Button>
              {pending ? (
                <Button variant="secondary" size="sm" onClick={branding.refetch}>
                  Check again
                </Button>
              ) : null}
            </div>
          </CardBody>
        )}
      </Card>

      <p className="text-xs leading-relaxed text-muted">
        Logo uploads (light/dark, PNG/SVG, max 2 MB), versioned branding with
        draft/publish flow (REQ-BRD-004) and favicon are part of the same
        pending contract.
      </p>
    </div>
  );
}
