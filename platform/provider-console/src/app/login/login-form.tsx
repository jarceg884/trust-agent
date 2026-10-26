"use client";

/**
 * Form del provider token (spec 014 REQ-PC-010, D-PC-02): pega única del
 * token; POST a /auth/provider/login que valida contra el control plane y
 * fija la cookie httpOnly. El token jamás se guarda en el navegador más allá
 * del valor del input (no localStorage).
 */
import { useState } from "react";

export function LoginForm({ initialError }: { initialError?: string }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(
    initialError ? decodeURIComponent(initialError) : null,
  );
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!token.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/auth/provider/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token.trim() }),
      });
      if (res.ok) {
        // Navegación full-page INTENCIONAL: la cookie httpOnly queda en el
        // documento y el gate del layout debe re-evaluarse desde cero.
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.href = "/tenants";
        return;
      }
      let message = `Sign-in failed (HTTP ${res.status}).`;
      let reason = "";
      try {
        const body = (await res.json()) as {
          message?: string;
          reason?: string;
        };
        if (body.message) message = body.message;
        if (body.reason) reason = ` (${body.reason})`;
      } catch {
        // no-JSON
      }
      setError(`${message}${reason}`);
    } catch {
      setError("The Provider Console server is unreachable. Try again.");
    } finally {
      setLoading(false);
    }
  }

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
        <label
          htmlFor="token"
          className="mb-1.5 block text-sm font-medium text-foreground"
        >
          Provider token
        </label>
        <input
          id="token"
          name="token"
          type="password"
          autoComplete="off"
          autoFocus
          spellCheck={false}
          placeholder="Paste the operations token"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          className="w-full rounded-lg border border-border-subtle bg-surface px-3.5 py-2.5 text-sm text-foreground placeholder:text-faint focus:border-accent focus:outline-none"
        />
        <p className="mt-1.5 text-xs leading-relaxed text-muted">
          The token is validated against the control plane and stored in an
          httpOnly session cookie — it is never exposed to page scripts.
        </p>
      </div>
      <button
        type="submit"
        disabled={!token.trim() || loading}
        className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
      >
        {loading ? "Validating…" : "Sign in"}
      </button>
    </form>
  );
}
