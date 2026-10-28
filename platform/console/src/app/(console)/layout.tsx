/**
 * Layout del console (spec 009 D-AC-01/05): gate server-side.
 *
 * Resuelve /api/me con el token de la cookie httpOnly (llamada directa al
 * control plane, sin pasar por el proxy): 401 → /login; 403 mfa_required →
 * puerta MFA; ok → Shell con el principal (roles/permissions data-driven).
 */
import type { ReactNode } from "react";
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ApiError, api } from "@/lib/control-plane";
import { accentThemeCss } from "@/lib/branding-theme";
import { getSessionToken } from "@/lib/session";
import {
  consoleTitle,
  getPublishedBranding,
  PROVIDER_FALLBACK_NAME,
} from "@/lib/published-branding";
import type { WhoAmI } from "@/lib/types";
import { Shell } from "@/components/shell/shell";
import { MfaGate } from "@/components/shell/mfa-gate";
import { PendingActivationGate } from "@/components/shell/pending-activation-gate";
import { ToastProvider } from "@/components/ui";

/**
 * Títulos de página dinámicos (spec 028 D-028-08, REQ-BRD2-007,
 * t_42e23b01): el product_name PUBLICADO del tenant define el template
 * "Página — {product_name}" y el default "{product_name} — Admin Console".
 * El fetch se comparte con el acento vía cache() (una llamada por request).
 */
export async function generateMetadata(): Promise<Metadata> {
  const session = await getSessionToken();
  if (!session) return {};
  const branding = await getPublishedBranding(session);
  const name = branding?.product_name || PROVIDER_FALLBACK_NAME;
  return {
    title: {
      default: consoleTitle(name),
      template: `%s — ${name}`,
    },
  };
}

export default async function ConsoleLayout({
  children,
}: {
  children: ReactNode;
}) {
  const session = await getSessionToken();
  if (!session) redirect("/login");

  let me: WhoAmI;
  try {
    me = await api<WhoAmI>("/api/me", { bearer: session });
  } catch (cause) {
    if (cause instanceof ApiError) {
      // Sesión inválida/expirada y el refresh no aplica en RSC: a login.
      // (El proxy rota el refresh en el tráfico de datos; aquí basta el gate.)
      if (cause.status === 401) redirect("/login");
      if (cause.status === 403 && cause.reason === "mfa_required") {
        return <MfaGate />;
      }
      // Spec 026 (REQ-SS-002): tenant pending_activation — el control plane
      // responde 423 a toda la superficie; pantalla dedicada de solo-estado.
      if (cause.status === 423 && cause.reason === "tenant_pending_activation") {
        return <PendingActivationGate />;
      }
      // API caída u otro error: mostramos el estado real dentro del shell.
      me = {
        user_id: "",
        email: "",
        display_name: null,
        department_id: null,
        tenant_id: "",
        tenant_slug: "—",
        tenant_name: "",
        roles: [],
        permissions: [],
        mfa_verified: true,
        via: "session",
      };
    } else {
      throw cause;
    }
  }

  // Pulido v2 (hallazgo #8, t_3cf085b9): el acento del chrome SIGUE al
  // branding publicado del tenant (spec 028 white-label). Sin branding o
  // con hex inválido → null y el chrome conserva el rojo default
  // (spec 009 D-AC-04). Server-side: cero flash de color equivocado.
  // D-028-08: el GET se comparte con generateMetadata vía cache().
  const branding = await getPublishedBranding(session);
  const accentCss = accentThemeCss(branding?.accent_color);

  return (
    <ToastProvider>
      {/* hex validado en accentThemeCss — solo #rrggbb llega aquí */}
      {accentCss ? <style dangerouslySetInnerHTML={{ __html: accentCss }} /> : null}
      <Shell me={me}>{children}</Shell>
    </ToastProvider>
  );
}
