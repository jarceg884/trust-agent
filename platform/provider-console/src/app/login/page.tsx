/**
 * Login del operador del proveedor (spec 025, REQ-PS-003): pantalla dividida
 * enterprise — valor del producto a la izquierda, usuario+contraseña a la
 * derecha. Las credenciales se validan contra el control plane REAL y la
 * sesión vive en PostgreSQL (cookie httpOnly con el token pst1…).
 */
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getProviderToken } from "@/lib/provider-session";
import { LoginForm } from "./login-form";

export const metadata: Metadata = {
  title: "Sign in — Trust Agent Provider Console",
};

export default async function LoginPage({
  searchParams,
}: PageProps<"/login">) {
  // Ya hay sesión: a la lista de tenants.
  if (await getProviderToken()) redirect("/tenants");

  const params = await searchParams;
  const initialError =
    typeof params.error === "string" ? params.error : undefined;

  return (
    <main className="flex min-h-screen flex-col lg:flex-row">
      {/* Panel de marca */}
      <section className="flex flex-col justify-between bg-foreground px-8 py-10 text-white lg:w-[46%] lg:px-14 lg:py-16">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-white/60">
            Provider
          </p>
          <h1 className="mt-6 max-w-md text-3xl font-semibold leading-tight lg:text-4xl">
            Every customer.
            <br />
            Deployed and governed from one place.
          </h1>
          <p className="mt-4 max-w-md text-sm leading-relaxed text-white/70">
            Provision new organizations with a real Keycloak realm, seed
            departments, budgets and default policies — then suspend, reactivate
            and monitor each tenant from a single console.
          </p>
        </div>
        <ul className="mt-10 space-y-3 text-sm text-white/75 lg:mt-0">
          {[
            "Idiom-proof tenant provisioning",
            "One-time admin credentials, never stored",
            "Suspension cuts access instantly",
            "Per-tenant healthcheck and audit",
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

      {/* Formulario de acceso */}
      <section className="flex flex-1 items-center justify-center px-6 py-12">
        <div className="ta-rise w-full max-w-sm">
          <h2 className="text-xl font-semibold text-foreground">
            Sign in to the Provider Console
          </h2>
          <p className="mb-8 mt-1.5 text-sm text-muted">
            Enter your operator username and password.
          </p>
          <LoginForm initialError={initialError} />
        </div>
      </section>
    </main>
  );
}
