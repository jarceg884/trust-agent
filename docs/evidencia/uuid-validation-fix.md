# Evidencia del fix t_378503a1 — ids no-UUID responden 422, nunca 500

**Bug**: `PUT /api/budgets/dept/{id}` con id NO-UUID respondía **500 internal_error**
en lugar de **422 validation_failed** (hallado por `ui` durante la evidencia de la
spec 021, `docs/audit-ux-evidencia.md` §5).

**Causa raíz**: los path params UUID se pasaban sin validar a
`CAST(:x AS uuid)` en SQL (p. ej. `platform/app/governance/budgets.py:214`);
PostgreSQL lanza `DataError` 22P02 ("invalid input syntax for type uuid") y el
handler genérico lo convertía en 500.

## Fix

Doble capa (defense in depth):

1. **Validación en capa de ruta** — `require_uuid()` / `optional_uuid()` en
   `platform/app/errors.py:167-182`: valida y CANONIZA el UUID (mayúsculas y
   sin guiones se normalizan antes de comparar scopes) o lanza
   `422 invalid_request / validation_failed`. Aplicado en TODAS las rutas del
   control plane con path/query/body param UUID:
   `governance/router.py` (budgets dept/user, policies dept/user, agents,
   grants), `org/router.py` (users, departments), `devices/router.py`,
   `vault/router.py` + `vault/connections.py` (credenciales y conexiones),
   `models/router.py` (keys, access), `connectors/router.py` (instances),
   `jobs/router.py`, `auth/router.py` (notifications), `audit/router.py`
   (filtros de query).
2. **Red de seguridad** — `data_error_handler` (`errors.py:249-269`,
   registrado en `main.py:79`): un `DataError` 22P02 que escape a la
   validación responde 422 en vez de 500; cualquier otro `DataError`
   (corrupción genuina) sigue siendo 500. La transacción ya fue revertida por
   `get_tx()`/`engine.begin()` antes del handler, así que el pool no queda
   envenenado.

**Contrato preservado**: un UUID bien formado pero inexistente sigue dando
**404 not_found** (sin oráculo de existencia, D-CP-09); el 422 es SOLO de formato.

## Test de contrato

`platform/tests/test_uuid_validation.py` — 45 tests:
- AC-1 repro exacto del bug (`PUT /api/budgets/dept/not-a-uuid` → 422).
- AC-2 UUID inexistente → 404.
- AC-3 barrido de 26 rutas con path param UUID + 15 query/body params que
  llegan a `CAST(:x AS uuid)`.
- AC-4 red de seguridad: sonda con `CAST` crudo sin validación de ruta → 422,
  y el pool queda usable (la request siguiente funciona).
- AC-5 canonización: mayúsculas/sin guiones → mismo recurso (200).

```
$ .venv/bin/python -m pytest tests/test_uuid_validation.py -q
45 passed in 7.29s
```

## Evidencia en vivo (stack real, nada simulado)

Scripts: `live_repro.py` + `live_repro_extra.py` (workspace de la tarea;
salvas crudas: `live_evidence.json`, `live_evidence_extra.json`, copiadas a
este directorio). Flujo real contra el stack Docker vivo
(`trust-agent-api` con imagen `trust-agent/platform:local` del 2026-10-26
11:27, que incluye el fix — verificado con `grep require_uuid` dentro del
contenedor):

1. Proveedor real provisiona el tenant `uuid-fix` (`POST /api/admin/tenants`
   con el token del proveedor de `/root/trust-agent/.env`).
2. Login REAL PKCE S256 + enrolamiento TOTP RFC 6238 contra el Keycloak 26
   vivo → `id_token` con `acr=2`.
3. Mint de sesión (`POST /api/auth/session`, MFA verificada) → Bearer.
4. **Repro del bug**: `PUT /api/budgets/dept/not-a-uuid` con
   `{"amount_cents": 100}` → **HTTP 422** `{"error":{"code":"invalid_request",
   "reason":"validation_failed","message":"department_id must be a valid UUID."}}`
   (antes: 500 `internal_error`, request_id `req_c5698d02a8d541e5b0dc`).
5. **Control**: `PUT /api/budgets/dept/<uuid4 inexistente>` → **HTTP 404**
   `not_found`.

Resultado del barrido en vivo: **26/26 checks PASS** (18 en `live_repro.py`
+ 8 en `live_repro_extra.py`), cubriendo budgets dept/user, policies
dept/user (+ query), departments PUT/DELETE, users PUT/disable, devices
GET/revoke/token, vault credentials y connections (GET/PUT/rotate/revoke/use),
connectors instances, models keys, jobs, agents (+grants anidados),
notifications y filtros de audit.

## Suite completa

`pytest tests/` → **398 passed, 12 failed**. Los 12 fallos son ajenos a este
fix y atribuibles a lanes concurrentes (verificados por mtime):
- `test_policies_api`/`test_governance`/`e2e test_04_policies`: la migración
  `20261026_0014_auth_ux_policies.py` (spec 015, otra lane, 11:16) hace
  `require_mfa` variable (`invariable=false`) y los tests aún esperan el
  comportamiento viejo (422 → ahora 200).
- `test_provision`/`test_provider_admin`/`test_spend_cutoff`: áreas
  provision/provider/spend modificadas hoy por otras lanes (08:31–09:54),
  fallos de aserciones de negocio sin relación con validación UUID.
- `test_uuid_validation.py`: 45/45 PASS.

## Archivos del fix

- `platform/app/errors.py` — `invalid_uuid`, `require_uuid`, `optional_uuid`,
  `data_error_handler`.
- `platform/app/main.py` — registro del `data_error_handler`.
- `platform/app/{governance,org,devices,vault,models,connectors,jobs,auth,audit}/…`
  — llamadas a `require_uuid`/`optional_uuid` en cada param UUID.
- `platform/tests/test_uuid_validation.py` — test de contrato (45 tests).
