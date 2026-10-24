import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { AgentsPage } from "./agents-page";

export const metadata: Metadata = {
  title: "Shared Agents — Trust Agent Admin Console",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Shared Agents"
        description="Organization agents with explicit, auditable access grants."
      />
      <AgentsPage />
    </>
  );
}
