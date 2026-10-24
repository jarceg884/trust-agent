# Spec 002 — Capa de datos multi-tenant (PostgreSQL): schema, RLS y migraciones

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-23 |
| **Autor** | dev |
| **Padre** | specs/001-platform/spec.md (112 REQ), docs/arquitectura.md §5 (contratos NORMATIVOS) |
| **Idioma** | Español (docs) / inglés (código, identificadores) / USD centavos (montos) |

---

## 1. Alcance

Esta spec define **la capa de datos** de Trust Agent: DDL completo de PostgreSQL 17, roles de base de datos, Row Level Security multi-tenant, catálogos globales como datos, proyecto Alembic y suite de tests de aislamiento.

**Dentro de alcance**: `db/schema.sql` (DDL canónico), `db/scripts/bootstrap.sql` (roles), proyecto Alembic en `db/migrations/`, seeds de catálogos, funciones de base de datos (hash chain del audit, verificación), tests de aislamiento y de integridad en `db/tests/`, `db/README.md`.

**Fuera de alcance** (los consume la spec 003 / control plane): lógica de aplicación, endpoints, workers, cifrado en sí (la DB solo almacena ciphertext), integración LiteLLM/Keycloak en runtime, perfiles Hermes, agentes compartidos (REQ-AGT: su schema se añade en una spec posterior cuando se implemente el dominio AGT), branding versionado completo (REQ-BRD-004; v1 guarda branding activo en `tenants.branding`).

## 2. Decisiones de diseño (vinculantes para la implementación)

