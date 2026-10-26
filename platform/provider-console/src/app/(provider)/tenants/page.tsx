/**
 * Lista de tenants (spec 014 REQ-PC-011): server component con fetch real a
 * `GET /api/admin/tenants`; estados de carga/error reales, sin mocks.
 */
import Link from "next/link";
import { ApiError, api } from "@/lib/control-plane";
import { getProviderToken } from "@/lib/provider-session";
import type { TenantListResponse } from "@/lib/types";
import { Button, ErrorState, PageHeader } from "@/components/ui";
import { IconPlus } from "@/components/icons";
import { TenantsTable } from "./tenants-table";

export default async function TenantsPage() {
  const token = await getProviderToken();

  let payload: TenantListResponse | null = null;
  let error: { status: number; reason: string; message: string } | null = null;
  try {
    payload = await api<TenantListResponse>("/api/admin/tenants", {
      bearer: token,
    });
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
        message: "Unexpected error loading tenants.",
      };
    }
  }

  return (
    <>
      <PageHeader
        title="Tenants"
        description="Every customer organization deployed on this platform."
        actions={
          <Link href="/tenants/new">
            <Button variant="primary" size="sm">
              <IconPlus size={13} /> Provision new client
            </Button>
          </Link>
        }
      />
      {error ? (
        <ErrorState error={error} context="GET /api/admin/tenants" />
      ) : payload ? (
        <TenantsTable tenants={payload.tenants} />
      ) : null}
    </>
  );
}
