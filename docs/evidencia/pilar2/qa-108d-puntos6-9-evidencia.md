# QA t_108d8de5 — Verificación E2E Pilar 2, puntos 6–9 (agentes default, devices, audit con análisis, branding)

**Revisión maestra**: t_ef306f32 · **Perfil**: qa · **Fecha**: 2026-10-26/27
**Stack vivo durante toda la verificación**: API `:8000` (healthy), Admin Console `:3100` (healthy), Keycloak 26 `:8080`, PostgreSQL `:55432` — `docker compose ps` verificado en vivo.
**Tenant fresco de evidencia**: `p2r108d-223541` (`3207dcda-a126-4d7d-af54-d2280e32494a`), aprovisionado en vivo vía `POST /api/admin/tenants` durante esta revisión. Nada simulado: login PKCE S256 real contra Keycloak, TOTP real (acr=2), keypairs RSA-2048 reales para los devices, PNGs reales (canvas) para los uploads de branding.

## Metodología

1. **Provisioning + sesión** (`qa_108d_provision.py` → `qa_108d_provision.log`): tenant nuevo → primer login PKCE (OTA→password, acr=1) → `require_mfa=true` + `CONFIGURE_TOTP` vía KC Admin REST → re-login PKCE con `acr_values=2` + enrolamiento TOTP real (code 989596) → **acr=2** → sesión minteada (`mfa_verified: true`). 12/12 PASS.
2. **Fase browser real** (navegador contra `:3100`): login por la UI (org code → realm themed → credenciales → one-time code TOTP real) y ejercicio de `/agents`, `/devices`, `/audit`, `/branding` con la sesión real del admin.
3. **Cierre por API/DB**: exchange one-time de devices, heartbeats con device token, cruces de totales del audit contra la API directa, verificación de cadena y eventos en PostgreSQL.

## Punto 6 — Agentes default → **VERIFICADO OK**

**Evidencia UI (navegador real, sesión admin acr=2):**
- `/agents` renderiza la sección **"Default profiles"** con los 4 perfiles del catálogo (`analyst`, `coder`, `ops`, `support`), skills sugeridas, modelos/conectores sugeridos y estado `available` + botón Adopt por perfil.
- Dialog **"Effective policy"** muestra la policy EFECTIVA real del caller: `APPROVED MODELS (EFFECTIVE): glm-5.2 · ALLOWED CONNECTORS: none · BUDGET: within budget` — coincide con `GET /api/agent-profiles` → `effective_policy: {allowed_models: ["glm-5.2"], allowed_connectors: [], budget_cutoff: false}`.
- **Adopt "Analyst" desde la UI** → toast `"Analyst" adopted — agent 13a089ca… is now yours to grant` y la instancia aparece en la tabla Shared agents con `YOUR ACCESS: own` y botones Invoke/Grant access/Revoke grant.
- Dialog **Invoke**: con `glm-5.2` → **"Authorized (200) — billed to 4d8e6362… · access own · model glm-5.2"**; con `gpt-4o` → **"Denied (403) — model_not_approved: Model 'gpt-4o' is not approved for your scope."** (gobernanza visible desde la UI, spec 020 D-020-05).

**Evidencia API (tenant fresco):**
- `GET /api/agent-profiles` → 200, exactamente `['analyst', 'coder', 'ops', 'support']`.
- Re-adopt de `analyst` (ya adoptado) → **422**; key inexistente `/api/agent-profiles/noexiste/adopt` → **404** (AC-020-02).
- Audit en DB: `agent.profile_adopted` ×1, `agent.invoked` ×1, `model.access_denied` ×1.

**Observaciones:** ninguna. Cubierto por spec 020 (t_0ed22c94, done).

## Punto 7 — Devices → **VERIFICADO OK**

