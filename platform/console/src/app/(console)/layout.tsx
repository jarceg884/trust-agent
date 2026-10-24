/**
 * Layout del console (spec 009 D-AC-01/05): gate server-side.
 *
 * Resuelve /api/me con el token de la cookie httpOnly (llamada directa al
 * control plane, sin pasar por el proxy): 401 → /login; 403 mfa_required →
 * puerta MFA; ok → Shell con el principal (roles/permissions data-driven).
 */
import type { ReactNode } from "react";
import { redirect } from "next/navigation";
import { ApiError, api } from "@/lib/control-plane";
import { getSessionToken } from "@/lib/session";
import type { WhoAmI } from "@/lib/types";
import { Shell } from "@/components/shell/shell";
import { MfaGate } from "@/components/shell/mfa-gate";
import { ToastProvider } from "@/components/ui";

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
      // API caída u otro error: mostramos el estado real dentro del shell.
      me = {
        user_id: "",
        email: "",
        display_name: null,
        department_id: null,
        tenant_id: "",
        tenant_slug: "—",
        roles: [],
        permissions: [],
        mfa_verified: true,
        via: "session",
      };
    } else {
      throw cause;
    }
  }

  return (
    <ToastProvider>
      <Shell me={me}>{children}</Shell>
    </ToastProvider>
  );
}
