"use client";

/**
 * Iconos del panel legible (spec 036, D-36-06) — lucide-react explícito.
 *
 * El contrato declara iconos por NOMBRE (47 keys + 9 categorías); aquí viven
 * como imports explícitos (tree-shakeable, stroke del design system). Si un
 * nombre del contrato no está en el mapa (p. ej. lucide lo renombró), el
 * render cae al icono de la CATEGORÍA — nunca rompe la página.
 *
 * `console/scripts/check_policy_icons.mjs` verifica en CI que los 47+9
 * nombres del contrato existen en lucide-react y en este mapa (AC-036-007).
 */
import type { ComponentProps } from "react";
import {
  Activity,
  Archive,
  BadgeCheck,
  Ban,
  Bot,
  Box,
  Brain,
  Cable,
  CalendarClock,
  ClipboardCheck,
  Cloud,
  Cpu,
  CreditCard,
  Crosshair,
  Database,
  FileKey,
  FilePen,
  FileText,
  Gauge,
  GitBranch,
  Globe,
  Handshake,
  History,
  KeyRound,
  ListChecks,
  Lock,
  MessageSquare,
  MessagesSquare,
  Mic,
  Network,
  Package,
  Palette,
  Plug,
  Puzzle,
  Repeat,
  RefreshCw,
  Search,
  Send,
  Server,
  Share2,
  ShieldCheck,
  Sparkles,
  SquareTerminal,
  Target,
  Timer,
  Unlink,
  UserCheck,
  UserPlus,
  Users,
  Variable,
  Webhook,
  WifiOff,
  Wrench,
  Zap,
} from "lucide-react";
import type { PolicyCategory } from "@/lib/types";

type IconProps = ComponentProps<"svg"> & { size?: number };

/** Mapa nombre-contrato → componente lucide (D-36-06). */
const ICONS = {
  Activity,
  Archive,
  BadgeCheck,
  Ban,
  Bot,
  Box,
  Brain,
  Cable,
  CalendarClock,
  ClipboardCheck,
  Cloud,
  Cpu,
  CreditCard,
  Crosshair,
  Database,
  FileKey,
  FilePen,
  FileText,
  Gauge,
  GitBranch,
  Globe,
  Handshake,
  History,
  KeyRound,
  ListChecks,
  Lock,
  MessageSquare,
  MessagesSquare,
  Mic,
  Network,
  Package,
  Palette,
  Plug,
  Puzzle,
  Repeat,
  RefreshCw,
  Search,
  Send,
  Server,
  Share2,
  ShieldCheck,
  Sparkles,
  SquareTerminal,
  Target,
  Timer,
  Unlink,
  UserCheck,
  UserPlus,
  Users,
  Variable,
  Webhook,
  WifiOff,
  Wrench,
  Zap,
} as const;

export type PolicyIconName = keyof typeof ICONS;

/** Icono de categoría (fallback cuando el de la key no existe). Incluye
 *  los 6 grupos del builder de spec 041 (D-41-03) además de las 9
 *  categorías legibles de spec 036. */
const CATEGORY_FALLBACK: Record<string, PolicyIconName> = {
  security: "ShieldCheck",
  web: "Globe",
  agent: "Bot",
  code: "SquareTerminal",
  extensions: "Puzzle",
  data: "Database",
  models: "Cpu",
  integrations: "Plug",
  access: "Users",
  system: "SquareTerminal",
  comms: "MessagesSquare",
};

function opts({ size = 18, ...props }: IconProps) {
  return { size, strokeWidth: 1.75, "aria-hidden": true as const, ...props };
}

/**
 * Icono de una policy: el declarado en el contrato, con fallback al de su
 * categoría (D-36-06). `categories` es la lista real del backend.
 */
export function PolicyIcon({
  name,
  category,
  categories,
  size,
  className,
}: {
  name: string | undefined;
  category: string;
  categories: PolicyCategory[];
  size?: number;
  className?: string;
}) {
  const resolved =
    (name && name in ICONS ? ICONS[name as PolicyIconName] : undefined) ??
    (categories.find((c) => c.id === category)?.icon &&
    categories.find((c) => c.id === category)!.icon in ICONS
      ? ICONS[
          categories.find((c) => c.id === category)!.icon as PolicyIconName
        ]
      : undefined) ??
    ICONS[CATEGORY_FALLBACK[category] ?? "ShieldCheck"];
  const Resolved = resolved as (p: IconProps) => React.ReactElement;
  return <Resolved {...opts({ size, className })} />;
}
