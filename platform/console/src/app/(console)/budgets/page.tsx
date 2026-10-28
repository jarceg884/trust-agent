import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { BudgetsPage } from "./budgets-page";

export const metadata: Metadata = {
  title: "Budgets",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Budgets"
        description="Hierarchical budgets with cutoffs: organization → departments → users."
      />
      <BudgetsPage />
    </>
  );
}
