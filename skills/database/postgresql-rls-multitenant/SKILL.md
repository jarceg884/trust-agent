---
name: postgresql-rls-multitenant
description: "Row Level Security multi-tenant en PostgreSQL: tenant_id en todo, CREATE POLICY con current_setting, SET LOCAL por transacción, FORCE ROW LEVEL SECURITY. Patrones y pitfalls probados en PostgreSQL 17. Use when diseñar el aislamiento lógico de tenants."
version: 1.0.0
author: trust-agent dev
metadata:
  hermes:
    tags: [postgresql, rls, multi-tenant, row-level-security, tenant-id, isolation, database]
---

# PostgreSQL RLS multi-tenant (tenant_id + session variables)

**Patrón probado en PostgreSQL 17** (contenedor efímero `pg-rls-test`, 23-oct-2026).
Docs oficiales: https://www.postgresql.org/docs/current/ddl-rowsecurity.html y
https://www.postgresql.org/docs/current/sql-createpolicy.html

## 1. El patrón (verificado con evidencia real)

```sql
-- Tabla con tenant_id en TODAS las filas
CREATE TABLE documents (
  id bigserial PRIMARY KEY,
  tenant_id text NOT NULL,
  title text NOT NULL
);

-- Rol de aplicación SIN superuser, SIN dueño de tablas
CREATE ROLE app_user LOGIN PASSWORD '...';
GRANT SELECT, INSERT, UPDATE, DELETE ON documents TO app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;  -- ¡ver pitfall #1!

-- RLS on + FORCE (el dueño de la tabla también queda sujeto)
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;

-- Política única por tenant: USING filtra lecturas/updates/deletes,
-- WITH CHECK valida las filas NUEVAS de insert/update
CREATE POLICY tenant_isolation ON documents
  FOR ALL TO app_user
  USING      (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
```

La aplicación setea el tenant **por transacción** (nunca por sesión con pools):

```sql
BEGIN;
SET LOCAL app.tenant_id = 'acme';   -- expira con COMMIT/ROLLBACK: a prueba de pools
SELECT ... FROM documents;          -- solo filas de acme
INSERT INTO documents (tenant_id, title) VALUES ('acme', 'ok');
COMMIT;
```

### Evidencia de la prueba real (PostgreSQL 17)

| Caso | Resultado |
|---|---|
| `SET LOCAL app.tenant_id='acme'` + SELECT | solo ve las filas de `acme` (1 de 2) ✓ |
| SELECT sin SET (`current_setting(..., true)` → NULL) | **0 filas** (default deny) ✓ |
| INSERT con tenant correcto | `INSERT 0 1` ✓ |
| INSERT con `tenant_id='globex'` siendo acme | `ERROR: new row violates row-level security policy` ✓ |
| UPDATE que cambia `tenant_id` a otro tenant | `ERROR: new row violates row-level security policy` ✓ |

## 2. Semántica de RLS que hay que conocer (docs oficiales)

- `ENABLE ROW LEVEL SECURITY` + **sin políticas** = **default deny** (0 filas).
- **Superusuarios y roles con `BYPASSRLS` siempre saltan RLS**; el **dueño de la
  tabla también lo salta** salvo `ALTER TABLE ... FORCE ROW LEVEL SECURITY`.
- `USING` se evalúa para filas existentes (SELECT/UPDATE/DELETE); `WITH CHECK`
  para filas nuevas (INSERT/UPDATE). En `FOR ALL`/`FOR UPDATE`, si falta
  `WITH CHECK` se usa la expresión de `USING` para ambas.
- `TRUNCATE` y `REFERENCES` NO están sujetos a RLS.
- Políticas `PERMISSIVE` (default) se combinan con OR; `RESTRICTIVE` con AND —
  una política restrictive sola concede nada: se combina con permissive.
- Las expresiones de política corren con los privilegios del usuario que consulta
  (funciones `SECURITY DEFINER` pueden ver datos extra — cuidado con leaks).
- `current_setting('app.tenant_id', true)` con `missing_ok=true` devuelve NULL si
  no está seteada (→ la comparación da NULL → fila invisible = fail-closed).
  Sin `missing_ok`, lanza error si la GUC no existe.

## 3. Integración con SQLAlchemy (patrón para FastAPI)

