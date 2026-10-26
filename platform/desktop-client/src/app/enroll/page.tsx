/**
 * Pantalla de enrolamiento (REQ-DTB-002/003): generación de keypair local
 * (WebCrypto — la clave privada NUNCA sale del dispositivo), solicitud al
 * control plane y polling del canje hasta la aprobación admin.
 */
import { redirect } from "next/navigation";
import { EnrollFlow } from "./enroll-flow";
import { getSessionToken } from "@/lib/session";

export default async function EnrollPage() {
  const token = await getSessionToken();
  if (!token) redirect("/");
  return <EnrollFlow />;
}
