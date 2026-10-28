/**
 * Detalle de tenant (spec 014 REQ-PC-013/014/017): estado canónico, counts,
 * presupuesto/gasto, healthcheck en vivo, suspend/reactivate con confirmación,
 * link a la Admin Console del tenant (D-PC-07). Sin download link —
 * corrección Miller t_360a752e (vive en el Admin Console del cliente).
 */
import { ApiError, api } from "@/lib/control-plane";
import { getProviderToken } from "@/lib/provider-session";
import type { TenantListItem, TenantListResponse } from "@/lib/types";
import { ErrorState, PageHeader } from "@/components/ui";
import { TenantDetail } from "./tenant-detail";

export default async function TenantDetailPage({
  params,
}: PageProps<"/tenants/[slug]">) {
  const { slug } = await params;
  const token = await getProviderToken();

  let tenant: TenantListItem | null = null;
  let error: { status: number; reason: string; message: string } | null = null;
  try {
    const payload = await api<TenantListResponse>("/api/admin/tenants", {
      bearer: token,
    });
    tenant = payload.tenants.find((t) => t.slug === slug) ?? null;
    if (!tenant) {
      error = {
        status: 404,
        reason: "not_found",
        message: `Tenant "${slug}" not found.`,
      };
    }
  } catch (cause) {
    if (cause instanceof ApiError) {
      error = {
        status: cause.status,
        reason: cause.reason,
        message: cause.message,
      };
    } else {
      error = {
        status: 500,
        reason: "unknown",
        message: "Unexpected error loading the tenant.",
      };
    }
  }

  if (error || !tenant) {
    return (
      <>
        <PageHeader title="Tenant" description={slug} />
        <ErrorState
          error={error ?? { status: 404, reason: "not_found", message: "Not found." }}
          context="GET /api/admin/tenants"
        />
      </>
    );
  }

  return <TenantDetail tenant={tenant} />;
}
