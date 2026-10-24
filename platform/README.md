# Trust Agent — Control Plane API

Spec: [`specs/003-control-plane/spec.md`](../specs/003-control-plane/spec.md) ·
Contratos: [`docs/arquitectura.md`](../docs/arquitectura.md) §3.1/§5 ·
Schema: [`db/schema.sql`](../db/schema.sql) (spec 002).

Núcleo del control plane: FastAPI + SQLAlchemy 2 sobre el schema multi-tenant
con RLS. Auth OIDC authorization-code + PKCE con verificación JWKS, sesiones
y jobs en PostgreSQL (cero estado en memoria), RBAC data-driven y middleware
multi-tenant con `set_config('app.tenant_id', ..., true)` por transacción.

## Estructura

```
platform/
├── pyproject.toml        # deps fijadas (fastapi, sqlalchemy 2, psycopg, pyjwt[crypto]…)
├── alembic.ini           # wrapper -> db/migrations (única línea de migraciones, D-CP-11)
├── app/
│   ├── main.py           # factory + /healthz + envelope de errores (§5.2)
│   ├── config.py         # settings por env (fail-closed sin DATABASE_URL)
│   ├── db.py             # engine trust_app + tenant_tx (SET LOCAL por transacción)
│   ├── errors.py         # ApiError + reasons estables + request_id
│   ├── auth/             # jwks.py (verificación), service.py (sesiones), rbac.py, router.py
│   ├── tenants/          # resolución de tenant (provisioning = spec 004)
│   ├── governance/       # combinador deny-overrides + política efectiva
│   ├── models/           # catálogo global de modelos (LiteLLM = spec 005)
│   ├── devices/          # device tokens + DeviceGate (flujo = spec 007)
│   ├── connectors/       # catálogo + instancias + mensajerías (spec 006)
│   ├── vault/            # envelope encryption + API one-time (spec 006)
│   ├── audit/            # emisor de eventos + verificador de cadena (query API = spec 008)
│   ├── profiles/         # reservado (REQ-PRF, sin spec asignada)
│   └── jobs/             # cola en PostgreSQL + worker (claim SKIP LOCKED)
└── tests/                # 80 tests: unit + aislamiento + e2e Keycloak real
```

## Runbook local (verificado)

```bash
# 0) prerrequisitos: contenedor trust-db (127.0.0.1:55432) con roles bootstrap
#    (ver db/README.md pasos 1 y 3) y Keycloak local en 127.0.0.1:8080.

# 1) venv
cd /root/trust-agent/platform
uv venv .venv
uv pip install --python .venv/bin/python -e . pytest

# 2) migraciones (vía canónica: apunta a db/migrations)
export DATABASE_URL="postgresql+psycopg://postgres:trustlocal@127.0.0.1:55432/trust_agent"
.venv/bin/alembic -c alembic.ini upgrade head

# 3) API (puerto 8000, arquitectura §3.10)
export TRUST_DATABASE_URL="postgresql+psycopg://trust_app:trust_app_dev@127.0.0.1:55432/trust_agent"
export TRUST_DATABASE_MIGRATOR_URL="postgresql+psycopg://trust_migrator:trust_migrator_dev@127.0.0.1:55432/trust_agent"
export TRUST_DEVICE_TOKEN_SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"   # obligatoria: sin ella el API no arranca (fail-closed)
export TRUST_VAULT_KEK_B64="$(python3 -c 'import base64,os;print(base64.b64encode(os.urandom(32)).decode())')"
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
curl -s http://127.0.0.1:8000/healthz   # {"status":"ok","database":"up"}

# 4) worker de jobs (proceso propio, estado en DB)
.venv/bin/python -m app.jobs.worker            # continuo
.venv/bin/python -m app.jobs.worker --once     # drena la cola y sale

# 5) suite completa (levanta trust_agent_platform_test efímera vía Alembic)
.venv/bin/python -m pytest tests/ -q           # 80 passed
```

## Reglas del núcleo (vinculantes)

- **La app SIEMPRE conecta como `trust_app`** (sin `BYPASSRLS`, sujeto a RLS).
  La única excepción documentada es la conexión de claim del worker
  (`TRUST_DATABASE_MIGRATOR_URL`, rol `trust_migrator`) usada EXCLUSIVAMENTE
  para el `UPDATE ... SKIP LOCKED` de la cola — el schema no ofrece vía
  cross-tenant para `trust_app` (D-CP-10). El job se ejecuta como `trust_app`
  dentro del scope de su tenant.
