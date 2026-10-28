/**
 * Página de login (spec 009 REQ-AC-001): pantalla dividida enterprise con el
 * valor de gobernanza a la izquierda y el formulario de org code a la derecha.
 */
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getSessionToken } from "@/lib/session";
import { LoginForm } from "./login-form";

export const metadata: Metadata = {
  title: "Sign in — Trust Agent Admin Console",
};

export default async function LoginPage({
  searchParams,
}: PageProps<"/login">) {
  // Ya hay sesión: al Overview (el shell revalida contra el control plane).
  if (await getSessionToken()) redirect("/overview");

  const params = await searchParams;
  const initialError =
    typeof params.error === "string" ? params.error : undefined;
  // Spec 026: prefill del org code (p.ej. CTA post-signup /login?org=…).
  const initialOrg =
    typeof params.org === "string" ? params.org.trim().toLowerCase() : "";

  return (
    <main className="flex min-h-screen flex-col lg:flex-row">
      {/* Panel de marca (mockup: "One organization. Controlled access…").
          Pulido v2 (t_3cf085b9): contraste del pie white/40 → white/65
          (hallazgo #4) y glow radial sutil de la marca para dar profundidad
          al panel oscuro en vez de un plano muerto. */}
      <section className="relative flex flex-col justify-between overflow-hidden bg-foreground px-8 py-10 text-white lg:w-[46%] lg:px-14 lg:py-16">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(720px_circle_at_bottom_left,rgba(79,70,229,0.22),transparent_62%)]"
        />
        <div className="relative">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-white/75">
            Governance
          </p>
          <h1 className="mt-6 max-w-md text-3xl font-semibold leading-tight lg:text-4xl">
            One organization.
            <br />
            Controlled access for every department.
          </h1>
          <p className="mt-4 max-w-md text-sm leading-relaxed text-white/80">
            Budgets, approved models, shared agents, devices, credentials and an
            immutable audit trail — governed from one console.
          </p>
        </div>
        <ul className="relative mt-10 space-y-3 text-sm text-white/85 lg:mt-0">
          {[
            "SSO + MFA, always suggested",
            "Approved models only",
            "Explicit shared-agent grants",
            "Scoped credentials",
          ].map((item) => (
            <li key={item} className="flex items-center gap-3">
              <span
                aria-hidden="true"
                className="inline-block size-1.5 rounded-full bg-accent"
              />
              {item}
            </li>
          ))}
        </ul>
        {/* Pulido v2 r3: white/80 → white/90 — el critic visual siguió
            leyendo el pie como borderline AA (hallazgo #4). */}
        <p className="relative mt-10 text-xs text-white/90">
          Trust Agent — AI governance platform
        </p>
      </section>

      {/* Formulario — wordmark de producto arriba (estándar enterprise:
          el usuario siempre sabe dónde está iniciando sesión). */}
      <section className="flex flex-1 items-center justify-center px-6 py-12">
        <div className="ta-rise w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2.5">
            <span
              aria-hidden="true"
              className="flex size-7 items-center justify-center rounded-md bg-accent text-[11px] font-bold text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.15)]"
            >
              TA
            </span>
            <span className="text-sm font-semibold tracking-tight text-foreground">
              Trust Agent
            </span>
          </div>
          <h2 className="text-xl font-semibold text-foreground">
            Sign in to the Admin Console
          </h2>
          <p className="mb-8 mt-1.5 text-sm text-muted">
            Use your organization code to continue.
          </p>
          <LoginForm initialError={initialError} initialOrg={initialOrg} />
        </div>
      </section>
    </main>
  );
}
