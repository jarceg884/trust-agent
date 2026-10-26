/**
 * Sidebar del desktop (estilo mockup): marca, navegación y sesión del usuario.
 * Los ítems de navegación del agente (Sessions, Capabilities, Shared agents,
 * Artifacts) corresponden a la sesión de Hermes Agent — se muestran como
 * secciones del producto; los datos gobernados viven en las tarjetas reales.
 */
import { Brand } from "./brand";
import { StatusDot } from "./ui";
import type { Branding } from "@/lib/control-plane";

type NavItem = {
  label: string;
  icon: string;
  href?: string;
  active?: boolean;
  muted?: boolean;
};

export function Sidebar({
  userName,
  userEmail,
  orgSlug,
  signOutAction,
  branding,
}: {
  userName: string;
  userEmail: string;
  orgSlug: string;
  signOutAction: () => Promise<void>;
  branding?: Branding | null;
}) {
  const nav: NavItem[] = [
    { label: "Sessions", icon: "▤", muted: true },
    { label: "Capabilities", icon: "✦", muted: true },
    { label: "Shared agents", icon: "◇", muted: true },
    { label: "Artifacts", icon: "▣", muted: true },
    { label: "Devices", icon: "◉", href: "/dashboard", active: true },
  ];

  return (
    <aside className="flex w-full shrink-0 flex-col justify-between border-b border-border-subtle bg-surface md:h-screen md:w-64 md:border-r md:border-b-0">
      <div>
        <div className="flex items-center justify-between px-5 py-5">
          <Brand branding={branding} />
        </div>
        <nav aria-label="Main" className="px-3">
          <ul className="space-y-1">
            {nav.map((item) => (
              <li key={item.label}>
                {item.href ? (
                  <a
                    href={item.href}
                    aria-current={item.active ? "page" : undefined}
                    className="flex items-center gap-3 rounded-lg bg-panel px-3 py-2 text-sm font-medium text-foreground"
                  >
                    <span aria-hidden className="text-muted">{item.icon}</span>
                    {item.label}
                  </a>
                ) : (
                  <span
                    className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted/70"
                    title="Available in the agent session (Hermes Agent runtime)"
                  >
                    <span aria-hidden>{item.icon}</span>
                    {item.label}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </nav>
      </div>

      <div className="border-t border-border-subtle px-5 py-4">
        <div className="flex items-center gap-3">
          <span
            aria-hidden
            className="flex h-8 w-8 items-center justify-center rounded-full bg-panel-hover text-xs font-semibold text-foreground"
          >
            {userName.slice(0, 1).toUpperCase()}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-foreground">
              {userName}
            </p>
            <p className="truncate text-xs text-muted">{userEmail}</p>
          </div>
        </div>
        <div className="mt-3 flex items-center justify-between">
          <span className="flex items-center gap-1.5 text-xs text-muted">
            <StatusDot tone="success" /> {orgSlug}
          </span>
          <form action={signOutAction}>
            <button
              type="submit"
              className="rounded-md border border-border-subtle px-2.5 py-1 text-xs text-muted transition hover:bg-panel-hover hover:text-foreground"
            >
              Sign out
            </button>
          </form>
        </div>
      </div>
    </aside>
  );
}
