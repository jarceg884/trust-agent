/**
 * Change-password panel (spec 015 v1.3.0, D-AUTH-15): voluntary change for a
 * signed-in admin (linked from the user menu). The current password is
 * proven by the control plane with a direct grant before the reset.
 */
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getSessionToken } from "@/lib/session";
import { PasswordForm } from "../set-password/password-form";

export const metadata: Metadata = {
  title: "Change password — Trust Agent Admin Console",
};

export default async function ChangePasswordPage() {
  if (!(await getSessionToken())) redirect("/login");

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
          Change your password
        </h1>
        <p className="mb-8 mt-1.5 text-sm text-muted">
          Confirm your current password and choose a new one. Your other
          sessions stay active.
        </p>
        <PasswordForm mode="change" />
      </div>
    </main>
  );
}
