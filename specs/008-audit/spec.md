# Spec 008 — Audit log inmutable: consulta, verificación CLI e instrumentación

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-23 |
| **Autor** | dev (tarea t_51562aa7) |
| **Padre** | specs/001-platform/spec.md §4.10 (REQ-AUD-001…007), docs/arquitectura.md §5.6 (contrato NORMATIVO), specs/002-data-layer/spec.md (schema físico: cadena + inmutabilidad + `audit_verify_chain`), specs/003-control-plane/spec.md (REQ-CP-050: `audit.emit` + endpoint verify) |
| **Idioma** | Español (docs) / inglés (código, UI copy) / USD centavos (montos) |

---

## 1. Alcance

Esta spec completa la historia de audit de la plataforma sobre las primitivas ya entregadas
por las specs 002 (cadena hash en el schema, trigger de inmutabilidad, `audit_verify_chain`)
y 003 (`audit.emit`, `POST /api/admin/audit/verify`).

**Dentro de alcance**:

- **API de consulta filtrable** del audit log con alcance por rol (org_admin → tenant;
  dept_admin → eventos de sus departamentos; user → sus propios eventos), respetando RLS
  (REQ-AUD-004): filtros por usuario, departamento, tipo de evento, dispositivo, rango de
  tiempo y texto libre, con paginación.
- **Export** CSV/JSON del rango filtrado dentro del alcance del rol (REQ-AUD-005).
- **Verificador de cadena como herramienta CLI** (`verify-audit-chain`, REQ-AUD-002/007):
  un tenant o todos, con exit code utilizable por ops/cron.
- **Instrumentación de los flujos del núcleo** (spec 003): auth (login/logout/fallo,
  incl. reuso de refresh token) y jobs (job.created/job.completed). Toda ejecución de
  verificación emite `audit.verified` (la verificación queda auditada).
- **Contrato de emisión normativo** para los flujos de dominio de las specs 004–007
  (provisioning, políticas, presupuestos/spend, vault, dispositivos, conectores, grants
  de agentes): qué acción emitir, cuándo y con qué payload mínimo (REQ-AUD-003).
- **Tests de manipulación detectada** a nivel plataforma: UPDATE/DELETE de filas (vía
  superusuario de test con el trigger deshabilitado, único camino posible) → el
  verificador (API y CLI) señala la fila exacta.

**Fuera de alcance**:

- Cambios al schema de `audit_log` o a sus triggers — la cadena y la inmutabilidad son
  de la spec 002 (D-DB-07); esta spec SOLO INSERTa y llama `audit_verify_chain`.
- La emisión *dentro* de los flujos de dominio (provisioning, políticas, vault, spend,
  dispositivos, conectores, grants): la instrumenta cada spec dueña del flujo (004–007)
  conforme al contrato §5 de esta spec. Esta spec entrega el contrato y prueba la
  cadena con esos tipos de evento; el QA de convergencia (t_974c6a5c) verifica la
  matriz completa e2e.
- Retención/purga (REQ-AUD-006, prioridad S) — ver D-AU-08 (diferido con razón).
- Rate limiting de login (REQ-SEC-006) — ya diferido por spec 003 (D-CP-12).

