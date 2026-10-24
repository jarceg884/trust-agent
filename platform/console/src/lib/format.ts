/**
 * Formateo (spec 009 D-AC-08): la API trabaja en centavos USD; la UI
 * formatea con Intl. UI copy en inglés (constitution VI).
 */

/** Centavos → "$1,234.56" (USD). */
export function usd(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(cents / 100);
}

/** Centavos → "$1,234.56 / M tokens" para precios por millón. */
export function usdPerMillion(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  return `${usd(cents)} / M`;
}

/** USD decimales de un input → centavos enteros (D-AC-08: jamás float al API). */
export function usdToCents(input: string): number | null {
  const trimmed = input.trim().replace(/^\$/, "");
  if (!/^\d+(\.\d{1,2})?$/.test(trimmed)) return null;
  const [whole, frac = ""] = trimmed.split(".");
  const cents =
    parseInt(whole, 10) * 100 + parseInt((frac + "00").slice(0, 2), 10);
  return Number.isSafeInteger(cents) ? cents : null;
}

/** Centavos → string para un input de formulario ("1234.5"). */
export function centsToInput(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "";
  return (cents / 100).toFixed(2).replace(/\.00$/, "");
}

/** ISO → fecha/hora local corta. */
export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(d);
}

/** ISO → "3m ago" / "2h ago" / "5d ago". */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const s = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  return dateTime(iso);
}

/** UUID → "a1b2c3…9f" para columnas compactas. */
export function shortId(id: string | null | undefined): string {
  if (!id) return "—";
  return id.length <= 12 ? id : `${id.slice(0, 8)}…`;
}

/** Porcentaje usado de un presupuesto (0–100, cap 100). */
export function usagePct(spent: number, budget: number | null | undefined): number {
  if (!budget) return 0;
  return Math.min(100, Math.round((spent / budget) * 100));
}
