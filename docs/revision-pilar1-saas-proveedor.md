# Revisión e2e Pilar 1 — SaaS proveedor: tenants y link de descarga de Hermes

> Tarea: t_fb31dc49 (revisión Pilar 1 de la revisión maestra t_ef306f32).
> Fecha: 2026-10-26 · Perfil: qa · Entorno: dev-local vivo (api :8000, KC 26.2 :8080, console :3100, provider-console :3200, db PostgreSQL 17).
> Nada simulado: toda la evidencia abajo es de ejecución real contra los servicios vivos, con un tenant nuevo provisionado desde la UI durante esta revisión (`qa-pilar1-review`).

## Veredicto por flujo

### Flujo 1 — Crear clientes (tenants) desde la UI con provisioning idiom-proof → **PASS**
- Acción real: login en Provider Console (`http://127.0.0.1:3200/login`) con token de operador (cookie httpOnly); formulario `/tenants/new` llenado y enviado desde la UI: "QA Pilar1 Review Corp", admin `admin@qa-pilar1-review.test`, budget $250/mes, slug `qa-pilar1-review`, plan Standard, 4 deptos seed.
- Resultado UI: panel OTA one-time con password temporal mostrado UNA sola vez, issuer `http://127.0.0.1:8080/realms/qa-pilar1-review`, client `trust-agent-web`, healthcheck ok, 4 departamentos, 6 modelos aprobados, 3 políticas default.
- Verificación backend (todo real):
  - `curl http://127.0.0.1:8080/realms/qa-pilar1-review/.well-known/openid-configuration` → **200** (realm KC real).
  - `GET /api/admin/tenants/qa-pilar1-review` → tenant `active`, counts: departments 4, users 1, org_budgets 1, active_org_policies 3, approved_models 6.
  - psql (db `trust_agent`): tenants row `active`; `departments` = 4; `budgets` org = 25000 cents; `policies` = 3 (`approved_models_only`, `explicit_shared_agent_grants`, `scoped_credentials`); `model_access` = 6.
  - `audit_log`: `tenant.provisioned` + 6 × `model.approved` + 3 × `policy.updated`, todos `actor_role=provider` (ts 2026-10-26 19:20:49 UTC).
- Primer login del admin ejecutado de verdad: baile OIDC PKCE S256 (authorize con `code_challenge_method=S256`), `UPDATE_PASSWORD` forzado por Keycloak, sesión en Admin Console operativa.

### Flujo 2 — Listar tenants → **PASS**
- `/tenants` del Provider Console: tabla con nombre, slug, status, realm, users, departments, org budget, spend del período, fecha de creación y link a detalle.
- Antes del provisioning: 46 filas. Después: **48 filas**, con `QA Pilar1 Review Corp · qa-pilar1-review · Active · qa-pilar1-review · 1 · 4 · $250.00 · $0.00 · Oct 26, 2026`.
- Consistente con `GET /api/admin/tenants` (API del provider, bearer token).

### Flujo 3 — Ver el detalle de un tenant → **PASS**
- `/tenants/qa-pilar1-review`: nombre, badge Active, botón Suspend, slug, realm KC, created, users, departments, OIDC issuer, budget del período ($0.00 de $250.00), healthcheck en vivo (botón Run healthcheck), entry point del Admin Console (`http://127.0.0.1:3100/auth/login?org=qa-pilar1-review`).
- **Sin bloque de download link** en el detalle del Provider Console — corrección Miller (t_360a752e) aplicada y verificada en vivo.

### Flujo 4 — Suspender y reactivar un tenant → **PASS**
- Desde la UI con ConfirmDialog ("Suspend this tenant? … This action is audited"):
  - Suspend → UI badge **Suspended** + botón cambia a "Reactivate tenant"; psql `status=suspended`; audit `tenant.suspended` (actor provider, 19:22:31 UTC).
  - Reactivate → UI badge **Active**; psql `status=active`; audit `tenant.reactivated` (19:22:48 UTC).
- **El corte de acceso es real e inmediato**: con el tenant suspendido, la sesión ya autenticada del Admin Console muestra "This organization is currently suspended. GET /api/budgets/overview · tenant_suspended · request req_97be19e050e24dcdb4d2" y los datos dejan de cargar; tras reactivar, la misma sesión vuelve a ver el budget ($250.00) sin re-login.
- Ciclo repetido vía API (POST suspend/reactivate) con los mismos resultados y audit (`tenant.suspended` 19:27:31, `tenant.reactivated` 19:27:48).

### Flujo 5 — Enviar el link de descarga de Hermes por tenant → **PASS** (re-verificado 2026-10-27 tras convergencia de t_526030f3)

**Re-verificación post-convergencia (estado REAL, nada simulado):** el backend de la spec 023 está desplegado y vivo; el gap del primer intento (404) quedó cerrado.

- **Link generado y asociado al tenant correcto** (tenant de prueba `qa-pilar1-review`, provisionado desde la UI en el intento 1):
  - `GET http://127.0.0.1:8000/download/qa-pilar1-review` → **200**, página pública con branding real del tenant ("QA Pilar1 Review Corp · organization edition").
  - `GET /download/qa-pilar1-review/bundle` → **200**, tarball REAL de 32 KB (`application/gzip`): `install.sh`, `trust-login`, `plugin/trust-agent-governance/*` (spec 022), README y `manifest.json`.
  - `manifest.json` del bundle: `org_code: "qa-pilar1-review"`, `slug: qa-pilar1-review`, `oidc_issuer_url: http://127.0.0.1:8080/realms/qa-pilar1-review`, `sha256` por miembro — **binding al tenant correcto**.
  - Anti-enumeración: `GET /download/no-such-tenant-xyz` → **404**.
  - Audit `download.issued` por descarga sobre el tenant correcto (`cf8d884d…` = qa-pilar1-review): mi curl generó la entrada `2026-10-27 01:20:40 UTC` con actor system.