## 2. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| D-AU-01 | **La cadena vive en el schema**: `seq`/`hash_prev`/`hash` los asigna el trigger `audit_log_before_insert` bajo advisory lock (spec 002). La plataforma jamás calcula hashes: solo INSERTa vía `audit.emit` y verifica vía `audit_verify_chain(tenant)`. Cualquier fila alterada, eliminada o insertada retroactivamente rompe la cadena — detección, no prevención criptográfica (quien puede deshabilitar triggers es dueño de la DB; el verificador lo detecta). | 002-D-DB-07, REQ-AUD-002 |
| D-AU-02 | **Alcance por rol en la misma transacción con scope de tenant**: RLS aísla el tenant (defensa en profundidad, siempre activa); el alcance por rol es un filtro adicional de aplicación: org_admin → todo el tenant; dept_admin → eventos cuyo actor pertenece a alguno de sus departamentos (`users.department_id` del actor ∈ deptos de sus asignaciones `dept_admin`); user → solo eventos con `actor_id` = su usuario. Los eventos de emisor `system`/`provider` (`actor_id` NULL) solo los ve org_admin. El permiso de entrada es `audit.view` (matriz §2.1: los 3 roles lo tienen; el scoping hace el resto). | REQ-AUD-004, spec 001 §2.1, D-CP-02 |
| D-AU-03 | **Anti-pollution en `auth.login_failed`**: el evento se emite SOLO cuando el tenant ya está verificado (firma/claims del JWT válidos, tenant existente, `iss` coincidente) y el fallo es posterior (usuario desconocido o deshabilitado). Los fallos PRE-tenant (firma rota, `aud`/`iss` incorrecto, sin claim `tenant_id`, tenant inexistente) NO se auditan en la cadena de ningún tenant: un token forjado no puede contaminar la cadena de una víctima ni crear eventos en tenants inexistentes. Límite documentado: esos fallos quedan fuera del log por diseño; su captura requiere un log global del proveedor (tabla ops cross-tenant, evolución de capa de datos). | REQ-AUD-003, constitution IV |
| D-AU-04 | **El reuso de refresh token se audita como `auth.login_failed`** con `payload={"reason":"refresh_reuse"}` y target la sesión revocada, en la misma transacción que revoca, con commit explícito antes del 401 (patrón D-CP-04: la revocación debe sobrevivir al rollback del error). No se inventan acciones nuevas de catálogo: el reuso ES un intento fallido de autenticación. | REQ-AUD-003, D-CP-04 |
| D-AU-05 | **Toda verificación emite `audit.verified`** con `payload={"result","source","broken_seq"?}` (`source`: `api` \| `cli`), en la misma transacción scoped del tenant verificado. La ejecución del verificador queda ella misma en el log (trazabilidad de quién auditó cuándo). Si la cadena está rota, el evento se emite igual (append válido sobre cadena rota: el verificador sigue señalando la fila original). | REQ-AUD-003, catálogo `audit.verified` |
| D-AU-06 | **CLI verificador**: `python -m app.audit.cli verify <tenant_id>` o `verify --all`, expuesto también como console script `verify-audit-chain`. Conexión de verificación SIEMPRE como `trust_app` con `set_config` del tenant (cada query con scope; nada cross-tenant como app). `--all` lista los tenants con la conexión `trust_migrator` (BYPASSRLS) usada SOLO para el listado ops — mismo patrón que el claim de jobs (D-CP-10) — y verifica cada cadena en su propio scope. Exit codes: 0 = todas ok, 1 = alguna rota, 2 = error de uso/conexión. Salida: una línea JSON por tenant + resumen. | arq. §5.6, REQ-AUD-007, D-CP-10 |
| D-AU-07 | **El audit es parte del negocio, no best-effort**: los flujos emiten dentro de la MISMA transacción de negocio. Si el flujo hace rollback, el evento no existe; si el INSERT de audit falla, el flujo falla. Única excepción documentada: eventos que deben sobrevivir a un 401 (`auth.login_failed`) usan commit explícito antes de raise (D-AU-04). | constitution V, I7 |
| D-AU-08 | **Retención (REQ-AUD-006, S) diferida con razón**: el trigger `audit_log_immutable_trigger` rechaza `UPDATE`/`DELETE` para TODO rol (por diseño, REQ-AUD-001), y `trust_app` no tiene grant de `DELETE`. Una purga de retención requiere un procedimiento del plano ops (función `SECURITY DEFINER` dedicada o toggle de trigger operado por el proveedor) — cambio de schema cuya verdad canónica es la spec 002. Se documenta como evolución; el mínimo invariable de 365 días no puede relajarse porque nada puede borrar hoy. | REQ-AUD-006, 002-D-DB-07 |
| D-AU-09 | **Export**: `GET /api/audit/events/export?format=csv|json` con los mismos filtros y alcance que la consulta; CSV (`text/csv`, `Content-Disposition: attachment`, columnas fijas incl. `hash_prev`/`hash`) y JSON (array). Tope de 10 000 filas por solicitud (más = paginar por rango de tiempo); el conteo exportado == total de la misma query paginada. | REQ-AUD-005 |
| D-AU-10 | **Jobs del núcleo instrumentados**: `job.created` al encolar (solo cuando crea; el hit idempotente no re-emite) y `job.completed` al completar, con `actor_role="system"` (los emite el núcleo/worker; `enqueue` acepta `actor_id` opcional para el principal que encola). Un job fallido no emite: el catálogo no define `job.failed` (el catálogo es data-extensible; evolución documentada). | REQ-AUD-003, catálogo |
| D-AU-11 | **Texto libre** (`q`): `ILIKE` sobre `action`, `target_type`, `target_id`, `actor_role` y `payload::text`, con wildcards del usuario escapados (búsqueda literal de `%`/`_`). Sin índice especial en v1 (el filtro es opcional y el log por tenant es acotado); GIN sobre `payload` es evolución de capa de datos. | REQ-AUD-004 |

