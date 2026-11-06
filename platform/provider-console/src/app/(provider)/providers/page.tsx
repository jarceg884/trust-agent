/**
 * Sección "Model Providers" del Provider Console (spec 036 + 043, mandato
 * Miller): tarjetas estilo Dify con test connection real, gating BYOK por
 * cliente. Server component: fetch real a GET /api/admin/model-providers +
 * GET /api/admin/tenants + GET /api/admin/model-providers/byok-policy.
 */
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ApiError, api } from "@/lib/control-plane";
import { getProviderToken } from "@/lib/provider-session";
import type {
  ByokPolicyEntry,
  ModelProviderListResponse,
  TenantListResponse,
} from "@/lib/types";
import { ProvidersClient } from "./providers-client";

export const metadata: Metadata = {
  title: "Model Providers — Trust Agent Provider Console",
};

export default async function ModelProvidersPage() {
  const token = await getProviderToken();
  if (!token) redirect("/login");

  let providers: ModelProviderListResponse["providers"] = [];
  let tenants: { id: string; slug: string; name: string; status: string }[] = [];
  let byokPolicy: ByokPolicyEntry[] = [];
  let error: { status: number; reason: string; message: string } | null = null;
  try {
    const [list, tenantList, byokRes] = await Promise.all([
      api<ModelProviderListResponse>("/api/admin/model-providers", {
        bearer: token,
      }),
      api<TenantListResponse>("/api/admin/tenants?status=active", {
        bearer: token,
      }),
      api<{ tenants: ByokPolicyEntry[] }>(
        "/api/admin/model-providers/byok-policy",
        { bearer: token },
      ).catch(() => ({ tenants: [] as ByokPolicyEntry[] })),
    ]);
    providers = list.providers;
    tenants = tenantList.tenants.map((t) => ({
      id: t.id,
      slug: t.slug,
      name: t.name,
      status: t.status,
    }));
    byokPolicy = byokRes.tenants;
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 401) redirect("/login");
    error =
      cause instanceof ApiError
        ? {
            status: cause.status,
            reason: cause.reason,
            message: cause.message,
          }
        : {
            status: 503,
            reason: "unreachable",
            message: "The control plane is unreachable.",
          };
  }

  return (
    <ProvidersClient
      initialProviders={providers}
      tenants={tenants}
      byokPolicy={byokPolicy}
      error={error}
    />
  );
}
