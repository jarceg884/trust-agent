"use client";

/**
 * Login panel (spec 015 v1.3.0, ADR-019, D-AUTH-12 — AC-AUTH-011).
 *
 * The user signs in with org code + email + password (+ the one-time code
 * when they have MFA). The submit goes to the console BFF
 * (POST /auth/panel/login), which forwards to the control plane's Direct
 * Access Grant. The browser NEVER leaves the console origin — Keycloak is
 * invisible (no redirects to :8080, no IdP pages).
 *
 * Statuses routed by the control plane:
 *   ok              → /overview (session cookies set by the BFF)
 *   update_password → /set-password (first login: the one-time password was
 *                     already proven by the grant outcome)
 *   enroll_totp     → /mfa-setup (the enrollment dance already started)
 *   401 mfa_code_required → hint shown ONLY for accounts with MFA (the
 *                     generic 401 carries no oracle — D-AUTH-16)
 */
import { useState } from "react";
import { Building2, UserPlus } from "lucide-react";
import { isValidOrgCode } from "@/lib/config";
import { panelErrorMessage } from "@/lib/panel";
import { Button } from "@/components/ui";

export function LoginForm({
  initialError,
  initialOrg = "",
}: {
  initialError?: string;
  initialOrg?: string;
}) {
  const [org, setOrg] = useState(initialOrg);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [showOtp, setShowOtp] = useState(false);
  const [touched, setTouched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const trimmed = org.trim().toLowerCase();
  const valid = isValidOrgCode(trimmed);
  const showError = touched && !valid;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid) {
      setTouched(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/auth/panel/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          org: trimmed,
          email,
          password,
          totp: totp.trim() || null,
        }),
      });
      const data = (await res.json()) as {
        status: string;
        reason?: string;
        message?: string;
      };
      if (res.ok && data.status === "ok") {
        window.location.href = "/overview";
        return;
      }
      if (res.ok && data.status === "update_password") {
        window.location.href = "/set-password";
        return;
      }
      if (res.ok && data.status === "enroll_totp") {
        window.location.href = "/mfa-setup";
        return;
      }
      if (data.reason === "mfa_code_required") {
        setShowOtp(true);
        setError(
          panelErrorMessage(
            "mfa_code_required",
            "Enter your one-time code as well.",
          ),
        );
      } else {
        setError(
          panelErrorMessage(
            data.reason ?? "error",
            data.message ?? "Sign-in failed. Please try again.",
          ),
        );
      }
    } catch {
      setError("The console is unreachable. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  const urlError = initialError
    ? initialError === "invalid-org"
      ? "Enter a valid organization code (lowercase letters, numbers and dashes)."
      : initialError === "unknown-org"
        ? "We could not find that organization code. Check it with your administrator."
        : decodeURIComponent(initialError)
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
      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger"
        >
          {error}
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
      <div>
        <label
          htmlFor="email"
          className="mb-2 block text-sm font-medium text-foreground"
        >
          Email
        </label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="username"
          spellCheck={false}
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded-lg border border-border-subtle bg-surface px-3.5 py-2.5 text-sm text-foreground transition-all duration-150 placeholder:text-muted hover:border-foreground/25 focus:border-foreground/60 focus:outline-none focus:ring-[3px] focus:ring-foreground/[0.12]"
        />
      </div>
      <div>
        <label
          htmlFor="password"
          className="mb-2 block text-sm font-medium text-foreground"
        >
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-lg border border-border-subtle bg-surface px-3.5 py-2.5 text-sm text-foreground transition-all duration-150 placeholder:text-muted hover:border-foreground/25 focus:border-foreground/60 focus:outline-none focus:ring-[3px] focus:ring-foreground/[0.12]"
        />
      </div>
      <div>
        <label
          htmlFor="totp"
          className="mb-2 block text-sm font-medium text-foreground"
        >
          One-time code{" "}
          <span className="font-normal text-muted">
            (only if you have MFA configured)
          </span>
        </label>
        <input
          id="totp"
          name="totp"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={8}
          spellCheck={false}
          value={totp}
          onChange={(e) => {
            setTotp(e.target.value);
            if (e.target.value) setShowOtp(true);
          }}
          onFocus={() => setShowOtp(true)}
          className={`w-full rounded-lg border bg-surface px-3.5 py-2.5 text-sm text-foreground transition-all duration-150 placeholder:text-muted focus:outline-none ${
            showOtp && totp
              ? "border-foreground/60 focus:ring-[3px] focus:ring-foreground/[0.12]"
              : "border-border-subtle hover:border-foreground/25 focus:border-foreground/60 focus:ring-[3px] focus:ring-foreground/[0.12]"
          }`}
          placeholder="6-digit code"
        />
      </div>
      <Button
        type="submit"
        variant="primary"
        className="w-full py-2.5"
        disabled={busy}
      >
        {busy ? "Signing in…" : "Sign in"}
      </Button>

      {/* Seam spec 026 vs spec 015: "Join your organization" crea un USUARIO
          dentro de una org existente (spec 015 D-AUTH-04); "Create your
          organization" crea la ORGANIZACIÓN nueva (spec 026 /signup). */}
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
        Your organization may require multi-factor authentication — you can
        also set it up any time from the console.
      </p>
    </form>
  );
}