## 3. Requisitos (formato EARS)

Convenciones: plantilla EARS **Cuando** `<trigger>` **el sistema** `<respuesta>`; prioridad MoSCoW; cada REQ con AC verificable con evidencia real. Trazabilidad: `[001-REQ-AUD-…]` = spec maestra §4.10, `[ARQ-…]` = arquitectura, `[002-…]`/`[003-…]` = specs hijas de datos/control plane.

### 3.1 API de consulta

**REQ-AU-001** (M) [001-REQ-AUD-004] — Cuando un principal con permiso `audit.view` consulta `GET /api/audit/events`, el sistema devuelve los eventos del tenant (scope RLS del principal) ordenados por `id` DESC con filtros opcionales `actor_id`, `action`, `target_type`, `target_id`, `device_id`, `department_id`, `since`, `until`, `q` (texto libre) y paginación `limit` (default 50, max 200) / `offset`, respondiendo `{items, total, limit, offset}` donde cada item incluye `seq`, `ts`, `actor_role`, `actor_id`, `action`, `target_type`, `target_id`, `device_id`, `cost_cents`, `payload`, `hash_prev`, `hash`.
> **AC-AU-001**: eventos sembrados con acciones/actores/timestamps distintos → cada filtro devuelve exactamente el subconjunto esperado; `limit`/`offset` paginan sobre el mismo `total`; sin filtros → todos los del alcance.

**REQ-AU-002** (M) [001-REQ-AUD-004, D-AU-02] — Cuando consultan el mismo endpoint principals con roles distintos, el sistema aplica el alcance por rol: org_admin ve todo el tenant; dept_admin solo eventos cuyo actor pertenece a sus departamentos; user solo eventos con `actor_id` propio; los eventos de emisor `system`/`provider` solo los ve org_admin; otro tenant sigue invisible (RLS).
> **AC-AU-002**: la misma query con 3 roles (org_admin, dept_admin de A2, user Carol) + un principal del tenant B devuelve exactamente los alcances esperados (test de autorización sobre audit, AC-AUD-004 de spec 001).

**REQ-AU-003** (M) [001-REQ-AUD-005, D-AU-09] — Cuando un admin exporta (`GET /api/audit/events/export?format=csv|json`), el sistema genera el rango filtrado dentro de su alcance con las columnas completas (incl. hashes) y tope de 10 000 filas.
> **AC-AU-003**: export CSV de un rango de test → número de filas == `total` de la misma query paginada; el JSON == items de la query sin paginar (≤ tope).

### 3.2 Verificación de cadena

**REQ-AU-010** (M) [001-REQ-AUD-002/007, ARQ-§5.6, D-AU-06] — Cuando un operador ejecuta el CLI (`verify-audit-chain <tenant_id>` o `verify-audit-chain --all`), el sistema verifica la cadena completa de cada tenant solicitado dentro de su scope, imprime una línea JSON por tenant (`{tenant_id, status, seq?}`) y un resumen, y termina con exit 0 (todas ok), 1 (alguna `broken`) o 2 (error de uso/conexión).
> **AC-AU-010**: CLI sobre tenant con cadena íntegra → exit 0 y `{"status": "ok"}`; tras manipular una fila (AC-AU-012) → exit 1 con el `seq` de la primera fila inconsistente; `--all` verifica todos los tenants sembrados.

