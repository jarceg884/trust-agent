import { redirect } from "next/navigation";

export const metadata = { title: "Vault" };

export default function Page() {
  redirect("/vault");
}
