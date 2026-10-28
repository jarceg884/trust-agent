/**
 * Sesión del operador del proveedor (spec 025, D-PS-04).
 *
 * El login usuario+contraseña devuelve un token de sesión opaco
 * `pst1.<session_id>.<secret>` mintado por el control plane (vive en
 * PostgreSQL, `ops.provider_sessions` — nada en memoria). El BFF lo guarda
 * en la cookie httpOnly `ta_provider` — jamás en localStorage ni expuesto
 * a JS del navegador. El proxy BFF lo reenvía como `Authorization: Bearer`.
 */
import { cookies } from "next/headers";

export const PROVIDER_COOKIE = "ta_provider";

const COOKIE_OPTIONS = {
  httpOnly: true,
  sameSite: "lax" as const,
  secure: false, // local http; HTTPS en despliegue
  path: "/",
};

/** Lee el token de sesión del operador de la cookie (server-side). */
export async function getProviderToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(PROVIDER_COOKIE)?.value ?? null;
}

/** Fija la cookie de sesión del operador (token pst1… del login). */
export async function setProviderCookie(token: string): Promise<void> {
  const store = await cookies();
  store.set(PROVIDER_COOKIE, token, {
    ...COOKIE_OPTIONS,
    maxAge: 8 * 3600,
  });
}

/** Borra la cookie (logout). */
export async function clearProviderCookie(): Promise<void> {
  const store = await cookies();
  store.set(PROVIDER_COOKIE, "", { ...COOKIE_OPTIONS, maxAge: 0 });
}
