"use client";

/**
 * Banner MFA persistente (spec 015 D-AUTH-08, AC-AUTH-003/004).
 *
 * Visible en TODAS las páginas del shell mientras la sesión no tenga MFA
 * verificado (acr < 2) y la org no lo exija por policy — la app SIEMPRE
 * sugiere enrolarlo. "Set up MFA" reinicia el baile con acr=2: el flujo LoA
 * del realm muestra el formulario OTP (enrolamiento TOTP inline si el usuario
 * aún no tiene credencial) y al volver el mint marca mfa_verified=true →
 * el banner desaparece.
 */
import { ShieldCheck } from "lucide-react";
import type { WhoAmI } from "@/lib/types";

export function MfaBanner({ me }: { me: WhoAmI }) {
  if (me.mfa_verified) return null;

  const org = me.tenant_slug;
  return (
    <div
      role="status"
      aria-label="Multi-factor authentication suggestion"
      className="mb-4 flex flex-col gap-3 rounded-xl border border-warning/30 bg-warning-soft px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-start gap-3">
        <ShieldCheck
          className="mt-0.5 size-5 shrink-0 text-warning"
          strokeWidth={1.75}
          aria-hidden="true"
        />
        <div>
          <p className="text-sm font-semibold text-foreground">
            Protect your account
          </p>
          <p className="mt-0.5 text-xs leading-relaxed text-muted">
            Add multi-factor authentication (TOTP) to secure your sessions.
            It takes less than a minute.
          </p>
        </div>
      </div>
      <a
        href={`/auth/login?org=${encodeURIComponent(org)}&acr=2`}
        className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg bg-foreground px-4 py-2 text-xs font-semibold text-white transition-all duration-150 ease-out hover:opacity-90 active:scale-[0.98]"
      >
        Set up MFA
      </a>
    </div>
  );
}
