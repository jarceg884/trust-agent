import { redirect } from "next/navigation";

export const metadata = { title: "Policies" };

export default function Page() {
  redirect("/policies");
}
