import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { api } from "@/lib/control-plane";
import { getSessionToken } from "@/lib/session";
import type { WhoAmI } from "@/lib/types";
import { PageHeader } from "@/components/ui";
import { MarketplacePage } from "./marketplace-page";

export const metadata: Metadata = {
  title: "Agents",
};

/** Spec 045: Agents = marketplace de perfiles de Hermes (reemplaza a los agentes
 *  compartidos de la spec 037, D-045-08). Ver lo puede un dept_admin; importar,
 *  editar, archivar y descargar paquetes es del org_admin. */
export default async function Page() {
  const session = await getSessionToken();
  if (!session) redirect("/login");
  let me: WhoAmI;
  try {
    me = await api<WhoAmI>("/api/me", { bearer: session });
  } catch {
    redirect("/login");
  }
  const canManage = me.roles.some((r) => r.role_key === "org_admin");
  return (
    <>
      <PageHeader
        title="Agents"
        description="Marketplace de perfiles de Hermes. Importa perfiles exportados con `hermes profile export` y asígnalos en una política: en el escritorio, el agente del empleado es ese perfil."
      />
      <MarketplacePage canManage={canManage} />
    </>
  );
}
