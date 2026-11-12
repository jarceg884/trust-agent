/**
 * Public edge: LiteLLM's OpenAI-compatible inference routes under /llm/** (virtual-key
 * auth, SSE streamed through). Desktops receive `{public origin}/llm` as their model
 * base URL in desktop-config. LiteLLM's admin API and UI are never reachable here.
 */
import type { NextRequest } from "next/server";
import { config } from "@/lib/config";
import { edgeNotFound, relay } from "@/lib/edge-proxy";
import { isLlmRoute, LLM_REQUEST_HEADERS } from "@/lib/edge-routes";

async function handle(request: NextRequest): Promise<Response> {
  const { pathname, search } = request.nextUrl;
  const subpath = pathname.replace(/^\/llm\/?/, "");
  if (!isLlmRoute(request.method, subpath)) return edgeNotFound();
  return relay(request, `${config.litellmInternalUrl}/${subpath}${search}`, LLM_REQUEST_HEADERS);
}

export const GET = handle;
export const POST = handle;

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
