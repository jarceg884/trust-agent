# Trust Agent — Capa de datos (PostgreSQL)

Spec: [`specs/002-data-layer/spec.md`](../specs/002-data-layer/spec.md) ·
Contratos normativos: [`docs/arquitectura.md`](../docs/arquitectura.md) §5 ·
Skill base: `postgresql-rls-multitenant`.

Multi-tenancy con aislamiento lógico: `tenant_id` en todas las tablas de negocio,
Row Level Security `ENABLE`+`FORCE`, roles de DB separados para app y migraciones,
audit log append-only con hash chain por tenant y vault que solo almacena ciphertext.

## Estructura

```
db/
├── schema.sql               # DDL canónico (fuente de verdad del schema)
├── alembic.ini              # config de Alembic (URL vía DATABASE_URL)
├── scripts/bootstrap.sql    # roles trust_app / trust_migrator (idempotente)
├── migrations/              # proyecto Alembic (la migración inicial embebe schema.sql)
└── tests/                   # suite de aislamiento + constraints + audit + paridad
```

## Runbook local (verificado)

```bash
# 1) PostgreSQL 17 (contenedor dedicado, puerto 55432)
docker run -d --name trust-db -e POSTGRES_PASSWORD=trustlocal \
  -p 127.0.0.1:55432:5432 postgres:17

# 2) venv con las herramientas
cd /root/trust-agent/db
uv venv .venv && uv pip install --python .venv/bin/python \
  alembic sqlalchemy "psycopg[binary]" pytest

# 3) roles de DB (una vez, como superusuario; idempotente)
docker exec -i trust-db psql -U postgres -f - < scripts/bootstrap.sql

# 4) schema vía Alembic (la vía canónica)
export DATABASE_URL="postgresql+psycopg://postgres:trustlocal@127.0.0.1:55432/trust_agent"
docker exec trust-db psql -U postgres -c "CREATE DATABASE trust_agent"
.venv/bin/alembic upgrade head

# 5) suite completa (levanta su propia DB efímera trust_agent_test)
.venv/bin/python -m pytest tests/ -q
```

`schema.sql` y la migración producen **el mismo schema** (lo exige
`tests/test_schema_parity.py` comparando `pg_dump`); para cambios de schema:
edita `schema.sql`, regenera/emite la migración y corre la suite.

## Roles de base de datos

| Rol | Uso | Atributos |
|---|---|---|
| `trust_app` | **La aplicación SIEMPRE conecta aquí.** | `LOGIN`, sin `BYPASSRLS`, no dueño de objetos. Sujeto a RLS. |
| `trust_migrator` | Alembic y operaciones cross-tenant auditadas. | `LOGIN`, `BYPASSRLS`, dueño. **Jamás** para la app. |

Contraseñas de desarrollo en `scripts/bootstrap.sql` (`*_dev`): cámbialas fuera del entorno local.

## Regla de oro del multi-tenant (RLS)

Toda tabla de negocio tiene `tenant_id` + `ENABLE`/`FORCE ROW LEVEL SECURITY` +
una única política:

```sql
CREATE POLICY tenant_isolation ON <tabla>
  FOR ALL TO trust_app
  USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
```

La app **debe** setear el tenant por transacción (nunca `SET` de sesión, por los
pools de conexiones):

```sql
BEGIN;
SELECT set_config('app.tenant_id', '<uuid>', true);  -- == SET LOCAL
-- ... queries ...
COMMIT;
```

Sin `app.tenant_id` → `NULL` → **0 filas** (fail-closed). Con tenant → solo sus
filas; INSERT/UPDATE cross-tenant → error de RLS. Verificado por
`tests/test_isolation.py` (16 tablas × 6 vías).

## Planos del schema

| Schema | Contenido | RLS |
|---|---|---|
| `public` | 16 tablas de negocio (tenants, departments, users, user_roles, budgets, budget_counters, spend_ledger, policies, devices, vault_credentials, connector_instances, audit_log, model_access, api_keys, sessions, jobs) | Sí, en todas |
| `catalog` | Referencia global del proveedor: matriz RBAC (`roles`/`permissions`/`role_permissions`), `policy_keys` (4 invariables), `model_catalog`, `connector_catalog` (6 iniciales), `audit_actions` | No (sin datos de tenant; la app solo tiene `SELECT`) |
| `ops` | `spend_cursor` (cursor del worker spend-aggregator, arq. §5.3), `login_throttle` (contadores de rate limiting de login, REQ-SEC-006 / D-DB-14) | No (estado cross-tenant del proveedor) |

## Convenciones fijas

- **Dinero**: centavos enteros `BIGINT` (`amount_cents`, `cost_base_cents`,
  `cost_billed_cents`); márgenes en basis points (`tenants.maas_margin_bps`).
  Nunca float.
- **Niveles de gobernanza**: `'org' | 'dept' | 'user'` con CHECK de scope
  coherente (una fila de nivel `dept` exige `department_id`, etc.).
- **FKs compuestos**: las referencias intra-negocio son `(tenant_id, entidad_id)`
  → imposible apuntar a otro tenant incluso saltando la app.
- **IDs**: `uuid` (`gen_random_uuid()`); `audit_log.id` es `bigserial` (orden
  global) y `audit_log.seq` es por tenant.
- **Vault**: solo `ciphertext_secret` + `ciphertext_dek` + `nonce` + `key_id`
  (AES-256-GCM, envelope). La KEK vive fuera de la DB. Jamás plaintext.
- **api_keys**: solo `litellm_key_hash`; la key en claro no existe en la DB.
- **Catálogos como datos**: policy keys, roles/permisos, conectores, modelos y
  acciones de audit son filas, no constantes de código (I10).

## Audit log (arq. §5.6)

- `INSERT`-only: `trust_app` no tiene grant `UPDATE/DELETE` y el trigger
  `audit_log_immutable_trigger` rechaza la mutación **para cualquier rol**.
- Hash chain por tenant: `hash = sha256(hash_prev || canonical_payload)`,
  génesis `0^64`, calculado en `audit_log_before_insert` bajo advisory lock
  (sin forks de cadena bajo concurrencia); `seq` consecutivo por tenant.
- Verificación: `SELECT * FROM audit_verify_chain('<tenant_uuid>');` →
  `('ok', NULL)` o `('broken', <seq de la primera fila inconsistente>)`.

## Seeds

`catalog` viene sembrado: 4 roles + 18 permisos + matriz RBAC (spec 001 §2.1),
12 policy keys (4 invariables), 6 conectores, 2 modelos MaaS y 34 acciones de
audit. El pricing de `model_catalog` es un seed de referencia: el job
`pricing_sync` (REQ-MDL-004) lo sincroniza con el pricing vigente.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q   # 153 casos: aislamiento 16 tablas,
                                       # RLS/grants/índices, audit chain + tamper,
                                       # constraints, paridad schema↔Alembic,
                                       # trazabilidad REQ↔AC de la spec
```

La suite levanta `trust_agent_test` desde cero con `alembic upgrade head`,
siembra dos tenants con datos en las 16 tablas y ejecuta todo como `trust_app`
(rol real de aplicación). Última corrida: **153 passed**.
