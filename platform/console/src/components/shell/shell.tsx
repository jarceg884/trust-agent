"use client";

/**
 * Contenedor cliente del shell: sidebar colapsable + topbar + contenido.
 * El gate de sesión/MFA lo resuelve el layout server-side; aquí solo shell.
 */
import { useState, type ReactNode } from "react";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";
import { MfaBanner } from "./mfa-banner";
import type { WhoAmI } from "@/lib/types";

export function Shell({ me, children }: { me: WhoAmI; children: ReactNode }) {
  const [navOpen, setNavOpen] = useState(false);
  // Roles admin de la org: filtra items adminOnly del sidebar (t_360a752e).
  const isAdmin = me.roles.some((r) => r.role_key === "org_admin");
  // Spec 035: "Usage" es para org_admin Y dept_admin (el API scopea).
  const isAnyAdmin = me.roles.some(
    (r) => r.role_key === "org_admin" || r.role_key === "dept_admin",
  );
  return (
    <div className="flex min-h-screen">
      <Sidebar
        open={navOpen}
        onClose={() => setNavOpen(false)}
        isAdmin={isAdmin}
        isAnyAdmin={isAnyAdmin}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar me={me} onMenuClick={() => setNavOpen(true)} />
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 lg:px-8 lg:py-8">
          {/* Spec 015 D-AUTH-08: recordatorio MFA persistente en todas las
              páginas mientras la sesión no tenga acr >= 2. */}
          <MfaBanner me={me} />
          {children}
        </main>
      </div>
    </div>
  );
}