- **Toda query de negocio** corre en una transacción que empieza con
  `SELECT set_config('app.tenant_id', <uuid>, true)` (== `SET LOCAL`).
  Sin scope → 0 filas (fail-closed).
- **El tenant sale de credenciales verificadas**: claim firmado `tenant_id`
  del JWT o la fila `sessions` (buscada por el hint del token opaco dentro
  del scope del hint). Jamás de un header forjable.
- **Auth**: el cliente hace authorization-code + PKCE S256 contra el realm
  del tenant; el API solo verifica el `id_token` resultante contra JWKS
  (RS256/ES256, `exp`/`iat`/`aud`/`iss`/`sub`/`tenant_id`, leeway 60 s) y
  minta la sesión en PostgreSQL. Nada de ROPC para usuarios finales.
- **Sesiones**: tokens opacos `sts1.<tenant>.<session>.<secret>`; en DB solo
  hashes (`jti`, `refresh_token_hash`, `csrf_token_hash`). Refresh one-time
  con detección de reuso (replay → sesión revocada). Cookie de consola
  `Secure`+`HttpOnly`+`SameSite=Lax` + CSRF en mutaciones.
- **Rate limiting de login** (REQ-SEC-006): contadores persistidos en
  `ops.login_throttle` (plano cross-tenant del proveedor, D-DB-14) — por
  cuenta (claims verificados, subject hasheado) y por IP. 5 fallos en la
  ventana de 15 min → 429 `rate_limited` con `Retry-After` y lockout
  progresivo (30 s × 2^(fallos−5), tope 24 h). El contador vive en
  PostgreSQL: sobrevive al reinicio del API. El login exitoso resetea el
  contador de la cuenta (no el de la IP).
- **RBAC**: permisos calculados por request desde `catalog.role_permissions`
  (la matriz §2.1 de spec 001 vive en datos). `dept_admin` limitado a sus
  departamentos; cross-tenant → 404.

## Variables de entorno

| Variable | Uso | Default |
|---|---|---|
| `TRUST_DATABASE_URL` | DSN de la app (rol `trust_app`) — **obligatoria** | — |
| `TRUST_DATABASE_MIGRATOR_URL` | DSN de claim del worker (rol `trust_migrator`) | — |
| `TRUST_IDP_BASE_URLS` | Bases de issuer aceptadas (CSV) | `http://127.0.0.1:8080` |
| `TRUST_OIDC_AUDIENCE` | `aud` pineado | `trust-agent-web` |
| `TRUST_SESSION_TTL_MINUTES` | TTL de sesión server-side | `480` |
| `TRUST_LOGIN_MAX_FAILURES` | Fallos de login antes del primer lockout | `5` |
| `TRUST_LOGIN_WINDOW_SECONDS` | Ventana deslizante de fallos | `900` |
| `TRUST_LOGIN_LOCKOUT_BASE_SECONDS` | Duración del primer lock (duplica por fallo extra) | `30` |
| `TRUST_LOGIN_LOCKOUT_MAX_SECONDS` | Tope de duración del lock | `86400` |
| `TRUST_DEVICE_TOKEN_SECRET` | Firma de device tokens (HS256 pineado). **Obligatoria — fail-closed**: vacía o con placeholder `change-me` ⇒ `create_app()` rechaza el arranque | — |
| `TRUST_VAULT_KEK_B64` | KEK del vault (32 bytes b64, fuera de DB) | — |
| `TRUST_LITELLM_BASE_URL` | Admin API del proxy LiteLLM (spec 005). **Vacía ⇒ fail-closed**: emisión de keys, ingest de spend y enforcement responden 503 / el worker no arranca (exit 2) — la gobernanza de modelos NUNCA falla abierta | `""` |
| `TRUST_LITELLM_MASTER_KEY` | `LITELLM_MASTER_KEY` para `/key/*` y `/spend/*` | `""` |
| `TRUST_COOKIE_SECURE` | flag Secure de la cookie | `true` |
| `TRUST_MFA_MIN_ACR` | Umbral `acr` aceptado como evidencia MFA (D-MFA-01, spec 011). **Pineado a 2 en TODOS los entornos** (dev/staging/prod): con el `acr.loa.map` canónico del realm (`{"1":0,"2":1}`, wiring `ensure_mfa_acr()` del provisioning), `acr:"2"` == password+OTP y `acr:"1"` == password-only. Bajarlo a 1 sería fail-open (prohibido, constitución IV); subirlo bloquearía todo login. El umbral y el mapa LoA del realm están **acoplados**: cambiarse juntos como cambio de contrato versionado, nunca como knob de runtime | `2` |

