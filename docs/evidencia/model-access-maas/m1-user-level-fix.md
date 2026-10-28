# GAP M-1 (ALTO) — Aprobación de modelo a nivel `user` → 500 `CheckViolation`

**Fix**: t_f75e6a18 · **Spec**: 018 §2.2 (D-018-06, AC-018-06) · **Fecha**: 2026-10-27
**Reporte original**: `docs/revision-pilar2-governance-e2e.md` §"BUG M-1" (revisión E2E Pilar 2, t_a296ae9b).

## Resumen

`PUT /api/models/access {"level": "user", "user_id": "<uuid>"}` (el payload exacto
de la UI del console) devolvía **HTTP 500** `internal_error`: `approve()` insertaba
el `department_id` del body (NULL a nivel user) y la DB rechazaba la fila con
`model_access_scope_check` (exige `department_id IS NOT NULL` para `level='user'`).
El nivel usuario de la matriz de aprobaciones era inoperante (aprobar, denegar y
BYOK user).

**Decisión de contrato (D-018-06)**: el backend resuelve el depto. El body no
define el scope a nivel user: `approve()`/`revoke()` resuelven el departamento
REAL del usuario desde `users` (tenant-scoped). Ver spec 018 §2.2 para el
contrato completo de 422s (mismatch de depto, usuario sin depto, dept sin
`department_id`, org con scope, user sin `user_id`) y 404 cross-tenant.

## Cambios

- `platform/app/models/access.py`: `_resolve_scope()` nuevo — validación del
  triple (level, department_id, user_id) ANTES de tocar la tabla + resolución
  del depto real del usuario a nivel user. Aplicado en `approve()` y `revoke()`
  (revoke simétrico: denegación explícita por usuario sin depto en el body).
- `platform/tests/test_model_access.py`: `test_user_level_resolves_department`
  (regresión, corre contra el schema Alembic real → constraint vivo).
- `specs/018-model-access-maas/spec.md` §2.2 + `tasks.md` T8–T10.
- UI sin cambios (`models-page.tsx` manda `department_id: null` a nivel user —
  contrato válido: el backend resuelve).

## Evidencia (nada simulado; stack vivo)

### 1. Repro PRE-fix contra el stack (bug vivo)

Tenant fresco `m1-133122` + login REAL (PKCE S256, Keycloak :8080), repro exacto
del reporte:

```
PUT /api/models/access {"model_name": "glm-5.2", "level": "user",
                        "user_id": "<admin-uuid>", "mode": "central"}
→ HTTP 500 {"error":{"code":"internal_error",...}}
```

Traceback del API (docker logs trust-agent-api):

```
psycopg.errors.CheckViolation: new row for relation "model_access" violates
check constraint "model_access_scope_check"
[SQL: INSERT INTO model_access (..., department_id, user_id, ...) VALUES
(..., CAST(%(dept)s AS uuid), ...)]
[parameters: {'level': 'user', 'dept': None, 'usr': '40467edc-...', ...}]
```

### 2. Test de regresión (constraint REAL, no doble)

La suite de platform corre contra PostgreSQL real con el schema canónico
Alembic (`platform/alembic.ini` → `db/migrations`), así que el constraint
`model_access_scope_check` está VIVO en el test:

- **RED** (fix neutralizado vía plugin `m1_redcheck.py`, sin tocar el árbol
  compartido): `test_user_level_resolves_department` FALLA con el
  `CheckViolation` EXACTO del bug — el test reproduce el constraint.
- **GREEN** (fix presente): `tests/test_model_access.py` **12/12 passed**
  (DB aislada `TRUST_TEST_DB=trust_agent_platform_test_m1`).
- Suites relacionadas sin regresión: `test_governance`, `test_policies_api`,
  `test_governance_bundle`, `test_catalogs`, `test_custom_connections`,
  `test_dynamic_discovery`, `test_governance_attestation`,
  `test_governance_batch1`, `test_bundle_schema_extensible` →
  **129 passed** en 116.57 s.

### 3. Convergencia POST-fix contra el stack vivo (API)

Imagen `trust-agent/platform:local` reconstruida + `trust-agent-api` recreado
(healthz 200). Tenant fresco `m1-134718` + login REAL. Log completo:
`post_fix_evidence.log` (en este directorio). Script: `repro_m1.py`.

```
[PASS] P0a provisioning → 201          [PASS] P0b login REAL (PKCE S256)
>>> REPRO M-1: PUT level=user (sin department_id) → HTTP 201
[PASS] M-1b fila user-level en DB con department_id resuelto:
       ('user', '8777f8d1-…', 'd1800ad8-…', 'central', 'approved')
[PASS] M-1c POST revoke level=user (sin dept) → 200
[PASS] M-1d deny-all a nivel user tras revoke (intersección): models=[]
[PASS] M-1e re-aprobar level=user → 201
[PASS] M-1f allowed-for-me restaura glm-5.2: ['glm-5.2']
[PASS] M-1g department_id mismatch → 422 "department_id does not match
       the user's department."
RESULT: FIXED
```

### 4. Verificación desde la UI del console (navegador real)

Console :3100, login org `m1-134718` (baile PKCE real contra Keycloak),
página Model Access:

1. `DeepSeek-V4.1-Flash` → Approve → Level=Organization → **"approved at org
   level"** (subset rule para el paso 2). Badge `org` en la fila.
2. `DeepSeek-V4.1-Flash` → Approve → **Level=User** → UUID del admin →
   **"DeepSeek-V4.1-Flash approved at user level."** (toast capturado en
   vivo; antes de este fix el mismo flujo mostraba "An unexpected error
   occurred."). La fila queda con badges **`org user`**
   (`ui-user-level-approve.png`).
3. Fila en DB (aprobación hecha DESDE la UI, department_id resuelto
   server-side — el UI manda `null`):

```
('deepseek-v4.1-flash', 'org',  None, None, 'central', 'approved')
('deepseek-v4.1-flash', 'user', '8777f8d1-…', 'd1800ad8-…', 'central', 'approved')
```

4. Audit (`audit_log` del tenant): `model.approved {level: user,
   department_id: '8777f8d1-…', user_id: 'd1800ad8-…'}` y `model.revoked`
   simétrico — el payload de audit lleva el depto resuelto.

## Veredicto

**CONVERGIDO.** AC-018-06 satisfecho contra DB real y UI real: el nivel
usuario de la matriz de aprobaciones es operativo (aprobar + denegar
explícito, central y BYOK), y ningún camino de approve/revoke produce 500 por
`model_access_scope_check`.
