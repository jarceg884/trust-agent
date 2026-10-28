import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { UsagePage } from "./usage-page";

export const metadata: Metadata = {
  title: "Usage",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Usage"
        description="Requests and spend per user over the real ledger — with live presence, drill-down and CSV export."
      />
      <UsagePage />
    </>
  );
}
