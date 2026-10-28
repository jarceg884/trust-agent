/**
 * Proxy BFF (spec 014, D-PC-02).
 *
 * TODO el tráfico de datos del navegador pasa por aquí. El proxy:
 *   - reenvía método/query/body al control plane,
 *   - adjunta el provider token de la cookie httpOnly como
 *     `Authorization: Bearer`,
 *   - pasa el envelope de errores §5.2 y el content-type tal cual.
 *
 * Sin cookie → 401 JSON. Sin lógica de refresh (el provider token no rota).
 * Sin CSRF header: el control plane autentica por header Authorization, no
 * por cookie ambiental — un cross-site no puede disparar estas rutas.
 */
import { NextResponse, type NextRequest } from "next/server";
import { config } from "@/lib/config";
import { getProviderToken } from "@/lib/provider-session";

async function handle(
  request: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) {
  const { path: segments } = await ctx.params;
  // Normalizar: si el primer segmento ya es "api", no lo duplicar.
  const segs = segments[0] === "api" ? segments.slice(1) : segments;
  const path = `/api/${segs.map(encodeURIComponent).join("/")}`;
  const search = new URL(request.url).search;

  const token = await getProviderToken();
  if (!token) {
    return NextResponse.json(
      {
        error: {
          code: 401,
          reason: "invalid_token",
          message: "Provider session not found. Sign in again.",
          request_id: null,
        },
      },
      { status: 401 },
    );
  }

  const contentType = request.headers.get("content-type");
  const body =
    request.method === "GET" || request.method === "HEAD"
      ? null
      : await request.arrayBuffer();

  const headers: Record<string, string> = {
    "User-Agent": "trust-agent-provider-console/0.1.0",
    Authorization: `Bearer ${token}`,
  };
  if (contentType) headers["Content-Type"] = contentType;

  let res: Response;
  try {
    res = await fetch(`${config.apiBaseUrl}${path}${search}`, {
      method: request.method,
      headers,
      body: body ?? undefined,
      cache: "no-store",
    });
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

  const outHeaders = new Headers();
  const resCt = res.headers.get("content-type");
  if (resCt) outHeaders.set("Content-Type", resCt);
  outHeaders.set("Cache-Control", "no-store");
  return new NextResponse(await res.arrayBuffer(), {
    status: res.status,
    headers: outHeaders,
  });
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const DELETE = handle;

export const runtime = "nodejs";
