"use client";

/**
 * Password panel form (spec 015 v1.3.0, D-AUTH-15 — AC-AUTH-015).
 *
 * mode="set"    — first login (UPDATE_PASSWORD): the one-time password was
 *                 already proven by the grant outcome; only the new password
 *                 is asked (the control plane re-checks the pending action).
 * mode="change" — voluntary change: the current password is required and the
 *                 control plane proves it with a direct grant before
 *                 resetting (generic 401 otherwise — no oracle).
 */
import { useState } from "react";
import { panelErrorMessage } from "@/lib/panel";
import { Button } from "@/components/ui";

export function PasswordForm({ mode }: { mode: "set" | "change" }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [totp, setTotp] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (next.length < 12) {
      setError("The new password must be at least 12 characters.");
      return;
    }
    if (next !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/auth/panel/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: current,
          new_password: next,
          confirm,
          totp: totp.trim() || null,
        }),
      });
      const data = (await res.json()) as {
        status: string;
        reason?: string;
        message?: string;
        redirect?: string | null;
      };
      if (res.ok && data.status === "ok") {
        window.location.href = data.redirect || "/overview";
        return;
      }
      setError(
        panelErrorMessage(
          data.reason ?? "error",
          data.message ?? "The password could not be updated.",
        ),
      );
    } catch {
      setError("The console is unreachable. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  const inputClass =
    "w-full rounded-lg border border-border-subtle bg-surface px-3.5 py-2.5 text-sm text-foreground transition-all duration-150 placeholder:text-muted hover:border-foreground/25 focus:border-foreground/60 focus:outline-none focus:ring-[3px] focus:ring-foreground/[0.12]";

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
      {mode === "change" ? (
        <div>
          <label
            htmlFor="current"
            className="mb-2 block text-sm font-medium text-foreground"
          >
            Current password
          </label>
          <input
            id="current"
            type="password"
            autoComplete="current-password"
            required
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            className={inputClass}
          />
        </div>
      ) : null}
      <div>
        <label
          htmlFor="new"
          className="mb-2 block text-sm font-medium text-foreground"
        >
          New password
        </label>
        <input
          id="new"
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          value={next}
          onChange={(e) => setNext(e.target.value)}
          className={inputClass}
        />
        <p className="mt-1.5 text-xs text-muted">
          At least 12 characters. Use a phrase you can remember.
        </p>
      </div>
      <div>
        <label
          htmlFor="confirm"
          className="mb-2 block text-sm font-medium text-foreground"
        >
          Confirm new password
        </label>
        <input
          id="confirm"
          type="password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          className={inputClass}
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
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={8}
          value={totp}
          onChange={(e) => setTotp(e.target.value)}
          className={inputClass}
          placeholder="6-digit code"
        />
      </div>
      <Button type="submit" variant="primary" className="w-full py-2.5" disabled={busy}>
        {busy ? "Updating…" : mode === "set" ? "Set password and continue" : "Change password"}
      </Button>
    </form>
  );
}
