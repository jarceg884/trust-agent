/**
 * Página de auto-registro (spec 015 D-AUTH-04): el cliente final crea su
 * usuario con contraseña. Misma pantalla dividida enterprise que /login.
 */
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { getSessionToken } from "@/lib/session";
import { RegisterForm } from "./register-form";

export const metadata: Metadata = {
  title: "Create your account — Trust Agent Admin Console",
};

export default async function RegisterPage({
  searchParams,
}: PageProps<"/register">) {
  // Ya hay sesión: no tiene sentido registrarse.
  if (await getSessionToken()) redirect("/overview");

  const params = await searchParams;
  const initialOrg =
    typeof params.org === "string" ? params.org.trim().toLowerCase() : "";

  return (
    <main className="flex min-h-screen flex-col lg:flex-row">
      {/* Panel de marca (paridad con /login) */}
      <section className="flex flex-col justify-between bg-foreground px-8 py-10 text-white lg:w-[46%] lg:px-14 lg:py-16">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-white/60">
            Governance
          </p>
          <h1 className="mt-6 max-w-md text-3xl font-semibold leading-tight lg:text-4xl">
            Create your account.
            <br />
            Your organization, your credentials.
          </h1>
          <p className="mt-4 max-w-md text-sm leading-relaxed text-white/70">
            Sign in with a password you choose, or with your Google account
            from the sign-in screen. Multi-factor authentication is one click
            away whenever you want it.
          </p>
        </div>
        <ul className="mt-10 space-y-3 text-sm text-white/75 lg:mt-0">
          {[
            "Password or Google sign-in",
            "MFA suggested, never forced",
            "Immutable audit trail",
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
            Create your account
          </h2>
          <p className="mb-8 mt-1.5 text-sm text-muted">
            Join your organization in less than a minute.
          </p>
          <RegisterForm initialOrg={initialOrg} />
          <p className="mt-6 text-sm text-muted">
            Already have an account?{" "}
            <Link
              href="/login"
              className="font-semibold text-accent transition-colors duration-150 hover:text-accent-hover hover:underline"
            >
              Sign in
            </Link>
          </p>
        </div>
      </section>
    </main>
  );
}
