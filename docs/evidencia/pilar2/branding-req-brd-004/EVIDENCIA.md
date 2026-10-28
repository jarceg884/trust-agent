# Evidencia E2E — REQ-BRD-004: branding versionado draft/publish + logos + favicon

**Tarea**: t_d2b65bcf (gap B3 de la revisión Pilar 2, t_1c65ff67 §9) · **Spec**: 028 (`specs/028-branding-versioning/`) · **Fecha**: 2026-10-26 · **Perfil**: dev

**Stack vivo**: API `:8000` (imagen `trust-agent/platform:local` rebuild, md5 `branding.py` = `6f8859040c93…` verificado dentro del contenedor), Admin Console `:3100`, desktop `:9090`, Keycloak 26 `:8080`, PostgreSQL `:55432`. Migración `0018_branding_versioning` aplicada (head compartido con las lanes paralelas: cadena válida de 23 revisiones, única cabeza `0020_device_attestation`).

**Fixture**: tenant `console-smoke2` (smoke canónico), usuario `admin@console-smoke2.test` (org_admin). Login 100% REAL desde la UI del console: org code → PKCE S256 contra el realm → primer login con one-time password (Admin API reset) → enrolamiento TOTP RFC 6238 real (secret leído del DOM de la página de enrolamiento de KC, clave = bytes UTF-8 raw) → UPDATE_PASSWORD → sesión con MFA (acr=2). Re-login posterior: password + código OTP como segundo factor.

## Flujo verificado (todo desde la UI real, nada simulado)

1. **Estado inicial corregido** (`/branding`): `published: v0 · source: provider | draft: in sync` — el stamp de provisioning (spec 004) proyecta sobre el default del proveedor y NO cuenta como "unpublished changes" (bug real encontrado y corregido durante la evidencia: comparación de proyecciones efectivas, no canon crudo).
2. **Draft editado**: Product name `Smoke2 Branding Corp`, tagline `Governed AI, our way`, accent `#0F766E` → **Save draft** → toast `Draft saved — publish to make it visible to users.` (PUT real).
3. **Uploads desde la UI** (DataTransfer con archivos reales generados, PNG/SVG válidos):
   - `logo-light.png` 538 B → toast `uploaded to the draft (538 B).`
   - `logo-dark.svg` 299 B → draft.
   - `favicon.png` 99 B → draft.
   - Previews renderizando desde `/api/proxy/api/branding/assets/{id}`.
4. **REQ-BRD-004 (draft invisible)**: tras el save del draft, `GET /api/branding` (lo que ven los usuarios) devolvió `{"name":"Trust Agent","source":"provider","version":0,"logo":null}` — el draft NO es visible hasta publicar.
5. **Publish**: botón Publish → toast `Published as version 1 — visible to all users.` → `GET /api/branding` = `Smoke2 Branding Corp`, `source: tenant`, `version: 1`, con `logo_light_url`, `logo_dark_url`, `favicon_url` y el alias retrocompatible `logo_url` = asset light.
6. **Console sirve el logo**: sidebar = `Smoke2 Branding Corp` + `<img src="…/api/proxy/api/branding/assets/6e5e9f33…">`; favicon del tenant aplicado en runtime (`<link rel="icon">` → asset favicon); asset vía proxy = `200, image/png, 538 bytes`, magic bytes `[137,80,78,71,13,10,26,10]`.
7. **Desktop sirve el logo** (:9090, sesión real vía SSO): dashboard = `SMOKE2 BRANDING CORP` + `<img alt="Smoke2 Branding Corp logo" src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0…">` — el logo DARK resuelto server-side desde el asset del control plane con el bearer de la sesión (el desktop es dark-theme, prefiere dark).
8. **Integridad de bytes extremo a extremo**: sha256 de los archivos locales == sha256 en `branding_assets` == payload del audit:
   - light `d32c16f5c14a65d9db5a4c38cb589275fd9c293d718030d9bfbc4fdadb636152`
   - dark `d511e0d4157611f94cba916295a2dc3c716c0e88b13d1ce4539c391aa3c36ffd`
   - favicon `67aac5c81c10e6d245372b8629573c370d29e894b405773b8b04d53b4f3da4ea`
9. **Rollback E2E desde la UI**: rename → publish v2 (`Smoke2 Renamed Inc @v2`) → `Rollback to v1` (ConfirmDialog) → toast `Rolled back to version 1 (republished as version 3).` → `GET /api/branding` = `Smoke2 Branding Corp @v3`; historial append-only: `v3 (live) · v2 · v1`.

## Audit real (audit_log del tenant, en orden de cadena)

```
branding.asset.uploaded  org_admin {"kind":"logo_light","mime":"image/png","sha256":"d32c16f5…","asset_id":"6e5e9f33-…","size_bytes":538}
branding.asset.uploaded  org_admin {"kind":"logo_dark","mime":"image/svg+xml","sha256":"d511e0d4…","asset_id":"5be7166c-…","size_bytes":299}
branding.asset.uploaded  org_admin {"kind":"favicon","mime":"image/png","sha256":"67aac5c8…","asset_id":"554f169a-…","size_bytes":99}
branding.draft.updated   org_admin {"keys":["accent_color","product_name","tagline"]}
branding.published       org_admin {"keys":[…],"version":1}
(+ v2: branding.draft.updated, branding.published version=2)
(+ rollback: branding.rolled_back {"from_version":2,"to_version":1,"new_version":3})
```

`branding_versions`: v1 `Smoke2 Branding Corp`, v2 `Smoke2 Renamed Inc`, v3 (= data exacto de v1). `tenants.branding_version` = 3.

## Tests

- `platform/tests/test_branding_versioning.py` (nuevo, 7 tests): AC-028-1…5 — **26 passed** junto con `test_org_endpoints.py` (actualizado al contrato draft/publish).
- `db` suite (RLS coverage, aislamiento, paridad schema.sql↔migración): **207 passed** (`TRUST_DB_CONTAINER=trust-agent-db`).
- Console: `eslint` limpio + `next build` OK. Desktop: `next build` OK (4 problemas de lint preexistentes de otras lanes, verificados ajenos a este diff).
- Nota: corridas full-suite de platform mostraron fallos fluctuantes en `test_model_access`/`test_spend_cutoff`/`test_device_revocation_sweep` — atribuidos a lanes paralelas editando `devices/` y corriendo la suite contra la misma DB efímera (cada archivo pasa aislado; `devices/service.py` modificado 2 min antes por otra lane, 245 líneas sin commitear).

## Capturas

- `console-branding-page.png` — /branding con badges draft/published, uploads con preview e historial con rollback.
- `desktop-white-label.png` — dashboard del desktop con el brand del tenant y el logo (data URI).

## Contrato nuevo (resumen para QA)

- `PUT /api/branding` → edita el **draft** (respuesta `{draft, published}`; audit `branding.draft.updated`).
- `POST /api/branding/publish` → publica atómico vN+1 (audit `branding.published` con versión).
- `POST /api/branding/rollback {version}` → re-publica como versión nueva (audit `branding.rolled_back`).
- `GET /api/branding/draft` · `GET /api/branding/versions` (branding.manage).
- `POST /api/branding/assets` (multipart `kind`+`file`, PNG/SVG ≤ 2 MB, magic bytes, SVG sin contenido activo) · `GET /api/branding/assets/{id}` (cualquier principal del tenant; RLS).
- `GET /api/branding` mantiene la shape publicada + `version` + URLs de assets.
