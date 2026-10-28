# Spec 014 — Plan técnico

> Referencia: spec.md §2 (decisiones D-PC-01…08). Puertos canónicos: API 8000 · Keycloak 8080 · Admin Console 3100 · **Provider Console 3200** · DB 55432.

## 1. Backend (control plane, `platform/app/tenants/`)

### 1.1 `service.py` (nuevo módulo de servicio provider)

- `list_tenants(engine, migrator_engine) -> dict`:
  1. `SELECT id, slug, name, status, oidc_realm, oidc_issuer_url, created_at FROM tenants ORDER BY created_at` vía **migrator** (D-PC-04).
  2. Por cada tenant: conexión `trust_app` con `set_tenant` → counts (`users`, `departments`), `org_budget_cents` (presupuesto org `status='active'`, `amount_cents`), `spent_cents_period` (`SUM(spent_cents)` de `budget_counters` nivel org con `period_start <= current_date <= period_end`; 0 si no hay fila) (D-PC-08).
  3. Responde `{"tenants": [...]}` con `id`/fechas serializados a string ISO.
- `set_tenant_status(engine, migrator_engine, slug, target) -> dict` (`target ∈ {suspended, active}`):
  1. `find_tenant_by_slug` vía migrator → 404 si no existe.
  2. Transacción `trust_app` con scope: `SELECT status` actual; si ya está en `target` → idempotente (sin UPDATE ni audit); si no → `UPDATE tenants SET status=:target` + `audit_service.emit(action='tenant.suspended'|'tenant.reactivated', actor_role='provider', payload={'previous': …})` en la misma transacción (D-PC-05).
  3. Devuelve `{"tenant": tenant_state(...)}`.

### 1.2 `router.py` (superficie v2)

- `GET ""` → `list_tenants` (provider principal + `tenants.provision`).
- `POST "/{slug}/suspend"` → `set_tenant_status(..., 'suspended')`.
- `POST "/{slug}/reactivate"` → `set_tenant_status(..., 'active')`.
- Todas con `Depends(get_provider_principal)` + `_require_provision_permission` (D-PC-03) — paridad 401/403/503 con spec 004.

### 1.3 Tests (`platform/tests/test_provider_admin.py`)

Contra la DB efímera de conftest (tenants seed A/B, sin Keycloak — suspend/list no tocan KC):
- 401 sin token / 403 sin permiso (fila de catálogo borrada y restaurada) para las 3 rutas.
- List: 200, ≥2 tenants seed, campos completos, `org_budget_cents` correcto del seed, orden.
- Suspend: 200 + SQL `suspended` + evento `tenant.suspended` actor provider; sesión del tenant → 403 `tenant_suspended` (vía mint de conftest).
- Idempotencia: segunda suspend no añade evento; reactivate → `active` + evento; 404 slug aleatorio.
- Higiene: el provider token no aparece en ningún cuerpo de respuesta.

## 2. Frontend (`platform/provider-console/`, Next.js 16 App Router + TS strict + Tailwind v4)

Scaffold espejo de `platform/console/` (mismas versiones fijadas: next 16.4.3, react 19.3, tailwind ^4, TS ^5 strict).

### 2.1 `src/lib/`

- `config.ts` — `apiBaseUrl` (TRUST_API_BASE_URL, default `http://127.0.0.1:8000`), `adminConsoleUrl` (default `http://127.0.0.1:3100`), validación org/slug.
- `provider-session.ts` — cookie `ta_provider` httpOnly (get/set/clear via `next/headers`).
- `control-plane.ts` — fetch server-side con `Authorization: Bearer <token>`; preserva status + envelope de errores.
- `types.ts` — tipos derivados de los contratos (TenantListItem, ProvisionResult, OneTimeActivation, HealthcheckResult…).
- `format.ts` — USD desde centavos, fechas.

### 2.2 Route handlers (`src/app/auth/provider/`)

- `login/route.ts` — POST {token} → valida contra `GET /api/admin/tenants` (200 ⇒ set cookie; 401/403/503 ⇒ redirect con error real).
- `logout/route.ts` — POST → borra cookie → redirect /login.

### 2.3 Proxy BFF (`src/app/api/proxy/[...path]/route.ts`)

Reenvía method/query/body a `${apiBaseUrl}/api/<path>` con `Authorization` desde la cookie; sin cookie → 401 JSON; pasa el envelope tal cual. Sin lógica de refresh (el provider token no rota) ni CSRF header (auth por header, D-PC-02).

### 2.4 Páginas (`src/app/(provider)/`)

- `layout.tsx` — shell enterprise (sidebar: Tenants / Provision New Client; topbar con "Provider Console" + logout). Gate: sin cookie → redirect /login.
- `tenants/page.tsx` + `tenants-table.tsx` — server component fetch `list_tenants`; tabla: Name, Status (badge), Realm, Users, Budget (USD), Spend (USD), Created, acción View. Estados loading/error reales.
- `tenants/new/page.tsx` + `provision-form.tsx` (client) — formulario (org name, admin email, budget USD, model plan: standard | custom lista, departamentos: chips de las 4 plantillas, slug opcional, branding product_name opcional) → POST vía proxy → resultado: card del tenant + **OneTimeActivationPanel** (warning "shown only once", password en `<code>` + copy, issuer, client_id; solo React state). Errores con reason real.
- `tenants/[slug]/page.tsx` + `tenant-detail.tsx` (client) — estado canónico, counts, budget/spend, botón **Run healthcheck** (GET live), **Suspend/Reactivate** con ConfirmDialog, link **Open Admin Console** (`${adminConsoleUrl}/auth/login?org=slug`, target _blank).
- `login/page.tsx` — pantalla split enterprise (panel de marca "Deploy and govern every customer…" + form del token).
- UI kit mínimo (`components/ui.tsx`): Button, Card, Badge, Table, Input, Select, Dialog/Confirm, Toast, Skeleton, EmptyState, ErrorState, CopyButton, OneTimeSecret — paridad visual con la Admin Console (tokens de globals.css de spec 009).

### 2.5 Empaquetado

- `Dockerfile` espejo del console (puerto 3200).
- `docker-compose.yml`: servicio `provider-console` (network_mode host, depends_on api healthy, env TRUST_API_BASE_URL + TRUST_ADMIN_CONSOLE_URL).

## 3. Despliegue y evidencia

1. `docker compose build api provider-console && docker compose up -d api provider-console` (los workers comparten imagen).
2. Tests: `cd platform && .venv/bin/python -m pytest tests/test_provider_admin.py tests/test_provision.py -q` (+ suite completa para no-regresión).
3. `npm run build && npm run lint` limpios en provider-console.
4. Flujo real en navegador (browser tool): login con token real → lista → provision "Globex Industries" → panel OTA (screenshot) → detalle + healthcheck ok → suspend → reactivate (screenshots) → OCR (`tesseract`) de cada screenshot.
5. `docs/provider-console.md` (español): arquitectura, decisiones, runbook, evidencia (curl + SQL + OCR), puertos.

## 4. Riesgos y mitigaciones

- **RLS**: `trust_app` no ve `tenants` sin scope → listado vía migrator (D-PC-04), stats con scope. Mitigado por diseño y tests.
- **Rebuild de imagen**: el API corre en contenedor → tras cambiar `app/`, rebuild + restart (healthcheck del compose lo valida).
- **OTA en UI**: riesgo de "cachear" el secret → solo React state, sin persistence (D-PC-06), test manual de no-reaparición.
- **Colisión de puertos**: 3200 libre (verificado con ss).
