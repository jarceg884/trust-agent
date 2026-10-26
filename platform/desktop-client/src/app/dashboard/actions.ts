"use server";

/**
 * Server actions del dashboard: logout y marcar notificaciones leídas.
 */
import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { api } from "@/lib/control-plane";
import { clearSessionCookies, getSessionToken } from "@/lib/session";

export async function signOut() {
  const token = await getSessionToken();
  if (token) {
    // Revoca la sesión server-side (audit auth.logout en el control plane).
    await api("/api/auth/logout", { method: "POST", bearer: token }).catch(
      () => undefined,
    );
  }
  await clearSessionCookies();
  redirect("/");
}

export async function markNotificationRead(formData: FormData) {
  const token = await getSessionToken();
  const id = String(formData.get("notificationId") ?? "");
  if (!token || !id) return;
  await api(`/api/me/notifications/${id}/read`, {
    method: "POST",
    bearer: token,
  }).catch(() => undefined);
  revalidatePath("/dashboard");
}
