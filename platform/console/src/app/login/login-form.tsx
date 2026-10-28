"use client";

/**
 * Login (spec 009 REQ-AC-001): el admin introduce el org code (slug = realm
 * Keycloak) y el navegador navega a /auth/login?org=…, que inicia el baile
 * PKCE server-side. Sin MFA en el realm, el control plane responderá 403
 * mfa_required tras el callback y el shell mostrará la puerta MFA.
 *
 * Pulido v2 (t_3cf085b9, crítica visual 4/10 → meta 7+):
 * - Focus del input NEUTRO (hallazgo #1): el autoFocus encendía el borde
 *   rojo del focus en el primer paint y parecía un error permanente. El
 *   rojo ahora solo aparece con error real (touched + inválido).
 * - Validación al blur, no al primer caracter: escribir "a" ya marcaba
 *   error; ahora el juicio llega cuando el usuario abandona el campo.
 * - Botón primario con profundidad (sombra sutil) y disabled gris neutro
 *   (hallazgo #2): el rojo a 40% de opacidad se leía como salmón roto.
 * - "Join/Create your organization" como enlaces planos bajo un divisor
 *   (hallazgo #3): las dos cajas bordeadas competían con la CTA primaria y
 *   desbordaban; ahora son navegación terciaria clara y alineada.
 */
import { useState } from "react";
import { Building2, UserPlus } from "lucide-react";
import { isValidOrgCode } from "@/lib/config";
import { Button } from "@/components/ui";

const ERROR_MESSAGES: Record<string, string> = {
  "invalid-org":
    "Enter a valid organization code (lowercase letters, numbers and dashes).",
  "unknown-org":
    "We could not find that organization code. Check it with your administrator.",
};

export function LoginForm({
  initialError,
  initialOrg = "",
}: {
  initialError?: string;
  initialOrg?: string;
}) {
  const [org, setOrg] = useState(initialOrg);
  const [touched, setTouched] = useState(false);
  const trimmed = org.trim().toLowerCase();
  const valid = isValidOrgCode(trimmed);
  const showError = touched && !valid;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid) {
      // El botón vive siempre habilitado (patrón Stripe): el juicio de
      // validez aparece AL ENVIAR, con borde y mensaje de error reales —
      // nunca antes como estado falso.
      setTouched(true);
      return;
    }
    // Navegación full-page INTENCIONAL: /auth/login es un route handler
    // que responde con redirect externo al IdP — router.push no aplica.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = `/auth/login?org=${encodeURIComponent(trimmed)}`;
  }

  const urlError = initialError
    ? (ERROR_MESSAGES[initialError] ?? decodeURIComponent(initialError))
    : null;

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {urlError ? (
        <p
          role="alert"
          className="rounded-lg border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger"
        >
          {urlError}
        </p>
      ) : null}
      <div>
        <label
          htmlFor="org"
          className="mb-2 block text-sm font-medium text-foreground"
        >
          Organization code
        </label>
        <input
          id="org"
          name="org"
          type="text"
          autoComplete="off"
          autoFocus
          spellCheck={false}
          placeholder="e.g. acme-corp"
          value={org}
          onChange={(e) => setOrg(e.target.value)}
          onBlur={() => setTouched(true)}
          aria-invalid={showError}
          aria-describedby={showError ? "org-error" : undefined}
          className={`w-full rounded-lg border bg-surface px-3.5 py-2.5 text-sm text-foreground transition-all duration-150 placeholder:text-muted focus:outline-none ${
            showError
              ? "border-danger focus:border-danger focus:ring-[3px] focus:ring-danger/[0.10]"
              : "border-border-subtle hover:border-foreground/25 focus:border-foreground/60 focus:ring-[3px] focus:ring-foreground/[0.12]"
          }`}
        />
        {showError ? (
          <p id="org-error" role="alert" className="mt-1.5 text-xs text-danger">
            Lowercase letters, numbers and dashes (e.g. acme-corp).
          </p>
        ) : null}
      </div>
      <Button type="submit" variant="primary" className="w-full py-2.5">
        Continue
      </Button>

      {/* Seam spec 026 vs spec 015: "Join your organization" crea un USUARIO
          dentro de una org existente (spec 015 D-AUTH-04); "Create your
          organization" crea la ORGANIZACIÓN nueva (spec 026 /signup).
          Enlaces planos (no cajas) para no competir con la CTA primaria. */}
      <div className="pt-2">
        <div className="relative" aria-hidden="true">
          <div className="absolute inset-0 flex items-center">
            <span className="w-full border-t border-border-subtle" />
          </div>
          <div className="relative flex justify-center">
            <span className="bg-background px-3 text-xs font-medium uppercase tracking-wide text-foreground/60">
              New to Trust Agent?
            </span>
          </div>
        </div>
        {/* Pulido v2 r3 (crítica visual: "secondary actions read like muted
            utility text"): botones fantasma con borde — affordance clara de
            táctil sin competir con la CTA primaria (patrón Stripe/Linear). */}
        <div className="mt-3 grid grid-cols-2 gap-2">
          <a
            href={`/register${valid ? `?org=${encodeURIComponent(trimmed)}` : ""}`}
            className="flex items-center justify-center gap-1.5 rounded-lg border border-border-subtle bg-surface px-2 py-2 text-[13px] font-medium text-foreground underline-offset-4 transition-all duration-150 ease-out hover:border-foreground/30 hover:bg-surface-alt hover:text-accent active:scale-[0.98]"
          >
            <UserPlus size={14} className="shrink-0 text-muted" />
            Join your organization
          </a>
          <a
            href="/signup"
            className="flex items-center justify-center gap-1.5 rounded-lg border border-border-subtle bg-surface px-2 py-2 text-[13px] font-medium text-foreground underline-offset-4 transition-all duration-150 ease-out hover:border-foreground/30 hover:bg-surface-alt hover:text-accent active:scale-[0.98]"
          >
            <Building2 size={14} className="shrink-0 text-muted" />
            Create your organization
          </a>
        </div>
      </div>

      <p className="pt-1 text-xs leading-relaxed text-muted">
        You will sign in on your organization&apos;s secure page. Your
        organization may require multi-factor authentication — you can also
        set it up any time from the console.
      </p>
    </form>
  );
}
