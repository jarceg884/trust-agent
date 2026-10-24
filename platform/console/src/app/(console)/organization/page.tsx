import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { api } from "@/lib/control-plane";
import { getSessionToken } from "@/lib/session";
import type { WhoAmI } from "@/lib/types";
import { PageHeader } from "@/components/ui";
import { OrganizationPage } from "./organization-page";

export const metadata: Metadata = {
  title: "Organization — Trust Agent Admin Console",
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
        title="Organization"
        description="Identity, budget baseline and organization-level policies."
      />
      <OrganizationPage me={me} />
    </>
  );
}