**REQ-AU-011** (M) [001-REQ-AUD-007, D-AU-05] — Cuando se ejecuta una verificación (endpoint `POST /api/admin/audit/verify` o CLI), el sistema emite un evento `audit.verified` con `payload={"result", "source", "broken_seq"?}` dentro del tenant verificado, además del resultado de la verificación.
> **AC-AU-011**: tras POST verify → existe fila `audit.verified` con `source="api"` y `result="ok"`; tras CLI → `source="cli"`; la cadena sigue verificando OK (el append es válido).

**REQ-AU-012** (M) [001-REQ-AUD-001/002, AC-AUD-002] — Cuando cualquier actor manipula filas existentes de `audit_log` (UPDATE de payload, DELETE de una fila del medio), el sistema lo detecta: la verificación (API y CLI) reporta `broken` con el `seq` de la primera fila inconsistente. La manipulación solo es posible deshabilitando el trigger de inmutabilidad con privilegios de dueño de DB (el camino de la app está cerrado por grants + trigger, verificado en spec 002).
> **AC-AU-012**: tenant dedicado con 3 eventos: UPDATE del payload de la fila 2 con superusuario de test (trigger deshabilitado y re-habilitado) → API verify responde `{"status":"broken","seq":2}` y CLI termina exit 1 con el mismo seq; DELETE de la fila 2 → `broken` en la fila 3 (hueco de `seq`/`hash_prev`); intento de UPDATE/DELETE como `trust_app` → error de permiso de PostgreSQL (sin trigger necesario).

### 3.3 Instrumentación del núcleo (auth + jobs)

**REQ-AU-020** (M) [001-REQ-AUD-003, D-AU-03] — Cuando una sesión se minta (`POST /api/auth/session` exitoso), el sistema emite `auth.login` con actor (usuario + rol de mayor precedencia), target la sesión, y `payload={"kind","mfa_verified","via"}`; cuando el JWT es válido pero el usuario no existe o está deshabilitado, emite `auth.login_failed` con `payload={"reason"}` y commit explícito antes del 401; los fallos pre-tenant NO se auditan (D-AU-03).
> **AC-AU-020**: mint exitoso → fila `auth.login` con `actor_id` del usuario y `target_id` = session id; JWT válido con `sub` desconocido → 401 Y fila `auth.login_failed` `reason="unknown_user"`; usuario disabled → `reason="user_disabled"`; JWT con firma rota → 401 y CERO filas nuevas de audit.

**REQ-AU-021** (M) [001-REQ-AUD-003] — Cuando una sesión se revoca por logout (`POST /api/auth/logout`), el sistema emite `auth.logout` con actor el principal y target la sesión revocada.
> **AC-AU-021**: logout → fila `auth.logout` con `actor_id` del principal y `target_id` = session id.

**REQ-AU-022** (M) [001-REQ-AUD-003, D-AU-04] — Cuando un refresh token consumido se re-canjea (replay), el sistema emite `auth.login_failed` con `payload={"reason":"refresh_reuse"}` y target la sesión, en la transacción que revoca la sesión, con commit explícito antes del 401.
> **AC-AU-022**: refresh #1 → 200; replay → 401, sesión `revoked` Y fila `auth.login_failed` `reason="refresh_reuse"` persistida (sobrevive al rollback del 401).

**REQ-AU-023** (M) [001-REQ-AUD-003, D-AU-10] — Cuando el núcleo encola un job nuevo, el sistema emite `job.created` (target el job, `payload={"job_type","idempotency_key"?}`); cuando el worker lo completa, emite `job.completed` (target el job). El hit idempotente de `enqueue` no re-emite.
> **AC-AU-023**: enqueue nuevo → fila `job.created`; segunda llamada con la misma `idempotency_key` → sin fila nueva; worker procesa el job → fila `job.completed`.

### 3.4 Contrato de emisión para flujos de dominio (normativo para specs 004–007)

