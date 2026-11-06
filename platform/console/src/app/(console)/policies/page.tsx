import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { api } from "@/lib/control-plane";
import { getSessionToken } from "@/lib/session";
import type { WhoAmI } from "@/lib/types";
import { PageHeader } from "@/components/ui";
import { PolicyManagerPage } from "./policy-manager";

export const metadata: Metadata = {
  title: "Policies",
};

export default async function Page() {
  const session = await getSessionToken();
  if (!session) redirect("/login");
  let me: WhoAmI;
  try {
    me = await api<WhoAmI>("/api/me", { bearer: session });
  } catch {
    redirect("/login");
  }
  return (
    <>
      <PageHeader
        title="Policies"
        description="Una política se asigna a un departamento o a una persona. Dentro defines Desktop restrictions, prompt organizacional, modelos y agentes."
      />
      <PolicyManagerPage />
    </>
  );
}
