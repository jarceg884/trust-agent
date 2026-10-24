"use client";

/**
 * Login (spec 009 REQ-AC-001): el admin introduce el org code (slug = realm
 * Keycloak) y el navegador navega a /auth/login?org=…, que inicia el baile
 * PKCE server-side. Sin MFA en el realm, el control plane responderá 403
 * mfa_required tras el callback y el shell mostrará la puerta MFA.
 */
import { useState } from "react";
import { isValidOrgCode } from "@/lib/config";

const ERROR_MESSAGES: Record<string, string> = {
  "invalid-org":
    "Enter a valid organization code (lowercase letters, numbers and dashes).",
};

export function LoginForm({ initialError }: { initialError?: string }) {
  const [org, setOrg] = useState("");
  const [touched, setTouched] = useState(false);
  const valid = isValidOrgCode(org.trim().toLowerCase());

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid) {
      setTouched(true);
      return;
    }
    // Navegación full-page INTENCIONAL: /auth/login es un route handler
    // que responde con redirect externo al IdP — router.push no aplica.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = `/auth/login?org=${encodeURIComponent(
      org.trim().toLowerCase(),
    )}`;
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
          className="mb-1.5 block text-sm font-medium text-foreground"
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
          placeholder="acme-corp"
          value={org}
          onChange={(e) => {
            setOrg(e.target.value);
            setTouched(true);
          }}
          aria-invalid={touched && !valid}
          aria-describedby={touched && !valid ? "org-error" : undefined}
          className="w-full rounded-lg border border-border-subtle bg-surface px-3.5 py-2.5 text-sm text-foreground placeholder:text-faint focus:border-accent focus:outline-none"
        />
        {touched && !valid ? (
          <p id="org-error" className="mt-1.5 text-xs text-danger">
            Lowercase letters, numbers and dashes (e.g. acme-corp).
          </p>
        ) : null}
      </div>
      <button
        type="submit"
        disabled={!valid}
        className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
      >
        Continue to sign-in
      </button>
      <p className="text-xs leading-relaxed text-muted">
        You will be redirected to your organization&apos;s identity provider.
        Sign-in requires multi-factor authentication.
      </p>
    </form>
  );
}
