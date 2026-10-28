"use client";

/**
 * Topbar (mockup Admin): organización, notificaciones reales de gobernanza
 * (/api/me/notifications), menú de usuario con logout y acciones rápidas.
 */
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api-client";
import type { GovernanceNotification, WhoAmI } from "@/lib/types";
import { timeAgo } from "@/lib/format";
import { Badge, Button, useToast } from "@/components/ui";
import {
  IconBell,
  IconChevronDown,
  IconDownload,
  IconLogout,
  IconMenu,
  IconPlus,
} from "@/components/icons";

export function Topbar({
  me,
  onMenuClick,
}: {
  me: WhoAmI;
  onMenuClick: () => void;
}) {
  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border-subtle bg-surface/95 px-4 backdrop-blur lg:px-6">
      <button
        type="button"
        onClick={onMenuClick}
        aria-label="Open navigation"
        className="rounded-md p-1.5 text-muted hover:bg-surface-alt hover:text-foreground lg:hidden"
      >
        <IconMenu size={18} />
      </button>

      <div className="flex min-w-0 items-center gap-2">
        {/* t_b14cbefb: the topbar is human-facing — show the organization
            NAME (tenants.name via whoami.tenant_name) with the slug
            degraded to tooltip + secondary text, never the raw slug
            alone ("hermes-dl" read like a machine identifier). */}
        <span
          className="truncate text-sm font-semibold text-foreground"
          title={me.tenant_slug}
        >
          {me.tenant_name || me.tenant_slug}
        </span>
        {me.tenant_name && me.tenant_name !== me.tenant_slug ? (
          <span className="hidden max-w-40 truncate text-xs text-faint md:inline">
            {me.tenant_slug}
          </span>
        ) : null}
        <Badge tone="success">Connected</Badge>
      </div>

      <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
        <Link
          href="/users"
          className="hidden sm:inline-flex"
          title="Invite user (requires the user directory API)"
        >
          <Button variant="secondary" size="sm">
            <IconPlus size={13} /> Invite user
          </Button>
        </Link>
        {/* CORRECCIÓN MILLER (t_360a752e): "Download desktop app" del mockup
            (D-AC-04) ahora enlaza a la sección Hermes Download del console —
            el admin del cliente genera y envía el link a sus empleados. */}
        {me.roles.some((r) => r.role_key === "org_admin") && (
          <Link
            href="/hermes-download"
            className="hidden sm:inline-flex"
            title="Hermes Download — share the desktop app with employees"
          >
            <Button variant="secondary" size="sm">
              <IconDownload size={13} /> Download desktop app
            </Button>
          </Link>
        )}
        <NotificationsBell />
        <UserMenu me={me} />
      </div>
    </header>
  );
}

