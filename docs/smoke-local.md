# Smoke del empaquetado local single-node — evidencia real

> Tarea kanban `t_d36b3f31` (perfil deploy) · 2026-10-24 · Constitution I: nada simulado —
> toda la evidencia abajo es salida real de comandos ejecutados contra el stack
> compose corriendo en este servidor (`ecs-hermes`).

## Veredicto

| Verificación | Resultado | Evidencia |
|---|---|---|
| Stack compose completo (8 servicios) | **UP, todos sanos** | §1 `docker compose ps` |
| Migraciones vía canónica (Alembic → db/migrations) | **exit 0** | §1 logs de `trust-agent-migrate` |
| Health checks (API/IdP/console/doble LiteLLM) | **OK** | §2 |
| E2E global en vivo (provision → admin → usuario → dispositivo → agente → modelo → corte → audit → revocación) | **PASS 79/79** (Fase A 54 + Fase B 25) | §3, logs adjuntos |
| Reinicio del API **con compose** (`docker compose restart api`) — sesiones sobreviven | **PASS** (la Fase B arranca con las sesiones de la A) | §3.2 |
| `jobs-worker` del compose: invite asíncrono (spec 012) → usuario real en Keycloak | **PASS 5/5** | §4 |
| `spend-worker` del compose: ingest continuo con idempotencia | **PASS** (ingirió una invocación nueva en vivo, cursor avanzó) | §5 |
| Admin Console del compose: login PKCE S256 + TOTP real + step-up MFA + BFF | **SMOKE PASS 19/19** | §6, log adjunto |

**Conclusión**: el empaquetado (`docker-compose.yml` + `README.md`) levanta la
plataforma completa desde cero en este servidor y el flujo demo de un tenant
corre de punta a punta con evidencia real. Nada quedó en "debería funcionar".

## 0. Qué se empaquetó

```
docker-compose.yml                     8 servicios (db, idp, migrate, api,
                                       jobs-worker, spend-worker,
                                       litellm-double, console)
.env / .env.example                    secretos fail-closed (device token,
                                       vault KEK, provider token, KC admin)
platform/Dockerfile                    control plane (python:3.12-slim, no-root)
platform/console/Dockerfile            console (node:22-slim, npm ci --include=dev)
platform/console/.dockerignore         excluye node_modules/.next del contexto
platform/scripts/run_litellm_double.py doble LiteLLM declarado en puerto fijo 39989
README.md                              arranque paso a paso + salud + operación
```

Imágenes construidas: `trust-agent/platform:local` (350 MB) y
`trust-agent/console:local` (1.56 GB). Puertos canónicos del runbook:
55432 (db) · 8080 (idp) · 8000 (api) · 39989 (litellm) · 3100 (console).

Decisiones de empaquetado:
- **`network_mode: host`** para api/migrate/workers/litellm-double/console: los
  tokens OIDC llevan issuer `http://127.0.0.1:8080/realms/<org>` y el API verifica
  JWKS en esa URL exacta; bridge rompería esa identidad. Linux-only, aceptable
  para validación local single-node (constitution VII).
- **Doble LiteLLM declarado** (D-GOV-15) como servicio `litellm-double`: costo $0,
  sin credenciales MaaS; contra un proxy real solo cambia `TRUST_LITELLM_BASE_URL`.
- **IdP incluido**: Keycloak 26.2 fresco; el provisioning crea el realm por tenant
  vía Admin REST (D-ONB-13) — no requiere import de realm.
- **Migraciones como servicio one-shot** (`migrate`): el API espera
  `service_completed_successfully` — jamás arranca contra un schema a medias.

## 1. Stack arriba

Entorno ad-hoc anterior detenido (procesos uvicorn :8000/:8001, next :3100,
doble LiteLLM :39989 del host; contenedores `trust-db` y `keycloak` preservados
para rollback — ver §7).

```
$ docker compose ps
NAME                         STATUS
trust-agent-api              Up (healthy)
trust-agent-console          Up (healthy)
trust-agent-db               Up (healthy)
trust-agent-idp              Up (healthy)
trust-agent-jobs-worker      Up
trust-agent-litellm-double   Up (healthy)
trust-agent-spend-worker     Up

$ docker logs trust-agent-migrate   # one-shot, exit 0
# alembic upgrade head contra db/migrations (vía canónica, D-CP-11)
```

## 2. Verificación de salud (salida real)

```
$ curl -s http://127.0.0.1:8000/healthz
{"status":"ok","database":"up"}

$ curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/realms/master/.well-known/openid-configuration
200

$ curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/login
200

$ curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:39989/key/info?key=x'
401        # el doble está vivo y cerrado sin master key
```

## 3. Flujo demo punta a punta — `e2e_final_live.py` (79/79 PASS)