**REQ-AU-030** (M) [001-REQ-AUD-003, D-AU-07] — Cuando un flujo de dominio ejecuta una operación de la lista de REQ-AUD-003, el spec dueña del flujo emite el evento correspondiente vía `audit.emit` DENTRO de la misma transacción de negocio, con la acción del catálogo `catalog.audit_actions` y el payload mínimo del contrato §5. La matriz de cobertura completa (cada tipo de evento producido por su flujo real) se verifica en el converge de QA (t_974c6a5c).
> **AC-AU-030**: por cada grupo de acciones del contrato §5 existe un test en la spec dueña que produce el evento desde el flujo real y afirma su presencia con actor/tenant/acción/target; esta spec aporta el test de cadena para acciones representativas de cada grupo (ver AC-AU-031).

**REQ-AU-031** (M) [001-REQ-AUD-003] — Cuando se emiten eventos de acciones de dominio (provisioning `tenant.provisioned`, política `policy.updated`, vault `vault.credential_used`, presupuesto `budget.cutoff`, dispositivo `device.enrolled`/`device.revoked`), el sistema los encadena correctamente (FK de catálogo válida, `seq` consecutivo, verificación OK) incluyendo `cost_cents` y `device_id` cuando aplican.
> **AC-AU-031**: emisión de las acciones representativas de cada grupo con `cost_cents`/`device_id` poblados → todas persisten con `seq` consecutivos y `audit_verify_chain` → OK (la instrumentación de los flujos que las producen es de las specs 004–007, REQ-AU-030).

### 3.5 Diferido

**REQ-AU-040** (S, diferido) [001-REQ-AUD-006, D-AU-08] — Retención/purga del audit log: diferido a evolución de la capa de datos (spec 002). Razón: la inmutabilidad (REQ-AUD-001) prohíbe `DELETE` a todo rol por diseño; la purga requiere un procedimiento del plano ops aún no especificado. El mínimo invariable de 365 días no puede relajarse mientras no exista vía de borrado.
> **AC-AU-040**: N/A (diferido documentado; sin código que lo simule).

