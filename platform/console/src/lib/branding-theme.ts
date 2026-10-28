/**
 * Tema de acento del tenant (spec 028 + hallazgo #8 del pulido v2,
 * t_3cf085b9).
 *
 * `GET /api/branding` expone `accent_color` como parte del contrato
 * white-label (spec 001 AC-ONB-007 / spec 028): el sidebar ya consumía
 * product_name/logos, pero el chrome (botones, barra activa, badges,
 * iconos de pasos) seguía fijo en el rojo default #c90104 — con un tenant
 * cuyo acento publicado es otro (p. ej. el indigo #4F46E5 del default del
 * proveedor), la página mezclaba dos marcas y el rojo se leía como error
 * donde era "seleccionado" (crítica visual: "unify the informational
 * accent to the brand hue, reserve red strictly for destructive").
 *
 * Esta helper valida el hex (nada de CSS inyectable desde el draft) y
 * deriva la familia completa de tokens que usa el design system:
 * accent / accent-hover / accent-soft / accent-border / accent-ring.
 * El layout la inyecta como <style> server-rendered (cero flash, cero JS).
 */

const HEX_RE = /^#([0-9a-f]{6})$/i;

/** Hex #rrggbb → [r, g, b] (1–255). null si el formato no es válido. */
function hexToRgb(hex: string): [number, number, number] | null {
  const match = HEX_RE.exec(hex.trim());
  if (!match) return null;
  const value = Number.parseInt(match[1], 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

/** Oscurece un canal hacia negro por la fracción dada (0–1). */
function darken(channel: number, fraction: number): number {
  return Math.max(0, Math.round(channel * (1 - fraction)));
}

/**
 * CSS de tema para el acento del tenant, listo para <style>.
 * Devuelve null si el color no es un hex #rrggbb válido (el chrome
 * conserva entonces el rojo default del tema — spec 009 D-AC-04).
 */
export function accentThemeCss(accentColor: string | null | undefined): string | null {
  const rgb = accentColor ? hexToRgb(accentColor) : null;
  if (!rgb) return null;
  const [r, g, b] = rgb;
  const hover = `rgb(${darken(r, 0.1)}, ${darken(g, 0.1)}, ${darken(b, 0.1)})`;
  return [
    `:root{`,
    `--color-accent:rgb(${r}, ${g}, ${b});`,
    `--color-accent-hover:${hover};`,
    `--color-accent-soft:rgba(${r}, ${g}, ${b}, 0.07);`,
    `--color-accent-border:rgba(${r}, ${g}, ${b}, 0.22);`,
    `--color-accent-ring:rgba(${r}, ${g}, ${b}, 0.14);`,
    `}`,
  ].join("");
}
