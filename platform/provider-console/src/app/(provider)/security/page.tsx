/**
 * Sección "Security" del Provider Console (spec 025, REQ-PS-009).
 *
 * Server gate: sin cookie → /login; sesión inválida (401) → /login. El
 * estado MFA real se lee del control plane (`/api/provider/auth/me`) —
 * nada se simula. El layout (provider) ya bloquea con /change-password si
 * el required action del primer login sigue pendiente (D-PS-08).
 */
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ApiError, api } from "@/lib/control-plane";
import { getProviderToken } from "@/lib/provider-session";
import { SecurityClient } from "./security-client";

export const metadata: Metadata = {
  title: "Security — Trust Agent Provider Console",
};

interface Me {
  username: string;
  mfa_enrolled: boolean;
}

export default async function SecurityPage() {
  const token = await getProviderToken();
  if (!token) redirect("/login");

  let me: Me | null = null;
  try {
    me = await api<Me>("/api/provider/auth/me", { bearer: token });
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 401) redirect("/login");
    // 503 u otro: el cliente muestra el error real al interactuar.
  }

  return (
    <SecurityClient
      username={me?.username ?? ""}
      mfaEnrolled={me?.mfa_enrolled ?? false}
    />
  );
}