```python
from sqlalchemy import event, text

# Por transacción: SET LOCAL (no SET de sesión — los pools reutilizan conexiones)
@event.listens_for(Session, "after_begin")
def set_tenant(session, transaction, connection):
    connection.exec_driver_sql(
        "SELECT set_config('app.tenant_id', :tenant, true)",  # true = local (transacción)
        {"tenant": session.info["tenant_id"]},
    )
```

`set_config(..., is_local=true)` == `SET LOCAL`: el valor muere con la
transacción. Setear el tenant SIEMPRE dentro de la misma transacción que lee
los datos; nunca confiar en un SET de sesión persistente con connection pools.

Migraciones/admin: el rol de migraciones (dueño) con `FORCE ROW LEVEL SECURITY`
también queda filtrado — para operaciones cross-tenant usar un rol con
`BYPASSRLS` dedicado y auditado, o `SET LOCAL app.tenant_id` explícito.

## 4. Pitfalls (el #1 y #2 los golpeamos en la prueba real)

1. **`GRANT` sobre la tabla NO alcanza para `bigserial`/`serial`**: el INSERT
   falló con `ERROR: permission denied for sequence documents_id_seq`. Fix:
   `GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;`
   (o `ALTER DEFAULT PRIVILEGES ... GRANT USAGE ON SEQUENCES`).
2. **El dueño de la tabla salta RLS** aunque haya políticas: sin
   `FORCE ROW LEVEL SECURITY`, conectar la app como dueño = aislamiento
   inexistente. La app SIEMPRE conecta con un rol sin BYPASSRLS y no dueño.
3. **`SET` (sesión) + connection pool = fuga de tenant entre requests** si una
   conexión vuelve al pool sin reset. Usar `SET LOCAL`/`set_config(..., true)`
   dentro de la transacción, o `DISCARD ALL`/`RESET` en el reset del pool.
4. **RLS no cubre TRUNCATE** — revocar TRUNCATE al rol de app.
5. **`current_setting` sin `missing_ok` lanza error** en vez de NULL — decidir
   fail-closed (missing_ok=true → 0 filas) vs fail-loud según el caso.
6. **Fugas por funciones no leakproof**: expresiones de usuario (WHERE del
   caller) pueden evaluarse tras el check de RLS; funciones marcadas LEAKPROOF
   se aplican antes. No exponer funciones que filtren datos de otros tenants.
7. **Contadores/`SELECT count(*)` también se filtran** (bien), pero los
   índices: crear índice compuesto `(tenant_id, ...)` — la política compara
   `tenant_id` por fila en cada query.
8. **Políticas por rol (`TO app_user`)**: si hay varios roles de app, crear la
   política `TO PUBLIC` o una por rol; una política sin `TO` aplica a todos.

## 5. Test de aislamiento obligatorio (constitution III)

Todo cambio de schema con RLS debe pasar un test que verifique, con un rol de
app real: (a) SELECT ve solo su tenant, (b) INSERT cross-tenant falla,
(c) UPDATE que reasigna tenant falla, (d) sin tenant seteado → 0 filas. La
seccción 1 de este skill es exactamente ese test (reproducible con
`docker run -e POSTGRES_PASSWORD=testpass postgres:17`).

## 6. Lecciones verificadas al implementar el schema Trust Agent (spec 002, PG 17)

1. **Tabla `tenants` auto-referencial**: `tenant_id uuid GENERATED ALWAYS AS (id) STORED NOT NULL`
   da RLS uniforme en TODAS las tablas; insertar un tenant nuevo exige scope
   `app.tenant_id = <su propio id>` (flujo de provisioning), y con otro scope el
   INSERT cae por WITH CHECK. Un UPDATE de la columna generada es error de
   sintaxis, no de RLS — aceptar cualquier error en tests.
2. **Orden de validación**: RLS WITH CHECK se evalúa ANTES que NOT NULL/CHECK —
   un INSERT sin `tenant_id` vía rol de app da error RLS, no NotNullViolation;
   para probar constraints usa un rol con BYPASSRLS. CHECK se evalúa antes que
   FK: un valor fuera de enum + inexistente en catálogo cae por CHECK.
3. **Hash chain de audit en la DB**: `sha256()` es nativo desde PG 13 (no hace
   falta pgcrypto); trigger BEFORE INSERT con `pg_advisory_xact_lock` por tenant
   evita forks de cadena bajo concurrencia; el payload canónico usa `jsonb::text`
   (jsonb normaliza claves) y `extract(epoch FROM ts)` (independiente de timezone).
