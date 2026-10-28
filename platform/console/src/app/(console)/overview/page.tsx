import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { OverviewPage } from "./overview-page";

export const metadata: Metadata = {
  title: "Overview",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Governance overview"
        description="One organization. Controlled access for every department."
      />
      <OverviewPage />
    </>
  );
}
