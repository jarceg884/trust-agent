import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { UsersPage } from "./users-page";

export const metadata: Metadata = {
  title: "Users & Roles — Trust Agent Admin Console",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Users & Roles"
        description="Who belongs to the organization and what they can govern."
      />
      <UsersPage />
    </>
  );
}
