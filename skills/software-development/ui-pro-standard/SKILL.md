---
name: ui-pro-standard
description: "Estándar UI pro de trust-agent (mandato Miller): lucide-react con stroke 1.75, capa de fluidez con keyframes ta-* bajo prefers-reduced-motion, un solo design system para console/provider. Use when construir o revisar CUALQUIER pantalla de platform/console, platform/provider-console o páginas públicas."
version: 1.0.0
author: trust-agent ui
metadata:
  hermes:
    tags: [ui, lucide-react, tailwind, animaciones, design-system, trust-agent]
---

# Estándar UI pro — lucide-react + fluidez + animaciones

Fuente canónica: **`/root/trust-agent/docs/ui-standard.md`** (léelo completo si
vas a construir pantallas nuevas). Esto es el resumen operativo, verificado en
vivo (t_7cd4f40e, 2026-10-26).

## Iconos

- `lucide-react` instalado en las 3 apps. `components/icons.tsx` (console y
  provider) es una **capa de compatibilidad** que re-exporta lucide con los
  defaults del sistema: `strokeWidth 1.75`, `size 16`, `aria-hidden`.
- Usa los `Icon*` existentes o importa directo de `"lucide-react"` pasando
  `strokeWidth={1.75}`. Prohibido: SVG inline casero, glifos de texto (▤ ✦ ◉),
  emojis, mezclar sets.
- Nombres lucide verificados en v1.61.0: `TriangleAlert` (NO `AlertTriangle`),
  `Building2` NO existe (usa `Landmark`), `CheckCircle2` NO existe (usa
  `CircleCheck`).

## Fluidez (clases ya definidas en globals.css de cada app)

| Clase | Uso |
|---|---|
| `ta-fade-in` | apariciones sutiles (main ya lo tiene global) |
| `ta-rise` | tarjetas de flujo, escalonadas con `animation-delay` |
| `ta-dialog-backdrop` / `ta-dialog-panel` | automáticas en `Dialog` |
| `ta-toast-in` | automática en toasts |
| `ta-pop` | automática en `Badge` — para replay al cambiar estado, `key` que incluya el valor |

- Botones: `transition-all duration-150 ease-out active:scale-[0.98]` (ya en
  `Button`; réplica manual en botones ad-hoc).
- Inputs: `transition-colors duration-150` + focus visible global (no lo tapes).
- Tablas: hover de fila global (`tbody tr`), no lo reimplementes.
- Barras de presupuesto: `transition-[width] duration-500 ease-out` (ya en
  `Progress`/`ProgressBar`).
- **Toda animación nueva** va bajo `@media (prefers-reduced-motion: no-preference)`.
  framer-motion solo si CSS no alcanza (hoy nada lo necesita).
- Carga = skeletons que reservan espacio (cero CLS); `ErrorState` + Retry para
  errores; `ConfirmDialog` para destructivos (Esc cierra, Enter confirma).

## Trampas verificadas

1. **No toques `components/shell/sidebar.tsx` ni `shell.tsx` del console sin
   mirar el board** — son hotspot de varias lanes (t_360a752e los editó en
   paralelo). La capa de compatibilidad de icons.tsx existe precisamente para
   que la migración a lucide NO requiera tocar páginas ni shell.
2. **Los keyframes dentro de `@media` funcionan en Tailwind v4** (globals.css
   plano, no `@theme`).
3. **Rebuild de contenedores**: `docker compose build console provider-console`
   SÍ escribió imágenes nuevas en este host (verificado con
   `docker run --rm <img> grep -l ta-dialog-panel /app/.next`); tras el build,
   `docker compose up -d --no-deps <svc>` para recrear. Un contenedor con build
   viejo puede crashear client-side páginas nuevas (pasó con /budgets).
4. **Evidence de screenshots con sesión**: acuña la sesión con el patrón de
   `console/scripts/smoke_authorized.py` (IdP de test :8908 con JWKS real,
   issuer base habilitado en `TRUST_IDP_BASE_URLS`), inyecta las cookies
   `ta_console_session`/`ta_console_refresh`/`ta_console_csrf` vía CDP
   `Network.setCookie` (httpOnly) y captura. Las sesiones minteadas pueden
   caducar entre llamadas — verifica con `fetch('/api/proxy/me')` antes de
   capturar y re-minta si da 401.
5. **Las suites E2E de /policies comparten el mismo worker y se contaminan
   entre sí** (verificado t_edd8acaf): `e2e_041_practicantes_ui.mjs` asigna
   `max_daily_sessions: 2` al worker → la suite 036
   (`e2e_policies_ux_shots.mjs`) falla su check "el control vuelve al valor
   vigente (5)" porque el vigente YA es 2; y el cascade-delete final del
   builder (`e2e_named_policies_shots.mjs`) remueve el override de usuario
   `allowed_tools` que la 036 espera ("✗ Web search" + "Set for this user").
   Orden seguro: builder → practicantes → borrar Practicantes (cascade) →
   re-poner el override (`PUT /api/policies/user/<id>` allowed_tools
   `[read_file, write_file, governance_status]`) → 036. Si un check de
   "valor vigente" falla, sospecha estado residual antes que del código.
6. **Smoke del console**: requiere el tenant `demo-org` provisionado real con
   `oidc_issuer_url` NULL y el `TENANT_ID` correcto hardcodeado en el script.
   Si la DB se resetea, re-provisiona con `POST /api/admin/tenants` (provider
   token) y actualiza la constante.
