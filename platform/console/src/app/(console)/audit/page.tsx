import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { AuditPage } from "./audit-page";

export const metadata: Metadata = {
  title: "Audit Log",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Audit Log"
        description="Immutable, hash-chained record of every governed action."
      />
      <AuditPage />
    </>
  );
}
