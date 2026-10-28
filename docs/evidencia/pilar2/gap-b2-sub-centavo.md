# GAP B-2 — Spend sub-centavo truncado a 0¢: CONVERGIDO (t_8bd1fee8)

**Fecha**: 2026-10-27 · **Severidad**: MEDIA · **Spec**: 005-governance §2 D-GOV-17 / §3.1 REQ-GOV-016 (AC-GOV-016…016c) · **Migración**: `0029_sub_cent_spend_precision`

## El gap (repro original, t_a296ae9b §BUG B-2)

Llamadas reales pequeñas vía el gateway (ej. 174 tokens deepseek-v4-flash = $0.000045) → spend worker → `spend_ledger`: **9 de 10 filas con `cost_base_cents = 0`**; un worker con 4 llamadas reales quedaba con counters user/depto en 0.0. Solo la llamada ≥ medio centavo movía counters → los presupuestos sub-contaban gasto real y el corte llegaba tarde o nunca.

**Root cause**: `spend_worker.py` `cost_base_cents = round(log.spend_usd * 100)` (entero) + columnas `bigint` en `spend_ledger`/`budget_counters`. La spec 018 migró los PRECIOS del catálogo a `numeric(12,3)` pero ledger/counters seguían en centavos enteros.

## La solución (D-GOV-17)

- **Migración 0029** (guard-serializada sobre 0028, anunciada en la card): `spend_ledger.cost_base_cents`/`cost_billed_cents` y `budget_counters.spent_cents` → `numeric(12,3)` — centavos con precisión de **milli-centavo (0.001¢)**, misma convención que los precios de spec 018. `budgets.amount_cents` y `budget_counters.budget_cents` siguen `bigint` (el admin fija presupuestos en centavos enteros). Paridad con `db/schema.sql` §5.6/§5.7 verificada por el test de paridad (pg_dump idéntico).
- **Ingest**: `_usd_to_milli_cents()` — cadena `Decimal` exacta (`Decimal(str(usd)) * 100`) cuantizada a 0.001¢ `ROUND_HALF_UP` (conservador para el corte: jamás redondea por debajo del costo real). El margen MaaS se aplica en `Decimal` exacto re-cuantizado a 0.001¢ (antes: floor entero `// 10_000`).
- **Corte sin redondeo adicional**: `spent >= amount` contra el acumulado exacto — N llamadas de 0.004¢ cruzan un presupuesto de 1¢ cuando la SUMA alcanza 1.000¢. Umbral 80 % igual (comparación Decimal exacta).
- **API compatible**: `_cents_out()` — int cuando el monto es entero (contrato previo, ej. bundle AC-GOH-002 exige `remaining_cents` int) y float solo si existe fracción sub-centavo. Export CSV por request en USD con 5 decimales (`0.25000`) para que el sub-centavo sea visible.
- **Observación menor (decidida)**: los requests denegados (403) siguen ingresando al ledger con costo 0 **por diseño** — evidencia de auditoría del intento; documentado en la spec.

## Evidencia REAL (en vivo, gateway real 127.0.0.1:4000 → MaaS)

Key virtual `subcent-evidence-b2` (tenant `litellm-real`, user `admin@litellm-real.test`), 4 llamadas reales `deepseek-v4-flash` (~$0.00003 c/u):

```
request_id                      | model             | tokens_in/out | cost_base_cents | cost_billed_cents
086d556473ecbcd862923d2e9943777a | deepseek-v4-flash | 5/119        | 0.003          | 0.003
e4e3bd65008a76caf825d121a32f9338 | deepseek-v4-flash | 5/114        | 0.003          | 0.003
041087f055990b69c8f66b01a19664ab | deepseek-v4-flash | 5/110        | 0.003          | 0.003
d0dd8a27b23749a3171555ab6dc3739d | deepseek-v4-flash | 5/91         | 0.003          | 0.003
```

**Antes del fix estas 4 filas habrían sido `cost_base_cents = 0`** (round(0.003) = 0). Query de control sobre la ventana de 2 h: `filas_cero = 0, filas_subcent = 4, total = 4`.

Counters (ventana corriente) — el gasto sub-centavo ACUMULA y descuenta:

```
level | entity_id                            | spent_cents | budget_cents
user  | d177e046… (admin@litellm-real.test)  | 0.012       | 0 (sin fila de budget → sin corte, AC-GOV-012a)
dept  | 5db85113…                            | 2.012       | 30000   (2.000 previos + 0.012 nuevos)
org   | 8f94d7af… (litellm-real)             | 2.012       | 50000
```

Nota operativa hallada durante la evidencia: el proxy real v1.106.3 NO estampa `spend_logs_metadata` en los logs (`metadata.spend_logs_metadata = null` en `LiteLLM_SpendLogs`) — la resolución tenant/usuario del worker funciona en el stack real por el **fallback de hash** contra `api_keys` (D-GOV-05), que es el camino que exercised esta evidencia.

## Tests

- **Nuevos** (`platform/tests/test_spend_subcent.py`, 5): regresión del truncamiento (10 filas de 0.004¢ → ledger > 0 + counters delta exacto + idempotencia + API), redondeo half-up unitario ($0.000045 → 0.005¢, jamás 0¢), margen Decimal exacto (0.005¢ × 1.1 → 0.006¢), **corte por SUMA** (fase 1: N-1 llamadas sub-centavo → key activa; fase 2: UNA más → blocked + audit `budget.cutoff` + notificación + `/api/budgets/me` cutoff=true), robustos al estado compartido de la suite (deltas + key propia).
- **Actualizados por el cambio de contrato**: `test_spend_cutoff` (margen 39.600 exacto vs floor 39; coerciones int para `amount_cents`), `test_budget_rollover` (ídem), `test_usage_analytics` (export requests a 5 decimales), `e2e/test_usage_e2e` (deepseek = 2.700¢ real, no 3¢ redondeados), `e2e/test_control_plane_flow` (coerción), `litellm_fake.per_call_cents` ahora devuelve el costo EXACTO Decimal (mismo cálculo que el worker).
- **Suites**: platform `pytest tests/` → 666 passed, 11 failed **todos preexistentes/ajenos** (4 = acoplamiento cutoff↔usage por filas del ledger compartidas — conteos, no Decimal; 7 = provision/jobs/discovery/model_access que pasan aislados, subsistemas no tocados). db layer → **207 passed** (paridad schema↔migración incluida). Subconjunto spend/budgets/e2e/bundle/provider → 118 passed, 0 failed.
- **Deploy**: imagen `trust-agent/platform:local` reconstruida, `docker compose up -d` — `alembic_version = 0029_sub_cent_spend_precision` en la DB viva, columnas `numeric(12,3)` verificadas con `\d`.

## Archivos

- `db/migrations/versions/20261027_0029_sub_cent_spend_precision.py` (nueva, guard-serializada)
- `db/schema.sql` (§5.6/§5.7 paridad)
- `platform/app/governance/spend_worker.py`, `spend.py`, `budgets.py`, `usage.py`
- `platform/app/tenants/provider_service.py`
- `platform/tests/test_spend_subcent.py` (nueva), `litellm_fake.py`, `test_spend_cutoff.py`, `test_budget_rollover.py`, `test_usage_analytics.py`, `e2e/test_usage_e2e.py`, `e2e/test_control_plane_flow.py`
- `specs/005-governance/spec.md` (D-GOV-17 + REQ-GOV-016/AC-GOV-016…016c + trazabilidad)
