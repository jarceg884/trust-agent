import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { CredentialsPage } from "./credentials-page";

export const metadata: Metadata = {
  title: "Credentials",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Credentials"
        description="Encrypted vault and application connectors."
      />
      <CredentialsPage />
    </>
  );
}
