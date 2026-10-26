"use client";

/**
 * Shell del Provider Console (spec 014 REQ-PC-011): sidebar + topbar.
 * Navegación mínima del operador: Tenants / Provision New Client.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { IconMenu, IconLogout } from "@/components/icons";

const NAV_ITEMS = [
  { href: "/tenants", label: "Tenants" },
  { href: "/tenants/new", label: "Provision New Client" },
] as const;

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  async function logout() {
    await fetch("/auth/provider/logout", { method: "POST" });
    // Navegación full-page INTENCIONAL: borra la cookie y re-evalúa el gate.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = "/login";
  }

  return (
    <div className="flex min-h-screen">
      {open ? (
        <div
          aria-hidden="true"
          className="fixed inset-0 z-30 bg-foreground/30 lg:hidden"
          onClick={() => setOpen(false)}
        />
      ) : null}
      <aside
        aria-label="Provider navigation"
        className={`fixed inset-y-0 left-0 z-40 flex w-60 shrink-0 flex-col border-r border-border-subtle bg-surface transition-transform lg:static lg:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex h-14 items-center gap-2.5 border-b border-border-subtle px-5">
          <span
            aria-hidden="true"
            className="flex size-7 items-center justify-center rounded-lg bg-foreground text-[13px] font-bold text-white"
          >
            T
          </span>
          <div className="leading-tight">
            <p className="text-sm font-semibold text-foreground">Trust Agent</p>
            <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-faint">
              Provider Console
            </p>
          </div>
        </div>

        <p className="px-5 pb-1 pt-5 text-[10px] font-semibold uppercase tracking-[0.18em] text-faint">
          Operations
        </p>
        <nav className="flex-1 space-y-0.5 px-3 pb-4">
          {NAV_ITEMS.map(({ href, label }) => {
            const active =
              href === "/tenants"
                ? pathname === "/tenants" ||
                  (pathname.startsWith("/tenants/") &&
                    !pathname.startsWith("/tenants/new"))
                : pathname === href;
            return (
              <Link
                key={href}
                href={href}
                onClick={() => setOpen(false)}
                aria-current={active ? "page" : undefined}
                className={`flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors ${
                  active
                    ? "bg-accent-soft font-medium text-accent"
                    : "text-muted hover:bg-surface-alt hover:text-foreground"
                }`}
              >
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-border-subtle px-5 py-3">
          <p className="text-[10px] leading-relaxed text-faint">
            Every provider operation is recorded in each tenant&apos;s
            immutable audit log.
          </p>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border-subtle bg-surface/95 px-4 backdrop-blur lg:px-6">
          <button
            type="button"
            onClick={() => setOpen(true)}
            aria-label="Open navigation"
            className="rounded-md p-1.5 text-muted hover:bg-surface-alt hover:text-foreground lg:hidden"
          >
            <IconMenu size={18} />
          </button>
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-semibold text-foreground">
              Provider Console
            </span>
            <span className="inline-flex items-center gap-1 rounded-full border border-success/20 bg-success-soft px-2 py-0.5 text-[11px] font-medium leading-4 text-success">
              Operator
            </span>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={logout}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border-subtle bg-surface px-2.5 py-1.5 text-xs font-medium text-muted transition-colors hover:bg-surface-alt hover:text-foreground"
            >
              <IconLogout size={13} />
              Sign out
            </button>
          </div>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 lg:px-8">
          {children}
        </main>
      </div>
    </div>
  );
}
