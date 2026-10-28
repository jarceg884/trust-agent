"use client";

/**
 * Brand del tenant en runtime (REQ-BRD-002, spec 028): el sidebar muestra
 * el product_name + logo PUBLICADOS (nunca el draft — REQ-BRD-004) y el
 * favicon del tenant se aplica como <link rel="icon"> en runtime, sin
 * redeploy. Lee GET /api/branding como cualquier principal autenticado.
 */
import { useEffect, useSyncExternalStore } from "react";
import { onBrandingUpdated } from "@/lib/branding-events";
import { consoleTitle } from "@/lib/published-branding";
import { useApi } from "@/hooks/use-api";
import { proxyUrl } from "@/lib/api-client";
import type { TenantBranding } from "@/lib/types";

const FALLBACK_NAME = "Trust Agent";
// Provider default del branding (spec 004 PROVIDER_DEFAULT): el mismo
// indigo que devuelve GET /api/branding para tenants sin branding propio.
const FALLBACK_ACCENT = "#4F46E5";

const DARK_MQ = "(prefers-color-scheme: dark)";

function subscribePrefersDark(onChange: () => void): () => void {
  const mq = window.matchMedia(DARK_MQ);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

/** Logo según el color scheme del console (light/dark, REQ-BRD-001).
 *  useSyncExternalStore: sin setState síncrono en efectos (regla del
 *  proyecto, ver use-api.ts). */
function usePrefersDark(): boolean {
  return useSyncExternalStore(
    subscribePrefersDark,
    () => window.matchMedia(DARK_MQ).matches,
    () => false,
  );
}

export function TenantBrand() {
  const branding = useApi<TenantBranding>("/api/branding");
  const dark = usePrefersDark();
  const b = branding.data;

  const name = b?.product_name || FALLBACK_NAME;
  const accent = b?.accent_color || FALLBACK_ACCENT;
  const logoPath = dark
    ? (b?.logo_dark_url ?? b?.logo_light_url ?? b?.logo_url)
    : (b?.logo_light_url ?? b?.logo_url);
  const logo = logoPath ? proxyUrl(logoPath) : null;

  // Refresh sin reload (spec 028 D-028-09, REQ-BRD2-008, t_42e23b01):
  // publish/rollback desde /branding emite trust:branding-updated →
  // re-fetch del publicado → sidebar, favicon y título al día sin
  // recargar la página.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- refetch es estable (useCallback)
  useEffect(() => onBrandingUpdated(() => branding.refetch()), [branding.refetch]);

  // Favicon del tenant en runtime (spec 028 REQ-BRD2-005).
  useEffect(() => {
    if (!b?.favicon_url) return;
    const href = proxyUrl(b.favicon_url);
    let link = document.querySelector<HTMLLinkElement>("link[rel='icon']");
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      document.head.appendChild(link);
    }
    link.href = href;
  }, [b?.favicon_url]);

  // <title> dinámico (REQ-BRD2-007): el título llega server-side desde
  // generateMetadata ("Página — {product_name}"); este efecto lo mantiene
  // al día cuando el product_name cambia SIN reload (D-028-09),
  // reemplazando el segmento final tras " — ". Idempotente en el mount:
  // con el nombre sin cambios, el título queda exactamente igual.
  useEffect(() => {
    if (!b) return;
    const parts = document.title.split(" — ");
    if (parts.length > 1) {
      parts[parts.length - 1] = name;
      document.title = parts.join(" — ");
    } else {
      document.title = consoleTitle(name);
    }
  }, [b, name]);

  return (
    <div className="flex h-14 items-center gap-2.5 border-b border-border-subtle px-5">
      {logo ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={logo}
          alt={`${name} logo`}
          className="size-7 rounded-lg object-contain"
        />
      ) : (
        <span
          aria-hidden="true"
          className="flex size-7 items-center justify-center rounded-lg text-[13px] font-bold text-white"
          style={{ background: accent }}
        >
          {name.charAt(0).toUpperCase()}
        </span>
      )}
      <div className="leading-tight">
        <p className="text-sm font-semibold text-foreground">{name}</p>
        <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-faint">
          Admin Console
        </p>
      </div>
    </div>
  );
}
