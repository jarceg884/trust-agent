"use client";

/**
 * Form del cambio de password del operador (spec 025, REQ-PS-006).
 *
 * POST /api/provider/auth/password vía el proxy BFF (la cookie httpOnly
 * porta la sesión). Política: nueva ≥ 12 chars. Al completarse, las demás
 * sesiones quedan revocadas (D-PS-08) y la UI vuelve a /tenants.
 */
import { useState } from "react";

const MIN_LEN = 12;

export function ChangePasswordForm() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const mismatch = confirm !== "" && newPassword !== confirm;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (loading) return;
    if (newPassword.length < MIN_LEN) {
      setError(`The new password must be at least ${MIN_LEN} characters.`);
      return;
    }
    if (mismatch) {
      setError("The two new passwords do not match.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/proxy/provider/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      if (res.ok) {
        // Full-page INTENCIONAL: el gate del layout re-evalúa el flag y la
        // cookie de sesión se relee limpia (mismo patrón que el logout).
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.href = "/tenants";
        return;
      }
      let message = `Change failed (HTTP ${res.status}).`;
      try {
        const body = (await res.json()) as { error?: { message?: string } };
        if (body.error?.message) message = body.error.message;
      } catch {
        // no-JSON
      }
      setError(message);
    } catch {
      setError("The Provider Console server is unreachable. Try again.");
    } finally {
      setLoading(false);
    }
  }

  const inputClass =
    "w-full rounded-lg border border-border-subtle bg-surface px-3.5 py-2.5 text-sm text-foreground placeholder:text-faint focus:border-accent focus:outline-none";

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger"
        >
          {error}
        </p>
      ) : null}
      <div>
        <label htmlFor="current" className="mb-1.5 block text-sm font-medium text-foreground">
          Current password
        </label>
        <input
          id="current"
          type="password"
          autoComplete="current-password"
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          className={inputClass}
        />
      </div>
      <div>
        <label htmlFor="new" className="mb-1.5 block text-sm font-medium text-foreground">
          New password
        </label>
        <input
          id="new"
          type="password"
          autoComplete="new-password"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          className={inputClass}
        />
        <p className="mt-1.5 text-xs text-muted">
          At least {MIN_LEN} characters. All your other sessions will be
          signed out when the password changes.
        </p>
      </div>
      <div>
        <label htmlFor="confirm" className="mb-1.5 block text-sm font-medium text-foreground">
          Confirm new password
        </label>
        <input
          id="confirm"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          aria-invalid={mismatch || undefined}
          className={inputClass}
        />
        {mismatch ? (
          <p role="alert" className="mt-1 text-xs text-danger">
            The two new passwords do not match.
          </p>
        ) : null}
      </div>
      <button
        type="submit"
        disabled={!currentPassword || !newPassword || !confirm || mismatch || loading}
        className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white transition-all duration-150 ease-out hover:bg-accent-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
      >
        {loading ? "Changing…" : "Change password and continue"}
      </button>
    </form>
  );
}
