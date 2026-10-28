# Spec 028 — Branding REQ-BRD-004: logos light/dark + favicon, versionado draft/publish

> Origen: gap B3 detectado por QA en la revisión del Pilar 2
> (t_1c65ff67, docs/revision-pilar2-panel-admin.md §9). REQ-BRD-004 de la
> spec 001 estaba SIN implementar y SIN tarea. Esta spec lo cierra.
> Extiende: specs/001-platform §4.11 (REQ-BRD-001…005) y
> specs/012-org-endpoints (REQ-ORG-110/111, GET/PUT /api/branding).

## 1. Problema

El core de branding (product_name/tagline/accent_color vía GET/PUT
`/api/branding`) existe y está verificado, pero:

1. **PUT publica en caliente**: un cambio queda visible para todos los
   usuarios al instante, sin preview ni publicación deliberada — viola
   REQ-BRD-004 ("preview antes de publicar; los cambios no publicados no
   son visibles para usuarios").
2. **No hay uploads**: `logo_url` solo acepta http(s) externo; no hay logo
   light/dark ni favicon por tenant (REQ-BRD-001 pide PNG/SVG ≤ 2 MB
   versionados).
3. **No hay versionado**: sin historial ni rollback a una versión anterior.
4. La página /branding del console lo documenta como "pending contract".

## 2. Alcance

- **Control plane**: flujo draft/publish versionado, uploads de assets
  (logo_light/logo_dark/favicon, PNG/SVG ≤ 2 MB), servicio de assets al
  console y al desktop, rollback, audit de cada acción, RLS.
- **Admin Console** (/branding): edición de draft con preview, uploads con
  preview, estados draft/published, publish, historial de versiones y
  rollback. El brand del sidebar consume el branding publicado (logo
  incluido) y el favicon del tenant se aplica en runtime.
- **Desktop**: el dashboard resuelve el logo del tenant (asset del control
  plane → data URI con el bearer de la sesión) y lo inyecta en `Brand`.

Fuera de alcance: crops/resize de imágenes, CDN, cache invalidation
avanzada (ETag por sha256 es suficiente en local-first), branding del
email, tema de Keycloak por tenant.

## 3. Decisiones

- **D-028-01 — Storage: `branding_assets` con `bytea`, NO jsonb ni
  filesystem.** Tabla dedicada `(id, tenant_id, kind, mime, size_bytes,
  sha256, content bytea, created_by, created_at)`, RLS tenant. Razones: la
  política de aislamiento es la misma que el resto del negocio (RLS +
  `app.tenant_id`), el backup es pg_dump, y el publish es atómico en la
  misma transacción (un asset y su versión no pueden divergir). 2 MB máx
  hace viable bytea. Cada upload inserta una fila nueva (los assets
  referenciados por versiones publicadas nunca se mutan).
- **D-028-02 — Draft en `tenants.branding_draft` (jsonb) + versión en
  `tenants.branding_version` (int).** El draft es el estado de edición;
  `tenants.branding` sigue siendo SOLO lo publicado (los consumidores —
  console, desktop, usuarios — jamás ven el draft: REQ-BRD-004).
- **D-028-03 — Historial en `branding_versions` `(tenant_id, version)`
  con el jsonb publicado exacto + published_by/published_at.** Publish =
  INSERT de la versión N+1 + UPDATE de `tenants.branding` en UNA
  transacción. Rollback = re-publicar el data de una versión anterior como
  versión nueva (el historial es append-only; rollback auditable).
- **D-028-04 — `PUT /api/branding` pasa a escribir el DRAFT** (merge de
  claves canónicas + referencias de assets). Respuesta `{draft, published}`.
  Audit `branding.draft.updated`. Es un cambio de contrato documentado
  respecto de la spec 012 (que publicaba en caliente): REQ-BRD-004 manda.
- **D-028-05 — Contrato de assets.** `POST /api/branding/assets`
  (multipart: `kind` ∈ logo_light|logo_dark|favicon + `file`) valida magic
  bytes PNG (`\x89PNG\r\n\x1a\n`) o SVG (utf-8 estricto, raíz `<svg`,
  sin `<script>`, sin atributos `on*`, sin `javascript:`), tamaño ≤ 2 MB,
  y cuelga el asset del draft. `GET /api/branding/assets/{id}` sirve los
  bytes con el mime correcto, `ETag: sha256`, `Cache-Control: private`,
  `X-Content-Type-Options: nosniff`, para CUALQUIER principal autenticado
  del tenant (RLS hace el aislamiento; el logo no es secreto, pero sí
  scoped). Audit `branding.asset.uploaded`.
- **D-028-06 — URLs canónicas.** `GET /api/branding` (publicado, shape
  existente) añade `version`, `logo_light_url`, `logo_dark_url`,
  `favicon_url` (rutas API `/api/branding/assets/{id}`) y mantiene
  `logo_url` como alias retrocompatible (URL externa si existe, si no el
  asset light). El console las consume vía BFF proxy; el desktop las
  resuelve server-side con su bearer a data URI.
- **D-028-07 — Publish/rollback.** `POST /api/branding/publish`
  (branding.manage) publica atómico → audit `branding.published` con
  `{version}`. `POST /api/branding/rollback {version}` re-publica esa
  versión → audit `branding.rolled_back` `{from_version, to_version,
  new_version}`. `GET /api/branding/versions` lista el historial;
  `GET /api/branding/draft` expone el draft + diff contra lo publicado.
- **D-028-08 — Page titles dinámicos (t_42e23b01, obs-1 QA Pilar 2).**
  El título de cada página del console se deriva del branding PUBLICADO:
  `generateMetadata` en el layout del console (fetch server-side con
  `cache()` de React — una sola llamada por request, compartida con el
  tema de acento) define `title.template = "%s — {product_name}"` y
  `title.default = "{product_name} — Admin Console"`; cada página
  exporta solo su segmento ("Overview", "Branding", …). Fallback
  `Trust Agent` si no hay branding. Las páginas pre-auth (login,
  signup, register) conservan el título estático del proveedor: el
  tenant no es conocido hasta que existe sesión (el org code se entra
  en el formulario) — razón explícita de la divergencia, constitution II.
  El segmento "Admin Console" se quita de los títulos de página (ya lo
  dice el subtítulo del brand en el sidebar; el título queda
  "Página — {product_name}", consistente con el sidebar).
- **D-028-09 — Refresh del shell sin reload (t_42e23b01, obs-2 QA).**
  Publish/rollback desde /branding emite un evento de cliente
  (`trust:branding-updated`, CustomEvent sobre window) y dispara
  `router.refresh()` (re-render RSC → acento server-side al día). El
  `TenantBrand` del sidebar escucha el evento y re-fetcha
  `GET /api/branding`: product_name, logo y favicon del shell se
  actualizan SIN reload; el `<title>` se actualiza en el mismo efecto
  (reemplaza el segmento final tras " — "). Sin evento, el comportamiento
  anterior (aplica en el siguiente GET/reload) sigue siendo válido —
  REQ-BRD2-005 no cambia.

## 4. Requisitos

- **REQ-BRD2-001** — El org_admin edita un DRAFT (product_name, tagline,
  accent_color, logo_url externo) vía `PUT /api/branding`; ningún usuario
  ve el cambio hasta publicar.
- **REQ-BRD2-002** — El org_admin sube logo_light/logo_dark/favicon
  (PNG/SVG ≤ 2 MB); tipo/tamaño/contenido activo se validan (422); el
  asset queda referenciado en el draft.
- **REQ-BRD2-003** — `POST /api/branding/publish` publica atómico
  (versión N+1, historial, `tenants.branding`); audit `branding.published`
  con versión; los usuarios ven el cambio en el siguiente GET.
- **REQ-BRD2-004** — `POST /api/branding/rollback {version}` restaura una
  versión anterior como versión nueva; audit `branding.rolled_back`.
- **REQ-BRD2-005** — El console y el desktop sirven el logo/favicon del
  tenant en runtime (sin redeploy): el sidebar del console renderiza el
  logo publicado, el favicon del tenant se aplica en runtime, y el
  dashboard del desktop inyecta el logo (data URI desde el asset).
- **REQ-BRD2-006** — RLS en `branding_assets`/`branding_versions`; un
  asset de otro tenant da 404; sin `branding.manage` las mutaciones dan
  403; cada acción audita.
- **REQ-BRD2-007** (t_42e23b01) — El `<title>` de cada página del
  console refleja el `product_name` PUBLICADO ("Overview — Acme Corp"),
  resuelto server-side en el layout (D-028-08). Las páginas pre-auth
  conservan el título estático del proveedor (razón documentada en
  D-028-08).
- **REQ-BRD2-008** (t_42e23b01) — Tras publish/rollback desde /branding,
  el shell (sidebar: product_name + logo, favicon, `<title>`, acento)
  se actualiza SIN reload de la página (D-028-09).

## 5. Criterios de aceptación

- **AC-028-1 (draft invisible)**: PUT draft → GET /api/branding de otro
  usuario NO refleja el cambio; publish → sí (test de API).
- **AC-028-2 (uploads)**: PNG válido 200 + asset en draft; SVG con
  `<script>` 422; > 2 MB 422; tipo no permitido 422 (test de API).
- **AC-028-3 (publish atómico)**: publish → `branding_versions` tiene la
  versión, `tenants.branding` coincide, audit `branding.published`
  payload.version correcto (test de API + DB).
- **AC-028-4 (rollback)**: publicar v1, cambiar, publicar v2, rollback a
  v1 → GET devuelve el branding de v1 con versión 3 (test de API).
- **AC-028-5 (aislamiento)**: asset de tenant B inaccesible para
  principal de tenant A → 404; RLS FORCE en las tablas nuevas
  (db/tests/test_rls_coverage.py).
- **AC-028-6 (E2E real)**: upload desde la UI /branding del console
  (:3100, sesión real) → publish → el console sirve el logo (sidebar + 
  GET del asset vía proxy) y el desktop (:9090) lo renderiza → audit con
  versión. Evidencia en docs/evidencia/pilar2/ (o docs/).
- **AC-028-7 (titles + refresh live, t_42e23b01)**: con branding
  publicado (`product_name` custom), el navegador contra :3100 muestra
  `document.title` = "Página — {product_name}" en las páginas del
  console (REQ-BRD2-007); tras Publish desde /branding con un
  product_name nuevo, sidebar + favicon + `<title>` cambian SIN reload
  (REQ-BRD2-008). Evidencia: navegador real + screenshot.