function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<GovernanceNotification[] | null>(null);
  const [error, setError] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { push } = useToast();

  useEffect(() => {
    if (!open || items || error) return;
    api<{ notifications: GovernanceNotification[] }>("/api/me/notifications")
      .then((r) => setItems(r.notifications))
      .catch(() => setError(true));
  }, [open, items, error]);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const unread = items?.filter((n) => !n.read_at).length ?? 0;

  async function markRead(id: string) {
    try {
      await api(`/api/me/notifications/${id}/read`, { method: "POST" });
      setItems(
        (prev) =>
          prev?.map((n) =>
            n.id === id ? { ...n, read_at: new Date().toISOString() } : n,
          ) ?? null,
      );
    } catch {
      push("error", "Could not mark the notification as read.");
    }
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`}
        aria-expanded={open}
        className="relative rounded-md p-1.5 text-muted transition-colors hover:bg-surface-alt hover:text-foreground"
      >
        <IconBell size={17} />
        {unread > 0 ? (
          <span
            aria-hidden="true"
            className="absolute -right-0.5 -top-0.5 flex size-3.5 items-center justify-center rounded-full bg-accent text-[9px] font-bold text-white"
          >
            {unread}
          </span>
        ) : null}
      </button>
      {open ? (
        <div className="absolute right-0 top-full z-30 mt-2 w-80 rounded-xl border border-border-subtle bg-surface shadow-lg">
          <p className="border-b border-border-subtle px-4 py-2.5 text-xs font-semibold text-foreground">
            Governance notifications
          </p>
          <div className="max-h-80 overflow-y-auto">
            {error ? (
              <p className="px-4 py-4 text-xs text-muted">
                Could not load notifications.
              </p>
            ) : items === null ? (
              <p className="px-4 py-4 text-xs text-muted">Loading…</p>
            ) : items.length === 0 ? (
              <p className="px-4 py-4 text-xs text-muted">
                No notifications. Budget cutoffs and threshold alerts appear here.
              </p>
            ) : (
              items.map((n) => (
                <div
                  key={n.id}
                  className={`flex items-start justify-between gap-2 border-b border-border-subtle px-4 py-3 last:border-0 ${
                    n.read_at ? "opacity-60" : ""
                  }`}
                >
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-foreground">
                      {String(n.payload?.title ?? n.type)}
                    </p>
                    {n.payload?.body ? (
                      <p className="mt-0.5 line-clamp-2 text-[11px] text-muted">
                        {String(n.payload.body)}
                      </p>
                    ) : null}
                    <p className="mt-1 text-[10px] text-faint">
                      {timeAgo(n.created_at)}
                    </p>
                  </div>
                  {!n.read_at ? (
                    <button
                      type="button"
                      onClick={() => markRead(n.id)}
                      className="shrink-0 text-[10px] font-medium text-accent hover:underline"
                    >
                      Mark read
                    </button>
                  ) : null}
                </div>
              ))
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function UserMenu({ me }: { me: WhoAmI }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const topRole = me.roles[0]?.role_key ?? "user";

  // Pulido v2 (hallazgo #7) + t_b14cbefb: el chip del topbar NO muestra el
  // email crudo truncado. El control plane persiste display_name = email
  // cuando el usuario no tiene nombre propio; un nombre REAL se reconoce
  // porque difiere del email. Con nombre real el chip gana contexto
  // humano "Nombre — rol" (el rol descriptivo acompaña, el email completo
  // vive en el menú con truncate CSS + title); sin nombre, fallback a la
  // parte local del email (comportamiento v2).
  const email = me.email || "?";
  const realName =
    me.display_name && me.display_name !== email ? me.display_name : null;
  const chipLabel = realName ?? email.split("@")[0];

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex items-center gap-2 rounded-lg border border-border-subtle bg-surface px-2.5 py-1.5 text-xs transition-all duration-150 ease-out hover:border-foreground/20 hover:bg-surface-alt"
      >
        <span
          aria-hidden="true"
          className="flex size-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[11px] font-semibold text-accent"
        >
          {(me.display_name || me.email || "?").charAt(0).toUpperCase()}
        </span>
        <span className="hidden max-w-32 truncate font-medium text-foreground sm:inline">
          {chipLabel}
        </span>
        {realName ? (
          <span className="hidden max-w-24 truncate text-muted sm:inline">
            — {topRole}
          </span>
        ) : null}
        <IconChevronDown size={13} className="text-muted" />
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-full z-30 mt-2 w-64 rounded-xl border border-border-subtle bg-surface shadow-lg"
        >
          <div className="border-b border-border-subtle px-4 py-3">
            <p className="truncate text-xs font-semibold text-foreground">
              {me.display_name || me.email}
            </p>
            <p className="truncate text-[11px] text-muted">{me.email}</p>
            <div className="mt-2 flex flex-wrap gap-1">
              {me.roles.map((r) => (
                <Badge key={`${r.role_key}-${r.department_id ?? "org"}`} tone="accent">
                  {r.role_key}
                </Badge>
              ))}
            </div>
          </div>
          <div className="px-2 py-2">
            <p className="px-2 pb-1 text-[10px] uppercase tracking-wide text-faint">
              Signed in as {topRole}
            </p>
            <form action="/auth/logout" method="post">
              <button
                type="submit"
                role="menuitem"
                className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-xs text-muted transition-colors hover:bg-surface-alt hover:text-foreground"
              >
                <IconLogout size={14} /> Sign out
              </button>
            </form>
          </div>
        </div>
      ) : null}
    </div>
  );
}
