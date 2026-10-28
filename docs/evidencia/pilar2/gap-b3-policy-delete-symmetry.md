# GAP B-3 CONVERGIDO — simetría PUT/DELETE en policies de usuario (t_fdc728d2)

**Bug original**: `docs/revision-pilar2-governance-e2e.md` §"BUG B-3" —
`PUT /api/policies/user/{user_id}` resuelve y guarda el `department_id` del
usuario (200), pero `DELETE /api/policies/user/{key}?user_id=…` SIN
`department_id` respondía **404** `Policy not found at this level.` — la fila
escrita por el PUT era inalcanzable por el DELETE salvo repitiendo el depto a
mano (workaround verificado en la revisión: `&department_id=…` → 204).
Asimetría de contrato PUT/DELETE. La UI del console hoy solo borra policies
org → no expuesto por UI, pero defecto de contrato API.

**Root cause**: `platform/app/governance/policies.py` `deactivate()` filtra
`(department_id IS NULL) = :dept_null`; sin `department_id` en la query,
`dept_null=true` no matchea las filas de nivel user (que llevan el depto del
usuario estampado por el upsert). El PUT (`put_user_policy`) resuelve el
depto desde `users`; el DELETE no aplicaba la misma resolución.

## Fix (REQ-GOV-025, spec 005 §3.2)

| Archivo | Cambio |
|---|---|
| `specs/005-governance/spec.md` | **REQ-GOV-025** (nuevo) + **AC-GOV-025**: simetría del contrato DELETE — el DELETE de nivel user resuelve el depto desde `users.department_id` igual que el upsert; `department_id` explícito sigue soportado (compat); usuario inexistente → 404 `User not found.` (mismo contrato que el PUT); nivel depto sin `department_id` sigue sin resolución (no hay entidad única); la resolución aplica también al audit y al barrido de conectores. Trazabilidad actualizada (REQ-GOV-020…025). |
| `platform/app/governance/router.py` | `delete_policy`: cuando `level=user` con `user_id` y sin `department_id`, resuelve el depto del usuario con la MISMA query que `put_user_policy` y lo pasa a `_policy_scope`, `policies.deactivate` (audit) y `_sweep_connectors`. |
| `platform/tests/test_policies_api.py` | `test_delete_user_policy_without_department_id` (AC-GOV-025): repro exacto del bug — PUT sin depto → 200, DELETE sin `department_id` → 204 (antes del fix: 404, verificado RED), segundo DELETE → 404, user inexistente → 404 `User not found.` |

## Evidencia real (en vivo, tenant fresco `b3-165613`, stack desplegado post-fix)

Imagen `trust-agent/platform:local` rebuild (`678ea63a3c56`) + `trust-agent-api`
recreado; md5 de `app/governance/router.py` dentro del contenedor = md5 del
árbol (`8eedfa1ba141aaf58a5ce3791d6dcee5`). Script:
`docs/evidencia/pilar2/evidencia_b3_policy_delete_symmetry.py`
(log: `evidencia-b3-live.log`), 13/13 PASS:

```
=== t_fdc728d2 (gap B-3) — simetría PUT/DELETE de policies de usuario (tenant b3-165613) ===
[PASS] E0a POST /api/admin/tenants → 201 (realm KC nuevo)
[PASS] E0b login REAL admin (PKCE S256)  · acr=1
[PASS] E0c POST /api/auth/register (auto-registro) → 201
[PASS] E0d POST /api/departments → 201
[PASS] E0e PUT /api/users/{id} (asignar depto) → 200
[PASS] E0f el usuario tiene department_id en la DB  · department_id=66e7e482-…
[PASS] E1 PUT /api/policies/user/{user_id} SIN department_id → 200 (el upsert resuelve el depto)
       · HTTP 200 {"id":"4ca57e33-…","key":"allowed_models","level":"user","version":1,"value":["glm-5.2"]}
[PASS] E2 DELETE /api/policies/user/allowed_models?user_id=… SIN department_id → 204 (REQ-GOV-025; antes del fix: 404)
       · HTTP 204
[PASS] E3 segundo DELETE idéntico → 404 (la fila quedó desactivada)
       · HTTP 404 {"error":{"reason":"not_found","message":"Policy not found at this level."}}
[PASS] E4 re-PUT (misma fila reactivada, version+1) → 200  · version=2
[PASS] E5 DELETE con department_id EXPLÍCITO sigue → 204 (compat)
[PASS] E6 DELETE con user_id inexistente → 404 User not found (mismo contrato que el PUT)
       · HTTP 404 {"error":{"reason":"not_found","message":"User not found."}}
[PASS] E7 el audit del deactivate lleva el department_id RESUELTO
       · payload.department_id=66e7e482-aba0-4c8e-80b7-055d29e9ffc3
=== 13 PASS, 0 FAIL ===
```

## Tests

- `tests/test_policies_api.py` **8/8 PASS** (7 previos + el nuevo de
  regresión), DB aislada `TRUST_TEST_DB=trust_platform_b3`.
- **RED verificado**: con el fix desactivado temporalmente, el test nuevo
  FALLA (reproduce el 404 del bug) — el test sí captura la regresión.
- Regresión del contrato policies (orden alfabético real de la suite):
  `test_connector_policy_sweep + test_custom_connections +
  test_dynamic_discovery + test_model_access + test_policies_api +
  test_policies_matrix + test_policy_catalog_v2 + test_uuid_validation`
  → **96 passed, 1 failed** (`test_dynamic_discovery.py::
  test_periodic_refresh_job_idempotent`). Ese fallo es **preexistente y
  ajeno a este fix**: falla IDÉNTICO con el fix desactivado y el test nuevo
  deseleccionado (contaminación orden-dependiente entre archivos, pasa
  aislado y pasa con su archivo completo); atribución documentada abajo.
- Nota de atribución (skill parallel-lane-convergence): correr el subconjunto
  en otro orden (policies_api antes de connector_policy_sweep) rompe 4 tests
  del sweep por el override dept A2 que `test_dept_cannot_relax_org` deja sin
  limpiar — preexistente, independiente de B-3, no tocado aquí.
