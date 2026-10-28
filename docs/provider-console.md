# Provider Console — panel del proveedor (spec 014)

> **Estado**: Convergida (2026-10-26). Spec: `specs/014-provider-console/`.
> Tarea kanban: `t_4151f562` (feedback Miller #1 + aclaración "Hermes download link").

## 1. Qué es

Panel del **proveedor** para desplegar y operar clientes (tenants) de la plataforma.
Resuelve el feedback Miller #1: el provisioning era solo `POST /api/admin/tenants` vía
script/CLI; ahora existe una superficie visual enterprise.

**App separada** `platform/provider-console/` en el puerto **3200** (D-PC-01), con
modelo de auth distinto al de la Admin Console (:3100):

| | Admin Console (:3100) | Provider Console (:3200) |
|---|---|---|
| Actor | usuarios del tenant (org_admin…) | operador del proveedor |
| Auth | OIDC/PKCE del realm del tenant + cookie `trust_session` | operador del proveedor: usuario+contraseña(+TOTP) → cookie **httpOnly** `ta_provider` (spec 025; `TRUST_PROVIDER_TOKEN` queda como break-glass deprecado) |
| Permiso | roles del tenant | `tenants.provision` (rol `platform_admin`, data-driven) |
| BFF | proxy con cookie de sesión + CSRF | proxy con `Authorization: Bearer` desde la cookie |

Mezclar ambos primitivos cruzaría fronteras de confianza: una sesión de tenant jamás
debe portar `tenants.provision` (constitution III/IV).

## 2. Funcionalidad

1. **Login del operador** — pega única del provider token; validación REAL contra el
   control plane (`GET /api/admin/tenants`); cookie httpOnly; fail-closed.
2. **Lista de tenants** — nombre, estado (badge active/suspended), realm KC, usuarios,
   departamentos, presupuesto org (USD), gasto del período (USD) con barra de uso,
   fecha de creación. Datos reales de `GET /api/admin/tenants`.
3. **Provisioning desde la UI** — reusa el flujo idiom-proof de spec 004 TAL CUAL
   (misma capa `provision()`): realm KC real, admin inicial con password temporal
   (primer login fuerza `UPDATE_PASSWORD`; el TOTP **no** se fuerza como required
   action — desde spec 015 el enrolamiento es policy-aware: forzado solo con
   `require_mfa=true`, si no sugerido vía banner "Set up MFA" en la Admin Console),
   4 deptos seed con plantillas, presupuesto org, políticas default,
   `model_plan`. El **panel OTA one-time** muestra las credenciales UNA sola vez
   (solo React state, sin persistencia — D-PC-06).
4. **Suspend / Reactivate** — con diálogo de confirmación; transaccional + auditado
   (`tenant.suspended`/`tenant.reactivated`, actor `provider`) + idempotente; el corte
   es inmediato (403 `tenant_suspended` en el core).
5. **Detalle por tenant** — estado canónico, counts, presupuesto/gasto, **healthcheck
   en vivo** (10 checks: DB, discovery, JWKS, admin KC, MFA LoA, budget, políticas,
   deptos, modelos, cadena de audit) y link **Open Admin Console**
   (`:3100/auth/login?org=<slug>`).
6. **Hermes download link** — **[ELIMINADO — corrección Miller 2026-10-26,
   t_360a752e]** El link de descarga NO se genera ni se envía desde aquí: lo
   genera y envía el **admin del cliente** desde SU Admin Console (sección
   "Hermes Download", spec 009 REQ-AC-033) a sus propios empleados. El proveedor
   solo crea/gestiona tenants. La sección §5.6 de abajo queda como evidencia
   histórica de la implementación revocada (D-PC-09/REQ-PC-017 revocados en la
   spec 014). Evidencia post-corrección:
   `docs/evidencia/hermes-download-admin-console/`.
   > **t_b8769ecb (mandato Miller)**: el desktop-client demo (:9090) fue
   > RETIRADO por completo del stack y del repo — nunca fue el producto. El
   > flujo real de descarga/enrolamiento es spec 023 (`/download/{slug}`) y
   > está documentado en `docs/hermes-download.md`.

## 3. Superficie API nueva (backend, spec 014 §4)

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/api/admin/tenants` | provider token + `tenants.provision` | lista + stats por tenant |
| POST | `/api/admin/tenants/{slug}/suspend` | ídem | suspende (idempotente, auditado) |
| POST | `/api/admin/tenants/{slug}/reactivate` | ídem | reactiva (idempotente, auditado) |

Implementación: `platform/app/tenants/provider_service.py` + `router.py`.
Listado: filas vía `trust_migrator` (BYPASSRLS, solo proveedor — D-PC-04); stats por
tenant como `trust_app` DENTRO del scope de cada uno. Suspend/reactivate: UPDATE +
evento de audit en la MISMA transacción como `trust_app` con scope (D-PC-05).

Tests: `platform/tests/test_provider_admin.py` — **13 passed** (matriz 401/403
data-driven, list con seeds y stats, ciclo suspend/reactivate + audit + idempotencia,
403 `tenant_suspended` sobre sesión activa, 404, higiene de secretos).

> **Nota (colisión transitoria)**: la suite compartida de `platform/tests/` está
> temporalmente rota por el trabajo EN VUELO de spec 019 (t_d41f95dc): su check
> fail-closed `validate_policy_jws_key` (main.py) exige `TRUST_POLICY_JWS_PRIVATE_KEY_B64`
> y la conftest aún pinea el env sin esa clave. Los 13 tests de spec 014 pasaron antes
> de que ese cambio aterrizara (misma sesión, mismo código). Es responsabilidad de
> spec 019 añadir la clave al env pineado; no se tocó la conftest para no colisionar.

## 4. Puertos y despliegue

| Servicio | Puerto | Compose |
|---|---|---|
| Control plane API | 8000 | `api` |
| Keycloak | 8080 | `idp` |
| Admin Console (por tenant) | 3100 | `console` |
| **Provider Console** | **3200** | `provider-console` (nuevo) |

> El servicio `desktop-client` (:9090) fue retirado (t_b8769ecb): era un double
> de desarrollo (spec 010), no el producto. El desktop real es Hermes Agent
> instalado desde el bundle de `/download/{slug}/bundle` (spec 023).

```bash
cd /root/trust-agent
docker compose up -d          # levanta todo (provider-console incluido)
# Provider Console: http://127.0.0.1:3200  (login usuario+contraseña del operador
#   seedeado — ver docs/provider-superuser.md; la OTA se loguea UNA vez al arrancar el API)
```

Variables del `provider-console`: `TRUST_API_BASE_URL` (8000) y
`TRUST_ADMIN_CONSOLE_URL` (3100). (`TRUST_DESKTOP_APP_URL` fue eliminado en la
corrección Miller t_360a752e.)

> Hallazgo de entorno: en este host `docker compose build` es silenciosamente no-op;
> usar `docker build` directo para reconstruir imágenes.

## 5. Evidencia real (AC-PC-010…017)

Capturas en `docs/evidencia/provider-console/` (OCR verificado con `tesseract`);
flujo ejecutado con browser automation contra el stack real (PostgreSQL 17 +
Keycloak 26 + API :8000), el 2026-10-26.

### 5.1 Provisioning REAL desde la UI — "Globex Industries"

Formulario (`/tenants/new`): org "Globex Industries", admin `admin@globex.com`,
budget $750/mes, plan standard, 4 deptos seed. Resultado en UI (shot
`03-provision-ota-panel.png`): tenant creado, slug/realm `globex-industries`,
healthcheck `ok`, y panel OTA one-time con password temporal + issuer + client_id
("Shown only once — copy it now").

Verificación SQL (docker exec psql):

```
tenant: globex-industries | active
departments: 4                    (todos con profile_template)
users: 1                          (org_admin)
budget org cents: 75000 | period=monthly        ($750.00)
policies org activas: 4 | claves: approved_models_only, explicit_shared_agent_grants, require_mfa, scoped_credentials
model_access aprobadas: 6
audit tenant.provisioned: 1       (actor provider)
KC discovery realm globex-industries: HTTP 200  (realm real)
password one-time en tenants/users/audit_log/policies: 0 apariciones
```

### 5.2 Idempotencia en la UI (AC-PC-012)

Re-ejecución con el mismo nombre (shot `05-idempotent-rerun.png`): "Tenant already
exists" (200, `created=false`), **sin re-entrega de credenciales** ("No credentials
returned: this slug already existed"), healthcheck `ok`. Paridad D-ONB-04/05.

### 5.3 Lista de tenants (AC-PC-011)

Shot `02-tenants-list.png` (12 tenants reales del entorno) y
`04-tenants-list-con-globex.png` (Globex Industries · Active · realm
globex-industries · 1 user · 4 deptos · $750.00). Badge distinto para suspendido
(shot `08`).

### 5.4 Detalle + healthcheck en vivo (AC-PC-014)

Shot `06-tenant-detail.png` y `07-healthcheck-ok.png`: estado canónico, budget/spent
($0.00 of $750.00), botón **Run healthcheck** con resultado real — todos los checks
`ok` (audit_chain ok, idp_discovery 200, idp_jwks 200, admin user con
`requiredActions=['CONFIGURE_TOTP','UPDATE_PASSWORD']`, MFA LoA ok, etc.). Links:
Admin Console `http://127.0.0.1:3100/auth/login?org=globex-industries` (y, al
momento de la captura, un Hermes download link `http://127.0.0.1:9090/?org=…`
hoy retirado — ver §2.6).

