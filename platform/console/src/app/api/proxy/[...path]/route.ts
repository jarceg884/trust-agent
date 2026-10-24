/**
 * Proxy BFF (spec 009 REQ-AC-003, D-AC-01).
 *
 * TODO el tráfico de datos del navegador pasa por aquí. El proxy:
 *   - reenvía método/query/body al control plane,
 *   - adjunta la sesión como cookie `trust_session` (del control plane),
 *   - adjunta X-CSRF-Token en mutaciones (lo valida el control plane contra
 *     el hash de la sesión — REQ-SEC-010; el proxy NO relaja ese check),
 *   - sobre 401 con refresh disponible: rota el refresh UNA vez
 *     (POST /api/auth/refresh), actualiza las cookies y reintenta,
 *   - pasa el envelope de errores §5.2 y el content-type tal cual
 *     (incluye text/csv del export de audit).
 */
import { NextResponse, type NextRequest } from "next/server";
import { config } from "@/lib/config";
import {
  COOKIE,
  getRefreshToken,
  getSessionToken,
} from "@/lib/session";

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

interface RefreshResult {
  session_token: string;
  refresh_token: string;
}

async function upstream(
  path: string,
  search: string,
  method: string,
  sessionCookie: string | null,
  csrfToken: string | null,
  body: ArrayBuffer | null,
  contentType: string | null,
): Promise<Response> {
  const headers: Record<string, string> = {
    "User-Agent": "trust-agent-console/0.1.0",
  };
  if (sessionCookie) headers.Cookie = `trust_session=${sessionCookie}`;
  if (contentType) headers["Content-Type"] = contentType;
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;

  return fetch(`${config.apiBaseUrl}${path}${search}`, {
    method,
    headers,
    body: body ?? undefined,
    cache: "no-store",
  });
}

async function tryRefresh(
  refreshToken: string,
): Promise<RefreshResult | null> {
  try {
    const res = await fetch(`${config.apiBaseUrl}/api/auth/refresh`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "User-Agent": "trust-agent-console/0.1.0",
      },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as RefreshResult;
  } catch {
    return null;
  }
}

function cookieOptions(maxAge: number) {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: false, // local http; HTTPS en despliegue
    path: "/",
    maxAge,
  };
}

async function handle(
  request: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) {
  const { path: segments } = await ctx.params;
  // El frontend pasa paths completos ("/api/audit/events") y los smokes pasan
  // paths relativos ("/me"). Normalizar: si el primer segmento ya es "api",
  // no lo duplicar (bug real: /api/api/* → 404 en todas las páginas de datos).
  const segs = segments[0] === "api" ? segments.slice(1) : segments;
  const path = `/api/${segs.map(encodeURIComponent).join("/")}`;
  const search = new URL(request.url).search;

  const session = await getSessionToken();
  const refresh = await getRefreshToken();

  const contentType = request.headers.get("content-type");
  const body =
    request.method === "GET" || request.method === "HEAD"
      ? null
      : await request.arrayBuffer();

  // CSRF double-submit estricto: SOLO el header del navegador (que lo lee de
  // la cookie ta_csrf legible por JS). Sin fallback a la cookie: un atacante
  // cross-site no puede leer la cookie ni setear headers custom, y el control
  // plane valida el hash (REQ-SEC-010).
  const csrf = MUTATING.has(request.method)
    ? request.headers.get("x-csrf-token")
    : null;

  let res: Response;
  try {
    res = await upstream(
      path,
      search,
      request.method,
      session,
      csrf,
      body,
      contentType,
    );
  } catch {
    return NextResponse.json(
      {
        error: {
          code: 503,
          reason: "unreachable",
          message:
            "The Trust Agent control plane is unreachable. Check your connection and try again.",
          request_id: null,
        },
      },
      { status: 503 },
    );
  }

  // 401 con refresh disponible → rotar UNA vez y reintentar (AC-AC-003).
  if (res.status === 401 && refresh && path !== "/api/auth/refresh") {
    const rotated = await tryRefresh(refresh);
    if (rotated) {
      try {
        res = await upstream(
          path,
          search,
          request.method,
          rotated.session_token,
          csrf,
          body,
          contentType,
        );
        if (res.status !== 401) {
          const out = new NextResponse(await res.arrayBuffer(), {
            status: res.status,
            headers: {
              "Content-Type":
                res.headers.get("content-type") ?? "application/json",
            },
          });
          out.cookies.set(COOKIE.session, rotated.session_token, cookieOptions(8 * 3600));
          out.cookies.set(COOKIE.refresh, rotated.refresh_token, cookieOptions(8 * 3600));
          return out;
        }
      } catch {
        // reintento falló de red → devolver el 401 original abajo
      }
    }
  }

  const headers = new Headers();
  const resCt = res.headers.get("content-type");
  if (resCt) headers.set("Content-Type", resCt);
  headers.set("Cache-Control", "no-store");
  return new NextResponse(await res.arrayBuffer(), {
    status: res.status,
    headers,
  });
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const DELETE = handle;

export const runtime = "nodejs";