## Gobernanza (spec 005)

Dominios y endpoints (todos con MfaGate + RBAC + scope de tenant):

- **Presupuestos jerárquicos** (`app/governance/budgets.py`): `PUT /api/budgets/org|dept/{id}|user/{id}` con validación de suma (Σ deptos ≤ org, Σ usuarios ≤ depto), `force=true` auditable para bajar bajo el gasto (REQ-BUD-013), `GET /api/budgets/overview` con remanente no asignado, `GET /api/budgets/me` con estado de corte por nivel.
- **Políticas** (`app/governance/policies.py`): `PUT /api/policies/org|dept/{id}|user/{id}` — claves del catálogo tipado, invariables → 422, depto/usuario no puede relajar el nivel superior (misma semántica del combinator deny-overrides), versionado + audit; `DELETE /api/policies/{level}/{key}` desactiva TODAS las versiones del scope.
- **Model access + LiteLLM** (`app/models/`): `PUT/POST /api/models/access(/revoke)` con regla de subconjunto, `GET /api/models/allowed-for-me` (intersección de niveles ∩ política), `POST /api/models/verify` (403 `model_not_approved` + audit), `POST /api/models/keys` (virtual key por usuario: plaintext UNA vez, DB solo hash sha256 — `D-GOV-02`), `GET /api/models/keys/me`, `POST /api/models/keys/{id}/revoke`.
- **Agentes compartidos** (`app/governance/agents.py`): publicar sin grants, grants explícitos `invoke`/`view` (manage = dueño/dept_admin/org_admin por rol), sin grant → 404, view sin invoke → 403, revocación inmediata, `POST /api/agents/{id}/invoke` factura al invocador.
- **Spend + corte** (`app/governance/spend.py` + `spend_worker.py`): worker `python -m app.governance.spend_worker` que ingiere `/spend/logs/v2` con cursor en `ops.spend_cursor` (idempotente por `request_id`), aplica margen MaaS (central) o registra BYOK sin margen, actualiza `budget_counters` por nivel y ejecuta el enforcement: bloqueo/desbloqueo de keys por hash (`/key/block`/`/key/unblock`), audit `budget.cutoff`/`budget.threshold` y notificaciones de inbox (`GET /api/me/notifications`) con dedupe por ventana.

En tests, LiteLLM es un **doble HTTP declarado** (`tests/litellm_fake.py`, D-GOV-15): mismo patrón que el IdP mock — HTTP real con la semántica documentada de la Admin API (corte nativo 429, key bloqueada 401, models de la key). Contra un proxy real solo cambia `TRUST_LITELLM_BASE_URL`.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
# 211 passed: health/envelope, JWKS (6 negativos), sesiones (mint/refresh/
# replay/logout/restart), MFA+CSRF, RBAC matriz data-driven (54 combinaciones),
# aislamiento cross-tenant (404) + tenant suspendido, jobs (idempotencia,
# SKIP LOCKED concurrente, worker real en subproceso, restart), audit chain,
# deny-overrides, vault crypto, device gate, catálogos, e2e PKCE contra el
# Keycloak real, login throttle, devices flow, vault/connectores (spec 006),
# tenant onboarding (spec 004), gobernanza completa (spec 005: budgets 7,
# políticas 7, model access 6, agentes 5, spend/cortes/notificaciones 8) y
# la suite e2e de QA (tests/e2e/: flujo completo del control plane,
# checklist de seguridad AIDLC C1-C6, aislamiento multi-tenant API+DB).
```

La suite levanta `trust_agent_platform_test` desde cero con
`alembic -c alembic.ini upgrade head` (la vía canónica de db/migrations), 
siembra dos tenants y corre todo como `trust_app`. Última corrida:
**226 passed** (2026-10-24, tras D-MFA-01/spec 011: 211 previos + 15 de
`tests/test_mfa_acr.py`; dos corridas back-to-back en verde).
La suite de `db/` sigue en verde: **171 passed**.
