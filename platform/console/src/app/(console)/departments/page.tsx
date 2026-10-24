import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { DepartmentsPage } from "./departments-page";

export const metadata: Metadata = {
  title: "Departments — Trust Agent Admin Console",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Departments"
        description="Budgets and policies for every department in the organization."
      />
      <DepartmentsPage />
    </>
  );
}
