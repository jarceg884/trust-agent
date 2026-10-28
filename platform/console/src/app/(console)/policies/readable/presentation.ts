/**
 * Helpers de la capa de presentación legible (spec 036).
 *
 * - `t()`: render por locale (hoy inglés, constitution VI); el contrato viaja
 *   es+en completo — cambiar de locale es data-only (D-36-07).
 * - `buildStatement()`: la frase de la vista por destinatario, derivada del
 *   valor EFECTIVO que computa el backend (D-36-04) rellenando {value} con
 *   labels legibles de opciones/ítems, número+unidad o modo.
 * - `fallbackPresentation()`: control genérico por value_type para una key
 *   sin presentación, marcada "Untranslated" (D-36-05) — forward-compatible
 *   con catálogos extendidos por migración.
 *
 * La UI JAMÁS hardcodea nombres/frases de policies: todo sale del bloque
 * `presentation` de GET /api/policies/matrix (D-36-01).
 */
import type {
  DynamicItem,
  LocalizedText,
  PolicyCatalogEntry,
  PolicyControl,
  PolicyKnownItem,
  PolicyPresentation,
} from "@/lib/types";

export type Locale = "en" | "es";

export const UI_LOCALE: Locale = "en";

/** Texto del contrato en el locale del console (con degradación al otro). */
export function t(text: LocalizedText | undefined, locale: Locale = UI_LOCALE): string {
  if (!text) return "";
  return text[locale] || text.es || text.en || "";
}

/** Etiqueta de ítem: conocida del contrato → dinámica del endpoint → slug
 * tal cual (es un nombre, no un key técnico — spec 036 §5). */
export function itemLabel(
  value: string,
  known: PolicyKnownItem[] | undefined,
  dynamic: DynamicItem[] | undefined,
  locale: Locale = UI_LOCALE,
): string {
  const k = known?.find((i) => i.value === value);
  if (k) return t(k.label, locale);
  const d = dynamic?.find((i) => i.value === value);
  if (d) return d.label;
  return value;
}

/** Unidad con singular ingenuo ("1 minute", "2 minutes"). */
export function unitLabel(
  unit: LocalizedText | undefined,
  n: number,
  locale: Locale = UI_LOCALE,
): string {
  const raw = t(unit, locale);
  if (n === 1 && raw.endsWith("s")) return raw.slice(0, -1);
  return raw;
}

/** ¿El control es un toggle booleano legible? */
export function isToggleKind(kind: PolicyControl["kind"]): boolean {
  return kind === "toggle-allow" || kind === "toggle-require";
}

/**
 * La frase legible del valor EFECTIVO (D-36-04). `labelFor` resuelve la
 * etiqueta de un ítem de lista (conocida/dinámica/slug).
 */
export function buildStatement(
  pres: PolicyPresentation,
  value: unknown,
  labelFor: (value: string) => string = (v) => v,
  locale: Locale = UI_LOCALE,
): string {
  const kind = pres.control.kind;
  if (isToggleKind(kind) || kind === "locked") {
    const onOff = value ? pres.statement?.on : pres.statement?.off;
    return t(onOff, locale) || (value ? "On" : "Off");
  }
  const template = t(pres.statement_template, locale) || "{value}";
  return template.replace("{value}", valueSummary(pres, value, labelFor, locale));
}

