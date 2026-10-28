import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import { ModelsPage } from "./models-page";

export const metadata: Metadata = {
  title: "Model Access",
};

export default function Page() {
  return (
    <>
      <PageHeader
        title="Model Access"
        description="Approved models per level — central gateway or BYOK."
      />
      <ModelsPage />
    </>
  );
}
