/**
 * Dashboard del usuario final (REQ-DTB-004, D-DTB-06) — sesión gobernada.
 *
 * Todo es dato REAL del control plane (spec 003/005/007): identidad, presupuesto
 * por nivel con corte, políticas efectivas, modelos aprobados, dispositivos y
 * notificaciones. Sin sesión → login; error de API → estado de error con
 * request_id (REQ-DTB-007). Cero datos inventados (constitution I).
 */
import { redirect } from "next/navigation";
import {
  BudgetCard,
  DevicesCard,
  IdentityHeader,
  ModelsCard,
  NotificationsCard,
  PoliciesCard,
} from "@/components/dashboard";
import { Sidebar } from "@/components/sidebar";
import { ErrorState } from "@/components/ui";
import {
  ApiError,
  api,
  type Branding,
  type BudgetStatus,
  type EffectivePolicy,
  type GovernanceNotification,
  type MyDevice,
  type WhoAmI,
} from "@/lib/control-plane";
import { getSessionToken } from "@/lib/session";
import { markNotificationRead, signOut } from "./actions";

export default async function DashboardPage() {
  const token = await getSessionToken();
  if (!token) redirect("/");

  // Identidad primero: valida la sesión (401 → login).
  let me: WhoAmI;
  try {
    me = await api<WhoAmI>("/api/me", { bearer: token });
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 401) redirect("/");
    return <DashboardError cause={cause} />;
  }

  // Resto del estado gobernado en paralelo (cada tarjeta degrada sola).
  // El branding del tenant se lee en runtime tras el login (REQ-BRD-002).
  const [budget, policies, models, devices, notifications, branding] =
    await Promise.all([
      api<BudgetStatus>("/api/me/budget", { bearer: token }).catch(() => null),
      api<EffectivePolicy>("/api/me/policies", { bearer: token }).catch(
        () => null,
      ),
      api<{ models: string[] }>("/api/models/allowed-for-me", {
        bearer: token,
      })
        .then((r) => r.models)
        .catch(() => null),
      api<{ devices: MyDevice[] }>("/api/devices/me", { bearer: token })
        .then((r) => r.devices)
        .catch(() => null),
      api<{ notifications: GovernanceNotification[] }>(
        "/api/me/notifications",
        {
          bearer: token,
          // unread_only=false: la tarjeta muestra histórico + no leídas.
        },
      )
        .then((r) => r.notifications)
        .catch(() => null),
      api<Branding>("/api/branding", { bearer: token }).catch(() => null),
    ]);

  const hasActiveDevice = (devices ?? []).some((d) => d.status === "active");

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <Sidebar
        userName={me.display_name ?? me.email}
        userEmail={me.email}
        orgSlug={me.tenant_slug}
        signOutAction={signOut}
        branding={branding}
      />

      <main className="flex-1 overflow-y-auto px-6 py-8 md:px-10">
        <div className="mx-auto max-w-4xl space-y-6">
          <IdentityHeader me={me} />

          {!hasActiveDevice && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#3a2c14] bg-[#221a0e] px-5 py-4">
              <p className="text-sm text-[#e0a52e]">
                This device is not enrolled yet. Enroll it to use governed agent
                sessions on this machine.
              </p>
              <a
                href="/enroll"
                className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white transition hover:brightness-110"
              >
                Enroll this device
              </a>
            </div>
          )}

          <div className="grid gap-6 lg:grid-cols-2">
            {budget ? (
              <BudgetCard budget={budget} />
            ) : (
              <CardError what="budget" />
            )}
            {policies ? (
              <PoliciesCard policies={policies} />
            ) : (
              <CardError what="policies" />
            )}
            {models ? (
              <ModelsCard models={models} />
            ) : (
              <CardError what="approved models" />
            )}
            {devices ? (
              <DevicesCard devices={devices} thisDeviceFingerprint={null} />
            ) : (
              <CardError what="devices" />
            )}
            {notifications ? (
              <NotificationsCard
                notifications={notifications}
                markReadAction={markNotificationRead}
              />
            ) : (
              <CardError what="notifications" />
            )}
          </div>

          <p className="pt-2 text-center text-xs text-muted">
            Governed session — only organization-approved models, budgets and
            policies apply. All activity is audited.
          </p>
        </div>
      </main>
    </div>
  );
}

function CardError({ what }: { what: string }) {
  return (
    <section className="rounded-xl border border-border-subtle bg-panel p-5">
      <ErrorState
        message={`Could not load your ${what}. The control plane returned an error.`}
      />
    </section>
  );
}

function DashboardError({ cause }: { cause: unknown }) {
  const message =
    cause instanceof ApiError
      ? cause.message
      : "The Trust Agent control plane is unreachable.";
  const requestId = cause instanceof ApiError ? cause.requestId : null;
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-md">
        <ErrorState message={message} requestId={requestId} />
        <a
          href="/"
          className="mt-4 block text-center text-sm text-muted underline underline-offset-4 hover:text-foreground"
        >
          Back to sign in
        </a>
      </div>
    </main>
  );
}
