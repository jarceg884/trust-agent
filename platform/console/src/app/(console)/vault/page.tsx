import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { VaultPage } from "./vault-page";

export const metadata: Metadata = {
  title: "Vault",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Vault"
        description="Secretos cifrados de la organización, con rotación y acceso controlado por política."
      />
      <VaultPage />
    </>
  );
}
