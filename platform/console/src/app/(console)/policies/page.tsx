import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { api } from "@/lib/control-plane";
import { getSessionToken } from "@/lib/session";
import type { WhoAmI } from "@/lib/types";
import { PageHeader } from "@/components/ui";
import { PoliciesPage } from "./policies-page";

export const metadata: Metadata = {
  title: "Policies — Trust Agent Admin Console",
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
        description="Every policy as a plain-language card you can apply to the organization, a department or a user. Lower levels can only tighten (deny-overrides)."
      />
      <PoliciesPage me={me} />
    </>
  );
}
