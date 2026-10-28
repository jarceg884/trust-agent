/**
 * Puerta de activación pendiente (spec 026, REQ-SS-002/005): sesión válida
 * pero el tenant está `pending_activation` — el control plane responde 423
 * tenant_pending_activation a TODA la superficie de negocio. Pantalla
 * dedicada de solo-estado (no el shell) con sign out.
 */
import { IconClock } from "@/components/icons";

export function PendingActivationGate() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-md rounded-xl border border-border-subtle bg-surface p-8 text-center shadow-[0_1px_2px_rgba(16,24,40,0.04)]">
        <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-warning-soft text-warning">
          <IconClock size={22} />
        </div>
        <h1 className="mt-4 text-lg font-semibold text-foreground">
          Your organization is pending activation
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          Your account was created correctly, but nothing is functional yet —
          no agents, models, devices or spend — until the provider activates
          your organization and assigns its initial budget. You will be
          notified as soon as it is ready.
        </p>
        <p className="mt-3 text-xs leading-relaxed text-faint">
          This page is the only thing you can see while your organization is
          pending. Sign in again later to check its status.
        </p>
        <form action="/auth/logout" method="post" className="mt-6">
          <button
            type="submit"
            className="inline-flex w-full items-center justify-center rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.10),0_1px_2px_rgba(16,24,40,0.12)] transition-all duration-150 ease-out hover:bg-accent-hover active:scale-[0.98]"
          >
            Sign out
          </button>
        </form>
      </div>
    </main>
  );
}