| ID | Decisión | Fuente |
|---|---|---|
| D-DB-01 | **Tres planos de schema**: `catalog` (referencia global del proveedor: modelos, conectores, claves de política, matriz RBAC, acciones de audit — sin `tenant_id`, sin RLS, solo `SELECT` para la app), `public` (tablas de negocio con `tenant_id` + RLS) y `ops` (estado operativo cross-tenant del proveedor: `spend_cursor`). "Tenant_id en TODO" aplica a **tablas de negocio** (AC-TEN-001); los catálogos globales no contienen datos de tenant por diseño (REQ-MDL-006, REQ-CON-001). | spec 001 §2.1/§4.14, arq. §5.3 |
| D-DB-02 | **Patrón RLS** (verificado en PostgreSQL 17, skill `postgresql-rls-multitenant`): política única `tenant_isolation FOR ALL TO trust_app` con `USING`/`WITH CHECK` `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`; `ENABLE` + `FORCE ROW LEVEL SECURITY`; la app conecta con rol `trust_app` (sin `BYPASSRLS`, no dueño); `trust_migrator` es dueño con `BYPASSRLS` para migraciones y ops cross-tenant auditadas. `SET LOCAL`/`set_config(..., true)` por transacción, nunca `SET` de sesión. | arq. ADR-008, skill RLS |
| D-DB-03 | `tenants` es auto-referencial: `tenant_id uuid GENERATED ALWAYS AS (id) STORED NOT NULL` — RLS uniforme en todas las tablas `public`. | verificado en PG 17 |
| D-DB-04 | Identificadores: `uuid` (`gen_random_uuid()`) para entidades de negocio; `audit_log.id bigserial` (orden global) + `audit_log.seq bigint` por tenant (asignado bajo advisory lock). | arq. §5.6 |
| D-DB-05 | **Dinero**: centavos enteros `BIGINT`, nunca float. Márgenes en basis points (`maas_margin_bps`) por tenant; `cost_billed_cents = cost_base_cents * (10000 + margin_bps) / 10000` al ingestar; el histórico nunca se reescribe. | spec 001 §9.1, arq. §5.3 |
| D-DB-06 | Niveles de gobernanza con valores físicos `'org' | 'dept' | 'user'` en presupuestos, políticas, model access y contadores (mapeo 1:1 con organization/department/user de spec 001). | arq. §5.3/§5.7 |
| D-DB-07 | **audit_log INSERT-only a nivel DB**: sin `GRANT UPDATE/DELETE` a `trust_app` + trigger `BEFORE UPDATE OR DELETE` que lanza excepción (aplica a cualquier rol, incluido el dueño). Hash chain por tenant: `hash = sha256(hash_prev || canonical_payload)` con génesis `0^64`, calculado en trigger bajo `pg_advisory_xact_lock` por tenant; función `audit_verify_chain(tenant)` devuelve `ok` o la primera fila rota con su `seq`. | REQ-AUD-001/002/007, arq. §5.6 |
| D-DB-08 | **Vault envelope encryption**: por credencial, `ciphertext_secret` (AES-256-GCM del secreto con DEK propia), `ciphertext_dek` (DEK cifrada con la KEK), `nonce`, `key_id` (versión de KEK). La KEK vive fuera de la DB (env/KMS). Prohibido cualquier columna con secreto en claro. | REQ-VLT-001, arq. §5.5, ADR-009 |
| D-DB-09 | **Integridad cross-tenant por FKs compuestos**: toda referencia intra-negocio usa `(tenant_id, entidad_id)` → tabla `(tenant_id, id)` con `UNIQUE (tenant_id, id)`; es imposible apuntar a una fila de otro tenant incluso saltando RLS. | C3 |
| D-DB-10 | `api_keys` almacena **solo el hash** de la virtual key de LiteLLM (`litellm_key_hash`), jamás la key; incluye `litellm_team_id` (depto) y `device_id` opcional (key por usuario/disposituo). | arq. §5.3, investigación LiteLLM |
| D-DB-11 | **Catálogos como datos** (I10): `catalog.policy_keys` (con flag `invariable`), `catalog.roles`/`catalog.permissions`/`catalog.role_permissions` (matriz RBAC §2.1), `catalog.connector_catalog` (6 iniciales), `catalog.model_catalog`, `catalog.audit_actions` (enum versionado de eventos). Nada de esto es constante de código. | REQ-POL-003, REQ-CON-001, arq. §5.6 |
| D-DB-12 | **Paridad schema.sql ↔ Alembic**: `schema.sql` es el DDL canónico; la migración inicial ejecuta el mismo DDL (embebido); un test de paridad compara `pg_dump` de ambas vías y exige igualdad (excluyendo `alembic_version`). | constitution II |
| D-DB-13 | Enums físicos como `TEXT` + `CHECK (IN (...))` — portables, diffs limpios en migraciones. | convención |
| D-DB-14 | **Rate limiting de login en `ops.login_throttle`** (resuelve D-CP-12 de spec 003): el conteo de fallos es PRE-autenticación y cross-tenant (no hay `tenant_id` confiable aún; `audit_log` exige tenant y es append-only por tenant), así que vive en el plano `ops` del proveedor — sin `tenant_id`, sin RLS, grants `SELECT/INSERT/UPDATE` a `trust_app` (sin `DELETE`: expiración por ventana lógica). Se descarta la función `SECURITY DEFINER` (superficie de dueño que este schema evita, mismo criterio que D-CP-10 para jobs). Claves despersonalizadas: por cuenta solo con claims criptográficamente verificados (`acct:<tenant>:<sha256(sub)>`), por IP para tokens basura (`ip:<inet>`). | 001-REQ-SEC-006, 003-D-CP-10/12 |
| D-DB-15 | **Rechazo durable de logs de spend huérfanos en `ops.spend_rejections`** (QA H2, t_82b2ffaa): un log del gateway que referencia un tenant inexistente no puede auditarse en `audit_log` (su `tenant_id` es FK a `tenants` y el tenant NO existe — mismo razonamiento que D-DB-14), así que el rechazo vive en el plano `ops`: `tenant_id` es referencia forense sin FK, sin RLS, grants `SELECT/INSERT` a `trust_app` (append-only, sin `UPDATE/DELETE`), dedupe `UNIQUE (tenant_id, request_id, reason)`. El worker de spend salta el log con este registro en lugar de tumbar el ciclo completo. | 005-REQ-BUD-005, QA t_82b2ffaa |

## 3. Requisitos (formato EARS)

Convenciones: plantilla EARS **Cuando** `<trigger>` **el sistema** `<respuesta>`; prioridad MoSCoW; cada REQ termina con su(s) AC verificables con evidencia real. Trazabilidad: `[001-REQ-…]` = spec maestra, `[ARQ-§…]` = docs/arquitectura.md, `[SKILL-RLS]` = skill postgresql-rls-multitenant.

### 3.1 Estructura y roles de base de datos

**REQ-DB-001** (M) [ARQ-§3.9] — Cuando se aprovisiona la base de datos, el sistema crea los roles `trust_app` (LOGIN, sin `BYPASSRLS`, no dueño de objetos) y `trust_migrator` (LOGIN, `BYPASSRLS`, dueño de los objetos), y los schemas `catalog`, `public` (existente) y `ops`.
> **AC-DB-001**: `\du` muestra ambos roles con los atributos correctos; `trust_app` NO es miembro de `trust_migrator` ni dueño de tabla alguna.

