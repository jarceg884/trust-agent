"use client";

/**
 * Auto-registro (spec 015 D-AUTH-04, AC-AUTH-002): el cliente final crea su
 * usuario con contraseña desde la app — sin admin, sin ROPC. El POST va al
 * control plane por el proxy BFF (ruta pública, rate-limited por IP); tras
 * el 201 el navegador navega al baile de sign-in normal con esa contraseña.
 */
import { useState } from "react";
import { isValidOrgCode } from "@/lib/config";

interface FieldErrors {
  org?: string;
  email?: string;
  display_name?: string;
  password?: string;
}

export function RegisterForm({ initialOrg }: { initialOrg: string }) {
  const [org, setOrg] = useState(initialOrg);
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  function validate(): FieldErrors {
    const errors: FieldErrors = {};
    if (!isValidOrgCode(org.trim().toLowerCase())) {
      errors.org = "Lowercase letters, numbers and dashes (e.g. acme-corp).";
    }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) {
      errors.email = "Enter a valid email address.";
    }
    if (!displayName.trim()) {
      errors.display_name = "Enter your full name.";
    }
    if (password.length < 12) {
      errors.password = "Use at least 12 characters.";
    }
    return errors;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setServerError(null);
    const errors = validate();
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setSubmitting(true);
    try {
      const res = await fetch("/api/proxy/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          org: org.trim().toLowerCase(),
          email: email.trim(),
          password,
          display_name: displayName.trim(),
        }),
      });
      if (res.ok) {
        setDone(true);
        return;
      }
      let message = `Registration failed (HTTP ${res.status}).`;
      try {
        const env = (await res.json()) as {
          error?: { reason?: string; message?: string };
        };
        if (env.error?.reason === "self_registration_disabled") {
          message =
            "This organization does not allow self-registration. Ask an administrator to invite you.";
        } else if (env.error?.reason === "conflict") {
          message =
            "A user with that email already exists in this organization.";
          setFieldErrors({ email: message });
          return;
        } else if (env.error?.reason === "not_found") {
          message = "Unknown organization code.";
          setFieldErrors({ org: message });
          return;
        } else if (env.error?.reason === "rate_limited") {
          message = "Too many attempts. Please wait and try again.";
        } else if (env.error?.message) {
          message = env.error.message;
        }
      } catch {
        // respuesta no-JSON
      }
      setServerError(message);
    } catch {
      setServerError("The service is unreachable. Check your connection.");
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <div className="ta-rise space-y-4">
        <h2 className="text-xl font-semibold text-foreground">
          Account created
        </h2>
        <p className="text-sm leading-relaxed text-muted">
          Your account is ready. Continue to sign in with your email and
          password — you can add multi-factor authentication at any time.
        </p>
        <a
          href={`/auth/login?org=${encodeURIComponent(org.trim().toLowerCase())}`}
          className="inline-flex w-full items-center justify-center rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white transition-all duration-150 ease-out hover:bg-accent-hover active:scale-[0.98]"
        >
          Continue to sign-in
        </a>
      </div>
    );
  }

  const inputClass =
    // Pulido v2 (t_3cf085b9): focus neutro — el borde rojo en un input
    // vacío se leía como error falso (hallazgo #1 de la crítica visual).
    "w-full rounded-lg border border-border-subtle bg-surface px-3.5 py-2.5 text-sm text-foreground placeholder:text-faint transition-all duration-150 hover:border-foreground/25 focus:border-foreground/40 focus:ring-[3px] focus:ring-foreground/[0.08] focus:outline-none";
  const labelClass = "mb-1.5 block text-sm font-medium text-foreground";

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {serverError ? (
        <p
          role="alert"
          className="rounded-lg border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger"
        >
          {serverError}
        </p>
      ) : null}

      <div>
        <label htmlFor="org" className={labelClass}>
          Organization code
        </label>
        <input
          id="org"
          type="text"
          autoComplete="off"
          spellCheck={false}
          placeholder="acme-corp"
          value={org}
          onChange={(e) => setOrg(e.target.value)}
          aria-invalid={Boolean(fieldErrors.org)}
          className={inputClass}
        />
        {fieldErrors.org ? (
          <p className="mt-1.5 text-xs text-danger">{fieldErrors.org}</p>
        ) : null}
      </div>

      <div>
        <label htmlFor="email" className={labelClass}>
          Work email
        </label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-invalid={Boolean(fieldErrors.email)}
          className={inputClass}
        />
        {fieldErrors.email ? (
          <p className="mt-1.5 text-xs text-danger">{fieldErrors.email}</p>
        ) : null}
      </div>

      <div>
        <label htmlFor="display_name" className={labelClass}>
          Full name
        </label>
        <input
          id="display_name"
          type="text"
          autoComplete="name"
          placeholder="Ada Lovelace"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          aria-invalid={Boolean(fieldErrors.display_name)}
          className={inputClass}
        />
        {fieldErrors.display_name ? (
          <p className="mt-1.5 text-xs text-danger">
            {fieldErrors.display_name}
          </p>
        ) : null}
      </div>

      <div>
        <label htmlFor="password" className={labelClass}>
          Password
        </label>
        <input
          id="password"
          type="password"
          autoComplete="new-password"
          placeholder="At least 12 characters"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-invalid={Boolean(fieldErrors.password)}
          className={inputClass}
        />
        {fieldErrors.password ? (
          <p className="mt-1.5 text-xs text-danger">{fieldErrors.password}</p>
        ) : null}
      </div>

      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.10),0_1px_2px_rgba(16,24,40,0.12)] transition-all duration-150 ease-out hover:bg-accent-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-surface-alt disabled:text-faint disabled:shadow-none"
      >
        {submitting ? "Creating your account…" : "Create account"}
      </button>

      <p className="text-xs leading-relaxed text-muted">
        Your password is stored by your organization&apos;s identity service and
        never logged. You will sign in with it right after creating the
        account.
      </p>
    </form>
  );
}
