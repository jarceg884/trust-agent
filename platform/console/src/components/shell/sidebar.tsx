"use client";

/**
 * Sidebar GOVERNANCE (mockup Admin): navegación completa del console.
 * Responsive: drawer en <1024 px con backdrop (REQ-AC-032).
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import {
  IconAgents,
  IconAudit,
  IconBranding,
  IconBudgets,
  IconCredentials,
  IconDepartments,
  IconDevices,
  IconHermesDownload,
  IconModels,
  IconOrganization,
  IconOverview,
  IconPolicies,
  IconPulse,
  IconUsers,
} from "@/components/icons";
import { TenantBrand } from "@/components/shell/tenant-brand";

interface NavItem {
  href: string;
  label: string;
  icon: (p: { size?: number; className?: string }) => React.ReactNode;
  /** Solo roles admin de la org (p. ej. "Hermes Download", t_360a752e). */
  adminOnly?: boolean;
  /** org_admin O dept_admin (p. ej. "Usage", spec 035 — el API scopea). */
  anyAdminOnly?: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/overview", label: "Overview", icon: IconOverview },
  { href: "/organization", label: "Organization", icon: IconOrganization },
  { href: "/departments", label: "Departments", icon: IconDepartments },
  { href: "/users", label: "Users & Roles", icon: IconUsers },
  { href: "/budgets", label: "Budgets", icon: IconBudgets },
  // Spec 035 (mandato Miller): requests y consumos por usuario sobre el
  // ledger real. Visible para org_admin Y dept_admin (el API scopea).
  {
    href: "/usage",
    label: "Usage",
    icon: IconPulse,
    anyAdminOnly: true,
  },
  { href: "/models", label: "Model Access", icon: IconModels },
  { href: "/policies", label: "Policies", icon: IconPolicies },
  { href: "/agents", label: "Shared Agents", icon: IconAgents },
  { href: "/devices", label: "Devices", icon: IconDevices },
  {
    href: "/hermes-download",
    label: "Hermes Download",
    icon: IconHermesDownload,
    adminOnly: true,
  },
  { href: "/credentials", label: "Credentials", icon: IconCredentials },
  { href: "/audit", label: "Audit Log", icon: IconAudit },
  { href: "/branding", label: "Branding", icon: IconBranding },
];

export function Sidebar({
  open,
  onClose,
  isAdmin,
  isAnyAdmin = false,
}: {
  open: boolean;
  onClose: () => void;
  /** true si el principal tiene rol org_admin (filtra items adminOnly). */
  isAdmin: boolean;
  /** true si el principal es org_admin O dept_admin (items anyAdminOnly). */
  isAnyAdmin?: boolean;
}) {
  const pathname = usePathname();

  // Escape cierra el drawer móvil (REQ-AC-031).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <>
      {open ? (
        <div
          aria-hidden="true"
          className="fixed inset-0 z-30 bg-foreground/30 lg:hidden"
          onClick={onClose}
        />
      ) : null}
      <aside
        aria-label="Main navigation"
        className={`fixed inset-y-0 left-0 z-40 flex w-60 shrink-0 flex-col border-r border-border-subtle bg-surface transition-transform lg:static lg:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <TenantBrand />

        <p className="px-5 pb-1 pt-5 text-[10px] font-semibold uppercase tracking-[0.18em] text-faint">
          Governance
        </p>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 pb-4">
          {/* Pulido v2 r3 (hallazgo #8): el activo usa el TINT del acento de
              marca (bg-accent-soft + texto accent + barra) — la pill gris
              neutra no se leía como "seleccionado" y convivía con el acento
              violeta del logo (crítica: "bring the sidebar selected state
              onto the same accent"). El acento sigue al branding del tenant
              (re-tema del layout, spec 028). */}
          {NAV_ITEMS.filter(
            (item) =>
              (!item.adminOnly || isAdmin) &&
              (!item.anyAdminOnly || isAnyAdmin),
          ).map(
            ({ href, label, icon: Icon }) => {
              const active = pathname === href || pathname.startsWith(`${href}/`);
              return (
                <Link
                  key={href}
                  href={href}
                  onClick={onClose}
                  aria-current={active ? "page" : undefined}
                  className={`relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors ${
                    active
                      ? "bg-accent-soft font-medium text-accent"
                      : "text-muted hover:bg-surface-alt/70 hover:text-foreground"
                  }`}
                >
                  {active ? (
                    <span
                      aria-hidden="true"
                      className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-accent"
                    />
                  ) : null}
                  <Icon size={15} className="shrink-0" />
                  {label}
                </Link>
              );
            },
          )}
        </nav>

        <div className="border-t border-border-subtle px-5 py-3">
          <p className="text-[10px] leading-relaxed text-muted">
            Every action on this console is recorded in the organization&apos;s
            immutable audit log.
          </p>
        </div>
      </aside>
    </>
  );
}
