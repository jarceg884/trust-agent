# Revisión e2e Pilar 2 — Panel admin por cliente (console de org completa)

**Tarea**: t_1c65ff67 (revisión maestra t_ef306f32) · **Perfil**: qa · **Fecha**: 2026-10-26
**Stack vivo**: API `:8000` (imagen rebuild del día), Admin Console `:3100`, Keycloak 26 `:8080`, LiteLLM real `:39989/:4000`, PostgreSQL `:55432` — todos Up durante la revisión.

## Metodología (nada simulado)

- **Run API E2E**: `docs/evidencia/pilar2/qa_pilar2-api-e2e.log` (script reproducible `qa_pilar2_e2e.py` en la misma carpeta). Provisioning de tenant fresco (`pilar2-142440`) vía `POST /api/admin/tenants` → login REAL PKCE S256 contra Keycloak → sesión minteada por el control plane → 71 checks en vivo: **68 PASS / 3 FAIL**.
- **Run browser E2E**: sesión de navegador aislada contra el Admin Console `:3100` con tenant fresco `pilar2-ui-142530` (login real por la UI: org code → credenciales → overview).
- Verificación de estado REAL de las 7 tareas relacionadas del board (ver §Cobertura).

## Reporte por punto (formato: punto → estado → evidencia → tareas que lo cierran)

### 1. Departments CRUD → **PASS**
- **Evidencia API** (P1a-d): `POST /api/departments` con budget inicial → 201 (`budget_cents: 5000`); `PUT` rename → 200; `DELETE` de depto vacío → 200 `{"deleted": true}`; audit en DB: `department.created` ×5, `department.renamed`, `department.deleted`.
- **Evidencia UI**: `/departments` renderiza con "Add department" y tabla de deptos reales (Cloud/Customer/Finance/IT Operations).
- **Tareas**: t_955c98d6 (spec 016, **done**). Cerrado.

### 2. Usuarios y roles → **PASS**
- **Evidencia API** (P2a-f): `GET /api/users` lista admin + usuario; `POST /api/users/{id}/roles` (dept_admin scoped) → 201; `DELETE .../roles/dept_admin?department_id=` → 204; `PUT /api/users/{id}` (asignar depto) → 200; **escalada**: usuario plain asignando roles → 403 `Missing permission: roles.assign`; audit `role.assigned`/`role.revoked` en DB.
- **Evidencia UI**: `/users` renderiza ("Directory of the organization's users… Role changes are audited (role.assigned / role.revoked)").
- **Tareas**: t_5576ee94 (spec 017, **done**). Cerrado.

### 3. Budgets jerárquicos → **PASS**
- **Evidencia API** (P3a-h): `GET /api/budgets/overview` anida deptos y users con Σ; `PUT org` → 200; `PUT dept` → 200; **Σ deptos > org → 422** con mensaje accionable; `PUT /api/budgets/user/{id}` → 200 (edición por usuario, gap F3 cerrado); **Σ usuarios > depto → 422**; id no-UUID → **422** (bug t_378503a1 curado en vivo); `GET /api/budgets/history` → 200.
- **Evidencia UI**: `/budgets` muestra jerarquía "Organization → departments → users. A cutoff blocks all model usage at that level."
- **Tareas**: t_877b0cb5 (QA repro, **done**) + t_6b40c758 (F3, **done**) + t_378503a1 (**done**). Cerrado.

### 4. Policies → **PASS con 1 bug nuevo (derivado a dev)**
- **Evidencia API** (P4a-g): `GET /api/policies?level=org` → 200; `PUT org allowed_models=['glm-5.2']` → 200 y reflejado en la policy efectiva; `PUT user` deny → 200; `DELETE` de policy de usuario → 204; audit `policy.updated` ×6. **require_mfa exigible por policy verificado en vivo**: con `require_mfa=true`, el enroll de device y las sesiones sin MFA reciben 403 `mfa_required` (P7a0/P7a3).
- **BUG (nuevo, B2)**: `allowed_models=[]` (deny-all explícito) **no se aplica**: el combinator deny-overrides computa la policy efectiva del usuario como `allowed_models: []` (`GET /api/me/policies`, verificado), pero `effective_models_for_user` (platform/app/models/access.py:197-199, `policy.get("allowed_models") or []` + truthiness) ignora la lista vacía y `GET /api/models/allowed-for-me` del usuario devuelve `["glm-5.2"]`. Un deny explícito de org/usuario no restringe el gate de modelos. Mismo patrón en los filtros `if dept:`/`if user:` de aprobaciones por nivel.
- **Tareas**: core cubierto (t_3b4fb364, t_f4f99970, t_d41f95dc, **done**). Bug B2 → **nueva tarea dev** (ver §Gaps).

