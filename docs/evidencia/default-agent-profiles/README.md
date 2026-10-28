# Spec 020 — Evidencia real: perfiles de agente default gobernados por policy

> Tarea kanban `t_0ed22c94` (feedback Miller #7) · 2026-10-26 · Nada simulado:
> todo lo documentado se ejecutó contra el stack vivo (API :8000, Keycloak 26.2 :8080,
> PostgreSQL `trust-agent-db`, Admin Console :3100) con navegador real (Chromium,
> sesión nombrada `t0ed22c94-e2e`) y login PKCE S256 + TOTP RFC 6238.

## Tenant de prueba

Aprovisionado real vía `POST /api/admin/tenants` (provider token dev):

- Slug/realm: `profiles-e2e-020` · tenant_id `6ec48ff9-e1fa-4099-b31c-fb83879dcd08`
- Admin: `admin@profiles-e2e-020.test` · plan `standard` · budget org $100.00
- Departamentos seed: `it_operations`, `finance`
- One-time password de activación entregada una sola vez (24 chars, archivo local
  `/tmp/ota-020.txt`, invalidada al primer login; jamás en este repo ni en la DB).

## Flujo ejecutado (AC-020-07)

1. **Login real desde la UI**: `http://127.0.0.1:3100/login` → org code
   `profiles-e2e-020` → redirect PKCE S256 (`code_challenge_method=S256`,
   `acr_values=2`) al realm real → one-time password → **CONFIGURE_TOTP**
   (secret base32 capturado del formulario KC, código generado por HMAC-SHA1
   RFC 6238) → **UPDATE_PASSWORD** → callback → sesión admin con MFA en
   `/overview`. Sin loops de redirect (F1 ya corregido por su lane).
2. **`/agents` — sección "Default profiles"**: lista los 4 templates del producto
   (Analyst, Coder, Ops, Support) con skills, modelos/conectores SUGERIDOS y
   estado `available` + botón **Adopt** cada uno.
3. **Effective policy (dialog real)**: APPROVED MODELS =
   `deepseek-v4-flash, deepseek-v4.1-flash, glm-5.1, glm-5.2, glm-5.3, qwen3-32b`
   (intersección real org∩dept∩user), ALLOWED CONNECTORS = `none`
   (los conectores sugeridos del template NO se auto-aprueban — deny-overrides),
   BUDGET = `within budget`.
4. **Adopt "Analyst" desde la UI** → toast
   `"Analyst" adopted — agent 6b3310cb… is now yours to grant (explicit access, deny-overrides).`
   · estado del perfil pasa a `adopted · 6b3310cb…` · la instancia aparece en la
   tabla "Shared agents" (scope org, access `own`) con Invoke/Grant access/Revoke grant.
5. **Invoke con modelo aprobado** (`glm-5.2` explícito) →
   `Authorized (200) — billed to 6ddf9be9… · access own · model glm-5.2`.
   También 200 con modelo default resuelto (`deepseek-v4-flash`).
6. **Invoke con modelo NO aprobado** (`gpt-4o`) →
   `Denied (403) — model_not_approved — Model 'gpt-4o' is not approved for your scope.`
   mostrado en la propia UI.

Screenshot: `01-agents-default-profiles-adopted.png` (sección Default profiles con
Analyst `adopted · 6b3310cb…` + tabla Shared agents).

## Verificación de backend (misma corrida)

Logs del API (`docker logs trust-agent-api`):

```
GET  /api/agent-profiles                              200 OK
POST /api/agent-profiles/analyst/adopt                201 Created
POST /api/agents/6b3310cb-…/invoke                    200 OK   (default deepseek-v4-flash)
POST /api/agents/6b3310cb-…/invoke                    200 OK   (glm-5.2)
POST /api/agents/6b3310cb-…/invoke                    403 Forbidden (gpt-4o)
```

DB (`trust-agent-db`, tabla `agents`):

```
id 6b3310cb-0783-4bd2-938d-ba6b56c6b6ae | profile_key analyst | name Analyst | scope org | status active
```

Audit trail del tenant (append-only, hash chain):

```
agent.profile_adopted  {"profile_key": "analyst", "name": "Analyst"}
agent.invoked          {"model_name": "glm-5.2", "billed_to_user_id": "6ddf9be9-…"}
agent.invoked          {"model_name": "deepseek-v4-flash", "billed_to_user_id": "6ddf9be9-…"}
model.access_denied    (403 gpt-4o)
auth.login · tenant.provisioned · model.approved ×6 · policy.updated ×3 …
```

## Tests (AC-020-01…06)

`platform/tests/test_agent_profiles.py` (6 tests) + `test_profiles_templates.py` (4)
+ `test_agents.py` (5) — **15/15 PASS** contra PostgreSQL vivo con DB aislada
(`TRUST_TEST_DB=trust_agent_t0ed22c94_test`; la DB compartida
`trust_agent_platform_test` estaba bloqueada por una lane hermana concurrente —
hotspot ya reportado en el card). Los tests son catalog-agnósticos: derivan el
modelo aprobado del seed vía SQL, no hardcodean nombres.

Cobertura de ACs: AC-020-01 (list + effective_policy + aislamiento),
AC-020-02 (adopt 201/422/404 + audit + access own), AC-020-03 (instancia por
tenant, índice único), AC-020-04 (grants explícitos: invoke 200 con grant, 404
sin grant), AC-020-05 (modelo aprobado 200 / no aprobado 403 + audit),
AC-020-06 (budget cutoff → 403 `budget_cutoff`), AC-020-07 (este documento).

## Migración

`db/migrations/versions/20261026_0009_default_agent_profiles.py` (+ merge
`12c08d4fb858` de las tres ramas 0009 paralelas) y espejo en `db/schema.sql`:
columna `agents.profile_key`, índice único parcial `agents_tenant_profile_uidx`
(tenant_id, profile_key) WHERE active, seed `catalog.audit_actions`
`agent.profile_adopted`. Aplicada en la DB live (verificada: columna e índice
presentes en `agents`).