/** El {value} de la plantilla, en palabras según el tipo de control. */
function valueSummary(
  pres: PolicyPresentation,
  value: unknown,
  labelFor: (value: string) => string,
  locale: Locale,
): string {
  const control = pres.control;
  switch (control.kind) {
    case "number": {
      const n = typeof value === "number" ? value : Number(value);
      return `${n} ${unitLabel(control.unit, n, locale)}`;
    }
    case "select": {
      const opt = control.options?.find((o) => o.value === value);
      return opt ? t(opt.label, locale) : String(value ?? "—");
    }
    case "multiselect-allow":
    case "multiselect-deny": {
      const items = Array.isArray(value) ? value : [];
      if (control.wildcard && items.includes(control.wildcard.value)) {
        return t(control.wildcard.label, locale);
      }
      if (!items.length) return t(pres.empty_label, locale) || "none";
      return items.map((v) => labelFor(String(v))).join(", ");
    }
    case "domains": {
      const spec = (value ?? {}) as { mode?: string; domains?: string[] };
      const mode = control.mode_options?.find((m) => m.value === spec.mode);
      const modeLabel = mode ? t(mode.label, locale) : String(spec.mode ?? "open");
      const domains = spec.domains ?? [];
      if (!domains.length) return modeLabel;
      const domainWord = (t(control.domains_label, locale) || "domains").toLowerCase();
      return `${modeLabel} (${domains.length} ${domainWord})`;
    }
    case "messaging": {
      const spec = (value ?? {}) as Record<
        string,
        { allowed?: boolean; mode?: string } | undefined
      >;
      const platforms = Object.entries(spec).filter(([, p]) => p?.allowed);
      if (!platforms.length) return t(pres.empty_label, locale) || "none enabled";
      return platforms
        .map(([platform, p]) => {
          const mode = control.mode_options?.find((m) => m.value === p?.mode);
          return `${labelFor(platform)}: ${mode ? t(mode.label, locale) : String(p?.mode)}`;
        })
        .join("; ");
    }
    default:
      if (value === null || value === undefined) return "—";
      return typeof value === "object" ? JSON.stringify(value) : String(value);
  }
}

/** Etiqueta del toggle en palabras ("Require MFA for everyone" / "MFA optional"). */
export function toggleLabel(
  control: PolicyControl,
  on: boolean,
  locale: Locale = UI_LOCALE,
): string {
  return t(on ? control.on_label : control.off_label, locale) || (on ? "On" : "Off");
}

// --- Fallback genérico (D-36-05) ----------------------------------------------

const ACRONYMS = new Set(["mfa", "ai", "api", "mcp", "ssh", "ip", "totp", "byok"]);

/** "require_mfa" → "Require MFA" (solo para el fallback sin traducir). */
export function humanizeKey(key: string): string {
  return key
    .split("_")
    .map((w) =>
      ACRONYMS.has(w) ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1),
    )
    .join(" ");
}

export interface ResolvedPresentation {
  pres: PolicyPresentation;
  /** true = key sin contrato → control genérico + badge "Untranslated". */
  untranslated: boolean;
}

/**
 * Resuelve la presentación de una key: la del contrato o el fallback genérico
 * por value_type (D-36-05). Hoy el fallback es imposible (test de cobertura
 * 1:1 en CI), pero el catálogo es extensible por migración (spec 029).
 */
export function resolvePresentation(
  entry: PolicyCatalogEntry,
  presentation: Record<string, PolicyPresentation>,
): ResolvedPresentation {
  const pres = presentation[entry.key];
  if (pres) return { pres, untranslated: false };

  const control: PolicyControl =
    entry.value_type === "boolean"
      ? { kind: "toggle-allow" }
      : entry.value_type === "integer"
        ? { kind: "number", min: 0 }
        : entry.value_type === "string" && entry.choices
          ? {
              kind: "select",
              options: entry.choices.map((c) => ({
                value: c,
                label: { es: c, en: c },
              })),
            }
          : entry.value_type === "list"
            ? { kind: "multiselect-allow", known_items: [] }
            : { kind: "raw" };
  return {
    untranslated: true,
    pres: {
      category: "security",
      icon: "",
      invariable: entry.invariable,
      org_only: entry.org_only,
      name: { es: humanizeKey(entry.key), en: humanizeKey(entry.key) },
      description: {
        es: "Política del catálogo sin traducción de presentación todavía.",
        en: "Catalog policy without a presentation translation yet.",
      },
      control: entry.invariable ? { kind: "locked" } : control,
      statement_template: { es: "{value}", en: "{value}" },
    },
  };
}

// --- Badges de origen (REQ-036-005) --------------------------------------------

export type PolicySource = "default" | "org" | "dept" | "user";

/** Badge legible de origen según el destinatario que se está viendo. */
export function originLabel(source: PolicySource, scope: "org" | "dept" | "user"): string {
  if (scope === "org") {
    return source === "org" ? "Set by the organization" : "Provider default";
  }
  switch (source) {
    case "user":
      return "Set for this user";
    case "dept":
      return "Set by the department";
    default:
      return "Inherited from organization";
  }
}
