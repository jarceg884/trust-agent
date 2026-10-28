# Plan técnico — Spec 028 (branding REQ-BRD-004)

## 1. DB (migración 0018 vía migration_guard, paridad schema.sql §16)

- `tenants` += `branding_draft jsonb NOT NULL DEFAULT '{}'`,
  `branding_version int NOT NULL DEFAULT 0`.
- `branding_assets (id uuid PK, tenant_id uuid NOT NULL REFERENCES
  tenants(id), kind text CHECK IN ('logo_light','logo_dark','favicon'),
  mime text NOT NULL, size_bytes int NOT NULL, sha256 text NOT NULL,
  content bytea NOT NULL, created_by uuid, created_at timestamptz)`,
  índice `(tenant_id, kind)`.
- `branding_versions (tenant_id uuid, version int, data jsonb NOT NULL,
  published_by uuid, published_at timestamptz, PK (tenant_id, version))`.
- RLS ENABLE+FORCE + política `tenant_isolation` para trust_app en ambas;
  grants DML; triggers updated_at no aplican (append-only/insert).
- `catalog.audit_actions` += `branding.draft.updated`,
  `branding.published`, `branding.rolled_back`, `branding.asset.uploaded`.
- `db/tests/conftest.py` BUSINESS_TABLES += las dos tablas.

## 2. Control plane (platform/app/org/branding.py + router.py)

- `branding.py`: mantener get_branding (publicado) y añadir:
  - `_draft_state(conn)`: lee draft + assets referenciados → shape con URLs.
  - `put_draft` (ex put_branding): merge canónico sobre draft, audit
    `branding.draft.updated`, respuesta `{draft, published}`.
  - `publish(conn, principal)`: valida draft no vacío, INSERT versión
    N+1 con el data canónico del draft (incluye asset ids), UPDATE
    tenants.branding/branding_version, audit `branding.published`.
  - `rollback(conn, principal, version)`: lee branding_versions, replica
    el publish con ese data, audit `branding.rolled_back`.
  - `list_versions`, `upload_asset` (validación magic bytes/mime/size/SVG
    sanitizado), `get_asset` (bytes + headers).
- `router.py`: `GET /api/branding` (igual), `PUT /api/branding` (draft),
  `GET /api/branding/draft`, `POST /api/branding/publish`,
  `GET /api/branding/versions`, `POST /api/branding/rollback`,
  `POST /api/branding/assets` (multipart: `UploadFile` + `File` +
  `Form`), `GET /api/branding/assets/{asset_id}` (Response binario).
  Permisos: `branding.manage` en todas las mutaciones; GET asset para
  cualquier principal autenticado (RLS).
- Dependencia nueva: `python-multipart` (FastAPI form parsing) en
  platform/pyproject.toml → rebuild imagen api.

## 3. Admin Console (platform/console)

- `branding-page.tsx` rework completo: estado draft (inputs controlados),
  preview reactivo (light/dark), uploads con preview (FileReader), botón
  Save draft (PUT), botón Publish (POST), badge draft/published con
  versión, historial de versiones con rollback. Cero toasts falsos.
- `types.ts`: `TenantBranding` += version/logo URLs; tipos draft/versiones.
- Sidebar (`shell`): brand del tenant (logo publicado o inicial con
  accent) vía GET /api/branding; componente `TenantFavicon` que fija
  `<link rel="icon">` en runtime.

## 4. Desktop (platform/desktop-client)

- `control-plane.ts`: helper que resuelve una URL de asset API a data URI
  (fetch con bearer del token de sesión).
- `dashboard/page.tsx`: branding con logo resuelto (dark primero, si no
  light) → `Brand`/`Sidebar` sin cambios de contrato.

## 5. Tests

- `platform/tests/test_branding_versioning.py` (nuevo): AC-028-1…5.
- `test_org_endpoints.py`: actualizar los 2 tests de branding al contrato
  draft/publish (documentar el cambio de contrato D-028-04).
- db suite (RLS/paridad/schema) en verde.

## 6. Evidencia real (AC-028-6)

Rebuild api+migrate+console+desktop → migración aplicada → flujo real
desde la UI del console (:3100): upload PNG real → publish → verificación
de que console y desktop sirven el logo + audit `branding.published` con
versión. Log/evidencia en docs/evidencia/pilar2/.
