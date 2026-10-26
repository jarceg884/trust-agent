/**
 * Sesión del operador del proveedor (spec 014, D-PC-02).
 *
 * El provider token se pega UNA vez en el login, se valida contra el control
 * plane y vive en cookie httpOnly `ta_provider` — jamás en localStorage ni en
 * respuestas de API. El proxy BFF lo reenvía como `Authorization: Bearer`.
 */
import { cookies } from "next/headers";

export const PROVIDER_COOKIE = "ta_provider";

const COOKIE_OPTIONS = {
  httpOnly: true,
  sameSite: "lax" as const,
  secure: false, // local http; HTTPS en despliegue
  path: "/",
};

/** Lee el provider token de la cookie (server-side). */
export async function getProviderToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(PROVIDER_COOKIE)?.value ?? null;
}

/** Fija la cookie de sesión del operador. */
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
