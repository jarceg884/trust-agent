/**
 * Required action del primer login (spec 025, REQ-PS-006, D-PS-08).
 *
 * Vive FUERA del grupo (provider): el gate de ese layout redirige aquí
 * mientras `must_change_password=true`, y esta página es la única pantalla
 * permitida. Gate propio: sin sesión → /login; required action ya
 * completada → /tenants.
 */
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ApiError, api } from "@/lib/control-plane";
import { getProviderToken } from "@/lib/provider-session";
import { ChangePasswordForm } from "./change-password-form";

export const metadata: Metadata = {
  title: "Change password — Trust Agent Provider Console",
};

interface Me {
  username: string;
  must_change_password: boolean;
}

export default async function ChangePasswordPage() {
  const token = await getProviderToken();
  if (!token) redirect("/login");

  let username = "";
  try {
    const me = await api<Me>("/api/provider/auth/me", { bearer: token });
    if (!me.must_change_password) redirect("/tenants");
    username = me.username;
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 401) redirect("/login");
    // 503: el form muestra el error real al intentar el cambio.
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="ta-rise w-full max-w-md">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-faint">
          Required action
        </p>
        <h1 className="mt-2 text-xl font-semibold tracking-tight text-foreground">
          Set a new password before continuing
        </h1>
        <p className="mb-8 mt-1.5 text-sm leading-relaxed text-muted">
          {username ? (
            <>
              Welcome, <strong className="text-foreground">{username}</strong>.
              Your one-time bootstrap password must be replaced now — all
              Provider Console operations stay blocked until you do.
            </>
          ) : (
            "Your one-time bootstrap password must be replaced now — all Provider Console operations stay blocked until you do."
          )}
        </p>
        <ChangePasswordForm />
      </div>
    </main>
  );
}
