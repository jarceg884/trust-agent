/**
 * Layout del Provider Console (spec 025): gate server-side.
 *
 * Sin cookie `ta_provider` → /login. Con cookie, valida la sesión contra el
 * control plane (`GET /api/provider/auth/me` — funciona también con la
 * required action pendiente): 401 → borra el gate a login (fail-closed);
 * `must_change_password` → /change-password (D-PS-08: toda ruta de negocio
 * está bloqueada hasta completar el cambio); ok → Shell de operador.
 */
import type { ReactNode } from "react";
import { redirect } from "next/navigation";
import { ApiError, api } from "@/lib/control-plane";
import { getProviderToken } from "@/lib/provider-session";
import { Shell } from "@/components/shell";
import { ToastProvider } from "@/components/ui";

interface Me {
  username: string;
  must_change_password: boolean;
  mfa_enrolled: boolean;
}

export default async function ProviderLayout({
  children,
}: {
  children: ReactNode;
}) {
  const token = await getProviderToken();
  if (!token) redirect("/login");

  try {
    // Validación real de la sesión de operador contra el control plane.
    const me = await api<Me>("/api/provider/auth/me", { bearer: token });
    if (me.must_change_password) redirect("/change-password");
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 401) {
      redirect("/login");
    }
    // 503 u otro: el shell se renderiza y cada página muestra su error real.
  }

  return (
    <ToastProvider>
      <Shell>{children}</Shell>
    </ToastProvider>
  );
}
