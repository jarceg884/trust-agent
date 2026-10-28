/**
 * Self-signup de ORGANIZACIÓN (spec 026, REQ-SS-005, mandato Miller): la
 * entrada pública del SaaS donde un cliente NUEVO crea su cuenta completa.
 * Nace pending_activation y sin budget — el proveedor la activa después.
 *
 * Seam con spec 015 (auth UX): "/register" crea un USUARIO dentro de una org
 * existente; esta página crea la ORGANIZACIÓN misma.
 */
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { getSessionToken } from "@/lib/session";
import { SignupForm } from "./signup-form";

export const metadata: Metadata = {
  title: "Create your organization — Trust Agent Admin Console",
};

export default async function SignupPage() {
  // Ya hay sesión: no tiene sentido crear otra organización.
  if (await getSessionToken()) redirect("/overview");

  return (
    <main className="flex min-h-screen flex-col lg:flex-row">
      {/* Panel de marca (paridad con /login y /register) */}
      <section className="flex flex-col justify-between bg-foreground px-8 py-10 text-white lg:w-[46%] lg:px-14 lg:py-16">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-white/60">
            Governance
          </p>
          <h1 className="mt-6 max-w-md text-3xl font-semibold leading-tight lg:text-4xl">
            Create your organization.
            <br />
            Govern every agent, budget and device.
          </h1>
          <p className="mt-4 max-w-md text-sm leading-relaxed text-white/70">
            Set up your organization in less than a minute. Your workspace is
            provisioned instantly and activated by our team with an initial
            budget — you keep full control from day one.
          </p>
        </div>
        <ul className="mt-10 space-y-3 text-sm text-white/75 lg:mt-0">
          {[
            "Your own identity realm (SSO + optional Google)",
            "Departments, budgets and policies ready to configure",
            "Immutable audit trail from the first event",
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
        <div className="ta-rise w-full max-w-sm">
          <h2 className="text-xl font-semibold text-foreground">
            Create your organization
          </h2>
          <p className="mb-8 mt-1.5 text-sm text-muted">
            New here? This is your first step. Already have an organization?{" "}
            <Link
              href="/login"
              className="font-semibold text-accent transition-colors duration-150 hover:text-accent-hover hover:underline"
            >
              Sign in
            </Link>
          </p>
          <SignupForm />
        </div>
      </section>
    </main>
  );
}
