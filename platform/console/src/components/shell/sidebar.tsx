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
  IconModels,
  IconOrganization,
  IconOverview,
  IconUsers,
} from "@/components/icons";

export const NAV_ITEMS = [
  { href: "/overview", label: "Overview", icon: IconOverview },
  { href: "/organization", label: "Organization", icon: IconOrganization },
  { href: "/departments", label: "Departments", icon: IconDepartments },
  { href: "/users", label: "Users & Roles", icon: IconUsers },
  { href: "/budgets", label: "Budgets", icon: IconBudgets },
  { href: "/models", label: "Model Access", icon: IconModels },
  { href: "/agents", label: "Shared Agents", icon: IconAgents },
  { href: "/devices", label: "Devices", icon: IconDevices },
  { href: "/credentials", label: "Credentials", icon: IconCredentials },
  { href: "/audit", label: "Audit Log", icon: IconAudit },
  { href: "/branding", label: "Branding", icon: IconBranding },
] as const;

export function Sidebar({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
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
        <div className="flex h-14 items-center gap-2.5 border-b border-border-subtle px-5">
          <span
            aria-hidden="true"
            className="flex size-7 items-center justify-center rounded-lg bg-accent text-[13px] font-bold text-white"
          >
            T
          </span>
          <div className="leading-tight">
            <p className="text-sm font-semibold text-foreground">Trust Agent</p>
            <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-faint">
              Admin Console
            </p>
          </div>
        </div>

        <p className="px-5 pb-1 pt-5 text-[10px] font-semibold uppercase tracking-[0.18em] text-faint">
          Governance
        </p>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 pb-4">
          {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={href}
                href={href}
                onClick={onClose}
                aria-current={active ? "page" : undefined}
                className={`flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors ${
                  active
                    ? "bg-accent-soft font-medium text-accent"
                    : "text-muted hover:bg-surface-alt hover:text-foreground"
                }`}
              >
                <Icon size={15} className="shrink-0" />
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-border-subtle px-5 py-3">
          <p className="text-[10px] leading-relaxed text-faint">
            Every action on this console is recorded in the organization&apos;s
            immutable audit log.
          </p>
        </div>
      </aside>
    </>
  );
}
