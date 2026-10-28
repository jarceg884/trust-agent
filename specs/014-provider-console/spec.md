# Spec 014 — Provider Console (panel del proveedor para desplegar tenants)

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-26 |
| **Autor** | ui (tarea t_4151f562, feedback Miller #1) |
| **Padre** | specs/004-tenant-onboarding/spec.md (provisioning idiom-proof), specs/009-admin-console/spec.md (patrón de console Next.js + BFF), specs/003-control-plane/spec.md (núcleo) |
| **Idioma** | Español (docs) / inglés (código, UI copy) / USD centavos (montos) |

---

> ## CORRECCIÓN DE MILLER (2026-10-26, tarea t_360a752e) — download link REMOVIDO
>
> El **"Hermes download link"** (D-PC-09, REQ-PC-017, AC-PC-017) fue implementado
> en esta console pero **NO pertenece aquí**: el proveedor solo crea/gestiona
> tenants. El link de descarga lo genera y envía el **ADMIN DEL CLIENTE** desde
> SU Admin Console (`platform/console`, sección "Hermes Download" — ver nota
> equivalente en specs/009-admin-console/spec.md). En esta corrida (t_360a752e):
>
> - Se eliminó el botón/link del detalle de tenant (`tenant-detail.tsx`),
>   la función `hermesDownloadUrl` y la config `desktopAppUrl`
>   (`TRUST_DESKTOP_APP_URL` fuera del docker-compose).
> - D-PC-09, REQ-PC-017 y AC-PC-017 quedan **REVOCADOS** (supercedidos por la
>   sección "Hermes Download" de la spec 009). La evidencia histórica
>   `docs/evidencia/provider-console/10-hermes-download-link-prefill.png`
>   documenta el estado ANTES de la corrección.
> - El resto de la spec (provisioning, lista, suspend/reactivate, healthcheck,
>   deep-link a la Admin Console) queda vigente sin cambios.

---

## 1. Problema y alcance

**Problema (feedback Miller #1)**: no existe panel donde el **proveedor** despliegue clientes. La Admin Console (:3100) es por-tenant (login OIDC del realm del cliente); el provisioning hoy es solo `POST /api/admin/tenants` vía script/CLI (`trust-provision`). La operación provider no tiene superficie visual.

**Dentro de alcance**:

1. **Extensión del API provider** (spec 004 §4, superficie v1 → v2):
   - `GET /api/admin/tenants` — lista de tenants con: nombre, slug, estado (`active`/`suspended`), realm KC, usuarios, departamentos, presupuesto org (USD centavos), gasto del período corriente, fecha de creación.
   - `POST /api/admin/tenants/{slug}/suspend` y `POST /api/admin/tenants/{slug}/reactivate` — cambio de estado auditable (REQ-TEN-006, aplazado desde spec 004).
2. **Provider Console** — app Next.js nueva (`platform/provider-console/`, puerto **3200**):
   - Login del operador del proveedor (provider token, verificado contra el control plane).
   - Lista de tenants (tabla enterprise con estado, realm, usuarios, presupuesto, gasto, creación).
   - Provisioning de nuevo cliente desde la UI — reusa el flujo idiom-proof de spec 004 TAL CUAL (misma capa de servicio `provision()`): realm KC real, admin inicial con password temporal (primer login fuerza `UPDATE_PASSWORD`; el TOTP **no** se fuerza como required action — corrección de copy QA Pilar 1, t_ccf901f4: desde spec 015 el enrolamiento TOTP es policy-aware, forzado solo con `require_mfa=true`, si no sugerido vía banner "Set up MFA" en la Admin Console), departamentos seed, presupuesto org, políticas default, `model_plan`. La **OTA one-time se muestra UNA sola vez** en la UI (panel con warning + copy; no se persiste en ningún store).
   - Suspender / reactivar tenant desde la UI (con confirmación).
   - Detalle por tenant: estado canónico, counts, healthcheck en vivo, y **link a su Admin Console** (`:3100/auth/login?org=<slug>`).
3. **Evidence real**: provisionar un tenant REAL desde la UI nueva y documentar evidencia (screenshots + OCR + curl + SQL) en `docs/provider-console.md`.

**Fuera de alcance** (documentado):

- Edición de branding/presupuestos del tenant desde el Provider Console (eso vive en la Admin Console del propio tenant, spec 009).
- Borrado (decomision) de tenants: imposible por diseño (audit append-only + FK, D-ONB-03/09); no se inventa.
- Email de invitación al admin (D-ONB-05: entrega one-time al operador).
- Nuevos permisos RBAC: se reusa `tenants.provision` (D-PC-03).

## 2. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| D-PC-01 | **App separada `platform/provider-console` en :3200** (no una sección de la Admin Console). Justificación: modelo de auth distinto — la Admin Console autentica usuarios de tenant vía OIDC/PKCE del realm del cliente y su BFF adjunta cookies de sesión `trust_session`; el Provider Console autentica al **operador del proveedor** vía bearer token (`TRUST_PROVIDER_TOKEN`, D-ONB-02) que el BFF inyecta como header `Authorization`. Mezclar ambos primitivos en un solo BFF cruzaría fronteras de confianza (una sesión de tenant jamás debe poder portar `tenants.provision`; `platform_admin` NO es rol de tenant). Puerto 3200 = convención local (3100 console, 3200 provider). | spec 009 D-AC-01, spec 004 D-ONB-02, constitution III/IV |
| D-PC-02 | **Login del operador = provider token pegado una vez**. El route handler `/auth/provider/login` valida el token contra el control plane (`GET /api/admin/tenants` → 200/401) y lo guarda en cookie **httpOnly** `ta_provider` (sameSite=lax, path=/). El proxy BFF (`/api/proxy/[...path]`) lo reenvía como `Authorization: Bearer`. El token jamás vive en localStorage ni en respuestas de API. Logout = borrar cookie. Sin token configurado en el API → el login falla con el 503 real del control plane (fail-closed). CSRF: las rutas provider del control plane autentican por **header**, no por cookie ambiental, por lo que un cross-site no puede dispararlas; el BFF además solo acepta same-origin. | constitution IV, D-ONB-02 |
| D-PC-03 | **Reuso del permiso `tenants.provision`** para TODAS las rutas `/api/admin/tenants*` nuevas (list/suspend/reactivate). Es data-driven (`catalog.role_permissions`, rol `platform_admin`); crear un permiso nuevo exigiría migración de catálogo sin beneficio de gobernanza (el actor es el mismo operador). Quitar la fila del catálogo debe negar también las rutas nuevas (mismo check). | D-ONB-02, I10 |
| D-PC-04 | **Listado cross-tenant**: las filas de `tenants` se leen vía la conexión `trust_migrator` (BYPASSRLS) — generalización documentada del lookup por slug de D-ONB-10, mismo actor (proveedor) y misma justificación (RLS oculta `tenants` a `trust_app` sin scope). Los **stats por tenant** (usuarios, deptos, presupuesto org, gasto del período) se leen como `trust_app` CON scope del tenant (bucle por tenant), nunca cross-tenant como app. | D-ONB-10, constitution III |
| D-PC-05 | **Suspend/reactivate es transaccional + auditado + idempotente**: `UPDATE tenants SET status=…` como `trust_app` en el scope del tenant + evento `tenant.suspended`/`tenant.reactivated` (actor `provider`) en la MISMA transacción. Re-suspender un tenant ya suspendido → `200` con estado actual, **sin** evento duplicado. El core ya niega cada request de un tenant suspendido (`403 tenant_suspended` en la validación de sesión, verificado en `auth/service.py`) — suspender NO toca Keycloak: el corte es inmediato en el control plane y el realm queda intacto para la reactivación. | REQ-TEN-006, constitution IV/V |
| D-PC-06 | **OTA one-time en la UI**: la respuesta `201` de provisioning contiene `one_time_activation` (password temporal, issuer, client_id) que se renderiza UNA vez en un panel con warning explícito ("shown only once — copy it now") y botones copy. El valor vive solo en memoria del componente (React state); no se persiste (sin localStorage/sessionStorage/DB) y al navegar se pierde — paridad exacta con D-ONB-05 (la re-ejecución idempotente no lo re-entrega). | D-ONB-05, constitution IV |
| D-PC-07 | **Link a la Admin Console del tenant** = `http://127.0.0.1:3100/auth/login?org=<slug>` (deep-link al baile PKCE del realm del cliente; el route handler de spec 009 acepta `?org=`). El Provider Console NO minta sesiones del tenant ni salta el login/MFA del cliente. | spec 009 REQ-AC-001, constitution IV |
| D-PC-08 | **Gasto del período corriente** = `SUM(spent_cents)` de `budget_counters` nivel `org` cuyo período contiene `today`; sin fila → `0`. Montos en centavos en el API; la UI formatea a USD. | schema 002 §5.6, constitution VI |
| D-PC-09 | **[REVOCADA — corrección Miller 2026-10-26, t_360a752e]** Link de descarga del desktop por tenant. Implementada originalmente aquí, fue REMOVIDA: el proveedor no envía links de descarga; el admin del cliente lo genera desde SU Admin Console (spec 009, sección "Hermes Download"). El contrato de binding (org_code público embebido en el link, sin secretos) se mantiene, ahora sobre el endpoint estable `/download/{tenant_slug}` de la spec 023. | comentario Miller, specs 007/010/023, constitution IV |

## 3. Requisitos (formato EARS)

### 3.1 Superficie API del proveedor (backend)

**REQ-PC-001** (M) [D-PC-03/04] — Cuando el proveedor llama `GET /api/admin/tenants`, el sistema devuelve la lista completa de tenants con `slug`, `name`, `status`, `oidc_realm`, `created_at` y stats por tenant (`users`, `departments`, `org_budget_cents`, `spent_cents_period`), ordenada por `created_at`; sin token → 401; sin permiso en la matriz → 403.
> **AC-PC-001**: con token válido → 200 con ≥1 tenant (los seed); cada item contiene todos los campos; sin `Authorization` → 401 `invalid_token`; con la fila `platform_admin→tenants.provision` borrada → 403 `permission_denied` (data-driven).

**REQ-PC-002** (M) [D-PC-05, REQ-TEN-006] — Cuando el proveedor suspende un tenant activo (`POST …/{slug}/suspend`), el sistema pone `status='suspended'`, registra `tenant.suspended` (actor `provider`) en la misma transacción y responde `200` con el estado resultante; un usuario con sesión activa del tenant recibe `403 tenant_suspended` en su siguiente request.
> **AC-PC-002**: suspend de un tenant seed activo → 200, `status='suspended'` (SQL), evento `tenant.suspended` en `audit_log` con `actor_role='provider'`; una request autenticada del tenant → 403 `tenant_suspended`.

**REQ-PC-003** (M) [D-PC-05] — Cuando el proveedor reactiva un tenant suspendido (`POST …/{slug}/reactivate`), el sistema pone `status='active'`, registra `tenant.reactivated` y responde `200`; slug inexistente → 404; re-suspender/reactivar sin cambio de estado → `200` idempotente sin evento nuevo.
> **AC-PC-003**: ciclo suspend→reactivate → `active` en SQL + evento `tenant.reactivated`; segunda suspend consecutiva → 200 y el conteo de eventos `tenant.suspended` del tenant NO cambia; slug aleatorio → 404.

**REQ-PC-004** (M) [D-PC-03] — Cuando cualquiera de las rutas nuevas recibe request sin token de proveedor o sin el permiso, el sistema responde 401/403 igual que las rutas existentes de spec 004 (mismo `Depends(get_provider_principal)` + check data-driven).
> **AC-PC-004**: matriz 401/403/200 replicada para `GET /api/admin/tenants`, suspend y reactivate.

**REQ-PC-005** (M) [constitution IV] — Cuando el API responde cualquier ruta provider, el sistema NO incluye secretos (ni el token del proveedor, ni passwords, ni KEK) en el cuerpo de respuesta.
> **AC-PC-005**: `grep` del valor del provider token sobre los cuerpos de respuesta de list/suspend/reactivate → 0 apariciones.

### 3.2 Provider Console (frontend)

**REQ-PC-010** (M) [D-PC-01/02] — Cuando el operador abre el Provider Console sin cookie `ta_provider`, el sistema muestra el login (pega del provider token); token inválido → error visible con el reason real del control plane; token válido → cookie httpOnly + redirect a la lista.
> **AC-PC-010**: flujo real en navegador: token incorrecto → mensaje de error; token correcto → lista de tenants visible; la cookie es httpOnly (inspección devtools); logout → vuelve al login.

**REQ-PC-011** (M) — Cuando el operador ve la lista, el sistema muestra por tenant: nombre, estado (badge active/suspended), realm KC, usuarios, presupuesto org (USD), gasto del período (USD), fecha de creación — datos reales de `GET /api/admin/tenants`, con estados de carga y error reales (sin mocks).
> **AC-PC-011**: la tabla renderiza los tenants reales del entorno (screenshot + OCR); un tenant suspendido muestra badge distinto.

**REQ-PC-012** (M) [spec 004 REQ-ONB-001, D-PC-06] — Cuando el operador completa el formulario de provisioning (organization name, admin email, budget USD, model plan, departamentos, slug opcional, branding opcional) y lo envía, el sistema llama al `POST /api/admin/tenants` real y muestra el resultado: tenant creado (id, slug, realm, issuer) y el panel **one-time** con las credenciales del admin inicial (password temporal, issuer, client_id) visible UNA sola vez con warning y copy; errores del API (422 de validación, 503, etc.) se muestran con su reason real.
> **AC-PC-012**: provisioning REAL desde la UI (navegador): tenant nuevo visible en la lista; SQL: 1 tenant, 4 deptos seed, 1 org_admin, 1 presupuesto org, 4 políticas, `model_access` aprobadas; realm KC existe (discovery 200); el panel OTA se muestra una vez y NO reaparece al recargar la lista/detalle.

**REQ-PC-013** (M) [D-PC-05] — Cuando el operador suspende/reactiva desde el detalle (con diálogo de confirmación), el sistema llama al endpoint real y refleja el nuevo estado (badge + toast); el estado en UI == estado en SQL.
> **AC-PC-013**: ciclo suspend→verify (SQL)→reactivate→verify desde la UI con screenshots.

**REQ-PC-014** (M) [D-PC-07] — Cuando el operador abre el detalle de un tenant, el sistema muestra estado canónico, counts, presupuesto/gasto, botón healthcheck en vivo (resultado real `GET …/healthcheck`) y el link "Open Admin Console" que abre `:3100/auth/login?org=<slug>`.
> **AC-PC-014**: detalle del tenant provisionado con healthcheck `status=ok` visible; el link apunta al realm correcto.

**REQ-PC-017** (M) — **[REVOCADO — corrección Miller 2026-10-26, t_360a752e]** El "Hermes download link" ya NO se muestra en el Provider Console: el detalle de tenant solo ofrece el deep-link a la Admin Console del cliente. El link de descarga vive en el Admin Console del cliente (spec 009, REQ-AC-033).
> **AC-PC-017** (revocado): el detalle de tenant NO muestra ningún bloque "Hermes download link" (verificado por screenshot + OCR tras la corrección).

**REQ-PC-015** (M) [constitution VI, tarea] — Cuando se construye la app, `npm run build` compila limpio (exit 0) y `npm run lint` sin errores.
> **AC-PC-015**: salida real de build/lint adjunta en docs.

**REQ-PC-016** (M) [tarea, constitution I] — Cuando la tarea converge, existe evidencia real del provisioning desde la UI: screenshots con OCR, curl de los endpoints nuevos y verificación SQL, documentada en `docs/provider-console.md`.
> **AC-PC-016**: docs con evidencia reproducible.

## 4. Contrato de superficie v2 (extiende spec 004 §4)

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/api/admin/tenants` | Provider token + `tenants.provision` | Lista de tenants + stats (REQ-PC-001) |
| POST | `/api/admin/tenants/{slug}/suspend` | ídem | Suspende (idempotente, auditado) (REQ-PC-002) |
| POST | `/api/admin/tenants/{slug}/reactivate` | ídem | Reactiva (idempotente, auditado) (REQ-PC-003) |

`GET /api/admin/tenants` responde:
```json
{ "tenants": [ { "id", "slug", "name", "status", "oidc_realm", "oidc_issuer_url",
                 "created_at", "counts": { "users", "departments" },
                 "org_budget_cents", "spent_cents_period" } ] }
```

Suspend/reactivate responden `{ "tenant": {…estado canónico (tenant_state)…} }`.

## 5. Checklist de convergencia

- [x] AC-PC-001…005 (tests reales `tests/test_provider_admin.py` contra PostgreSQL 17) — **13 passed** (2026-10-26). Nota: la suite compartida quedó transitoriamente rota por el check fail-closed JWS de spec 019 (en vuelo, conftest sin la clave aún) — ver docs/provider-console.md §3; los 13 tests pasaron antes de ese cambio, mismo código.
- [x] AC-PC-010…014 (flujo real en navegador + screenshots/OCR en `docs/evidencia/provider-console/`).
- [x] AC-PC-015 (build + lint limpios).
- [x] AC-PC-016 (`docs/provider-console.md` con evidencia curl + SQL + OCR).
- [x] AC-PC-017 (download link → desktop login con org prellenada, screenshot + DOM). **[REVOCADO después — corrección Miller t_360a752e: el link se mudó al Admin Console del cliente (spec 009 REQ-AC-033); el Provider Console ya no lo muestra (screenshot + OCR post-corrección: `docs/evidencia/hermes-download-admin-console/03-provider-console-tenant-detail-sin-download-link.png` — verificación DOM: sin "Hermes download link", sin "Copy link", sin URL `/download/{slug}`; la sección "Entry points" solo ofrece el deep-link a la Admin Console del cliente).]**
- [x] No-regresión: `test_provision.py` pasa (verificado junto a provider_admin antes de la colisión spec 019); suite completa bloqueada transitoriamente por spec 019 — desviación documentada, no ajustable desde spec 014 sin tocar código ajeno en vuelo.

Desviaciones documentadas:
- D-PC-09 (añadida tras el mandato de Miller del 2026-10-26): el target del download
  link es hoy el desktop white-label (:9090); el contrato `?org=<slug>` queda fijado
  para que spec 022 (puente Hermes real) reapunte `TRUST_DESKTOP_APP_URL` sin cambios
  en el Provider Console. **[SUPERSEDADA — corrección Miller 2026-10-26 (t_360a752e):
  D-PC-09/REQ-PC-017 revocados; el link de descarga vive en el Admin Console del
  cliente contra el endpoint estable `/download/{tenant_slug}` (spec 023).]**
- Corrección incidental de branding del desktop-client ("Harper Agent" → "Trust
  Agent", placeholder retirado por D-ONB-01).
