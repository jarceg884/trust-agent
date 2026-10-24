# Plan técnico — Spec 005 Gobernanza

Referencia: spec.md (mismo directorio). Base: platform/ de spec 003, schema de spec 002.

## 1. Capa de datos (evolución, D-GOV-14)

- `db/schema.sql`: nueva **sección 9** con el mismo estilo/criterios de las secciones 5–8:
  - 9.1 acciones de audit nuevas en `catalog.audit_actions` (`agent.*`, `model.key_*`, `budget.threshold`).
  - 9.2 `agents`, 9.3 `agent_grants`, 9.4 `notifications` — `tenant_id` + FKs compuestas `(tenant_id, …)` + UNIQUE por tenant.
  - 9.5 triggers `set_updated_at` para `agents`; RLS ENABLE+FORCE + política `tenant_isolation` para las 3 tablas (DO block propio — el de la sección 7 corre antes de que existan); grants explícitos a `trust_app`.
- `db/migrations/versions/20261023_0004_governance.py`: embebe la sección 9 verbatim (paridad pg_dump); encadenada sobre `0003_devices_enrollment_token` (única cabeza).
- `db/tests/test_schema_parity.py`: `test_alembic_version_tracked` compara contra el head real de la cadena en vez de `0001_initial` hardcodeado.
- `0001_initial` queda congelado (docstring lo aclara).

## 2. Módulos (estructura vinculante arq §3.1: budgets/grants/notificaciones → `governance/`, LiteLLM/model access → `models/`)

```
app/
├── config.py                     # + litellm_base_url, litellm_master_key
├── errors.py                     # + model_not_approved()
├── governance/
│   ├── budgets.py                # upsert con validaciones, overview, status por usuario
│   ├── policies.py               # upsert tipado + invariables + no-relajación + versionado
│   ├── notifications.py          # notify() + consultas de inbox
│   ├── spend.py                  # ingest (ledger+counters+margen) + enforcement (cortes/umbral/bloqueos)
│   ├── agents.py                 # publicar/grants/acceso/invoke
│   ├── router.py                 # /api/budgets/*, /api/policies/*, /api/agents/*
│   └── spend_worker.py           # python -m app.governance.spend_worker (cursor ops + LiteLLM)
├── models/
│   ├── litellm.py                # LiteLLMClient (Admin API real; fail-closed sin URL)
│   ├── access.py                 # aprobaciones por nivel, intersección efectiva, assert
│   └── router.py                 # + /api/models/access|allowed-for-me|verify|keys
└── auth/router.py                # + GET /api/me/notifications, POST read, GET /api/me/budget
```

## 3. Contratos internos clave

- `period_window(period, date) -> (start, end)`: monthly=mes natural, weekly=lunes→domingo ISO, daily=día.
- `ingest_entries(engine, tenant_id, entries)`: una `tenant_tx`; `INSERT … ON CONFLICT (request_id) DO NOTHING` con rowcount>0 ⇒ upsert de counters (user/dept/org con la ventana del presupuesto de cada nivel, default monthly).
- `enforce_tenant(engine, litellm, tenant_id)`: lee budgets+counters vigentes, calcula corte por nivel, sincroniza `api_keys.status` con `/key/block`//`key/unblock` (solo transiciones), emite audit + notificaciones deduplicadas.
- `LiteLLMClient`: `generate_key`, `block_key(hash)`, `unblock_key(hash)`, `key_info(hash)`, `spend_logs(start_date, end_date)`, `health()`. Montos: LiteLLM usa USD float; la plataforma centavos int (conversión en el borde).
- Spend worker: cursor en `ops.spend_cursor` (plain_tx); mapeo log→tenant por `metadata` con fallback `trust_migrator` read-only sobre `api_keys` por hash (D-GOV-05); ingest por tenant; enforcement por tenant; cursor al final.

## 4. Tests (PostgreSQL 17 real + doble HTTP de LiteLLM declarado)

- `tests/litellm_fake.py`: servidor HTTP real con la Admin API documentada + `/chat/completions` (corte nativo `max_budget` → 429; key bloqueada → 401; modelo fuera de la key → 403; precios de `catalog.model_catalog`; logs con `request_id`, `metadata`, hash de key).
- `conftest.py`: fixture `litellm_fake` + env `TRUST_LITELLM_BASE_URL/_MASTER_KEY`; seed de budgets (org A/B, deptos A1/A2, usuarios) y `model_access` (org A: 2 modelos; A1: subset).
- `test_budgets.py`, `test_policies_api.py`, `test_model_access.py`, `test_agents.py`, `test_spend_cutoff.py` (ingest idempotente, margen/BYOK, cortes 3 niveles, umbral 80 %, unblock, notificaciones, aislamiento).
- Suite db/ intacta (paridad con head dinámico).

## 5. Orden de implementación

1. Schema (sección 9 + migración + parity) → correr suite db/.
2. `config`/`errors`/`litellm.py`/`access.py` + router models.
3. `budgets.py`/`policies.py`/`notifications.py`/`agents.py` + router governance + me endpoints.
4. `spend.py` + `spend_worker.py`.
5. Fake LiteLLM + conftest + tests.
6. Converge: suite completa verde, greps de seguridad, verificación de ACs con evidencia.

## 6. Riesgos y mitigaciones

- **Paridad schema↔migración**: mismo SQL en mismo orden en ambas vías; el test de paridad es el guardián.
- **RLS cross-tenant en el worker**: resolución por metadata primero; fallback migrator SOLO lectura (patrón D-CP-10 ya probado).
- **Corte "0 s" de depto**: desviación documentada (D-GOV-03) — worker ≤ 60 s cumple §7 para depto/org.
- **Keys en claro**: aparecen una sola vez en la respuesta de emisión (one-time, constitution IV); tests grepuean que no persisten.