**Evidencia (registro + administración):**
- **Enrolamiento real por API** con keypairs RSA-2048 generados en vivo e `id_token` acr=2: `qa108d-device-1` y `qa108d-device-2` → 202 `pending` ambos con `enrollment_token` one-time. (El enroll con id_token sin MFA → 403 `mfa_required` está verificado en el run API del workspace: `pilar2-api-final.log` P7a0.)
- **UI `/devices`**: tabla con ambos devices (fingerprint, usuario, departamento IT Operations, estado pending, fecha), filtros por estado, presencia live ("0 online / 0 active · live").
- **Approve device-1 desde la UI** (dialog de confirmación) → estado `active`.
- **Reject device-2 desde la UI** (dialog de confirmación) → estado `rejected`, "No actions".
- **Exchange one-time por API**: token del device-1 (aprobado) → **200 con device token JWT**; segundo canje → **410 gone**; token del device-2 (rejected) → **410 gone** (sin oráculo, D-DEV-03/04).
- **DeviceGate real**: `POST /api/me/device-heartbeat` con el device token (Bearer + X-Device-Token) → **200** (`device_id 6dfcb826…`, `policy_update_available: true`).
- **Revoke device-1 desde la UI** (dialog: "Its 0 active session(s) are invalidated immediately") → estado `revoked` y el MISMO device token pasa a **401 `device_not_authorized`** en el heartbeat siguiente (invalidación inmediata, REQ-DEV-004).
- **Audit en DB**: `device.enrolled` ×2, `device.approved` ×1, `device.rejected` ×1, `device.revoked` ×1, `device.heartbeat` ×1 — reject y revocado como acciones DISTINTAS (F4).

**Observaciones:** ninguna. Cubierto por spec 007 + fixes F1/F2/F4/F5/F6 (todos done).

## Punto 8 — Audit con análisis → **VERIFICADO OK** (análisis real, no solo la página)

**Evidencia UI + cruces contra API directa:**
- `/audit` muestra los 36 eventos reales del tenant con pill **"Chain intact"** (auto-verify 1×/sesión) y botón **"Verify chain"** (re-verificación manual → "Chain intact"; `POST /api/admin/audit/verify` → `{"status":"ok"}`).
- **Filtros combinables 1:1 con la API**: `action=device.revoked` → UI total 1 == API total 1; `q=gpt-4o` → UI total 1 == API total 1 (`model.access_denied`); con `action=device.revoked` + `q=gpt-4o` activos → total 0 (intersección correcta). Búsqueda libre sobre payloads reales verificada por API: `q=QA 108d Ops` → `department.created`; `q=analyst` → `agent.profile_adopted`; `q=require_mfa` → `policy.updated`.
- **Timeline por actor/sesión** (pestaña con `aria-selected`): eventos del actor `org_admin 4d8e6362…` en orden cronológico, agrupados en **SESSION 1/2/3 — OPENED BY LOGIN** (límites `auth.login` reales), con payload resumido, hash corto y badge por categoría; el rango sin login muestra "EARLIER ACTIVITY" (heurística documentada D-21-04).
- **"View activity"** desde la tabla → aplica el filtro de actor (input `#f-actor` = UUID del admin) y activa la pestaña Timeline.
- **Export trazable**: botones "CSV · 28 / JSON · 28" (total del rango); export CSV del rango filtrado `q=gpt-4o` → 200, **1 fila de datos == total 1**, con columnas `hash_prev`/`hash` reales.
- **`audit.verified` emitido** por cada verificación (×2 en DB) — la verificación es real y auditada (D-AU-05).

**Observaciones:** ninguna. Cubierto por spec 008 (API) + spec 021 (UX, t_c14d6703, done).

## Punto 9 — Branding → **VERIFICADO OK** (2 observaciones menores)

