import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { DevicesPage } from "./devices-page";

export const metadata: Metadata = {
  title: "Devices — Trust Agent Admin Console",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Devices"
        description="Desktop enrollment requests, approvals and revocation."
      />
      <DevicesPage />
    </>
  );
}
