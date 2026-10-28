"use client";

/**
 * Tabla de tenants (spec 014 REQ-PC-011, AC-PC-011): nombre, estado (badge),
 * realm KC, usuarios, presupuesto org (USD), gasto del período (USD) y fecha
 * de creación — datos reales del control plane.
 */
import Link from "next/link";
import type { TenantListItem } from "@/lib/types";
import { usd, dateTime, usagePct } from "@/lib/format";
import {
  Badge,
  Card,
  EmptyState,
  Progress,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { IconOrganization } from "@/components/icons";

function StatusBadge({ status }: { status: TenantListItem["status"] }) {
  // Spec 026: pending_activation = nacido del self-signup público, aún sin
  // activar ni budget — cola de activación del proveedor.
  if (status === "pending_activation") {
    return <Badge tone="warning">Pending activation</Badge>;
  }
  return status === "active" ? (
    <Badge tone="success">Active</Badge>
  ) : (
    <Badge tone="danger">Suspended</Badge>
  );
}

export function TenantsTable({ tenants }: { tenants: TenantListItem[] }) {
  if (tenants.length === 0) {
    return (
      <Card>
        <EmptyState
          icon={<IconOrganization size={20} />}
          title="No tenants yet"
          description="Provision your first customer organization to see it here."
        />
      </Card>
    );
  }

  return (
    <Card>
      <Table>
        <thead>
          <tr>
            <Th>Organization</Th>
            <Th>Status</Th>
            <Th>Realm</Th>
            <Th className="text-right">Users</Th>
            <Th className="text-right">Departments</Th>
            <Th className="text-right">Org budget</Th>
            <Th className="text-right">Spend (period)</Th>
            <Th>Created</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {tenants.map((t) => {
            const pct = usagePct(t.spent_cents_period, t.org_budget_cents);
            return (
              <tr key={t.id} className="transition-colors hover:bg-surface-alt/50">
                <Td>
                  <Link
                    href={`/tenants/${t.slug}`}
                    className="font-medium text-foreground hover:text-accent"
                  >
                    {t.name}
                  </Link>
                  <p className="mt-0.5 font-mono text-xs text-faint">{t.slug}</p>
                </Td>
                <Td>
                  <StatusBadge status={t.status} />
                </Td>
                <Td>
                  <span className="font-mono text-xs text-muted">
                    {t.oidc_realm}
                  </span>
                </Td>
                <Td className="text-right tabular-nums">{t.counts.users}</Td>
                <Td className="text-right tabular-nums">
                  {t.counts.departments}
                </Td>
                <Td className="text-right tabular-nums">
                  {usd(t.org_budget_cents)}
                </Td>
                <Td className="text-right">
                  <p className="tabular-nums">{usd(t.spent_cents_period)}</p>
                  <div className="mt-1 w-24 md:ml-auto">
                    <Progress
                      pct={pct}
                      tone={pct >= 90 ? "danger" : pct >= 70 ? "warning" : "accent"}
                    />
                  </div>
                </Td>
                <Td className="whitespace-nowrap text-xs text-muted">
                  {dateTime(t.created_at)}
                </Td>
                <Td>
                  <Link
                    href={`/tenants/${t.slug}`}
                    className="text-xs font-medium text-accent hover:text-accent-hover"
                  >
                    View →
                  </Link>
                </Td>
              </tr>
            );
          })}
        </tbody>
      </Table>
    </Card>
  );
}