- **Gestión del link desde la UI como org_admin** (login PKCE S256 real contra el realm del tenant, `acr_values=1` coherente con `require_mfa=false`; password del admin reseteado vía Keycloak Admin REST para esta re-verificación):
  - `/hermes-download` del Admin Console: link estable `org: qa-pilar1-review` → `http://127.0.0.1:8000/download/qa-pilar1-review`, copy link, invitación al empleado (copy invite message / open email draft mailto por tenant).
  - Card "Link availability" con el toggle **ACTIVO** (el banner "Link management API not deployed yet" desapareció): badge **Enabled** + "Last changed 2026-10-27T00:08:46".
  - **Disable desde la UI** (botón + ConfirmDialog "This action is audited"): toast "Download link disabled — the public download page now refuses new downloads.", badge **Disabled**; efecto público real: bundle → **403 `link_disabled`** ("Downloads are currently disabled for this organization…", req_013dba3b9b3740a2bd37), página pública → 200 con estado honesto "disabled new downloads. Please contact them…"; audit `download.link.disabled` (actor org_admin, 01:23:52 UTC).
  - **Re-enable desde la UI**: toast "Download link enabled — employees can download the app again.", badge **Enabled**; página y bundle → **200** de nuevo; audit `download.link.enabled` (actor org_admin, 01:24:13 UTC).
- **Suite**: `platform/tests/test_hermes_download.py` → **9 passed** (re-ejecutada por QA en esta revisión).
- Evidencia adicional: `docs/evidencia/revision-pilar1/hermes-download-toggle-enabled.png` (Admin Console con toggle activo) y `download-page-disabled.html` (página pública con link disabled).
- Nota: el E2E completo download→install→trust-login→auto-enroll→approve→gobernanza fue verificado por t_526030f3 con navegador real (AC-HDL-001..010, docs/hermes-download.md); el follow-up abierto t_dd2f5f65 (race multi-proceso del auto-enroll, [MEDIO], lane plugin spec 022) NO afecta este flujo (link→descarga→binding) y ya está trackeado en su lane.

## Hallazgos (fuera de los 5 flujos)

1. **[MENOR — CERRADO por t_ccf901f4] Copy del panel OTA sobreprometía TOTP forzado** — el panel decía "The first sign-in forces a password change and TOTP (MFA) enrollment" cuando el TOTP solo es forzado con policy `require_mfa=true`. **Re-verificado en vivo tras el fix**: el copy nuevo ("TOTP (MFA) enrollment is enforced only when the organization policy require_mfa is enabled") está servido en los chunks de :3200 (1 aparición); el copy viejo ("forces a password change and TOTP" / "mandatory TOTP") ausente (0 apariciones). Coherente con lo observado en esta revisión: el login del admin usa `acr_values=1` y el Admin Console muestra el banner opcional "Set up MFA".
2. **[INFO] MFA por defecto opcional** — coherente con spec 015; no es bug, pero el producto prometido en DECISIONES ("SSO+MFA") queda a decisión de cada org vía policy `require_mfa`.

## Tareas que cierran los gaps
| Gap | Tarea | Estado |
|---|---|---|
| Endpoint público `/download/{slug}` + bundle real + enable/disable (flujo 5 backend) | t_526030f3 (spec 023, @dev) | **done** — re-verificado PASS por QA en esta revisión (enlazada a t_e8a28fce) |
| Copy OTA del Provider Console (TOTP "forzado") | t_ccf901f4 (@ui) | **done** — copy nuevo verificado servido en vivo (enlazada a t_e8a28fce) |

**Sin gaps abiertos**: los 5 flujos del Pilar 1 están verificados PASS con evidencia reproducible.

## Cobertura real de t_4151f562 frente a este alcance
t_4151f562 (Provider Console, **done**) cubre de verdad los flujos 1-4: provisioning idiom-proof desde la UI, lista, detalle con healthcheck, suspend/reactivate auditados — re-verificados aquí de primera mano. Su implementación original del download link en el Provider Console fue corregida por t_360a752e (**done**, mudanza al Admin Console del cliente). El backend del flujo 5 nunca estuvo en su alcance: es t_526030f3.

## Cómo reproducir
1. `curl -s http://127.0.0.1:8080/realms/qa-pilar1-review/.well-known/openid-configuration -o /dev/null -w '%{http_code}'` → 200
2. `curl -s -H "Authorization: Bearer $TRUST_PROVIDER_TOKEN" http://127.0.0.1:8000/api/admin/tenants/qa-pilar1-review` → JSON del tenant (active)
3. `docker exec trust-agent-db psql -U postgres -d trust_agent -c "SELECT action,actor_role,ts FROM audit_log WHERE tenant_id=(SELECT id FROM tenants WHERE slug='qa-pilar1-review') AND action LIKE 'tenant%' ORDER BY ts DESC;"` → provisioned/suspended/reactivated/suspended/reactivated
4. UI: `http://127.0.0.1:3200/tenants` (login con token de operador) · `http://127.0.0.1:3100/hermes-download` (login admin del tenant)
5. `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/download/qa-pilar1-review` → 200 (pública, sin login); `curl -s http://127.0.0.1:8000/download/qa-pilar1-review/bundle -o bundle.tar.gz` → tarball real con `manifest.json` (`org_code=qa-pilar1-review`)
6. Toggle: `http://127.0.0.1:3100/hermes-download` (login org_admin) → "Disable link" → `curl :8000/download/qa-pilar1-review/bundle` → 403 `link_disabled` → "Enable link" → 200; audit `download.link.disabled`/`download.link.enabled` en `audit_log`.
