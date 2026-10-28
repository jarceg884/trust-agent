# Plan técnico — Spec 023 (t_526030f3)

Referencia: spec.md §3 (decisiones D-023-01..13) y §5 (contratos).

## 1. Migración (una sola, serializada)

`python3 db/scripts/migration_guard.py new --slug hermes_download`:

1. `ALTER TABLE tenants ADD COLUMN download_enabled boolean NOT NULL DEFAULT true`
   `ADD COLUMN download_updated_at timestamptz NOT NULL DEFAULT now()`
2. `INSERT INTO catalog.permissions ('download_link.manage', ...)`
   `INSERT INTO catalog.role_permissions ('org_admin','download_link.manage')`
3. `INSERT INTO catalog.audit_actions ('download.issued','download.link.enabled','download.link.disabled')`

Paridad idéntica en `db/schema.sql` (sección nueva numerada). Al cerrar:
`docker compose build migrate && docker compose up -d` (el servicio migrate valida cabeza única).

## 2. Backend — `platform/app/download/` (módulo nuevo)

- `__init__.py` — docstring del módulo.
- `router.py`:
  - `GET /download/{slug}` — público. Throttle `dl:page:{ip}` (register_hit por request);
    slug desconocido → 404 + register_failure(ip) (anti-enumeración). Tenant suspendido →
    página de rehusado. Link disabled → página disabled (200). Sano → HTML de `page.py`.
  - `GET /download/{slug}/bundle` — público. Throttle `dl:bundle:{slug}:{ip}` estricto;
    disabled → 403 `link_disabled`; suspendido → 403; tarball de `bundle.py` +
    audit `download.issued` (actor_role=system) + header `X-Bundle-SHA256`.
  - `GET/PUT /api/download-link` — sesión MFA + permiso `download_link.manage`;
    GET lee `tenants.download_enabled/download_updated_at` y construye `url` desde
    `request.base_url`; PUT actualiza + audit `download.link.enabled|disabled`.
    Se registra en `app/main.py` (router público SIN dependencias de auth).
- `bundle.py` — `build_bundle(*, slug, api_base, branding, model, litellm_base_url) -> (bytes, sha256, filename)`:
  tarball in-memory (tarfile+gzip) con plugin copiado del árbol fuente (setting
  `hermes_plugin_dir`, default `/opt/trust-agent/hermes-plugins/trust-agent-governance`,
  sin `__pycache__`), `install.sh` renderizado desde template (valores horneados),
  `trust-login` (script Python stdlib), `README.md`, `manifest.json` (con sha256 de cada
  miembro). Sin secretos.
- `page.py` — `render_page(...)` y estados (ok/disabled/suspended/404 estándar via errors).
  HTML autocontenido: CSS inline con tokens del branding, SVGs lucide oficiales inline
  (stroke-width 1.75), `ta-rise` escalonado bajo `prefers-reduced-motion`, focus-visible,
  logo data URI si hay asset publicado.

## 3. Extensión de throttle (rate limit positivo)

`app/auth/throttle.py::register_hit(conn, key, *, window_secs, max_hits, base_secs, max_secs)`
— mismo SQL de `register_failure` parametrizado (contadores en `ops.login_throttle`;
`check()` existente sirve para ambos). Settings nuevos en `config.py`:
`download_page_rate_max/window` (default 30/60s) y `download_bundle_rate_max/window`
(default 5/60s), `hermes_plugin_dir`.

## 4. Infra

- `platform/Dockerfile`: `COPY platform/desktop-client/hermes/plugins /opt/trust-agent/hermes-plugins`
  (el API necesita el árbol del plugin para empaquetar).
- `docker-compose.yml`: sin cambios (el API ya corre con la imagen build-eada).

## 5. Tests — `platform/tests/test_hermes_download.py`

Contra la DB efímera de conftest (migración real) + TestClient:

1. Página 200 con branding del tenant A (product_name/accent en el HTML); tenant B no
   aparece en la página de A (aislamiento).
2. Slug desconocido → 404; N sondeos → 429.
3. Bundle: 200, tar válido, miembros esperados, manifest con org_code, sin secretos;
   audit `download.issued` +1 por descarga.
4. Rate limit de bundle: exceder max → 429 con Retry-After.
5. `GET /api/download-link` (org_admin) → contrato exacto `{url, enabled, updated_at}`;
   `PUT {enabled:false}` → audit `download.link.disabled` → bundle 403, página disabled;
   re-enable restaura. Permiso: user/dept_admin → 403 (solo org_admin).
6. Tenant suspendido → refuse.

## 6. E2E live — `platform/scripts/evidence_hermes_download.py`

Reutiliza el baile PKCE+TOTP de `e2e_final_live.py` (tenant real `hermes-dl-e2e`):
setup (tenant+admin+worker+policy allowed_tools+budget+key virtual) → download
(HTTP anónimo + navegador real via browser tool del worker) → install (home fresco
`/root/trust-agent/.e2e/hermes-dl-home`) → login (trust-login con navegador real) →
first-run (hermes chat) → approve (API admin) → governed turn (veto + governance_status).
Evidencia en `docs/hermes-download.md` + `docs/evidencia/hermes-download/`.

## 7. Orden de implementación

spec → migración (guard) → schema.sql → throttle/settings → download module → main.py →
Dockerfile → tests → build imágenes + migrate → pytest → E2E live → docs → converge.