### 5.5 Suspend → reactivate desde la UI (AC-PC-013)

Shot `08-suspended.png`: diálogo de confirmación → badge **Suspended** + toast
"access is cut immediately". Shot `09-reactivated.png`: badge **Active** + toast.
SQL: `audit tenant.suspended: 1`, `tenant.reactivated: 1`, actor `provider`;
estado final `active`. (El estado intermedio `suspended` en SQL y el corte 403
`tenant_suspended` de sesión activa están cubiertos por
`tests/test_provider_admin.py::test_suspend_cuts_active_sessions` y
`test_suspend_and_reactivate_lifecycle`, 13/13 passed.)

### 5.6 Hermes download link (AC-PC-017, mandato Miller) — **EVIDENCIA HISTÓRICA (REVOCADA)**

> **CORRECCIÓN MILLER (t_360a752e, 2026-10-26)**: esta sección describe la
> implementación ORIGINAL que fue REMOVIDA — el link de descarga ahora vive en
> el Admin Console del cliente (spec 009 REQ-AC-033). Se conserva como registro
> histórico; el detalle de tenant ya NO muestra este bloque.

Shot `10-hermes-download-link-prefill.png`: abrir
`http://127.0.0.1:9090/?org=globex-industries` → el desktop app muestra el login con
**ORGANIZATION CODE prellenado = `globex-industries`** (verificado en DOM:
`#orgCode.value == 'globex-industries'`). El empleado continúa con SSO+MFA del realm
y el enrolamiento del dispositivo (spec 007). Extensión mínima al desktop-client
(spec 010): el login acepta `?org=` (solo slugs válidos; el org_code no es raíz de
confianza — D-DEV-02).