4. **Paridad de dumps**: pg_dump 17 emite tokens aleatorios `\restrict`/`\unrestrict`
   por corrida — normalízalos antes de diffear dumps (tests de paridad schema↔migración).
5. **Downgrade de Alembic**: jamás `DROP SCHEMA public CASCADE` (destruye
   `alembic_version` y rompe el bookkeeping); dropea objetos explícitos y en orden:
   tablas primero (CASCADE elimina triggers), luego funciones (los triggers
   dependen de ellas), luego schemas de catálogo.

## 7. Lecciones verificadas al implementar el control plane sobre RLS (spec 003, FastAPI)

1. **Una transacción de negocio por request**: la primera sentencia es
   `SELECT set_config('app.tenant_id', :tid, true)` (== SET LOCAL, muere con
   la transacción). Verifica tras el commit que `current_setting(..., true)`
   vuelve a `''` en la conexión del pool — test de no-fuga obligatorio.
2. **Auth antes que scope**: para buscar la sesión (tabla RLS) hace falta el
   tenant ANTES de autenticar. Patrón: token opaco
   `sts1.<tenant_id>.<session_id>.<secret>` — el tenant del token es un HINT
   para abrir el scope; la credencial real es el `secret` (hash en
   `sessions.jti`). Un hint falsificado simplemente no encuentra la fila.
3. **Colas de jobs con RLS no tienen vista cross-tenant para el rol de app**:
   el worker no puede hacer `SELECT ... FOR UPDATE SKIP LOCKED` sobre la cola
   porque RLS le oculta los demás tenants. Solución usada: conexión dedicada
   con rol `BYPASSRLS` SOLO para el claim; el job se ejecuta como rol de app
   con `set_config` del tenant del job. Alternativa más limpia (pendiente):
   función `SECURITY DEFINER claim_next_job()` en el schema.
4. **`request.client.host` no es una IP válida bajo TestClient** ("testclient"):
   sanea antes de escribir en columnas `inet`.
5. **Excepciones dentro de `engine.begin()` revierten TODO, incluidas
   contramedidas**: si detectas reuso de refresh token y revocas la sesión
   antes de lanzar el 401, haz `conn.commit()` explícito antes del raise o la
   revocación se pierde con el rollback.

## 8. Lecciones verificadas al implementar el provisioning de tenants (spec 004)

1. **Insertar la fila raíz `tenants` como rol de app SÍ funciona con id
   pre-generado**: el provisioning no puede hacer `set_config` al tenant
   antes de que exista, pero como `tenant_id` es `GENERATED ALWAYS AS (id)`,
   basta pre-generar el UUID del cliente, hacer
   `set_config('app.tenant_id', <uuid>, true)` como PRIMERA sentencia y
   `INSERT INTO tenants (id, ...) VALUES (<mismo uuid>)` — el `WITH CHECK`
   de la política compara el valor GENERADO contra el setting y pasa. Todo
   el tenant (departamentos, usuarios, políticas, audit) se crea en ESA
   única transacción atómica como `trust_app`; verificado con probe real
   (probe_rls.py, tarea t_8b113637). Un setting que NO coincida con el id
   insertado cae por `InsufficientPrivilege: new row violates row-level
   security policy` — el check compara de verdad.
2. **Provisioning = Keycloak primero, DB al final**: la pierna externa
   (realm IdP) es compensable (DELETE realm); la DB es atómica. Al revés es
   una trampa: tras el commit, `audit_log` (append-only + FK a tenants)
   hace IMPOSIBLE borrar el tenant — no existe rollback post-commit.
3. **Lookup cross-tenant del proveedor**: encontrar un tenant por slug sin
   saber su id exige una conexión `BYPASSRLS` (migrator) SOLO para ese
   lookup; el resto de la verificación corre como rol de app con scope del
   tenant encontrado (mismo patrón que el claim de jobs).

## Fuentes

- https://www.postgresql.org/docs/current/ddl-rowsecurity.html (Row Security Policies)
- https://www.postgresql.org/docs/current/sql-createpolicy.html (CREATE POLICY)
- https://www.postgresql.org/docs/current/sql-set.html (SET / SET LOCAL)
- https://www.postgresql.org/docs/current/functions-admin.html (set_config, current_setting)
- Prueba real: contenedor `pg-rls-test` (postgres:17), evidencia en
  `/root/trust-agent/docs/investigacion.md` §4
