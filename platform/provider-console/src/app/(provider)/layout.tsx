/**
 * Layout del Provider Console (spec 014 REQ-PC-010): gate server-side.
 *
 * Sin cookie `ta_provider` → /login. Con cookie, valida el token contra el
 * control plane (`GET /api/admin/tenants`): 401/403 → borra el gate a login
 * (fail-closed); ok → Shell de operador.
 */
import type { ReactNode } from "react";
import { redirect } from "next/navigation";
import { ApiError, api } from "@/lib/control-plane";
import { getProviderToken } from "@/lib/provider-session";
import { Shell } from "@/components/shell";
import { ToastProvider } from "@/components/ui";

export default async function ProviderLayout({
  children,
}: {
  children: ReactNode;
}) {
  const token = await getProviderToken();
  if (!token) redirect("/login");

  try {
    // Validación real contra el control plane con el token de la cookie.
    await api("/api/admin/tenants", { bearer: token });
  } catch (cause) {
    if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) {
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