## 4. Contrato de superficie v1 (rutas nuevas)

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/api/audit/events` | Bearer/cookie + MFA + `audit.view` | Consulta filtrable con alcance por rol (REQ-AU-001/002) |
| GET | `/api/audit/events/export` | Bearer/cookie + MFA + `audit.view` | Export CSV/JSON del rango filtrado (REQ-AU-003) |
| POST | `/api/admin/audit/verify` | org_admin | Verificación de cadena (spec 003) + emisión de `audit.verified` (REQ-AU-011) |
| CLI | `verify-audit-chain <tenant_id> \| --all` | ops (DB directa) | Verificador de cadena con exit codes (REQ-AU-010) |

## 5. Contrato de emisión de eventos (normativo, specs 004–007)

Acciones del catálogo `catalog.audit_actions` (spec 002 §3.5). `emit()` SIEMPRE en la transacción del negocio (D-AU-07). Columnas: `actor_role`/`actor_id` = quién actuó; `target_type`/`target_id` = sobre qué; `device_id`/`cost_cents` cuando aplican; `payload` = detalle NO secreto (jamás secretos, tokens ni keys — constitution IV).

| Flujo (spec dueña) | Acción | target | payload mínimo |
|---|---|---|---|
| **Auth (003/008 — ya instrumentado)** | `auth.login` | `session:<id>` | `{kind, mfa_verified, via}` |
| | `auth.login_failed` | `session:<id>`? | `{reason: unknown_user\|user_disabled\|refresh_reuse}` |
| | `auth.logout` | `session:<id>` | `{}` |
| **Provisioning (004)** | `tenant.provisioned` | `tenant:<id>` | `{slug, plan, idempotency_key}`, actor `provider` |
| | `tenant.suspended` / `tenant.reactivated` | `tenant:<id>` | `{reason?}`, actor `provider` |
| | `user.invited` / `user.created` / `user.updated` / `user.disabled` | `user:<id>` | `{email, department_id?}` |
| | `role.assigned` / `role.revoked` | `user:<id>` | `{role_key, department_id?}` |
| | `department.created` | `department:<id>` | `{name}` |
| **Gobernanza (005)** | `policy.updated` | `policy:<key>` | `{level, department_id?, user_id?, version}` |
| | `budget.updated` | `budget:<id>` | `{level, amount_cents, period}` |
| | `budget.forced_below_spend` | `budget:<id>` | `{amount_cents, spend_cents}` |
| | `budget.cutoff` | `budget:<id>` | `{level, user_id?, department_id?, spend_cents}` |
| | `model.approved` / `model.revoked` | `model_access:<id>` | `{model_id, level, mode}` |
| | `model.access_denied` | `model:<id>` | `{user_id, reason}` |
| | `model.consumed` | `model:<id>` | `{user_id, cost_cents, …}` + `cost_cents` + `device_id` |
| | grants de agentes (acción a sembrar en catálogo por 005) | `agent:<id>` | `{grantee_user_id, grant_mode}` |
| **Vault/Conectores (006)** | `vault.credential_created` / `_rotated` / `_revoked` | `credential:<id>` | `{connector_slug}` — jamás el secreto |
| | `vault.credential_used` | `credential:<id>` | `{agent?, connector_slug}` |
| | `connector.connected` / `connector.disconnected` | `connector_instance:<id>` | `{connector_slug, mode}` |
| **Dispositivos (007)** | `device.enrolled` / `device.approved` / `device.rejected` / `device.revoked` | `device:<id>` | `{fingerprint, user_id}` (+ `outcome`) + `device_id` |
| **Jobs (003/008 — ya instrumentado)** | `job.created` / `job.completed` | `job:<id>` | `{job_type, idempotency_key?}` |
| **Audit (008)** | `audit.verified` | — | `{result, source, broken_seq?}` |

Acciones nuevas (p. ej. grants de agentes): se siembran como datos en `catalog.audit_actions` (tabla data-extensible, spec 002 §3.5) — nunca bypass del FK.

## 6. Checklist de convergencia

- [x] AC-AU-001…031 verificados con evidencia real (tests sobre PostgreSQL 17 real).
      Evidencia (2026-10-23): `tests/test_audit_query.py` (AC-AU-001/002/003/011/031) y
      `tests/test_audit_cli.py` (AC-AU-010/011/012) + `tests/test_audit_auth_events.py`
      (AC-AU-020/021/022/023) — 18/18 en verde.
- [x] Suite `platform/tests/` en verde (nuevos tests de audit + suite existente sin
      regresiones). Evidencia: 134 passed al 2026-10-23 16:35; los únicos fallos
      pendientes son de `tests/test_devices_flow.py` (WIP de la spec 007, lane ajena).
- [x] Manipulación detectada por API y CLI (AC-AU-012) con evidencia:
      `test_tamper_update_detected` (UPDATE fila 2 → `broken seq 2`, API + CLI exit 1),
      `test_tamper_delete_detected` (DELETE fila 2 → `broken seq 3`),
      `test_app_role_cannot_mutate` (UPDATE/DELETE como `trust_app` → permission denied).
- [x] CLI con exit codes verificados en proceso real (subprocess):
      `test_cli_verify_ok_and_audited` (exit 0 + `audit.verified` source=cli),
      `test_cli_verify_all` (`--all` vía listado migrator, exit 0).
- [x] Aislamiento: `test_role_scopes` — org_admin/dept_admin/user/otro tenant devuelven
      exactamente los alcances esperados (RLS + scope de rol, AC-AU-002).
- [x] Trazabilidad REQ↔AC: cada test nombra su AC en el docstring.
- [x] tasks.md cerrado; desviaciones: ninguna semántica. (La implementación usó
      `Response` en lugar de `StreamingResponse` para el CSV — equivalente, el tope
      de 10k filas hace innecesario el streaming.)

## Evolución spec 013 — REQ-AUD-006 implementado (oct-2026)

Detalle y ACs en `specs/013-spec001-gaps/spec.md` (D-013-04): retención `tenants.retention_days` expuesta como `GET/PUT /api/admin/audit/retention` (piso 365 invariable → 422, audit `audit.retention_updated` sembrada en catálogo); job de purga `python -m app.audit.cli purge [--tenant|--all]` (rol trust_migrator, flag transaccional `app.audit_purge`) que borra vencidos, escribe `audit_chain_checkpoint` (continuidad de cadena: verificación desde checkpoint e INSERT continúa tras purga total) y registra `audit.purged` con conteo. Schema §11 + migración `0006_audit_retention` (paridad pg_dump verificada). La app sigue siendo INSERT-only en `audit_log` y SELECT-only en el checkpoint.
