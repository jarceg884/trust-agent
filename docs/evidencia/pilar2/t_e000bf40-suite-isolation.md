# t_e000bf40 — Aislamiento de suites: contaminación del ledger compartido + estabilización de los 7 intermitentes

**Fecha**: 2026-10-27/28 · **Perfil**: qa · **Veredicto**: **CONVERGIDO** — suite completa `pytest tests/` → **686 passed, 0 failed** (dos corridas consecutivas verde; antes: 666 passed, 11 failed).

## 1. Acoplamiento ledger (el 4-fail de la card)

### Bug 1 — test_spend_cutoff leaves today's spend_ledger rows that break test_usage_analytics exact counts
- **Repro**: `cd platform && .venv/bin/python -m pytest tests/test_spend_cutoff.py tests/test_usage_analytics.py -q` → 4 failed (exactamente los de la card). Cada archivo aislado pasaba.
- **Root cause (más profundo de lo descrito)**: no era solo cutoff. El doble `litellm_fake` es **session-scoped** y acumula logs; el spend worker **re-fetchea toda la ventana del día** en cada ciclo (`spend_worker.py: start = last_occurred.date() - 1`) y su idempotencia por `request_id` solo aplica mientras la fila EXISTE. Borrar filas del ledger sin remover los logs del doble ⇒ el siguiente worker run las **re-ingesta**. Contaminadores de la ventana today/30d del tenant A identificados (evidencia por pares mínimos):
  1. `test_spend_cutoff` — chats de Carol/Alice (el de la card);
  2. `test_spend_worker_robustness::test_orphan_tenant_log_rejected_not_poison` — log `healthy-qa-h2` (Carol, hoy): repro par `robustness+usage` → 3 failed;
  3. `test_device_revocation_sweep` — 5 chats como Carol (glm-5.2);
  4. `test_model_access` — 3 chats con keys de Carol;
  5. `e2e/test_usage_e2e` — Carol×2 (glm-5.2 byok) + Bob×1; su limpieza (paso 7) borra filas pero NO logs ⇒ `test_tenant_self_signup` (último worker run antes de usage) los re-ingesta: repro `e2e_usage+self_signup+usage` → carol==4, total==7 (coincide con run4).
- **Fix (patrón decidido y aplicado)**: sandbox en el CONTAMINADOR, no en la víctima (los asserts de la víctima son el canario hermético; el API no permite filtrar por request_id sin debilitar los ACs). Fixture canónico **`ledger_delta_sandbox`** en `tests/conftest.py` (module-scope, opt-in vía `pytestmark = pytest.mark.usefixtures(...)`): snapshot de request_ids A/B + budget_counters + **logs del doble** → al salir borra SOLO el delta (identificación por diferencia: el doble genera uuid4 sin prefijo), restaura counters exactos y remueve sus logs. Aplicado a los 5 módulos contaminadores.

### Bugs latentes encontrados de paso (misma familia)
- **Bug 2 — e2e/test_usage_e2e runtime import of tests.conftest resolves post env-scrub**: `from tests.conftest import MIGRATOR_DB_URL` DENTRO de `_worker_run` — si el módulo no fue importado en collection, la import ocurre a tiempo de test, tras el scrub de `TRUST_*` del fixture `env` → `TEST_DB` cae al default y el migrator conecta a la DB equivocada. Solo se manifestaba bajo override `TRUST_TEST_DB` y sin otros módulos que importaran `tests.conftest` (en suite completa quedaba enmascarado). Fix: import a nivel de módulo desde `conftest`.
- **Bug 3 — hardcoded/runtine-read test DB name**: `test_cli_governed` y `test_hermes_download` HARDCODEABAN `TEST_DB = "trust_agent_platform_test"`; `test_rbac` y `e2e/test_security_checklist` lo leían de `os.environ` a tiempo de test (post-scrub → default). Bajo override de DB conectaban a otra suite (cross-talk real: durante run2 leyeron la DB de una corrida concurrente del dev). Fix: import de `TEST_DB` desde conftest (import-time).
- **Bug 4 — test_jobs::test_enqueue_is_idempotent no pedía fixtures**: primer test del archivo; corrido aislado fallaba con OperationalError (la DB de sesión se crea lazy). Fix: pide `db`.

## 2. Los 7 intermitentes (todos acoplamiento de orden, no azar)

