/**
 * Lista de tenants (spec 014 REQ-PC-011 + spec 026 REQ-SS-006): server
 * component con fetch real a `GET /api/admin/tenants`; estados de
 * carga/error reales, sin mocks. Filtro por estado — la cola de
 * `pending_activation` (self-signup público) con contador de pendientes.
 */
import Link from "next/link";
import { ApiError, api } from "@/lib/control-plane";
import { getProviderToken } from "@/lib/provider-session";
import type { TenantListResponse } from "@/lib/types";
import { Badge, Button, ErrorState, PageHeader } from "@/components/ui";
import { IconPlus } from "@/components/icons";
import { TenantsTable } from "./tenants-table";

const STATUS_FILTERS = [
  { key: "", label: "All" },
  { key: "pending_activation", label: "Pending activation" },
  { key: "active", label: "Active" },
  { key: "suspended", label: "Suspended" },
] as const;

export default async function TenantsPage({
  searchParams,
}: PageProps<"/tenants">) {
  const token = await getProviderToken();
  const params = await searchParams;
  const status =
    typeof params.status === "string" && params.status !== ""
      ? params.status
      : null;

  let payload: TenantListResponse | null = null;
  let pendingCount = 0;
  let error: { status: number; reason: string; message: string } | null = null;
  try {
    const query = status ? `?status=${encodeURIComponent(status)}` : "";
    payload = await api<TenantListResponse>(`/api/admin/tenants${query}`, {
      bearer: token,
    });
    if (!status) {
      // Contador de la cola de activación (spec 026): siempre visible.
      pendingCount = payload.tenants.filter(
        (t) => t.status === "pending_activation",
      ).length;
    } else {
      pendingCount = status === "pending_activation" ? payload.tenants.length : 0;
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
          <div className="flex items-center gap-3">
            {pendingCount > 0 ? (
              <Link href="/tenants?status=pending_activation">
                <Badge tone="warning">
                  {pendingCount} pending activation
                </Badge>
              </Link>
            ) : null}
            <Link href="/tenants/new">
              <Button variant="primary" size="sm">
                <IconPlus size={13} /> Provision new client
              </Button>
            </Link>
          </div>
        }
      />

      {/* Filtro por estado (spec 026 REQ-SS-006) */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {STATUS_FILTERS.map((f) => {
          const active = (status ?? "") === f.key;
          return (
            <Link
              key={f.key || "all"}
              href={f.key ? `/tenants?status=${f.key}` : "/tenants"}
              className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors duration-150 ${
                active
                  ? "border-accent-border bg-accent-soft text-accent"
                  : "border-border-subtle bg-surface text-muted hover:border-accent hover:text-accent"
              }`}
            >
              {f.label}
            </Link>
          );
        })}
      </div>

      {error ? (
        <ErrorState error={error} context="GET /api/admin/tenants" />
      ) : payload ? (
        <TenantsTable tenants={payload.tenants} />
      ) : null}
    </>
  );
}
