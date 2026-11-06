import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { AgentsPage } from "./agents-page";

export const metadata: Metadata = {
  title: "Shared Agents",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Agents"
        description="Crea agentes compartidos y luego inclúyelos en una política. El acceso se define en la política, nunca implícito."
      />
      <AgentsPage />
    </>
  );
}
