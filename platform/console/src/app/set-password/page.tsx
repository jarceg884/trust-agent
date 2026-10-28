/**
 * Set-password panel (spec 015 v1.3.0, D-AUTH-15): first login with the
 * one-time password — already proven by the direct grant outcome, so the
 * user only chooses the new one. 100% product screen (AC-AUTH-015a).
 */
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getPanelPendingCookie } from "@/lib/session";
import { PasswordForm } from "./password-form";

export const metadata: Metadata = {
  title: "Set your password — Trust Agent Admin Console",
};

export default async function SetPasswordPage() {
  const pending = await getPanelPendingCookie();
  if (!pending) redirect("/login");

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
          Welcome — set your password
        </h1>
        <p className="mb-8 mt-1.5 text-sm text-muted">
          This is your first sign-in for{" "}
          <span className="font-medium text-foreground">{pending.email}</span>.
          Choose the password you will use from now on.
        </p>
        <PasswordForm mode="set" />
      </div>
    </main>
  );
}
