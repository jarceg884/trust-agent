/**
 * MFA setup panel page (spec 015 v1.3.0, D-AUTH-14 — AC-AUTH-014).
 *
 * Entry points:
 *  - from the login panel (enroll_totp): the dance handle cookie is set and
 *    the pending cookie carries org/email — straight to the QR;
 *  - from the persistent banner / MFA gate (signed-in user): org/email are
 *    resolved server-side from the session (/api/me) and the user confirms
 *    their password first.
 * Everything is rendered by the product; the identity engine stays behind
 * the control plane.
 */
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { api, ApiError } from "@/lib/control-plane";
import {
  getPanelDanceCookie,
  getPanelPendingCookie,
  getSessionToken,
} from "@/lib/session";
import type { WhoAmI } from "@/lib/types";
import { MfaSetupClient } from "./mfa-setup-client";

export const metadata: Metadata = {
  title: "Set up multi-factor authentication — Trust Agent Admin Console",
};

export default async function MfaSetupPage() {
  const hasDance = Boolean(await getPanelDanceCookie());
  const pending = await getPanelPendingCookie();

  let org = pending?.org ?? "";
  let email = pending?.email ?? "";
  if (!org || !email) {
    const session = await getSessionToken();
    if (!session) redirect("/login");
    try {
      const me = await api<WhoAmI>("/api/me", { sessionCookie: session });
      org = me.tenant_slug;
      email = me.email;
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 403) {
        // mfa_required gate: the user must enroll to get in — continue with
        // the panel (the password step re-proves identity).
      } else {
        redirect("/login");
      }
    }
  }
  if (!org || !email) redirect("/login");

  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="ta-rise w-full max-w-sm">
        <div className="mb-8 flex items-center gap-2.5">
          <span
            aria-hidden="true"
            className="flex size-7 items-center justify-center rounded-md bg-accent text-[11px] font-bold text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.15)]"
          >
            TA
          </span>
          <span className="text-sm font-semibold tracking-tight text-foreground">
            Trust Agent
          </span>
        </div>
        <h1 className="text-xl font-semibold text-foreground">
          Protect your account
        </h1>
        <p className="mb-8 mt-1.5 text-sm text-muted">
          Add multi-factor authentication with your authenticator app. It
          takes less than a minute and secures every sign-in for{" "}
          <span className="font-medium text-foreground">{email}</span>.
        </p>
        <MfaSetupClient org={org} email={email} hasDance={hasDance} />
      </div>
    </main>
  );
}
