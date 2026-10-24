import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { BrandingPage } from "./branding-page";

export const metadata: Metadata = {
  title: "Branding — Trust Agent Admin Console",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Branding"
        description="White-label the console and desktop for your organization."
      />
      <BrandingPage />
    </>
  );
}
