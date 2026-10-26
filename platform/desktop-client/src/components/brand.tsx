/**
 * Marca white-label (D-DTB-08, REQ-BRD-002/003 — spec 013 D-013-06).
 *
 * Antes del login: branding estático del proveedor (AC-BRD-003 — el
 * endpoint exige sesión MFA). Tras el login, el dashboard lee
 * GET /api/branding en runtime y lo inyecta aquí: product_name, tagline,
 * accent_color y logo_url del tenant, sin redeploy ni re-empaquetado.
 */
import type { Branding } from "@/lib/control-plane";

/** Default del proveedor (lo que muestra la base instalada). */
export const PROVIDER_BRANDING = {
  product_name: "Trust Agent",
  tagline: "Your work. Your agents. Your organization's rules.",
  accent_color: null as string | null,
  logo_url: null as string | null,
};

type BrandFields = Pick<
  Branding,
  "product_name" | "tagline" | "accent_color" | "logo_url"
>;

function fields(branding?: Branding | null): BrandFields {
  if (!branding) return PROVIDER_BRANDING;
  return {
    product_name: branding.product_name || PROVIDER_BRANDING.product_name,
    tagline: branding.tagline ?? PROVIDER_BRANDING.tagline,
    accent_color: branding.accent_color,
    logo_url: branding.logo_url,
  };
}

export function Brand({
  compact = false,
  branding,
}: {
  compact?: boolean;
  branding?: Branding | null;
}) {
  const b = fields(branding);
  const initial = (b.product_name ?? "").trim().charAt(0).toUpperCase() || "H";
  return (
    <span className="flex items-center gap-2.5 select-none">
      {b.logo_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={b.logo_url}
          alt={`${b.product_name} logo`}
          className="h-7 w-7 rounded-lg object-contain"
        />
      ) : (
        <span
          aria-hidden
          className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent text-sm font-black text-white"
          style={
            b.accent_color ? { backgroundColor: b.accent_color } : undefined
          }
        >
          {initial}
        </span>
      )}
      {!compact && (
        <span className="text-sm font-semibold tracking-[0.18em] text-foreground">
          {b.product_name.toUpperCase()}
        </span>
      )}
    </span>
  );
}

export function BrandTagline({ branding }: { branding?: Branding | null }) {
  const b = fields(branding);
  return <p className="text-sm text-muted">{b.tagline}</p>;
}