### 5.7 Login del operador (AC-PC-010 — supercedido por spec 025)

Shot `01-login.png`. Desde el spec 025 el login es **usuario+contraseña del
operador seedeado en la BD** (la OTA del bootstrap se loguea una vez en el log
del API): credenciales malas → error visible con el reason real del control
plane ("Invalid username or password."), permanece en /login. Login correcto →
cookie httpOnly `ta_provider` + redirect (primer login → `/change-password`,
required action). Con MFA activa el login pide el código TOTP (`401
mfa_code_required` como prompt). Logout → vuelve al login. El viejo login por
`TRUST_PROVIDER_TOKEN` quedó como break-glass deprecado
(docs/provider-superuser.md).

### 5.8 Build y lint (AC-PC-015)

```
cd platform/provider-console
npm run build   # ✓ Compiled successfully · TypeScript OK · 9 rutas
npm run lint    # 0 problems
```

### 5.9 Higiene de secretos (AC-PC-005)

El provider token jamás aparece en cuerpos de respuesta (test
`test_no_secrets_in_provider_responses`); el password one-time no se persiste
(SQL §5.1: 0 apariciones); la cookie es httpOnly (no legible por JS de página).

## 6. Correcciones incidentales

> Las dos primeras bullets describen el desktop-client demo (:9090), RETIRADO
> después por t_b8769ecb (mandato Miller). Se conservan como registro histórico.

- **desktop-client** (histórico, retirado): branding "Harper Agent" → "Trust Agent" (placeholder retirado
  por D-ONB-01/DECISIONES.md; `brand.tsx`, `layout.tsx`, `enroll-flow.tsx`).
- **desktop-client**: Dockerfile nuevo + servicio compose durable `desktop-client`
  (:9090) — antes solo corría como proceso manual efímero; ahora el download link
  tiene target permanente.
- **db/migrations**: la DB de dev quedó en el head canónico `12c08d4fb858`
  (merge de las ramas 0009 paralelas) vía `docker compose run --rm migrate`.

## 7. Cómo verificarlo

```bash
cd /root/trust-agent && docker compose ps          # todo healthy
# Provider Console en el navegador: http://127.0.0.1:3200
#   login: usuario+contraseña del operador (spec 025; la OTA del bootstrap se
#   loguea UNA vez en `docker logs trust-agent-api`; primer login fuerza el
#   cambio de password). TRUST_PROVIDER_TOKEN ya no es el camino de la UI.
#   provision → OTA panel → detalle → healthcheck → suspend/reactivate
#   (sin download link — corrección Miller t_360a752e: lo envía el admin del
#    cliente desde SU Admin Console, http://127.0.0.1:3100/hermes-download)
# API directa:
curl -s http://127.0.0.1:8000/api/admin/tenants     # 401 sin token
# Tests (nota §3 sobre colisión transitoria de spec 019):
cd platform && .venv/bin/python -m pytest tests/test_provider_admin.py -q
```
