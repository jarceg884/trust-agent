# Plan 004 — Onboarding de tenant idiom-proof

Spec: `specs/004-tenant-onboarding/spec.md` · Estructura vinculante: arq. §3.1 (`tenants/` = aprovisionamiento idempotente + rollback; `profiles/` reservado para plantillas).

## 1. Estructura de archivos

```
platform/
├── pyproject.toml                    # + [project.scripts] trust-provision
├── app/
│   ├── config.py                     # + provider_token, keycloak_admin_*, oidc_redirect_uris
│   ├── main.py                       # + tenants router
│   ├── auth/service.py               # + get_provider_principal (token provider, RBAC data-driven)
│   ├── profiles/
│   │   ├── __init__.py               # docstring del paquete (ya reservado)
│   │   └── templates.py              # 4 plantillas REQ-PRF-001 (datos versionados, D-ONB-06)
│   └── tenants/
│       ├── service.py                # (existente) resolución de tenant
│       ├── keycloak_admin.py         # cliente Admin REST (bootstrap admin-cli, D-ONB-13)
│       ├── provision.py              # orquestador: pasos + compensación + resultado
│       ├── healthcheck.py            # verificación post-alta (D-ONB-11)
│       ├── router.py                 # /api/admin/tenants* (provider auth)
│       └── cli.py                    # trust-provision {provision,verify}
└── tests/
    ├── conftest.py                   # + env provider/KC-admin, fixture de limpieza de realms
    ├── test_profiles_templates.py    # AC-ONB-020/021
    └── test_provision.py             # AC-ONB-001…041 (e2e real)
```

## 2. Flujo del orquestador (`provision.py`)

```
provision(request):
  1. slug = explicit | slugify(organization_name); validar ^[a-z0-9][a-z0-9-]{2,48}$
  2. IDEMPOTENCIA: lookup tenants por slug vía trust_migrator (cross-tenant, D-ONB-10)
     → existe: return {created:false, tenant, healthcheck} (sin credenciales, D-ONB-04)
  3. Validaciones pre-servicios: departments contra el registro de plantillas (422 temprano),
     initial_budget_usd > 0, email no vacío
  4. tenant_id = uuid4()  (pre-generado, D-ONB-03)
  5. KEYCLOAK (compensable, en orden):
     a. ensure realm {slug}                    → compensar: DELETE realm
     b. unmanagedAttributePolicy=ENABLED       (D-ONB-12)
     c. ensure cliente trust-agent-web (PKCE S256, directAccessGrants OFF)
     d. ensure mapper tenant_id (user attribute → id_token claim firmado)
     e. create user admin (attributes.tenant_id, requiredActions=[CONFIGURE_TOTP])
     f. reset-password temporal (secrets.token_urlsafe) → one_time_password
  6. DB — UNA transacción como trust_app (set_config(tenant_id) primera sentencia):
     tenants → departments(seed) → users(admin, oidc_subject=KC uuid)
     → user_roles(org_admin) → budgets(org) → policies(invariables del catálogo)
     → model_access(resolver model_plan DENTRO de la tx; nombre inexistente → 422 → rollback)
     → audit: tenant.provisioned + department.created×N + user.created + role.assigned
               + budget.updated + policy.updated×K + model.approved×M
  7. FALLO en 5/6 → compensación: DELETE /admin/realms/{slug} (si el realm se creó)
     + envelope provisioning_failed con paso exacto y compensaciones (D-ONB-09)
  8. HEALTHCHECK post-commit (solo lectura) → incluir en la respuesta (D-ONB-11)
```

Puntos clave verificados contra el núcleo 003:
- El INSERT en `tenants` como `trust_app` pasa RLS: `tenant_id GENERATED ALWAYS AS (id)` + id explícito == `app.tenant_id` del `set_config` (D-ONB-03; probado con probe real antes de implementar).
- `audit.emit` funciona dentro de la misma tx (FK satisfecho por el INSERT del tenant; trigger asigna seq/hash).
- El claim `tenant_id` del realm nuevo es verificable por el núcleo: `TRUST_IDP_BASE_URLS` ya incluye el Keycloak local.

## 3. Healthcheck (`healthcheck.py`)

Checks (todos de solo lectura): `tenant_row` (activa, scope trust_app), `idp_discovery` (HTTP 200), `idp_jwks` (HTTP 200), `idp_admin_user` (Admin REST: existe, habilitado, requiredActions), `org_budget` (amount coincide), `default_policies` (invariables activas), `departments` (count esperado), `model_access` (count aprobado), `audit_chain` (`audit_verify_chain` = ok). Resultado: `{status: ok|failed, checks: [{name, status, detail}]}`.

## 4. Auth de proveedor (`auth/service.py`)

`get_provider_principal(request)`: Bearer vs `TRUST_PROVIDER_TOKEN` (secrets.compare_digest); sin env → 503; mal token → 401. Permisos cargados de `catalog.role_permissions` (rol `platform_admin`) — la ruta verifica `tenants.provision` contra la matriz (quitar la fila del catálogo → 403, data-driven). El principal provider NO usa `get_tx` (sin scope de tenant); los lookups cross-tenant van por `trust_migrator` y las verificaciones por `trust_app` con scope.

## 5. CLI (`tenants/cli.py`)

`argparse` con subcomandos `provision` y `verify`; construye engines desde settings (mismo env que el API) y llama al orquestador. Salida JSON a stdout (incluye `one_time_activation` solo en creación); errores como envelope JSON a stderr con exit 1. Instalado como console script (`uv pip install -e .` sobre el venv del proyecto).

## 6. Plan de tests (todos contra infra real)

| Test | AC | Qué prueba |
|---|---|---|
| test_profiles_templates | 020/021 | Registro: 4 plantillas completas; validación de departments |
| test_provision_full_flow | 001/004/005/006/007/009 | 201 + conteos SQL + realm real + admin KC + audit + one-time |
| test_provision_idempotent | 002 | 2ª ejecución → 200, sin dupes, sin credenciales |
| test_provision_rollback | 003 | model_plan inválido → 422, 0 huérfanos, realm 404, paso nombrado |
| test_provision_auth | 030 | 401 sin/mal token; 403 sin permiso en catálogo; 503 sin env |
| test_provision_state_and_healthcheck | 031/032 | GET estado + healthcheck ok |
| test_provision_isolation | 040 | Tenant A no ve al nuevo; cross → 404 |
| test_provision_no_secret_leak | 041 | password one-time ausente de la DB; greps limpios |
| test_cli_provision | 008 | Subproceso real: provision exit 0 + verify exit 0 |

Limpieza: fixture que borra los realms creados por la suite (Admin REST) para no ensuciar el Keycloak local.

## 7. Riesgos y mitigaciones

- **RLS bloquea el INSERT inicial en `tenants`** → probe real antes de implementar (id pre-generado + set_config); si fallara, alternativa documentada: INSERT del tenant vía `trust_migrator` y el resto como `trust_app` (sigue siendo una tx distribuida compensable).
- **Keycloak 26 User Profile descarta atributos** → `unmanagedAttributePolicy=ENABLED` en cada realm (D-ONB-12, hallazgo 003).
- **Realms huérfanos si el proceso muere entre 5 y 6** → la compensación corre en el manejador de errores del orquestador; un crash duro deja el realm sin tenant, detectable por el healthcheck/lookup (slug no existe en DB) — documentado como limitación v1 (el realm es reutilizable por un reintento idempotente).
- **Password policy del realm master** → password one-time de 24+ chars urlsafe; el realm nuevo no define política restrictiva.