### 5. Model access sobre MaaS oficial → **PASS**
- **Evidencia API** (P5a-g): `GET /api/models` == exactamente los 6 modelos oficiales de Huawei MaaS intl; glm-5.2 con contexto 1M/128K y precios USD/M de la doc oficial (140/440¢); `GET /api/models/access` con sección policy (`approved_models_only: true`); aprobación de deepseek-v4-flash → 201; key virtual → 201; **invocación REAL** glm-5.2 (key virtual → LiteLLM → MaaS) → 200 con contenido; modelo no aprobado con la misma key → 403.
- **Evidencia UI**: `/models` muestra catálogo con "Pricing in USD per million tokens", proveedor `huawei_maas`, precios $/M, bloque "Governance policy" con deny-overrides, botones Approve/Revoke.
- **Tareas**: t_e0943bd8 (spec 018, **done**). Cerrado.

### 6. Agentes default → **PASS**
- **Evidencia API** (P6a-e): `GET /api/agent-profiles` → 4 perfiles (analyst/coder/ops/support); adopt analyst → 201 (instancia tenant-scoped con profile_key); invoke con glm-5.2 → 200 (billed_to verificado); invoke con gpt-4o → 403 `model_not_approved`; audit `agent.profile_adopted` + `agent.invoked` en DB.
- **Evidencia UI**: `/agents` con sección "Default profiles", skills sugeridas, effective policy (models/connectors) y botón Adopt.
- **Tareas**: t_0ed22c94 (spec 020, **done**). Cerrado.

### 7. Devices → **PASS**
- **Evidencia API** (P7a0-g): enroll con id_token sin MFA → 403 `mfa_required` (fail-closed); `require_mfa=true` + enrolamiento TOTP real (baile KC con `acr_values=2`) → **acr=2**; enroll ×2 → 202 pending; approve → 200 active; reject → 200 rejected; **exchange del token one-time → device token** (JWT); audit en DB: `device.enrolled` ×2, `device.approved`, **`device.rejected`** (fix F4 vivo: reject ya NO se audita como device.revoked).
- **Evidencia UI**: `/devices` renderiza con presencia live ("0 online / 0 active · live"), filtros por estado y empty state correcto. El flujo completo desktop→approve→token ya fue verificado desde la UI por t_877b0cb5 (D1-D4 PASS) con los fixes F1/F2/F5/F6 aterrizados.
- **Tareas**: t_877b0cb5 + t_38ce1e82 (F1) + t_6f8a24a5 (F2) + t_9fddcb75 (F5) + t_d3fd75db (F6) + t_c076152d (F4) — todas **done**. Cerrado.

### 8. Audit con análisis → **PASS**
- **Evidencia API** (P8a-d): filtro por `action` → solo esa acción (n>0); filtro por `actor_id` → solo ese actor (n>0); búsqueda libre `q=analyst` → n>0; `POST /api/admin/audit/verify` → `{"status": "ok"}` (cadena íntegra); export CSV con columnas `hash_prev`/`hash` reales.
- **Evidencia UI**: `/audit` con filtros (Actor, Event type, Resource type, Device, Free text, From/To), pestañas Table/Timeline, pill **"Chain intact"**, export "CSV · 14 / JSON · 14", 14 eventos reales del tenant (tenant.provisioned, model.approved, policy.updated, auth.login, audit.verified…).
- **Tareas**: t_c14d6703 (spec 021, **done**) + t_c076152d (F4, **done**). Cerrado.