**REQ-DB-002** (M) [SKILL-RLS pitfall #1] — Cuando se crean grants, el sistema concede a `trust_app` `SELECT/INSERT/UPDATE/DELETE` sobre las tablas de negocio, `USAGE, SELECT` sobre TODAS las secuencias, `USAGE` sobre los schemas y `SELECT` sobre `catalog`; y revoca `UPDATE/DELETE` sobre `audit_log` y todo acceso de escritura a `catalog`.
> **AC-DB-002**: `has_table_privilege('trust_app','audit_log','update')` = false; `has_table_privilege('trust_app','spend_ledger','insert')` = true; `has_sequence_privilege('trust_app','audit_log_id_seq','usage')` = true; `has_table_privilege('trust_app','catalog.policy_keys','insert')` = false.

**REQ-DB-003** (M) [001-REQ-CON-001, REQ-MDL-006] — Cuando el proveedor publica catálogos, el sistema los crea como datos en `catalog`: `model_catalog` (con pricing por millón de tokens en centavos), `connector_catalog` (6 conectores iniciales: vmware, email, whatsapp, telegram, slack, teams), `policy_keys` (12 claves tipadas, 4 invariables), `roles`+`permissions`+`role_permissions` (matriz RBAC §2.1 exacta) y `audit_actions` (enum versionado).
> **AC-DB-003**: seeds consultables: 6 conectores, 4 roles, ≥ 17 permisos con su matriz, 12 policy_keys (4 con `invariable=true`), ≥ 25 audit_actions, ≥ 2 modelos.

**REQ-DB-004** (M) [001-REQ-SEC-006, 003-D-CP-12] — Cuando el control plane registra un intento de login fallido, el sistema persiste el contador en `ops.login_throttle` (clave despersonalizada, `fail_count`, ventana `window_started_at`/`last_fail_at`, `locked_until` para lockout progresivo), accesible para `trust_app` sin contexto de tenant y sin `DELETE`.
> **AC-DB-004**: `has_table_privilege('trust_app','ops.login_throttle','insert')` = true y `('trust_app','ops.login_throttle','delete')` = false; la tabla no tiene columna `tenant_id` ni RLS (plano del proveedor); el contador sobrevive al reinicio del proceso del API (persistido en PostgreSQL).

### 3.2 Tablas de negocio

**REQ-DB-010** (M) [001-REQ-ONB] — Cuando se crea un tenant, el sistema persiste en `tenants`: nombre, slug único, estado (`active|suspended`), realm/issuer/client OIDC, margen MaaS en bps, branding JSONB y retención (≥ 365 días), con `tenant_id` auto-referencial.
> **AC-DB-010**: INSERT válido persiste y `tenant_id = id`; retención < 365 → error de CHECK; slug duplicado → error de UNIQUE.

**REQ-DB-011** (M) [001-REQ-ORG] — Cuando se crean departamentos y usuarios, el sistema persiste `departments` (nombre único por tenant) y `users` (email único por tenant, estado, MFA, `oidc_subject`, depto opcional) con FK compuesto `(tenant_id, department_id)`.
> **AC-DB-011**: el mismo email en dos tenants distintos persiste sin conflicto; FK a un depto de otro tenant → error (verificado con FK compuesto).

**REQ-DB-012** (M) [001-REQ-ORG-003, §2.1] — Cuando se asigna un rol, el sistema persiste en `user_roles` solo claves existentes en `catalog.roles` y solo los roles de cliente (`org_admin|dept_admin|user`); `dept_admin` exige `department_id` de scope.
> **AC-DB-012**: `role_key='platform_admin'` → error de CHECK; `role_key='inventado'` → error de FK; `dept_admin` sin depto → error de CHECK.

**REQ-DB-013** (M) [001-REQ-BUD, ARQ-§5.3] — Cuando se define presupuesto, el sistema persiste en `budgets` exactamente un nivel por fila (`org|dept|user`) con scope coherente (CHECK), monto en centavos ≥ 0, período (`daily|weekly|monthly`) y umbral de aviso; y crea la tabla `budget_counters` (agregación por período: `level, entity_id, period_start, period_end, spent_cents, budget_cents`).
> **AC-DB-013**: fila `level='org'` con `department_id` → error de CHECK; `amount_cents=-1` → error; dos presupuestos `org` para el mismo tenant → error de UNIQUE; `budget_counters` acepta fila por (tenant, level, entity, período).

**REQ-DB-014** (M) [001-REQ-BUD-005/011, ARQ-§5.3] — Cuando se ingiere gasto, el sistema persiste en `spend_ledger` **una fila por request** con `request_id` UNIQUE, usuario, depto, modelo, modo (`central|byok`), `cost_base_cents`, `cost_billed_cents`, tokens y marcas de tiempo (`occurred_at`, `ingested_at`); y persiste el cursor de polling del worker en `ops.spend_cursor`.
> **AC-DB-014**: dos filas con el mismo `request_id` → error de UNIQUE; fila con `mode='byok'` y `cost_billed_cents = cost_base_cents` persiste (sin margen); `ops.spend_cursor` actualizable por `trust_app`.

**REQ-DB-015** (M) [001-REQ-POL-001/003/004, ARQ-§5.7] — Cuando se define una política, el sistema persiste en `policies` filas con nivel (`org|dept|user`), scope coherente, `key` FK a `catalog.policy_keys`, `value` JSONB y versión; las claves `invariable=true` del catálogo documentan el piso del proveedor.
> **AC-DB-015**: `key='clave_inventada'` → error de FK; fila `level='user'` sin `user_id` → error de CHECK; dos filas (tenant, nivel, scope, key) → error de UNIQUE.

**REQ-DB-016** (M) [001-REQ-DEV] — Cuando se enrola un dispositivo, el sistema persiste en `devices`: usuario, huella única por tenant, estado (`pending|active|revoked|rejected`), clave pública y trazas de aprobación/uso.
> **AC-DB-016**: huella duplicada en el mismo tenant → error de UNIQUE; la misma huella en otro tenant persiste sin conflicto.

**REQ-DB-017** (M) [001-REQ-CON, ARQ-§5.5] — Cuando un usuario conecta un conector, el sistema persiste en `connector_instances`: usuario, `connector_slug` FK al catálogo, estado (`active|disconnected|revoked`), modo de mensajería (`read_only|reply|full`, opcional) y credencial asociada por FK compuesto.
> **AC-DB-017**: `connector_slug='noexiste'` → error de FK; instancia única por (tenant, usuario, conector).

**REQ-DB-018** (M) [001-REQ-VLT-001, ARQ-§5.5, ADR-009] — Cuando se almacena una credencial, el sistema persiste en `vault_credentials` SOLO material cifrado: `ciphertext_secret`, `ciphertext_dek`, `nonce`, `key_id`, más metadatos (tipo, conector, scope, estado, rotación/uso); sin ninguna columna de secreto en claro.
> **AC-DB-018**: INSERT sin `ciphertext_secret` o sin `ciphertext_dek` → error de NOT NULL; el dump de la tabla no contiene los valores canarios en claro (test automatizado).

**REQ-DB-019** (M) [001-REQ-AUD-001/002, ARQ-§5.6] — Cuando se inserta un evento de audit, el sistema persiste en `audit_log`: `tenant_id`, `seq` por tenant, `actor_id`/`actor_role`, `action` FK a `catalog.audit_actions`, target, `device_id`/`cost_cents` opcionales, `payload` JSONB, `ts`, `hash_prev` y `hash = sha256(hash_prev || canonical_payload)` encadenado por tenant bajo advisory lock.
> **AC-DB-019**: tras 3 inserts del tenant A, `hash_prev` de la fila n+1 == `hash` de la fila n; `seq` es 1,2,3; el hash recalculado por la función de verificación coincide.

**REQ-DB-020** (M) [001-REQ-AUD-001] — Cuando cualquier rol intenta `UPDATE` o `DELETE` sobre `audit_log`, el sistema lo impide a nivel de base de datos (sin grant + trigger de rechazo).
> **AC-DB-020**: `UPDATE` como `trust_app` → error de permiso; `UPDATE` como superusuario → excepción del trigger; `DELETE` ídem.

**REQ-DB-021** (M) [001-REQ-AUD-007] — Cuando se verifica la cadena, el sistema (función SQL `audit_verify_chain(tenant)`) valida la cadena completa del tenant y reporta `ok` o la primera fila inconsistente con su `seq`.
> **AC-DB-021**: cadena íntegra → `ok`; tras manipular una fila (superusuario con trigger deshabilitado) → reporta el `seq` exacto de la fila alterada.

**REQ-DB-022** (M) [001-REQ-MDL, ARQ-§5.3] — Cuando se aprueba un modelo, el sistema persiste en `model_access`: modelo FK al catálogo, nivel (`org|dept|user`), scope coherente, modo (`central|byok`) y estado (`approved|pending|denied`), único por (tenant, modelo, nivel, scope).
> **AC-DB-022**: aprobación `org` y aprobación `user` del mismo modelo coexisten; duplicado exacto → error de UNIQUE.

**REQ-DB-023** (M) [001-REQ-MDL-002, ARQ-§5.3] — Cuando se provisiona una virtual key, el sistema persiste en `api_keys` SOLO el hash de la key, `litellm_team_id`, usuario, dispositivo opcional, modelos permitidos (JSONB), techo y duración de presupuesto, y estado (`active|blocked|revoked`).
> **AC-DB-023**: INSERT sin `litellm_key_hash` → error; no existe columna que pueda contener la key en claro (inspección + test canario).

**REQ-DB-024** (M) [001-REQ-SEC-003, C2] — Cuando se crea una sesión, el sistema persiste en `sessions`: usuario, dispositivo opcional, kind (`desktop|admin|api`), `jti`, hashes de refresh/CSRF, `mfa_verified`, expiración server-side y estado (`active|revoked|expired`).
> **AC-DB-024**: sesión con `expires_at` pasado persiste con estado consultable (la expiración se evalúa por query, no por magia); hashes únicos.

**REQ-DB-025** (M) [001-REQ-ORG-002, C2] — Cuando se encola un job, el sistema persiste en `jobs`: tipo, estado (`pending|running|succeeded|failed|cancelled`), payload/result JSONB, intentos, `run_after` e `idempotency_key` única por (tenant, tipo).
> **AC-DB-025**: dos jobs con igual (tenant, tipo, idempotency_key) → error de UNIQUE parcial; job sin `tenant_id` → error de NOT NULL.

### 3.3 RLS y aislamiento

**REQ-DB-030** (M) [001-REQ-TEN-001, ARQ-ADR-008] — Cuando se crea cualquier tabla de negocio, el sistema incluye `tenant_id` (FK a `tenants` o generado), activa `ENABLE` + `FORCE ROW LEVEL SECURITY` y crea la política `tenant_isolation FOR ALL TO trust_app` con `USING`/`WITH CHECK` por `current_setting('app.tenant_id')`.
> **AC-DB-030**: introspección: 16/16 tablas de `public` con columna `tenant_id`, RLS activada y forzada, y política existente; `catalog` y `ops` sin RLS por diseño.

**REQ-DB-031** (M) [SKILL-RLS] — Cuando la app consulta sin tenant en contexto, el sistema devuelve 0 filas (default deny, fail-closed por `NULLIF(...)` + cast).
> **AC-DB-031**: SELECT sin `SET LOCAL app.tenant_id` → 0 filas en todas las tablas de negocio.

**REQ-DB-032** (M) [001-REQ-TEN-002/003, constitution III] — Cuando un tenant intenta leer, escribir, reasignar o borrar filas de otro tenant con el rol de app, el sistema lo rechaza o lo filtra por RLS en TODAS las vías (SELECT, INSERT, UPDATE, DELETE).
> **AC-DB-032**: suite de aislamiento parametrizada sobre las 16 tablas: SELECT ve solo su tenant; INSERT cross-tenant → error RLS; UPDATE que reasigna `tenant_id` → error RLS; UPDATE/DELETE de filas del otro tenant → 0 filas afectadas. Todo en verde.

**REQ-DB-033** (M) [SKILL-RLS pitfall #7] — Cuando se crean índices, el sistema los crea compuestos con `tenant_id` como primera columna en los caminos calientes (email, nombre, estado, FKs, ledger por fecha, audit por seq).
> **AC-DB-033**: `\di` muestra índices `(tenant_id, …)` en todas las tablas de negocio.

### 3.4 Migraciones y verificación

**REQ-DB-040** (M) [constitution II] — Cuando se versiona el schema, el sistema mantiene un proyecto Alembic cuya migración inicial produce EXACTAMENTE el mismo schema que `schema.sql` (paridad verificada por `pg_dump`).
> **AC-DB-040**: `pg_dump` de DB aplicada con `schema.sql` == `pg_dump` de DB con `alembic upgrade head` (excluyendo `alembic_version`); diff vacío.

**REQ-DB-041** (M) [constitution I] — Cuando se ejecuta la suite `db/tests/`, el sistema valida con un rol de app real: cobertura RLS (REQ-DB-030/031/033), aislamiento por tabla (REQ-DB-032), inmutabilidad y cadena del audit (REQ-DB-019/020/021), constraints de negocio (REQ-DB-010…025) y paridad schema↔Alembic (REQ-DB-040).
> **AC-DB-041**: `pytest db/tests/ -q` termina OK con ≥ 40 casos en verde y evidencia en la salida.

**REQ-DB-042** (M) — Cuando un desarrollador despliega la capa de datos local, el sistema ofrece en `db/README.md` el runbook completo (contenedor, bootstrap de roles, `alembic upgrade head`, tests) y las convenciones (RLS, FKs compuestos, dinero en centavos, catálogos como datos).
> **AC-DB-042**: los comandos del README, ejecutados en orden sobre una DB limpia, dejan el schema operativo y la suite en verde (evidencia de esta corrida en §6).

## 4. Reconciliación con contratos normativos (docs/arquitectura.md §5)

| Contrato | Materialización física | Desviación |
|---|---|---|
| §5.3 `spend_ledger(tenant_id, request_id UNIQUE, user_id, dept_id, model, mode, cost_base_cents, cost_billed_cents, tokens_in, tokens_out, occurred_at, ingested_at)` | `public.spend_ledger` con esas columnas exactas (`model` → `model_name text`; `dept_id` → `department_id`) | Solo nombres físicos; semántica 1:1 |
| §5.3 `budget_counters(tenant_id, level, entity_id, period_start, period_end, spent_cents, budget_cents)` | `public.budget_counters` exacto; `level` CHECK `org/dept/user` | Ninguna |
| §5.3 cursor persistido (`spend_cursor` en DB) | `ops.spend_cursor` (singleton, actualizable por la app) | Ninguna |
| REQ-SEC-006 contadores de rate limiting de login en DB (cross-tenant, pre-autenticación) | `ops.login_throttle` (por cuenta verificada y por IP, lockout progresivo, sin RLS por diseño — D-DB-14) | Ninguna |
| QA H2 (t_82b2ffaa) rechazo durable de logs de spend con tenant inexistente | `ops.spend_rejections` (referencia forense sin FK, append-only para la app, dedupe por tenant+request+reason — D-DB-15) | Ninguna |
| §5.5 storage `ciphertext_dek` + `ciphertext_secret` (AES-256-GCM) | `public.vault_credentials.ciphertext_secret / ciphertext_dek / nonce / key_id` | Ninguna |
| §5.6 `audit_log(tenant_id, seq_per_tenant, actor_id, actor_role, action, target_type, target_id, device_id, cost_cents, payload, ts, hash_prev, hash)` + INSERT-only + trigger | `public.audit_log` con esas columnas exactas (`seq_per_tenant` → `seq`); sin grants UPDATE/DELETE + trigger `audit_log_immutable`; `action` FK a `catalog.audit_actions` (enum versionado como dato) | Ninguna |
| §5.6 verificación → `{status:"ok"}` o `{status:"broken", seq:N}` | función SQL `audit_verify_chain(uuid)` devuelve `(status, seq)` | Ninguna |
| §5.7 claves tipadas + invariables como datos | `catalog.policy_keys` con `invariable` + `policies` FK | Ninguna |
| ADR-008 / §10.1 rol app sin BYPASSRLS + FORCE + grants de secuencias | `trust_app` / `trust_migrator` + `FORCE` + `GRANT USAGE ON ALL SEQUENCES` | Rol nombrado `trust_app` (arq. sugería `app_api`): nombre físico, semántica idéntica |

## 5. Checklist de convergencia

- [x] AC-DB-001…042 verificados con evidencia real (tests + comandos).
- [x] Suite de aislamiento en verde (condición bloqueante, constitution III) — 153/153.
- [x] Paridad schema.sql ↔ Alembic demostrada por diff vacío (pg_dump normalizado).
- [x] Trazabilidad bidireccional REQ↔AC sin huérfanos (test_spec_traceability.py).
- [x] tasks.md con todas las tareas cerradas.

Evidencia de la corrida de convergencia (2026-10-23, PostgreSQL 17 en contenedor
`trust-db`): `pytest db/tests/ -q` → **153 passed** (aislamiento parametrizado
sobre 16 tablas × 6 vías, RLS/grants/índices, audit chain + detección de
manipulación con seq exacto, constraints de negocio, paridad schema↔Alembic,
trazabilidad REQ↔AC); ciclo `alembic downgrade base` → `upgrade head` → exit 0
con `alembic_version` conservada; runbook del README ejecutado de punta a punta
sobre DB limpia (bootstrap idempotente ×2, upgrade, RLS default-deny + scope +
`audit_verify_chain` = ok como `trust_app`).
