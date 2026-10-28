# Evidencia — UI Pro: lucide-react + fluidez + animaciones (t_7cd4f40e)

| Campo | Valor |
|---|---|
| **Fecha** | 2026-10-26 |
| **Tarea** | t_7cd4f40e (mandato Miller: "todo más pro — iconos de librería pro, todo fluido, animaciones si es necesario") |
| **Stack vivo** | Admin Console :3100 · Provider Console :3200 · Desktop :9090 (contenedores reconstruidos con la UI nueva) · API :8000 · PostgreSQL 17 |
| **Estándar** | `docs/ui-standard.md` |

Todo lo documentado se ejecutó contra el stack real. Sesión del Admin Console
acuñada con el patrón del smoke autorizado (IdP de test con JWKS real RS256,
firma verificada de verdad por el control plane, sesión admin en PostgreSQL
con RLS). Provider Console logueada con el provider token real validado contra
el control plane. Nada simulado.

## Contenido

- `antes/` — 18 screenshots del build anterior (SVG caseros, sin capa de fluidez).
- `despues/` — 18 screenshots del build con lucide-react + fluidez.
- `ocr-antes/`, `ocr-despues/` — texto OCR (tesseract) de cada screenshot.

## Páginas capturadas (antes/después)

| # | Superficie | Página |
|---|---|---|
| 01 | Admin Console | /login |
| 02 | Admin Console | /overview |
| 03 | Admin Console | /organization |
| 04 | Admin Console | /departments |
| 05 | Admin Console | /users |
| 06 | Admin Console | /budgets |
| 07 | Admin Console | /models |
| 08 | Admin Console | /agents |
| 09 | Admin Console | /devices |
| 10 | Admin Console | /credentials |
| 11 | Admin Console | /audit |
| 12 | Admin Console | /branding |
| 13 | Admin Console | /hermes-download |
| 14 | Provider Console | /login |
| 15 | Provider Console | /tenants |
| 16 | Provider Console | /tenants/final-e2e (detalle) |
| 17 | Provider Console | /tenants/new (provisioning) |
| 18 | Desktop | / (login white-label) |

## Qué cambió (verificable en el DOM vivo, no solo en screenshots)

Verificación ejecutada contra `http://127.0.0.1:3100/overview` con sesión real:

1. **Iconos lucide**: el DOM muestra 19 `svg.lucide` en Overview; el sidebar
   renderiza `lucide-layout-dashboard`, `lucide-landmark`, `lucide-grid-3x3`,
   `lucide-users`, `lucide-credit-card`, `lucide-cpu`, `lucide-bot`,
   `lucide-monitor`… todos con `stroke-width="1.75"` (stroke uniforme del
   design system). Cero SVG caseros: `components/icons.tsx` es ahora una capa
   de compatibilidad sobre `lucide-react`.
2. **Keyframes de fluidez en el CSS servido** (`2sel_dx9117l7.css`):
   `ta-dialog-in` (scale+fade 180ms), `ta-toast-in` (slide 220ms), `ta-pop`
   (badges 250ms), hover de filas `tbody tr`, y todo lo animado bajo
   `prefers-reduced-motion: no-preference`.
3. **Desktop**: glifos de texto del sidebar (▤ ✦ ◇ ▣ ◉) reemplazados por
   `MessagesSquare`, `Sparkles`, `Bot`, `Package`, `Monitor` de lucide.

## Nota sobre /budgets en "antes"

En el build anterior `/budgets` crasheaba client-side (boundary "This page
couldn't load") — artifacto del build desactualizado respecto al working tree
(la edición por usuario de t_6b40c758 no estaba en la imagen del contenedor).
En el build reconstruido la página renderiza con datos reales (ver
`ocr-despues/06-console-budgets.txt`); el smoke autorizado pasa 30/30 con el
build nuevo.

## Verificación de regresión

- `console/scripts/smoke_authorized.py` → **SMOKE PASS 30/30** con el build
  nuevo (sesión admin PostgreSQL + proxy BFF + CSRF + 11 páginas + logout).
  Nota de entorno: el smoke requería el fixture `demo-org` (tenant
  provisionado real vía provider API, `oidc_issuer_url` NULL para aceptar el
  IdP de test documentado) y el issuer base `http://127.0.0.1:8908` en
  `TRUST_IDP_BASE_URLS` (docker-compose.yml) — ambos ajustados en esta tarea.
- `npm run build` → exit 0 en las 3 apps (console 18 rutas, provider-console,
  desktop-client).
- `npm run lint` → 0 problems en console y provider-console. En desktop-client
  quedan 2 errores + 2 warnings PREEXISTENTES en archivos de otras lanes en
  vuelo (`dashboard/page.tsx`, `enroll/enroll-flow.tsx` de t_9fddcb75,
  `control-plane.ts`) — ninguno en archivos tocados por esta tarea.