Script validado por QA (t_bf72a163) ejecutado **sin modificaciones** contra el
stack compose. IdP 100% real: login PKCE S256 + enrolamiento TOTP RFC 6238 por
los formularios reales de Keycloak 26, `acr=2` (D-MFA-01). Tenant demo:
`final-e2e` (presupuesto org $500).

### 3.1 Fase A — provision → admin → usuario → dispositivo → agente → modelo: **54 PASS, 0 FAIL**

Selección de salidas reales:

```
[PASS] S1 proveedor: POST /api/admin/tenants → 201 created
[PASS] S1 tenant activo con issuer del realm real — .../realms/final-e2e
[PASS] S1 4 departamentos seed
[PASS] S1 presupuesto org 50000¢ ($500)
[PASS] S1 cadena de audit del tenant recién nacido: íntegra
[PASS] S2 login REAL del admin (PKCE + UPDATE_PASSWORD + CONFIGURE_TOTP) → code
[PASS] S2 el realm real emite acr=2 con password+OTP (D-MFA-01)
[PASS] S2 sesión admin minteada con MFA verificada (acr real, sin dobles)
[PASS] S3 sesión del obrero MFA-verificada
[PASS] S3 overview: org $500, depto IT $200, unallocated $300
[PASS] S4 POST /api/devices/enroll (org_code + id_token acr=2 + RSA-2048 real) → 202 pending
[PASS] S4 canje one-time → 200 device token
[PASS] S4 segundo canje → 410 (one-time estricto)
[PASS] S5 publicado SIN ningún grant (default cerrado, AC-AGT-001) — grants=0
[PASS] S5 el admin (con grant) invoca → 200 facturado al INVOCADOR
[PASS] S5 emisión de key virtual → 201, plaintext EXACTAMENTE una vez, max_budget $50
[PASS] S5 la DB guarda SOLO sha256(key) (C4)
[PASS] S5 invocación 1 al data plane con modelo aprobado → 200
[PASS] S5 modelo NO aprobado → 403 del gateway (lista de la key)

FASE A: 54 PASS, 0 FAIL
```

### 3.2 Reinicio del API con compose (AC-SEC-003: sesiones en PostgreSQL)

```
$ docker compose restart api
 Container trust-agent-api  Started
$ curl -s http://127.0.0.1:8000/healthz
{"status":"ok","database":"up"}
```

### 3.3 Fase B — sesiones → spend → corte → audit → revocación: **25 PASS, 0 FAIL**

```
[PASS] S7 reducción por debajo del gasto con force=true → 200 (auditable)
[PASS] S7 sin force → 422
[PASS] S7 la key virtual queda BLOQUEADA por hash en el gateway — blocked
[PASS] S7 el gateway rechaza la key bloqueada → 401
[PASS] S7 notificación budget_cutoff al obrero
[PASS] S7 /api/budgets/me: cutoff=true level=user
[PASS] S7 recuperación: subir presupuesto → 200
[PASS] S7 la key se rehabilita tras subir el presupuesto — active
[PASS] S8 audit contiene TODAS las acciones del flujo — 20 acciones distintas
[PASS] S8 verificación de cadena vía API → ok
[PASS] S8 CLI verify-audit-chain → exit 0, cadena ok
[PASS] S8 alcance del audit = su propio tenant (total == filas SQL del tenant) — api=39 sql=39
[PASS] S9 admin revoca el dispositivo → 200
[PASS] S9 la sesión ligada al dispositivo revocado → 401 INMEDIATO
[PASS] S9 cadena de audit íntegra tras TODO el flujo (CLI exit 0)

FASE B: 25 PASS, 0 FAIL
E2E FINAL PASS — flujo global completo con evidencia real
```

Las sesiones admin y obrero usadas en la Fase B son las minteadas en la Fase A
**antes** del `docker compose restart api` — sobrevivieron el reinicio porque
viven en PostgreSQL, no en memoria.

## 4. `jobs-worker` del compose — invite asíncrono real (spec 012)

Probe dedicado (no un subprocess del script): invite como admin del tenant demo
y el job lo drena el **contenedor** `trust-agent-jobs-worker`. **5/5 PASS**:

```
[PASS] invite → 202 job pendiente — HTTP 202 {'job_id': '00adac65-…', 'status': 'pending'}
[PASS] job 00adac65-… → succeeded por el jobs-worker del compose — succeeded
[PASS] one-time password en jobs.result (≥16 chars, one-time)
[PASS] usuario en el directorio del tenant — {'email': 'invited-probe@final-e2e.test',
       'status': 'active', 'roles': [{'role_key': 'user', …}]}
[PASS] usuario REAL en Keycloak con requiredActions=['CONFIGURE_TOTP', 'UPDATE_PASSWORD']
```

## 5. `spend-worker` del compose — ingest continuo en vivo

