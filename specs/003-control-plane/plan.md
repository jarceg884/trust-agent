# Plan 003 — Control plane core

Spec: `specs/003-control-plane/spec.md` · Base: `db/schema.sql` (spec 002) · Contratos: `docs/arquitectura.md` §3.1/§5.

## 1. Arquitectura de la implementación

```
platform/
├── pyproject.toml            # fastapi, uvicorn, sqlalchemy 2, psycopg, pyjwt[crypto],
│                             # pydantic-settings, cryptography, httpx, alembic, pytest
├── alembic.ini               # script_location -> ../db/migrations (D-CP-11, sin 2ª línea)
├── app/
│   ├── main.py               # create_app(): routers, exception handlers, /healthz
│   ├── config.py             # Settings por env (fail-closed sin DATABASE_URL)
│   ├── db.py                 # engine trust_app, tenant_tx() con set_config(...,true)
│   ├── errors.py             # ApiError + envelope §5.2 + request_id (contextvar)
│   ├── auth/
│   │   ├── jwks.py           # verificación JWT (PyJWKClient, RS256/ES256, require claims)
│   │   ├── service.py        # mint/lookup/revoke/refresh de sesiones (hashes en DB)
│   │   ├── rbac.py           # carga de roles/permisos desde catalog, require_permission
│   │   └── router.py         # /api/auth/session|refresh|logout, /api/me, /api/me/policies
│   ├── tenants/service.py    # estado del tenant (suspended -> 403), resolución
│   ├── governance/combinator.py  # deny-overrides puro (D-CP-15)
│   ├── audit/service.py      # emit() INSERT + verify() vía audit_verify_chain
│   ├── jobs/
│   │   ├── service.py        # enqueue idempotente, claim SKIP LOCKED (conexión migrator)
│   │   ├── worker.py         # `python -m app.jobs.worker` (loop, handlers registry)
│   │   └── router.py         # GET /api/jobs/{id}
│   ├── devices/{tokens.py,gate.py}   # device JWT HS256 pineado + DeviceGate
│   ├── vault/crypto.py       # envelope AES-256-GCM (DEK/KEK)
│   ├── models/service.py     # lectura catalog.model_catalog
│   ├── connectors/service.py # lectura catalog.connector_catalog
│   └── profiles/__init__.py  # reservado (sin spec asignada)
└── tests/
    ├── conftest.py           # DB efímera trust_agent_platform_test vía alembic canónico,
    │                         # mock IdP (JWKS por HTTP real con RSA de test), 2 tenants seed
    ├── test_health_errors.py test_jwks.py test_sessions.py test_mfa_csrf.py
    ├── test_rbac.py test_isolation.py test_jobs.py test_audit.py
    ├── test_governance.py test_vault_crypto.py test_devices.py test_catalogs.py
    └── test_e2e_keycloak.py  # PKCE real contra Keycloak (realm dedicado, Admin REST)
```

## 2. Flujo de request (orden de ejecución)

1. Middleware `request_id` (contextvar) → header `X-Request-Id`.
2. `authenticate(request)`: parse Bearer/cookie →
   - token `sts1.*`: hint de tenant → tx corta con scope → fila `sessions` (jti=sha256(secret), active, no expirada) + user + roles + permisos + tenant status.
   - JWT (3 segmentos): verificación JWKS → claim `tenant_id` → misma carga.
   - Fallo → 401 `invalid_token` (sin oráculo).
3. Dependencia `get_tx`: abre transacción de negocio, `set_config('app.tenant_id', principal.tenant_id, true)`, commit al final, rollback en error.
4. Gates por router: `require_mfa` (403 `mfa_required`), `require_permission(p)` (403 `permission_denied`), `require_device` (401 `device_not_authorized`), CSRF para mutaciones con cookie (403 `csrf_required`).
5. Tenant `suspended` → 403 `tenant_suspended` antes de abrir la transacción de negocio.

## 3. Decisiones de implementación puntuales

- **SQLAlchemy 2 Core-style** (text() + conexiones) para el núcleo: el schema ya existe (spec 002) y la paridad Alembic manda; no se generan modelos ORM duplicados que puedan divergir del DDL canónico. Los routers usan la conexión de `get_tx`.
- **PyJWKClient con `cache_keys=True, lifespan=300`** (skill keycloak-admin-oidc §4); jwks_uri derivado del `iss` validado.
- **Sesiones**: `jti = sha256(secret)`; lookup `WHERE id=:sid AND jti=:h AND status='active' AND expires_at > now()` dentro del scope del hint (D-CP-03/04).
- **Worker**: claim como `trust_migrator` (solo el claim, D-CP-10); ejecución como `trust_app` con `set_config` del tenant del job; handlers registry con handler `echo` de prueba del núcleo.
- **Mock IdP de tests**: par RSA generado en la sesión de test + `http.server` en puerto efímero sirviendo el JWKS en `{iss}/protocol/openid-connect/certs`; los JWT de test se firman de verdad y se verifican por HTTP real (nada de mocks de librería).
- **E2E Keycloak**: realm `trust-agent-cp` creado por Admin REST (idempotente), cliente público PKCE S256, mapper `tenant_id` (user attribute → claim en ID token), usuario con atributo `tenant_id` = tenant de seed; baile de cookies del login (skill §5.2) → code → token → mint de sesión → 403 `mfa_required` en ruta de negocio (sin OTP) como aserción de REQ-ORG-007.

## 4. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Cola de jobs invisible para `trust_app` (RLS) | Claim por conexión `trust_migrator` dedicada (D-CP-10); documentado para evolución (SECURITY DEFINER) |
| `amr` ausente en ID tokens de Keycloak | `mfa_verified` = `any(x in ("mfa","otp") for x in amr)` si el claim existe; ausente → False (fail-closed). E2E lo aserta como 403 `mfa_required` |
| Cookie `Secure` en http local | Flag configurable (`COOKIE_SECURE`, default true); TestClient no depende del flag |
| 3.14 + deps | venv propio con uv; versiones fijadas en pyproject |