**Evidencia UI (flujo draft→publish→rollback completo desde `/branding`):**
- Estado inicial: `published: v0 · source: provider`, `draft: in sync`.
- **Save draft** (product_name "P2108d Corp AI", tagline, accent #0F766E) → dispara PUT real (performance API: `branding`, `branding/draft`, `branding/versions`) con toast honesto "Draft saved — publish to make it visible to users."
- **Draft invisible (REQ-BRD-004)**: tras el save, el sidebar seguía en "Trust Agent" y la API confirma `GET /api/branding` (published) = `Trust Agent` v0 provider vs `GET /api/branding/draft` = `P2108d Corp AI` con `has_changes: true`.
- **Uploads reales desde la UI**: logo light PNG 1436 bytes y favicon PNG 412 bytes (generados con canvas, subidos por el input file) → quedan en el draft ("image/png · 1.4 KB", "image/png · 412 B", audit `branding.asset.uploaded` ×2).
- **Publish v1** → `published: v1 · source: tenant`, toast "Published as version 1 — visible to all users."; tras reload el **sidebar muestra "P2108d Corp AI" con el logo** servido del asset vía proxy (`/api/proxy/api/branding/assets/e0b916b9…`, alt "P2108d Corp AI logo").
- **Publish v2** (con favicon) → historial append-only; tras reload el **favicon se aplica en runtime**: `<link rel="icon">` → asset `ad2dbed4…` del tenant (REQ-BRD2-005, sin redeploy).
- **Rollback to v1** (dialog auditable) → **v3** con los datos de v1 (sin favicon), historial v1/v2/v3 completo append-only, toast "Rolled back to version 1 (republished as version 3)."
- **DB**: `branding_versions` = 3 filas (v1/v2/v3, published_by = admin); audit `branding.draft.updated` ×1, `branding.published` ×2 (payload con `version` 1 y 2), `branding.rolled_back` ×1 (`from_version: 2, to_version: 1, new_version: 3`).

**Observaciones (menores, no bloqueantes):**
1. **Títulos de página estáticos**: la copy de la propia UI dice "Product name — Shown in the sidebar, login and page titles", pero los `<title>` de todas las páginas del console son metadata estática ("… — Trust Agent Admin Console", `platform/console/src/app/(console)/*/page.tsx`) y no reflejan el `product_name` publicado. Discrepancia copy/comportamiento; el sidebar, el logo y el favicon sí se aplican.
2. **Refresco del brand del shell**: el sidebar/favicon solo reflejan lo publicado tras reload/navegación (el shell no re-fetcha `/api/branding` al publicar desde la propia página /branding). En runtime sí se aplica sin redeploy (REQ-BRD2-005 satisfecho).
3. El lado **desktop** de REQ-BRD2-005 (dashboard del desktop renderizando el logo) no se re-verificó aquí: el desktop-client demo :9090 fue retirado (mandato Miller, nota en docker-compose.yml) y la app enrolada real es territorio del Pilar 3 (no tocar, fuera de mi alcance). La evidencia del lado desktop quedó cubierta por t_d2b65bcf (spec 028, done).

## Veredicto

| Punto | Estado | Resumen de evidencia |
|---|---|---|
| 6. Agentes default | **VERIFICADO OK** | 4 perfiles en UI + adopt + invoke 200/403 visible + audit; re-adopt 422, key 404 |
| 7. Devices | **VERIFICADO OK** | enroll real ×2 → approve/reject/revoke desde UI → exchange 200/410 → heartbeat 200→401 tras revoke + audit completo |
| 8. Audit con análisis | **VERIFICADO OK** | filtros combinables (UI==API), timeline por actor/sesión, Chain intact + verify manual, export trazable, audit.verified |
| 9. Branding | **VERIFICADO OK** (2 obs. menores) | draft→publish v1/v2→rollback v3 desde la UI, uploads reales, draft invisible, sidebar+logo+favicon runtime, audit completo |

**4/4 puntos VERIFICADO OK con evidencia reproducible.** Sin gaps nuevos que deriven a tareas; las 2 observaciones menores del punto 9 quedan documentadas arriba para la consolidación.

## Artefactos de evidencia (workspace t_ef306f32)

- `qa_108d_provision.py` / `qa_108d_provision.log` — provisioning + login PKCE/TOTP + enroll devices (12/12 PASS)
- `qa_108d_p7_exchange.py` / `qa_108d_p7_exchange.log` — exchange one-time 200/410/410 + DeviceGate
- `qa_108d_final_db.py` / `qa_108d_final_db.log` — audit completo del tenant en DB + payloads de branding
- Heartbeats pre/post revoke: HTTP 200 → 401 `device_not_authorized` (curl en vivo)
- Fase browser: transcripciones en este documento (login UI real con TOTP, /agents, /devices, /audit, /branding)