### 9. Branding → **FAIL en UI (backend PASS) — 2 gaps derivados**
- **Evidencia API** (P9a-d): `GET /api/branding` → defaults del proveedor (`source: provider`); `PUT /api/branding` (product_name/accent_color/tagline) → 200; persistencia verificada (`source: tenant`, valores nuevos); audit `branding.updated` en DB.
- **BUG UI (nuevo, B1)**: en `/branding`, el botón **"Save branding" no dispara NINGÚN request HTTP** (verificado con `performance.getEntriesByType('resource')`: 0 entradas tras el click) y aun así muestra el toast **"Branding saved."** — éxito falso. La DB lo confirma: jsonb de `tenants.branding` sin cambio y **cero** eventos `branding.updated` para el tenant de la sesión UI. Root cause: `platform/console/src/app/(console)/branding/branding-page.tsx:143-149` — el `onClick` solo hace `push("success", "Branding saved.")`; el comentario del archivo (líneas 5-7, "El endpoint NO existe hoy") quedó desactualizado: el endpoint GET/PUT `/api/branding` SÍ existe (t_f72dc691, verificado en vivo). Viola constitution I (nada simulado) a nivel UI.
- **Gap pendiente (B3)**: logo uploads (light/dark, PNG/SVG, max 2 MB), branding versionado con draft/publish y favicon (**REQ-BRD-004**, spec 001) — documentado como "pending contract" en la propia UI y sin ninguna tarea del board que lo cubra.
- **Tareas**: backend cerrado (t_f72dc691, t_884bfcb4, **done**). B1 y B3 → **nuevas tareas** (ver §Gaps).

