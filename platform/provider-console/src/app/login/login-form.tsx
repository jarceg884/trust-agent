"use client";

/**
 * Login del operador (spec 025, REQ-PS-003/004/008): usuario+contraseña y,
 * si el operador tiene MFA activa, el código TOTP. El BFF valida contra el
 * control plane y guarda el token de sesión en cookie httpOnly.
 *
 * - 401 `mfa_code_required` ⇒ se muestra el campo TOTP (prompt de UX, no
 *   un error de credencial).
 * - 200 con `must_change_password` ⇒ redirect al required action
 *   (/change-password); si no, a /tenants.
 */
import { useState } from "react";

interface LoginOk {
  ok: true;
  operator: { username: string; must_change_password: boolean; mfa_enrolled: boolean };
}

export function LoginForm({ initialError }: { initialError?: string }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [needsTotp, setNeedsTotp] = useState(false);
  const [error, setError] = useState<string | null>(
    initialError ? decodeURIComponent(initialError) : null,
  );
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password || loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/auth/provider/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: username.trim(),
          password,
          ...(totpCode.trim() ? { totp_code: totpCode.trim() } : {}),
        }),
      });
      if (res.ok) {
        const body = (await res.json()) as LoginOk;
        // Navegación full-page INTENCIONAL: la cookie httpOnly queda en el
        // documento y el gate del layout debe re-evaluarse desde cero.
        window.location.href = body.operator.must_change_password
          ? "/change-password"
          : "/tenants";
        return;
      }
      let message = `Sign-in failed (HTTP ${res.status}).`;
      let reason = "";
      try {
        const body = (await res.json()) as { message?: string; reason?: string };
        if (body.message) message = body.message;
        if (body.reason) reason = body.reason;
      } catch {
        // no-JSON
      }
      if (reason === "mfa_code_required") {
        setNeedsTotp(true);
        setError("Your account has MFA enabled. Enter the 6-digit code from your authenticator app.");
      } else {
        setError(reason ? `${message} (${reason})` : message);
      }
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
          htmlFor="username"
          className="mb-1.5 block text-sm font-medium text-foreground"
        >
          Username
        </label>
        <input
          id="username"
          name="username"
          type="text"
          autoComplete="username"
          autoFocus
          spellCheck={false}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="w-full rounded-lg border border-border-subtle bg-surface px-3.5 py-2.5 text-sm text-foreground placeholder:text-faint focus:border-accent focus:outline-none"
        />
      </div>
      <div>
        <label
          htmlFor="password"
          className="mb-1.5 block text-sm font-medium text-foreground"
        >
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-lg border border-border-subtle bg-surface px-3.5 py-2.5 text-sm text-foreground placeholder:text-faint focus:border-accent focus:outline-none"
        />
      </div>
      <div className={needsTotp ? "block" : "hidden"}>
        <label
          htmlFor="totp"
          className="mb-1.5 block text-sm font-medium text-foreground"
        >
          Authenticator code (MFA)
        </label>
        <input
          id="totp"
          name="totp"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          spellCheck={false}
          placeholder="6-digit code"
          value={totpCode}
          onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, ""))}
          className="w-full rounded-lg border border-border-subtle bg-surface px-3.5 py-2.5 font-mono text-sm tracking-[0.3em] text-foreground placeholder:tracking-normal placeholder:text-faint focus:border-accent focus:outline-none"
        />
      </div>
      <button
        type="submit"
        disabled={!username.trim() || !password || loading}
        className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white transition-all duration-150 ease-out hover:bg-accent-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
      >
        {loading ? "Signing in…" : "Sign in"}
      </button>
      <p className="text-xs leading-relaxed text-muted">
        The session token is stored in an httpOnly cookie — it is never
        exposed to page scripts. Failed attempts are rate-limited.
      </p>
    </form>
  );
}
