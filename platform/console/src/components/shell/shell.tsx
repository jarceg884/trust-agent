"use client";

/**
 * Contenedor cliente del shell: sidebar colapsable + topbar + contenido.
 * El gate de sesión/MFA lo resuelve el layout server-side; aquí solo shell.
 */
import { useState, type ReactNode } from "react";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";
import type { WhoAmI } from "@/lib/types";

export function Shell({ me, children }: { me: WhoAmI; children: ReactNode }) {
  const [navOpen, setNavOpen] = useState(false);
  return (
    <div className="flex min-h-screen">
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar me={me} onMenuClick={() => setNavOpen(true)} />
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 lg:px-8 lg:py-8">
          {children}
        </main>
      </div>
    </div>
  );
}
