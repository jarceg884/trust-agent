/**
 * Provisioning de nuevo cliente (spec 014 REQ-PC-012): formulario que llama
 * al `POST /api/admin/tenants` REAL (flujo idiom-proof de spec 004) y muestra
 * el resultado con el panel OTA one-time (D-PC-06).
 */
import { PageHeader } from "@/components/ui";
import { ProvisionForm } from "./provision-form";

export const metadata = {
  title: "Provision new client — Trust Agent Provider Console",
};

export default function NewTenantPage() {
  return (
    <>
      <PageHeader
        title="Provision new client"
        description="Creates a complete tenant: Keycloak realm, initial admin, seed departments, org budget, default policies and approved models."
      />
      <ProvisionForm />
    </>
  );
}
