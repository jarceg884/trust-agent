import { redirect } from "next/navigation";

/** Raíz: decide entre login y Overview (el shell hace el gate de sesión). */
export default function Home() {
  redirect("/overview");
}
