import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { ModelsOverviewPage } from "./models-overview";

export const metadata: Metadata = {
  title: "Models",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Models"
        description="Proveedores de IA y catálogo. El proveedor del SaaS viene por defecto; los modelos se habilitan por política."
      />
      <ModelsOverviewPage />
    </>
  );
}
