"use client";

/**
 * Formulario de self-signup de organización (spec 026, REQ-SS-001/005):
 * POST público /api/auth/signup-org vía el proxy BFF (rate-limited por IP en
 * el control plane). El tenant nace pending_activation con budget 0¢; tras
 * el 201 se muestra el estado y el CTA a /login?org= para ver el estado.
 */
import { useState } from "react";
import { isValidOrgCode } from "@/lib/config";

interface FieldErrors {
  organization_name?: string;
  slug?: string;
  admin_email?: string;
  admin_display_name?: string;
  admin_password?: string;
}

/** Deriva el slug del nombre (misma normalización visual del backend). */
function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 49);
}

export function SignupForm() {
  const [orgName, setOrgName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [created, setCreated] = useState<{ org: string } | null>(null);

  const effectiveSlug = slugTouched ? slug : slugify(orgName);

  function onOrgNameChange(value: string) {
    setOrgName(value);
    if (!slugTouched) setSlug(slugify(value));
  }

  function validate(): FieldErrors {
    const errors: FieldErrors = {};
    if (!orgName.trim()) {
      errors.organization_name = "Enter your organization name.";
    }
    if (!isValidOrgCode(effectiveSlug.trim().toLowerCase())) {
      errors.slug =
        "Lowercase letters, numbers and dashes; 3–50 characters (e.g. acme-corp).";
    }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) {
      errors.admin_email = "Enter a valid email address.";
    }
    if (!displayName.trim()) {
      errors.admin_display_name = "Enter your full name.";
    }
    if (password.length < 12) {
      errors.admin_password = "Use at least 12 characters.";
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
      const res = await fetch("/api/proxy/auth/signup-org", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          organization_name: orgName.trim(),
          slug: effectiveSlug.trim().toLowerCase(),
          admin_email: email.trim(),
          admin_password: password,
          admin_display_name: displayName.trim(),
        }),
      });
      if (res.ok) {
        const body = (await res.json()) as { org: string };
        setCreated({ org: body.org });
        return;
      }
      let message = `Sign-up failed (HTTP ${res.status}).`;
      try {
        const env = (await res.json()) as {
          error?: { reason?: string; message?: string };
        };
        if (env.error?.reason === "conflict") {
          message = "That organization code is already taken. Choose another one.";
          setFieldErrors({ slug: message });
          return;
        } else if (env.error?.reason === "validation_failed") {
          message = env.error.message ?? message;
        } else if (env.error?.reason === "self_signup_disabled") {
          message =
            "Organization self-signup is currently disabled by the provider. Contact us to create your organization.";
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

  if (created) {
    return (
      <div className="ta-rise space-y-4">
        <h2 className="text-xl font-semibold text-foreground">
          Organization created
        </h2>
        <p className="text-sm leading-relaxed text-muted">
          <span className="font-semibold text-foreground">
            {created.org}
          </span>{" "}
          is provisioned and <strong>pending activation</strong> by the
          provider. Nothing is functional yet — no agents, models, devices or
          spend — until your account is activated with an initial budget. We
          will notify your admin account as soon as it is ready.
        </p>
        <a
          href={`/login?org=${encodeURIComponent(created.org)}`}
          className="inline-flex w-full items-center justify-center rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white transition-all duration-150 ease-out hover:bg-accent-hover active:scale-[0.98]"
        >
          Sign in to check your status
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
        <label htmlFor="organization_name" className={labelClass}>
          Organization name
        </label>
        <input
          id="organization_name"
          type="text"
          autoComplete="organization"
          placeholder="Acme Corp"
          value={orgName}
          onChange={(e) => onOrgNameChange(e.target.value)}
          aria-invalid={Boolean(fieldErrors.organization_name)}
          className={inputClass}
        />
        {fieldErrors.organization_name ? (
          <p className="mt-1.5 text-xs text-danger">
            {fieldErrors.organization_name}
          </p>
        ) : null}
      </div>

      <div>
        <label htmlFor="slug" className={labelClass}>
          Organization code
        </label>
        <input
          id="slug"
          type="text"
          autoComplete="off"
          spellCheck={false}
          placeholder="acme-corp"
          value={effectiveSlug}
          onChange={(e) => {
            setSlugTouched(true);
            setSlug(e.target.value);
          }}
          aria-invalid={Boolean(fieldErrors.slug)}
          className={`${inputClass} font-mono`}
        />
        {fieldErrors.slug ? (
          <p className="mt-1.5 text-xs text-danger">{fieldErrors.slug}</p>
        ) : (
          <p className="mt-1.5 text-xs text-faint">
            Auto-derived from the name — edit it if you prefer another code.
          </p>
        )}
      </div>

      <div>
        <label htmlFor="admin_email" className={labelClass}>
          Your work email
        </label>
        <input
          id="admin_email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-invalid={Boolean(fieldErrors.admin_email)}
          className={inputClass}
        />
        {fieldErrors.admin_email ? (
          <p className="mt-1.5 text-xs text-danger">
            {fieldErrors.admin_email}
          </p>
        ) : null}
      </div>

      <div>
        <label htmlFor="admin_display_name" className={labelClass}>
          Your full name
        </label>
        <input
          id="admin_display_name"
          type="text"
          autoComplete="name"
          placeholder="Ada Lovelace"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          aria-invalid={Boolean(fieldErrors.admin_display_name)}
          className={inputClass}
        />
        {fieldErrors.admin_display_name ? (
          <p className="mt-1.5 text-xs text-danger">
            {fieldErrors.admin_display_name}
          </p>
        ) : null}
      </div>

      <div>
        <label htmlFor="admin_password" className={labelClass}>
          Password
        </label>
        <input
          id="admin_password"
          type="password"
          autoComplete="new-password"
          placeholder="At least 12 characters"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-invalid={Boolean(fieldErrors.admin_password)}
          className={inputClass}
        />
        {fieldErrors.admin_password ? (
          <p className="mt-1.5 text-xs text-danger">
            {fieldErrors.admin_password}
          </p>
        ) : null}
      </div>

      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.10),0_1px_2px_rgba(16,24,40,0.12)] transition-all duration-150 ease-out hover:bg-accent-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-surface-alt disabled:text-faint disabled:shadow-none"
      >
        {submitting ? "Creating your organization…" : "Create organization"}
      </button>

      <p className="text-xs leading-relaxed text-muted">
        You become the administrator of this organization. Your password is
        stored by your organization&apos;s identity service and never logged.
        The account starts disabled and with zero budget until the provider
        activates it.
      </p>
    </form>
  );
}
