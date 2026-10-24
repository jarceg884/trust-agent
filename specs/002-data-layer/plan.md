# Plan 002 — Capa de datos multi-tenant (PostgreSQL)

| Campo | Valor |
|---|---|
| **Spec** | specs/002-data-layer/spec.md v1.0.0 |
| **Fecha** | 2026-10-23 |

## 1. Herramientas y entorno

- **PostgreSQL 17** en contenedor Docker `trust-db` (127.0.0.1:55432) — mismo major que la prueba RLS del padre (t_31934ca5).
- **Python 3.12 venv** en `db/.venv` con `alembic`, `sqlalchemy`, `psycopg[binary]`, `pytest` (instalado con `uv`).
- **psql** vía `docker exec trust-db psql` (no hay cliente en el host).

## 2. Estructura de entregables

```
db/
├── README.md               # runbook + convenciones (AC-DB-042)
├── schema.sql              # DDL canónico completo (AC-DB-040)
├── alembic.ini             # config Alembic (script_location = migrations)
├── scripts/
│   └── bootstrap.sql       # roles trust_app / trust_migrator (idempotente)
├── migrations/
│   ├── env.py              # lee DATABASE_URL del entorno
│   ├── script.py.mako
│   └── versions/20261023_0001_initial_schema.py   # DDL embebido (paridad 1:1)
└── tests/
    ├── conftest.py         # fixtures: DB efímera, migración, roles, seed 2 tenants
    ├── test_rls_coverage.py    # AC-DB-030/031/033
    ├── test_isolation.py       # AC-DB-032 (parametrizada, 15 tablas)
    ├── test_audit.py           # AC-DB-019/020/021
    ├── test_constraints.py     # AC-DB-010…025
    └── test_schema_parity.py   # AC-DB-040
```

## 3. Estrategia técnica

### 3.1 DDL canónico y paridad (D-DB-12)
`schema.sql` es la única fuente de verdad del DDL. La migración inicial de Alembic **ejecuta el mismo SQL embebido** (generado desde schema.sql, sin reescritura manual) — así el diff de `pg_dump` entre ambas vías es vacío por construcción, y el test de paridad lo verifica en cada corrida. Roles (`bootstrap.sql`) van aparte: son estado de clúster, no de schema; la migración los crea con bloques `DO` idempotentes para que `alembic upgrade head` funcione sobre una DB limpia.

### 3.2 RLS (D-DB-02)
Política única por tabla, `TO trust_app`, expresión `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid` (fail-closed: sin tenant → NULL → 0 filas). `FORCE` para que ni el dueño se salte el filtro. La app SIEMPRE conecta como `trust_app` y setea el tenant con `set_config('app.tenant_id', ..., true)` dentro de la transacción.

### 3.3 Hash chain del audit (D-DB-07)
Trigger `BEFORE INSERT` que: (1) toma `pg_advisory_xact_lock` por tenant (sin forks de cadena bajo concurrencia), (2) asigna `seq = prev+1`, (3) `hash_prev = hash` de la fila anterior (génesis `0^64`), (4) `hash = sha256(hash_prev || canonical_payload)` con la función `sha256()` nativa de PostgreSQL 13+ (sin pgcrypto) — el payload canónico serializa columnas en orden fijo con `jsonb::text` (jsonb normaliza claves). `audit_verify_chain(tenant)` recalcula y enlaza; devuelve `(status, seq)`.

### 3.4 Tests (constitution I: nada simulado)
La suite levanta una DB efímera real (`trust_agent_test`), aplica `alembic upgrade head` (vía canónica), crea los roles y siembra **dos tenants con datos en las 16 tablas**. Los tests de aislamiento se conectan como `trust_app` con `SET LOCAL app.tenant_id` — exactamente como hará la app. El test de paridad aplica `schema.sql` en una DB y la migración en otra y compara `pg_dump --schema-only`.

## 4. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Drift schema.sql ↔ migración | DDL embebido idéntico + test de paridad en CI |
| Forks de cadena de audit bajo concurrencia | advisory lock por tenant en el trigger |
| FK compuestos con columnas NULL (depto opcional) | `UNIQUE (tenant_id, id)` + FK sobre `(tenant_id, department_id)`; NULL permitido |
| RLS invisible si la app conecta como dueño | test AC-DB-001/002 + README: la app SIEMPRE es `trust_app` |
| Secuencias sin grant (pitfall #1 del skill) | `GRANT USAGE, SELECT ON ALL SEQUENCES` + `ALTER DEFAULT PRIVILEGES` |

## 5. Orden de implementación

1. `scripts/bootstrap.sql` + `schema.sql` → aplicar a DB de desarrollo y iterar hasta limpio.
2. Proyecto Alembic (`alembic.ini`, `env.py`, migración inicial generada desde schema.sql).
3. `alembic upgrade head` sobre DB limpia → test de paridad.
4. Suite de tests (conftest + 5 módulos) → iterar hasta verde.
5. `README.md` + cierre de tasks.md + verificación de convergencia (§5 de la spec).
