/**
 * Puerta MFA (spec 009 REQ-AC-002): sesión válida pero sin MFA verificada —
 * el control plane responde 403 mfa_required a toda la superficie de negocio.
 * Pantalla dedicada (no un error genérico) con re-autenticación.
 */
import { IconShield } from "@/components/icons";

export function MfaGate() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-md rounded-xl border border-border-subtle bg-surface p-8 text-center shadow-[0_1px_2px_rgba(16,24,40,0.04)]">
        <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-accent-soft text-accent">
          <IconShield size={22} />
        </div>
        <h1 className="mt-4 text-lg font-semibold text-foreground">
          Multi-factor authentication required
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          Your session is valid, but this organization requires MFA for all
          administrative access. Sign in again and complete the two-factor
          challenge configured in your identity provider.
        </p>
        <a
          href="/login"
          className="mt-6 inline-flex items-center justify-center rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.10),0_1px_2px_rgba(16,24,40,0.12)] transition-all duration-150 ease-out hover:bg-accent-hover active:scale-[0.98]"
        >
          Re-authenticate
        </a>
      </div>
    </main>
  );
}
