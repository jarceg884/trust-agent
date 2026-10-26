import { redirect } from "next/navigation";
import { getProviderToken } from "@/lib/provider-session";

/** Raíz: al login o a la lista de tenants según sesión (D-PC-02). */
export default async function Home() {
  const token = await getProviderToken();
  redirect(token ? "/tenants" : "/login");
}