Invocación extra al data plane con la key rehabilitada y el worker del compose
la ingiere en su ciclo (idempotente por `request_id`, cursor en `ops.spend_cursor`):

```
$ curl -X POST http://127.0.0.1:39989/chat/completions … → 200
$ docker logs --since 1m trust-agent-spend-worker
[spend-worker] {'logs': 3, … 'ingest': {'received': 3, 'ingested': 0, …}, 'cursor': '…02:38:43.508143'}
[spend-worker] {'logs': 4, … 'ingest': {'received': 4, 'ingested': 1, …}, 'cursor': '…02:40:08.297845'}
                                                                      ^^^^^^^^^ nueva invocación ingestada, cursor avanzó
```

## 6. Admin Console del compose — `smoke_console.py` (SMOKE PASS, 19/19)

Tenant `console-smoke2` aprovisionado al vuelo (201 + OTA one-time). Flujo 100%
real a través del **console del compose** (:3100): redirect al authorize del
realm con PKCE S256, login con one-time password, enrolamiento TOTP real
(código RFC 6238), callback → mint de sesión admin, step-up MFA (acr≥2), proxy
BFF con CSRF, datos reales, logout.

```
[PASS] GET /login → 200 con formulario de org code
[PASS] GET /auth/login?org → redirect al authorize del realm
[PASS] PKCE S256 en el authorize
[PASS] redirect URI del console (:3100) en el authorize
[PASS] login #1 real (PKCE + one-time password + enrolamiento TOTP + callback mint)
[PASS] cookies de sesión presentes (ta_console_session/ta_console_refresh/ta_console_csrf)
[PASS] GET /api/proxy/me → 200 whoami real (login real KC26 + step-up MFA)
[PASS] whoami: rol org_admin
[PASS] whoami MFA verificada (acr>=2 tras el step-up real)
[PASS] GET /api/proxy/budgets/overview → 200 datos reales
[PASS] overview coherente: sin presupuestos de depto, todo el org budget sin asignar
[PASS] GET /api/proxy/audit/events → 200 con eventos reales
[PASS] mutación sin X-CSRF-Token → 403 csrf_required (lo valida el control plane)
[PASS] mutación con X-CSRF-Token → 200 (presupuesto org actualizado)
[PASS] el nuevo presupuesto se refleja en el overview
[PASS] GET /api/proxy/users → 200 con directorio real (spec 012)
[PASS] GET /api/proxy/branding → 200 default provider (spec 012)
[PASS] GET /overview con sesión → 200 HTML (puerta MFA renderizada)
[PASS] GET /overview sin sesión → redirect a /login

SMOKE PASS — flujo REAL completo: PKCE S256 + one-time password + enrolamiento
TOTP real + step-up MFA (acr) + sesión admin MFA-verificada + datos reales por el proxy
```

## 7. Incidencias encontradas y resueltas durante el empaquetado

| # | Incidencia | Resolución |
|---|---|---|
| 1 | Build de la console fallaba: `Cannot find module '@tailwindcss/turbopack'` — `NODE_ENV=production` antes de `npm ci` excluía devDependencies | `platform/console/Dockerfile`: `npm ci --include=dev` y `NODE_ENV=production` solo tras el build |
| 2 | Healthcheck del IdP nunca pasaba: el probe enviaba `HTTP/1.0` y el grep buscaba `HTTP/1.1 200` | `docker-compose.yml`: grep `' 200 '` |
| 3 | `smoke_console.py` (spec 009) esperaba 404 en `/users` y `/branding`, pero la spec 012 los activó (404→200, verificado por QA) | Script actualizado a las expectativas de la spec 012 (200 + directorio real / branding default provider) |
| 4 | El doble LiteLLM bindiaba puerto aleatorio (es un doble de tests) | `platform/scripts/run_litellm_double.py`: puerto pineado 39989 (mismo patrón del launcher de QA) |

Ninguna requirió tarea dev: todas son infra de empaquetado/tests, no código de
plataforma. La plataforma (API, DB, IdP, console) pasó todo sin cambios.

## 8. Rollback

El entorno ad-hoc anterior quedó preservado (contenedores parados, no borrados):

```
docker compose down        # apagar el stack empaquetado
docker start trust-db keycloak   # restaurar DB y IdP anteriores
# API/console a mano según platform/README.md (runbook pre-compose)
```

Los datos del stack empaquetado viven en los volúmenes
`trust-agent_trust_db_data` y `trust-agent_kc_data` — `docker compose down`
sin `-v` los conserva entre corridas.

## 9. Logs completos (adjuntos a la tarea)

- `logs/e2e_phase_a.log` — Fase A completa (54 PASS)
- `logs/e2e_phase_b.log` — Fase B completa (25 PASS)
- `logs/probe_invite.log` — probe del jobs-worker del compose (5 PASS)
- `logs/smoke_console.log` — smoke del console del compose (19 PASS)
