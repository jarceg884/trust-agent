/**
 * Pantalla de login (REQ-DTB-001): el usuario final entra con el código de su
 * organización; el desktop lo lleva al SSO de la org (OIDC authorization code
 * + PKCE S256 contra el realm del tenant). Sin contraseñas aquí (constitution IV).
 *
 * Acepta `?org=<slug>` (spec 014 REQ-PC-017, D-PC-09): el "Hermes download
 * link" que la organización envía al empleado lleva el org_code embebido y
 * prellena este campo — el binding app→organización del flujo de enrolamiento.
 */
import { redirect } from "next/navigation";
import { Brand, BrandTagline } from "@/components/brand";
import { isValidOrgCode } from "@/lib/config";
import { getSessionToken } from "@/lib/session";
import { startLogin } from "./actions";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; org?: string }>;
}) {
  // Ya hay sesión: directo al dashboard gobernado.
  if (await getSessionToken()) redirect("/dashboard");

  const { error, org } = await searchParams;
  // Solo slugs válidos prellenan el campo (D-DEV-02: el org_code no es raíz
  // de confianza — el tenant siempre sale del claim firmado del id_token).
  const initialOrg =
    org && isValidOrgCode(org.toLowerCase()) ? org.toLowerCase() : "";

  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <Brand />
          <BrandTagline />
        </div>

        <div className="rounded-2xl border border-border-subtle bg-panel p-7 shadow-lg">
          <h1 className="text-lg font-semibold">Sign in to your organization</h1>
          <p className="mt-1.5 text-sm text-muted">
            You will be redirected to your organization&apos;s single sign-on
            (SSO + MFA).
          </p>

          {error && (
            <p
              role="alert"
              className="mt-4 rounded-lg border border-[#3a1a1f] bg-accent-soft px-3.5 py-2.5 text-sm text-[#ff8a95]"
            >
              {error}
            </p>
          )}

          <form action={startLogin} className="mt-5 space-y-4">
            <div>
              <label
                htmlFor="orgCode"
                className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-muted"
              >
                Organization code
              </label>
              <input
                id="orgCode"
                name="orgCode"
                type="text"
                required
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                placeholder="acme-corp"
                defaultValue={initialOrg}
                key={initialOrg}
                className="w-full rounded-lg border border-border-subtle bg-surface px-3.5 py-2.5 text-sm text-foreground placeholder:text-muted/60 focus:border-accent"
              />
            </div>
            <button
              type="submit"
              className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white transition hover:brightness-110"
            >
              Continue with SSO
            </button>
          </form>
        </div>

        <p className="mt-6 text-center text-xs text-muted">
          This device will be enrolled to your organization after sign-in.
        </p>
      </div>
    </main>
  );
}
