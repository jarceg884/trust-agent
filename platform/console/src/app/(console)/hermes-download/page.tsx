/**
 * Hermes Download (CORRECCIÓN MILLER, t_360a752e, 2026-10-26).
 *
 * El link de descarga de Hermes por tenant lo genera y envía el ADMIN DEL
 * CLIENTE desde SU Admin Console a sus propios empleados — NO el proveedor
 * (removido del Provider Console en esta misma tarea). Solo roles admin de
 * la org (gate server-side aquí + item adminOnly en el sidebar).
 *
 * El link estable (`GET /download/{tenant_slug}`, spec 023 / t_526030f3)
 * es determinista por tenant: se construye server-side y ya es copiable y
 * enviable. La gestión enable/disable depende del endpoint
 * `GET/PUT /api/download-link` (contrato spec 023, pendiente en el control
 * plane): la UI llama al endpoint real y degrada con un banner honesto
 * mientras no exista — cero estados simulados (constitution I).
 */
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ApiError, api } from "@/lib/control-plane";
import { hermesDownloadUrl } from "@/lib/config";
import { getSessionToken } from "@/lib/session";
import type { WhoAmI } from "@/lib/types";
import { PageHeader } from "@/components/ui";
import { HermesDownloadPage } from "./hermes-download-page";

export const metadata: Metadata = {
  title: "Hermes Download",
};

export default async function Page() {
  // Gate de sesión (el layout ya redirige, pero esta página se protege
  // igual: necesita el principal para el gate de rol y el tenant_slug).
  const session = await getSessionToken();
  if (!session) redirect("/login");

  let me: WhoAmI;
  try {
    me = await api<WhoAmI>("/api/me", { bearer: session });
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 401) redirect("/login");
    throw cause;
  }

  const isAdmin = me.roles.some((r) => r.role_key === "org_admin");

  return (
    <>
      <PageHeader
        title="Hermes Download"
        description="Your organization's stable download link for the Trust Agent desktop app — share it with employees to enroll their devices under your governance."
      />
      <HermesDownloadPage
        isAdmin={isAdmin}
        orgCode={me.tenant_slug}
        downloadUrl={hermesDownloadUrl(me.tenant_slug)}
      />
    </>
  );
}
