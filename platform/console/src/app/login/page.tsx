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

  return (
    <main className="flex min-h-screen flex-col lg:flex-row">
      {/* Panel de marca (mockup: "One organization. Controlled access…") */}
      <section className="flex flex-col justify-between bg-foreground px-8 py-10 text-white lg:w-[46%] lg:px-14 lg:py-16">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-white/60">
            Governance
          </p>
          <h1 className="mt-6 max-w-md text-3xl font-semibold leading-tight lg:text-4xl">
            One organization.
            <br />
            Controlled access for every department.
          </h1>
          <p className="mt-4 max-w-md text-sm leading-relaxed text-white/70">
            Budgets, approved models, shared agents, devices, credentials and an
            immutable audit trail — governed from one console.
          </p>
        </div>
        <ul className="mt-10 space-y-3 text-sm text-white/75 lg:mt-0">
          {[
            "SSO + MFA required",
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
        <p className="mt-10 text-xs text-white/40">
          Trust Agent — AI governance platform
        </p>
      </section>

      {/* Formulario */}
      <section className="flex flex-1 items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <h2 className="text-xl font-semibold text-foreground">
            Sign in to the Admin Console
          </h2>
          <p className="mb-8 mt-1.5 text-sm text-muted">
            Use your organization code to continue.
          </p>
          <LoginForm initialError={initialError} />
        </div>
      </section>
    </main>
  );
}