| Fail | Causa raíz | Fix |
|---|---|---|
| `test_provision_full_flow` (assert 7 == 17) + 3 en cascada (`_state["tenant_id"]` None) | El conteo `available` sin filtro de source: dynamic_discovery siembra ~10 filas byok 'available' en el catálogo global; el provisioning aprueba SOLO centrales (7). Aislado 7==7, suite completa 7==17. | `WHERE status='available' AND source='central'` (alineado con lo que provisioning aprueba). Las 3 cascadas desaparecen con el raíz. |
| `test_periodic_refresh_job_idempotent` (first==6 vs ==2) | `enqueue_periodic_refresh` devuelve el nº de tenants ACTIVOS; las e2e dejan ~4 tenants activos. El assert de conteo exacto no era el AC (cobertura + idempotencia por ventana sí). | `assert first == second and first >= 2` + **try/finally que drena la cola** (el assert roto filtraba 6 jobs pendientes). |
| `test_jobs::test_worker_processes_echo_job_subprocess` (timeout 120s) | El `--once` **drena toda la cola** y auto-encola refreshes periódicos de todos los tenants; con los jobs filtrados por el fail anterior procesaba refreshes lentos hasta expirar. | Sin la fuga basta, pero se sube el margen: timeout 120→300s. |
| `test_model_access::test_access_overview` (qwen3-32b 'available' ≠ 'stale') | ACOPLE INVERSO: cuando el subprocess de test_jobs EXPIRABA, qwen3-32b quedaba stale y model_access PASABA; cuando COMPLETABAN sus refreshes, fallaba. Mecanismo: `test_central_sync` fugaba `litellm_fake.prices` con qwen3-32b → el gateway "lo rutea" → el refresh con `TRUST_MAAS_ENDPOINT` vacío (`usable = routed`, discovery.py) lo restaura a available. | `test_central_sync` restaura `prices` en su `finally` (higiene de sesión). |

## 3. Flake de medianoche UTC (evidencia run2)

Suite que arrancó 23:59:43 UTC: `test_usage_requests_pagination` construía filas como `now - i minutos` → p4 cayó en AYER → `total 4 != 5`. Fix: anclar a `_at(0, 12) - i minutos` (hoy 12:00 UTC, siempre dentro de la ventana today).

## 4. Verificación real (nada simulado)

- Repro de la card (2 archivos): **13 passed** (antes 4 failed). Aislados: 8 y 5 passed.
- Pares/tríos mínimos de cada contaminador: todos verde tras su fix (cutoff+usage 13, robustness+usage 8, cadena completa de 7 módulos en orden de suite 45, e2e_usage+self_signup+usage 15).
- **Suite completa, corrida 5**: `687 passed, 0 failed` (incl. sonda forense que verificó `spend_ledger` tenants A/B = **0 filas** al final). **Corrida 6 (definitiva, sin sonda)**: `686 passed, 0 failed`, EXIT=0. Logs: `full_suite_run{1..6}.log` en el workspace de la card (run1 = baseline con los 11 fails reproducidos).
- Nota operativa: las corridas 5/6 usaron `TRUST_TEST_DB=trust_agent_platform_test_qa` (aislamiento de corridas concurrentes); el repro de la card también se verificó con la DB default. run2 documentó además que DOS suites completas concurrentes en el mismo host se colisionan por recursos compartidos (Keycloak, puertos, DB default) — con los fixes de TEST_DB ya no se cruzan.

## 5. Archivos cambiados (solo tests; cero código de app)

- `platform/tests/conftest.py` — fixture `ledger_delta_sandbox` (aditivo)
- `platform/tests/test_spend_cutoff.py`, `test_spend_worker_robustness.py`, `test_device_revocation_sweep.py`, `test_model_access.py`, `e2e/test_usage_e2e.py` — `pytestmark` sandbox (+import MIGRATOR_DB_URL a nivel de módulo en el e2e)
- `platform/tests/test_provision.py` — filtro `source='central'`
- `platform/tests/test_dynamic_discovery.py` — assert robusto + finally-drain en periodic_refresh; restore de `prices` en central_sync
- `platform/tests/test_jobs.py` — timeout 300s + fixture `db` en el primer test
- `platform/tests/test_usage_analytics.py` — anclaje medianoche en pagination
- `platform/tests/test_cli_governed.py`, `test_hermes_download.py`, `test_rbac.py`, `e2e/test_security_checklist.py` — `TEST_DB` desde conftest

**Observación para @dev (no bloqueante)**: `python -m app.jobs.worker --once` drena la cola COMPLETA y además auto-encola refreshes periódicos en su primera iteración (`next_refresh_enqueue = 0.0` es incondicional) — para un worker one-shot quizá no debería hacer scheduling; se dejó el comportamiento de producción intacto y se estabilizó el test.
