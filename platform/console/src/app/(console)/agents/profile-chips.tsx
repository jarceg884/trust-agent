/**
 * Piezas compartidas del marketplace de perfiles (spec 045): la página Agents y la
 * sección Agentes del editor de políticas muestran lo mismo con las mismas palabras.
 */
import type { MarketplaceSummary } from "@/lib/types";

/** Nombre de perfil de Hermes (hermes_constants.PROFILE_ID_RE). */
export const PROFILE_SLUG_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

const STRIPPED_LABEL: Record<string, string> = {
  credentials: "Credenciales (.env, auth, llaves)",
  memories: "Memorias",
  sessions: "Sesiones",
  "state.db": "Base de estado (state.db)",
  logs: "Logs",
  plugins: "Plugins",
  skins: "Temas",
  workspace: "Workspace",
  plans: "Planes",
  "skills-bookkeeping": "Metadatos de instalación de skills",
  "cron-runtime": "Salidas de tareas programadas",
  cache: "Caché",
  audio_cache: "Caché de audio",
  image_cache: "Caché de imágenes",
  hooks: "Hooks",
  pairing: "Emparejamientos",
};

export function strippedLabel(key: string): string {
  return STRIPPED_LABEL[key] ?? key;
}

export function formatBytes(n: number | null | undefined): string {
  const v = n ?? 0;
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / (1024 * 1024)).toFixed(1)} MB`;
}

const CHIP = "rounded-full bg-surface-alt px-2 py-0.5 text-[10px] text-muted";
const CHIP_WARN = "rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] text-amber-300";

/** Lo que trae el perfil, en una línea: skills, MCP, tareas programadas, scripts. */
export function ProfileChips({ summary }: { summary: MarketplaceSummary }) {
  const skills = summary.skills?.length ?? 0;
  const mcp = summary.mcp_servers ?? [];
  const cron = summary.cron_jobs ?? [];
  return (
    <div className="flex flex-wrap gap-1">
      <span className={CHIP}>
        {skills} {skills === 1 ? "skill" : "skills"}
      </span>
      {summary.has_soul ? <span className={CHIP}>persona (SOUL.md)</span> : null}
      {mcp.length > 0 ? (
        <span className={CHIP_WARN} title="La política decide en runtime qué servidores MCP se permiten">
          MCP: {mcp.join(", ")}
        </span>
      ) : null}
      {cron.length > 0 ? (
        <span className={CHIP_WARN} title="Solo corren si la política permite tareas programadas">
          {cron.length} {cron.length === 1 ? "tarea programada" : "tareas programadas"}
        </span>
      ) : null}
      {summary.has_scripts ? (
        <span className={CHIP_WARN} title="Se ejecutan solo con las herramientas de terminal que conceda la política">
          incluye scripts
        </span>
      ) : null}
    </div>
  );
}