### 10. Auth UX (Google + auto-registro + MFA opcional sugerido + Keycloak invisible) → **PARCIAL — cubierto por t_1844fb06 (en vuelo)**
- **Evidencia PASS**:
  - Login white-label de la app: `/login` pide **organization code** con "Continue to sign-in", "Join your organization" y "Create your organization" — sin rastro de Keycloak en esa pantalla.
  - **Auto-registro E2E real**: `POST /api/auth/register` → 201; login del auto-registrado **sin TOTP forzado** (MFA opcional, acr=1); entra con rol `user`.
  - **MFA opcional por defecto y exigible**: primer login del admin sin TOTP (acr=1, `require_mfa=false` — feedback #10/t_ccf901f4); con `require_mfa=true` + CONFIGURE_TOTP, el baile real enrola TOTP y entrega **acr=2**; enroll de devices y sesiones sin MFA → 403 fail-closed.
  - **Sugerencia de MFA siempre visible**: banner "Protect your account — Add multi-factor authentication (TOTP)… Set up MFA" presente en TODAS las páginas del console.
  - `GET /api/auth/org-config` público → `{require_mfa: false, allow_self_registration: true, google_login_enabled: false}`.
- **Evidencia FAIL (en vuelo)**:
  - **Keycloak NO invisible aún**: tras el org code, el navegador aterriza en `http://127.0.0.1:8080/realms/<slug>/protocol/openid-connect/auth?...` con título "🐴 Sign in to Trust Agent" (tema custom con placeholder 🐴 — la URL del realm es visible para el usuario).
  - **Google login NO configurado**: `google_login_enabled: false`; KC Admin REST del realm → 0 identity providers.
- **Tareas**: t_1844fb06 (spec 015, **running**) cubre exactamente ambos gaps (Keycloak invisible + Google brokering primera fase). No se crea tarea nueva; esta revisión queda a la espera de su convergencia.

## Cobertura real de las tareas relacionadas

| Tarea | Estado real | Qué cubre de verdad |
|---|---|---|
| t_1844fb06 (Auth UX) | **running** | Login white-label + auto-registro + MFA opcional YA parciales en vivo; Keycloak invisible y Google pendientes (verificados como ausentes) |
| t_955c98d6 (Departments) | done, verificado | Punto 1 completo (API+UI+audit) |
| t_5576ee94 (Roles) | done, verificado | Punto 2 completo, incl. anti-escalada 403 |
| t_e0943bd8 (Model access) | done, verificado | Punto 5 completo con invocación real MaaS |
| t_0ed22c94 (Agentes default) | done, verificado | Punto 6 completo con policy 403 |
| t_c14d6703 (Audit UX) | done, verificado | Punto 8 completo (filtros/timeline/verify/export) |
| t_877b0cb5 (QA console) | done, verificado | Puntos 3 y 7; sus 6 hallazgos F1-F6 están todos cerrados (done) y F4 re-verificado vivo aquí |

## Gaps convertidos en tareas (enlazadas al gate t_e8a28fce)

- **B1** (nueva, @ui): Branding UI — "Save branding" no llama al API; toast de éxito falso (branding-page.tsx:143-149).
- **B2** (nueva, @dev): Policy enforcement — `allowed_models=[]` (deny-all) ignorado por `effective_models_for_user` (models/access.py:197-199); contradice al combinator.
- **B3** (nueva, @dev): REQ-BRD-004 — logo uploads, branding versionado draft/publish y favicon (spec 001), hoy sin tarea.
- **En vuelo**: t_1844fb06 (@dev) — Keycloak invisible + Google (punto 10).

## Veredicto (primera pasada, 2026-10-26 ~14:50)

**8/10 puntos PASS con evidencia reproducible · 1 PASS con bug derivado (Policies) · 1 FAIL de UI con backend sano (Branding) · 1 PARCIAL en vuelo (Auth UX)**. Esta tarjeta quedó **bloqueada** esperando la convergencia de B1 (t_6d3f5016) + B2 (t_f0d3122d) + B3 (t_d2b65bcf) + t_1844fb06.

---

# RE-VERIFICACIÓN post-convergencia (run 219, 2026-10-26 ~21:30)

Los 4 padres convergieron (t_6d3f5016, t_f0d3122d, t_d2b65bcf, t_1844fb06 — todos done). Re-revisión de los puntos 4, 9 y 10 desde el estado REAL. Stack vivo: api/console/idp/db Up; el contenedor api corre EXACTAMENTE el código del host (md5 idénticos de `app/models/access.py` y `app/org/branding.py` en bind mount y site-packages); `alembic_version=0021_hermes_download`; `catalog.policy_keys.allowed_models` default `null` (migración 0019 aplicada).

### 4. Policies → **PASS (bug B2 cerrado y re-verificado en vivo)**
- Script del fix re-ejecutado por QA de forma independiente (dentro del contenedor api, tenant fresco `f0d3-031349`): **24 PASS / 0 FAIL** — `docs/evidencia/pilar2/qa-rerev-p4-deny-all.log`.
- Escenario exacto del fallo QA P4e (E2): org `allowed_models=["glm-5.2"]` + user `allowed_models=[]` → `GET /api/models/allowed-for-me` del worker = `[]` (antes devolvía `["glm-5.2"]`), `POST /api/models/verify` → 403 `model_not_approved`, audit `model.access_denied` en DB.
- Semántica completa: clave ausente = heredar (E1/E3/E5, default null), deny-all a nivel org bloquea a TODO el tenant incluido el admin (E4), una revocación jamás relaja.
- Tarea que lo cierra: t_f0d3122d (**done**).

### 9. Branding → **PASS (API 27/27 + UI real draft→publish)**
- **API** (probe QA independiente, tenant fresco `qa-p2r9-031802`, login real PKCE): **27 PASS / 0 FAIL** — `docs/evidencia/pilar2/qa-rerev-p9-branding.log` + script `qa_rerev_p9_branding.py`. Contrato spec 028 completo: PUT edita el DRAFT y el published NO cambia (REQ-BRD-004, verificado también en DB `tenants.branding` vs `branding_draft`); publish atómico v1/v2 con audit `branding.published`; historial append-only; upload de asset PNG multipart con sha256 local == reportado == ETag y bytes servidos EXACTOS (70/70); rollback re-publica como versión nueva (v3) preservando v1/v2; audit completo (`draft.updated` ×2, `published` ×2, `rolled_back`, `asset.uploaded`).
- **UI** (navegador real, login por la UI, `docs/evidencia/pilar2/qa-rerev-p9-p10-browser.log`): el bug B1 ("Save branding" con 0 requests y toast falso) está CERRADO — "Save draft" dispara PUT real (performance API: 4→7 entradas, incluida `/api/proxy/api/branding` + refetch de draft/versions) con toast honesto "Draft saved — publish to make it visible to users."; "Publish (→ v4)" dispara POST `/api/branding/publish` (7→11 entradas) con toast "Published as version 4 — visible to all users."; tras reload duro el sidebar muestra "QA P2R9 UI Corp". DB en sync: `published=QA P2R9 UI Corp version=4`, audit `branding.draft.updated` ×3 (2 API + 1 UI) y `branding.published` ×3, `branding_versions` = 4 filas append-only.
- Tareas que lo cierran: t_d2b65bcf (**done**, spec 028 + REQ-BRD-004) + t_6d3f5016 (**done**, fix UI preservado como base del flujo draft→publish).

### 10. Auth UX → **PASS con 2 gaps residuales derivados a tarea**
- **Keycloak invisible — cuerpo y título PASS**: la página del realm está themed (header "TRUST AGENT", "Sign in to your account", **0 menciones de "Keycloak"** en el DOM). El título SERVIDO es limpio: `<title>Sign in to Trust Agent</title>` (verificado con curl reproduciendo UA/headers/cookies del navegador y Accept-Encoding br/zstd, y con `fetch()` desde dentro de la página: 0 🐴). El "🐴" que ve el navegador del tooling de QA es un **artefacto del harness** (script inyectado vía CDP que prefija el emoji del favicon a `document.title`; atrapado con MutationObserver + trampa en el setter, stack `at <anonymous>:1:51`; correlación confirmada: el console :3100 no sirve favicon y no se le muta el título). La nota del dev en `docs/evidencia/auth-ux/EVIDENCIA.md` era correcta.
- **Gap residual REAL (nuevo, B4)**: el **favicon** servido del realm es el **caballo de Keycloak** — `platform/idp-themes/trust-agent/login/` no trae `img/favicon.ico` y KC sirve el del tema padre `keycloak.v2` (md5 `0980755aea543da1af848366656d1d9c` byte-idéntico vía `/resources/<v>/login/keycloak.v2/img/favicon.ico`). Un usuario real ve el caballo de KC en la pestaña → AC-AUTH-001 ("sin branding de Keycloak") no es 100%. → **tarea t_13f8b623 (@dev)**, enlazada al gate t_e8a28fce.
- **Auto-registro PASS**: re-verificado en vivo en el run API (E0d `POST /api/auth/register` → 201; E0e login REAL del auto-registrado → sesión operativa); login de la app con "Join your organization" / "Create your organization".
- **MFA opcional sugerido PASS**: banner persistente "Protect your account — Add multi-factor authentication (TOTP)… Set up MFA" verificado en /overview y /branding con sesión real acr=1; `org-config` público `{require_mfa: false, allow_self_registration: true}`; exigible por policy (403 mfa_required verificado en la primera pasada y por t_1844fb06).
- **Google — fail-closed PASS; punta a punta GAP (B5, decisión humana)**: sin credenciales del operador, `google_login_enabled=false` y **0 botones Google** en el login (verificado en navegador real — ningún elemento roto). AC-AUTH-006 (login Google punta a punta) requiere `TRUST_GOOGLE_CLIENT_ID/SECRET` del operador → **tarea t_f372f47f (@dev, bloqueada por decisión humana)**, enlazada al gate t_e8a28fce.
- Tareas que lo cierran: t_1844fb06 (**done**) + t_13f8b623 (favicon, nueva) + t_f372f47f (Google AC-AUTH-006, nueva bloqueada).

## Veredicto final (re-verificación)

**10/10 puntos verificados con evidencia reproducible**: 8 PASS limpios + punto 4 PASS (bug B2 cerrado y re-verizado 24/24) + punto 9 PASS (API 27/27 + UI real) + punto 10 PASS con 2 gaps residuales explícitos convertidos en tarea (B4 favicon → t_13f8b623; B5 Google AC-AUTH-006 → t_f372f47f, requiere decisión humana). Ambas tareas están enlazadas al gate final t_e8a28fce. Esta tarjeta queda **bloqueada** esperando la convergencia de t_13f8b623 + t_f372f47f; al converger se re-revisa el estado REAL de ambos gaps.
