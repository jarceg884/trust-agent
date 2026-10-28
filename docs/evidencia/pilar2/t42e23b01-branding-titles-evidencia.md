# Evidencia t_42e23b01 — Page titles dinámicos (REQ-BRD2-007) + refresh del shell sin reload (REQ-BRD2-008)

**Tarea**: t_42e23b01 ([BAJO] Branding: page titles estáticos + refresh de sidebar/favicon sin reload)
**Perfil**: ui · **Fecha**: 2026-10-27 · **Spec**: 028-branding-versioning (D-028-08/D-028-09, REQ-BRD2-007/008, AC-028-7)
**Origen**: observaciones 1 y 2 del punto 9 (Branding) de la verificación E2E del Pilar 2 (qa-108d-puntos6-9-evidencia.md).

**Stack vivo**: API `:8000` (healthy), Admin Console `:3100` (imagen `trust-agent/console:local` reconstruida con el fix, healthy), Keycloak `:8080`, PostgreSQL `:55432` — `docker compose ps` verificado. Nada simulado: tenant fresco `t42e2-194218` (`7d8c7821-f8e0-4aa5-90f0-0da3675aba0d`) aprovisionado en vivo, login PKCE S256 real (OTA→password, acr=1), sesión admin real minteada (`POST /api/auth/session` kind=admin), navegador real (Chromium) contra `:3100`.

## Cambios implementados

| Archivo | Cambio |
|---|---|
| `specs/028-branding-versioning/spec.md` | D-028-08 (titles), D-028-09 (refresh live), REQ-BRD2-007/008, AC-028-7 |
| `platform/console/src/lib/published-branding.ts` | NUEVO: `getPublishedBranding` con `cache()` de React (un solo GET por request, compartido título+acento) |
| `platform/console/src/lib/branding-events.ts` | NUEVO: evento de cliente `trust:branding-updated` (emit/subscribe) |
| `platform/console/src/app/(console)/layout.tsx` | `generateMetadata`: `title.template = "%s — {product_name}"`, `default = "{product_name} — Admin Console"`; acento consume el mismo fetch cacheado |
| `platform/console/src/app/(console)/*/page.tsx` (12) | `title` pasa de "X — Trust Agent Admin Console" a solo "X" (el template del layout añade " — {product_name}") |
| `platform/console/src/components/shell/tenant-brand.tsx` | Escucha `trust:branding-updated` → refetch; efecto que mantiene `document.title` al día sin reload (reemplaza el segmento final tras " — ") |
| `platform/console/src/app/(console)/branding/branding-page.tsx` | `refreshShell()` tras publish/rollback exitosos: `notifyBrandingUpdated()` + `router.refresh()` (acento server-side); hint del Product name corregido a "Shown in the sidebar and page titles of the console." |

Pre-auth (login/signup/register) conserva el título estático del proveedor — razón explícita documentada en D-028-08: el tenant no se conoce hasta que existe sesión. `npm run build` y `npm run lint` limpios; imagen Docker reconstruida (`docker compose build console && up -d`).

## REQ-BRD2-007 — titles dinámicos (AC-028-7, parte 1)

Publicación v1 desde la UI con `product_name: "T42E2 Titles Corp"` (accent `#0F766E`, favicon PNG 70 B):

- **HTML servido (curl con cookie de sesión real)**:
  - `/overview` → `<title>Overview — T42E2 Titles Corp</title>`
  - `/branding` → `<title>Branding — T42E2 Titles Corp</title>`
- **Navegador real**: `document.title` = "Overview — T42E2 Titles Corp" en `/overview` y "Branding — T42E2 Titles Corp" en `/branding`.
- **Baseline v0 (provider default)** verificado ANTES de publicar: "Overview — Trust Agent" / "Branding — Trust Agent" — el título sigue al publicado, no al draft.
- **Pre-auth**: `/login` sigue sirviendo `<title>Sign in — Trust Agent Admin Console</title>` (estático, por diseño D-028-08).

## REQ-BRD2-008 — refresh del shell SIN reload (AC-028-7, parte 2)

Flujo: navegador en `/branding` con la sesión admin → **Publish** clickeado en la UI (botón habilitado, draft con `has_changes: true`) → observación inmediata. Marcador `window.__no_reload_marker = 42` sembrado antes del clic; **siguió en 42 después** → mismo document, CERO reload.

| Elemento del shell | Antes del Publish | Después del Publish (sin reload) |
|---|---|---|
| `document.title` | `Branding — Trust Agent` | `Branding — T42E2 Titles Corp` |
| Sidebar (product_name) | `Trust Agent` | `T42E2 Titles Corp` |
| `<link rel="icon">` | (no presente) | `/api/proxy/api/branding/assets/efd8f67f-…` → fetch in-page **200 image/png** |
| `--color-accent` (computado) | default | `rgb(15, 118, 110)` = `#0F766E` (via `router.refresh()`, RSC re-render) |
| Badge de estado | `published: v0 · source: provider` | `published: v1 · source: tenant` |

Navegación client-side posterior a `/overview` (mismo document, marker 42): `document.title` = "Overview — T42E2 Titles Corp" (generateMetadata server-side con el publicado).

## Audit (PostgreSQL, tenant 7d8c7821…)

```
branding.draft.updated   | 2026-10-27 19:43:22.765 (PUT API: product_name/tagline/accent al draft)
branding.asset.uploaded  | 2026-10-27 19:43:22.826 (favicon PNG 70 B al draft)
branding.published       | v1 | 2026-10-27 19:43:41.075 (clic en Publish desde la UI)
```

REQ-BRD-004 intacto durante el flujo: tras el PUT del draft, `GET /api/branding` (published) seguía `Trust Agent` v0 provider mientras el draft ya decía `T42E2 Titles Corp`.

## Screenshot

`docs/evidencia/pilar2/t42e23b01-titles-live-refresh.png` — navegador real en `/overview` del tenant con branding v1 publicado: sidebar "T42E2 Titles Corp / ADMIN CONSOLE", acento teal `#0F766E`, título de página "Overview — T42E2 Titles Corp".

## Scripts

- `evidencia_t42e2_provision.py` — provisioning del tenant + login PKCE real + mint de sesión admin (ejecutado vía `docker exec -i trust-agent-api python3 -`).
- Draft/favicon por API: `curl` con `Authorization: Bearer <trust_session>` (PUT /api/branding, POST /api/branding/assets multipart).

## Veredicto

**REQ-BRD2-007 PASS** (titles dinámicos server-side + live) · **REQ-BRD2-008 PASS** (sidebar + favicon + title + acento sin reload tras publish desde la UI) · **AC-028-7 PASS**. Las dos observaciones menores del punto 9 quedan cerradas; REQ-BRD2-005 no cambió (el comportamiento con reload sigue válido).
