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

## Convención de serialización de `migrations/versions/` (hotspot declarado)

Varias lanes kanban editan este directorio concurrentemente (ya produjo heads
paralelos en 0009, resueltos por la merge `12c08d4fb858`, y el incidente del
migrate exit 255 — ver abajo). Hay un **mecanismo obligatorio** (ADR
[`docs/adr-0001-serializacion-migraciones-alembic.md`](../docs/adr-0001-serializacion-migraciones-alembic.md))
con dos barreras:

### Mecanismo (ADR 0001) — uso obligatorio

**Para crear una migración NO escribas el archivo a mano.** Usa el guardián:

```bash
python3 db/scripts/migration_guard.py new --slug mi_cambio
```

- Toma un `flock` exclusivo sobre `db/migrations/.versions.lock` (espera con
  timeout de 180 s si otro worker lo retiene).
- **Dentro del lock** lee el head REAL y el siguiente `NNNN`, y escribe
  `YYYYMMDD_NNNN_<slug>.py` ya apilado (`down_revision` = head del instante,
  `revision` ≤ 32 chars). Dos workers simultáneos quedan serializados: el
  segundo se apila sobre el primero — imposible bifurcar.
- Si la cadena ya está bifurcada, se niega a crear nada (fail-closed).

**Red de seguridad**: `python3 db/scripts/migration_guard.py check` valida el
grafo sin tocar la DB (única cabeza, sin ids duplicados, padres existentes,
sin ciclos, ids ≤ 32 chars). El servicio `migrate` de compose lo corre ANTES
de `alembic upgrade head`, así que un árbol bifurcado muere con el mensaje
accionable `MULTIPLES CABEZAS — ...` en vez del críptico `exit 255`.
Tests del guardián: `db/tests/test_migration_guard.py` (13 casos, sin DB).

### Reglas obligatorias para toda migración nueva

1. **Anuncia ANTES de crear el archivo**: comentario en tu card kanban con el
   `revision` propuesto y el head actual, para que las lanes hermanas se
   apilen detrás y no al lado.
2. **`down_revision` = head actual** (el guardián lo garantiza; si creas a
   mano, verifícalo con `alembic heads`, no de memoria). Una única cabeza
   SIEMPRE; si aparecen dos heads paralelos, crea una revisión merge
   (`down_revision = ('a', 'b')`) como
   `12c08d4fb858_merge_parallel_0009_branches_default_.py`.
3. **`revision` ≤ 32 caracteres**: `alembic_version.version_num` es
   `VARCHAR(32)` (lección de `0011_gov_agent_policy_keys`).
4. **Nombre de archivo**: `YYYYMMDD_NNNN_<slug>.py` con `NNNN` secuencial
   sobre el head (el guardián lo calcula). Si dos lanes chocan en el número,
   gana la que anunció primero; la otra se re-apila.
5. **Paridad `schema.sql`**: todo cambio de DDL/catálogo va en AMBOS lados
   (migración + `schema.sql`), exigido por `tests/test_schema_parity.py`.
6. **La BD live solo se mueve por la vía canónica** (`alembic upgrade head`
   vía el servicio `migrate` de compose). Nunca `alembic stamp` manual para
   "reconciliar" sin dejar el downgrade documentado en la card.

### El servicio `migrate` corre desde la IMAGEN, no desde el árbol (lección t_42131101)

`docker-compose.yml` build-ea `trust-agent/platform:local` y el `Dockerfile`
**copia** `db/migrations` en build time. Si añades una migración al árbol y NO
reconstruyes la imagen, el siguiente `docker compose up -d` (sin `--no-deps`)
relanza el one-shot `migrate` con migraciones VIEJAS y muere con:

```
service "migrate" didn't complete successfully: exit 255
FAILED: Can't locate revision identified by '<rev_que_no_está_en_la_imagen>'
```

(aunque la revisión SÍ exista en el árbol — Alembic solo ve lo copiado en la
imagen). Por eso, **toda lane que añada una migración debe cerrar con**:

```bash
cd /root/trust-agent && docker compose build migrate   # refresca la imagen
docker compose up -d                                    # aplica y verifica en verde
```

El schema no se daña en el fallo (Alembic aborta antes de tocar nada), pero el
`compose up` completo queda roto para todo el equipo hasta el rebuild.

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
