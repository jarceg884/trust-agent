/**
 * Iconos de librería pro — lucide-react (mandato Miller, t_7cd4f40e).
 *
 * Capa de compatibilidad sobre lucide-react: conserva los nombres `Icon*`
 * que usan las páginas y fija el estándar del design system (docs/ui-standard.md):
 * - stroke uniforme 1.75 (densidad enterprise, coherente con texto sm/xs);
 * - tamaño base 16 con alineación óptica (flex + shrink-0 en quien lo usa);
 * - `aria-hidden` por defecto (los iconos son decorativos; el texto accesible
 *   viaja siempre al lado).
 *
 * NADA de SVG casero: cada icono sale de la librería (tree-shakeable, stroke
 * consistente). Para iconos nuevos, importa directo de "lucide-react" con
 * strokeWidth 1.75.
 */
import type { ComponentProps } from "react";
import {
  Activity,
  Bell,
  Bot,
  Check,
  ChevronDown,
  Copy,
  Cpu,
  CreditCard,
  Download,
  ExternalLink,
  Grid3x3,
  KeyRound,
  Landmark,
  LayoutDashboard,
  LogOut,
  Menu,
  Monitor,
  Palette,
  Plus,
  RefreshCw,
  ScrollText,
  Search,
  ShieldCheck,
  TriangleAlert,
  Users,
  X,
} from "lucide-react";

type IconProps = ComponentProps<"svg"> & { size?: number };

/** Defaults del design system aplicados a cualquier icono lucide. */
function opts({ size = 16, ...props }: IconProps) {
  return { size, strokeWidth: 1.75, "aria-hidden": true as const, ...props };
}

export const IconOverview = (p: IconProps) => <LayoutDashboard {...opts(p)} />;
export const IconOrganization = (p: IconProps) => <Landmark {...opts(p)} />;
export const IconDepartments = (p: IconProps) => <Grid3x3 {...opts(p)} />;
export const IconUsers = (p: IconProps) => <Users {...opts(p)} />;
export const IconBudgets = (p: IconProps) => <CreditCard {...opts(p)} />;
export const IconModels = (p: IconProps) => <Cpu {...opts(p)} />;
export const IconAgents = (p: IconProps) => <Bot {...opts(p)} />;
export const IconDevices = (p: IconProps) => <Monitor {...opts(p)} />;
export const IconCredentials = (p: IconProps) => <KeyRound {...opts(p)} />;
export const IconAudit = (p: IconProps) => <ScrollText {...opts(p)} />;
export const IconBranding = (p: IconProps) => <Palette {...opts(p)} />;
export const IconBell = (p: IconProps) => <Bell {...opts(p)} />;
export const IconChevronDown = (p: IconProps) => <ChevronDown {...opts(p)} />;
export const IconMenu = (p: IconProps) => <Menu {...opts(p)} />;
export const IconClose = (p: IconProps) => <X {...opts(p)} />;
export const IconCheck = (p: IconProps) => <Check {...opts(p)} />;
export const IconCopy = (p: IconProps) => <Copy {...opts(p)} />;
export const IconDownload = (p: IconProps) => <Download {...opts(p)} />;
export const IconExternal = (p: IconProps) => <ExternalLink {...opts(p)} />;
export const IconRefresh = (p: IconProps) => <RefreshCw {...opts(p)} />;
export const IconPlus = (p: IconProps) => <Plus {...opts(p)} />;
export const IconAlert = (p: IconProps) => <TriangleAlert {...opts(p)} />;
export const IconShield = (p: IconProps) => <ShieldCheck {...opts(p)} />;
export const IconLogout = (p: IconProps) => <LogOut {...opts(p)} />;
export const IconSearch = (p: IconProps) => <Search {...opts(p)} />;
export const IconPulse = (p: IconProps) => <Activity {...opts(p)} />;
