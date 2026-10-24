# Spec 004 — Onboarding de tenant idiom-proof (aprovisionamiento por el proveedor)

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-23 |
| **Autor** | dev (tarea t_8b113637) |
| **Padre** | specs/001-platform/spec.md §4.1 (REQ-ONB), docs/arquitectura.md §2.4/§3.1, specs/003-control-plane/spec.md (núcleo), specs/002-data-layer/spec.md (schema físico) |
| **Idioma** | Español (docs) / inglés (código, UI copy) / USD centavos (montos) |

---

## 1. Alcance

Esta spec define el **aprovisionamiento completo de un nuevo cliente (tenant) por el proveedor**: una única operación orquestada, reproducible e "idiom-proof" (REQ-ONB-008: cero intervención manual en servicios), expuesta como **CLI** (`trust-provision`) y como **endpoint admin** (`POST /api/admin/tenants`), sobre el núcleo de la spec 003.

**Dentro de alcance**:

- **CLI `trust-provision`** (subcomandos `provision` y `verify`) — entry point de consola del operador del proveedor; invoca la misma capa de servicio que el endpoint.
- **Endpoint admin** `POST /api/admin/tenants` (+ `GET /api/admin/tenants/{slug}` y `GET /api/admin/tenants/{slug}/healthcheck`) con autenticación de proveedor.
- Creación completa del tenant en una operación orquestada:
  - fila `tenants` (org) con realm/issuer OIDC propios y branding (default del proveedor si no se provee, REQ-ONB-007);
  - **realm Keycloak** por tenant: realm + cliente público `trust-agent-web` (PKCE S256, `directAccessGrantsEnabled=false`) + mapper `tenant_id` + `unmanagedAttributePolicy=ENABLED` (hallazgo crítico Keycloak 26, spec 003);
  - **admin inicial** `org_admin`: usuario Keycloak con password **temporal** (one-time) + `requiredActions=["CONFIGURE_TOTP"]` y su fila `users`/`user_roles` en DB;
  - **presupuesto de organización** mensual en USD centavos (REQ-ONB-006);
  - **políticas default**: las claves `invariable=true` del catálogo con su `default_value` (REQ-ONB-005), leídas de datos;
  - **departamentos seed** con perfiles Hermes pre-diseñados (plantillas REQ-PRF-001: IT Operations, Cloud Operations, Finance, Customer Operations) — `departments.profile_template`;
  - **catálogo de modelos aprobado inicial** según `model_plan` (filas `model_access` nivel org, modo `central`).
- **Rollback transaccional total**: la parte de DB es UNA transacción atómica como `trust_app`; la parte Keycloak es compensable (DELETE del realm); un fallo a mitad no deja recursos huérfanos (REQ-ONB-003).
- **Idempotencia**: re-ejecutar con la misma identidad (slug) devuelve el estado del tenant existente sin duplicados (REQ-ONB-002).
- **Healthcheck post-alta automatizado**: verificación post-commit del tenant nuevo (DB + IdP + gobernanza), incluida en la respuesta y expuesta como endpoint/CLI.
- **Audit**: eventos `tenant.provisioned` (+ `department.created`, `user.created`, `role.assigned`, `budget.updated`, `policy.updated`, `model.approved`) con actor `provider` (REQ-ONB-009).
- Tests e2e del flujo completo contra PostgreSQL 17 y Keycloak 26 reales.

**Fuera de alcance** (documentado):

- Provisioning de **teams/keys LiteLLM** (arq. §2.4 paso 7): LiteLLM no corre aún en local; la integración completa (virtual keys, teams por depto, pricing sync) es de **spec 005-governance** (t_3b4fb364). El `model_plan` deja las aprobaciones en DB listas para que spec 005 las materialice en LiteLLM.
- **Envío de email** del enlace de activación (REQ-ONB-004 "envía al admin_email"): local-first sin SMTP; la entrega one-time se hace en la respuesta del API/CLI al operador (decisión D-ONB-05). El mecanismo de email es infraestructura de despliegue.
- **Suspensión/reactivación** de tenants (REQ-TEN-006): operación provider simple que acompana a presupuestos/corte (spec 005); aquí solo se documenta el estado `suspended` ya soportado por el núcleo (403 `tenant_suspended`).
- **Instalación de perfiles Hermes** en máquinas de usuario (`hermes profile install`): spec de integración Hermes (REQ-PRF-002); aquí se entregan las plantillas versionadas y su referencia por departamento.

## 2. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| D-ONB-01 | **CLI `trust-provision`** (no `harper-provision`): el placeholder "Harper" quedó retirado por DECISIONES.md ("nombre del producto: Trust Agent"). El cuerpo de la tarea usaba el placeholder antiguo; se adopta el nombre canónico del producto. | DECISIONES.md, constitution VI |
| D-ONB-02 | **Auth de proveedor = token de portador desde env** (`TRUST_PROVIDER_TOKEN`, comparación constant-time). `platform_admin` NO es un rol de tenant (CHECK del schema en `user_roles` lo excluye) y `sessions.tenant_id` es NOT NULL, así que ninguna sesión de usuario puede portar `tenants.provision`. El principal provider carga sus permisos **data-driven** desde `catalog.role_permissions` (rol `platform_admin`) y las rutas verifican `tenants.provision` contra esa matriz — sin constantes de permiso en código. Sin el env configurado → 503 (fail-closed). | spec 001 §2.1, schema 002 §5.4, I10 |
| D-ONB-03 | **Orden de pasos: Keycloak primero (compensable), DB al final (atómica)**. La DB se escribe en UNA transacción como `trust_app` con `tenant_id` pre-generado (UUID del cliente) + `set_config('app.tenant_id', ...)` como primera sentencia — el INSERT en `tenants` pasa el `WITH CHECK` de RLS porque `tenant_id` es `GENERATED ALWAYS AS (id)` y el id explícito coincide con el setting. Un fallo en cualquier paso de DB revierte TODO (nada commit-eado) y ejecuta la compensación `DELETE /admin/realms/{realm}`. DB-primero es inviable: tras el commit, `audit_log` (append-only, FK) haría imposible borrar el tenant. | D-CP-02, REQ-ONB-003, schema 002 §6.4 |
| D-ONB-04 | **Idempotencia por slug**: el slug del tenant (derivado deterministicamente de `organization_name` si no se pasa explícito, y alias del `idempotency_key` de spec 001) es la identidad idempotente. Re-ejecución con el mismo slug → `200` con el estado existente (`created=false`), sin duplicados y **sin re-entregar credenciales** (el password one-time no es recuperable — Keycloak solo guarda el hash). | REQ-ONB-002, C1 |
| D-ONB-05 | **Credenciales one-time del admin inicial**: Keycloak crea el usuario con password **temporal** (obliga a cambiarlo en el primer login) + `requiredActions=["CONFIGURE_TOTP"]` (obliga a enrolar MFA TOTP). El password temporal se genera con `secrets` (no derivable), se devuelve **exactamente una vez** en la respuesta 201 (API/CLI) y jamás se persiste en la DB. Esto implementa REQ-ONB-004 en local: primer uso → cambio de password obligatorio (el password one-time queda invalidado); sin enrolar TOTP no hay sesión utilizable (MfaGate del núcleo). | REQ-ONB-004, REQ-SEC-003, C1 |
| D-ONB-06 | **Plantillas de perfil como datos versionados en el paquete reservado `app/profiles/`** (4 plantillas REQ-PRF-001 con skills, conectores sugeridos, acciones rápidas, políticas base y presupuesto sugerido). Una tabla `catalog.profile_templates` sería evolución del schema canónico (dueño: spec 002) — fuera de alcance aquí; `departments.profile_template` guarda la clave de la plantilla. Default de departamentos seed = las 4 plantillas estándar; el request puede restringirlas. | REQ-PRF-001, arq. §3.1 (paquete reservado), D-DB-12 |
| D-ONB-07 | **Políticas default data-driven**: al aprovisionar se leen del catálogo las claves `invariable=true` con su `default_value` y se insertan como políticas org activas — cero constantes de política en código (las 4 de REQ-ONB-005 son exactamente las invariables del catálogo). | REQ-ONB-005, I10 |
| D-ONB-08 | **`model_plan`**: `'standard'` (todos los modelos `available` del catálogo global) o lista explícita de `model_name`. La resolución nombre→id ocurre DENTRO de la transacción de DB: un nombre inexistente falla a mitad (422) → rollback total + compensación del realm (ejercicio real del camino de rollback). Modo inicial `central` (D4). | REQ-ONB-001, REQ-MDL-002 |
| D-ONB-09 | **Audit de rollback — desviación documentada**: AC-ONB-003 pide "eventos de rollback en el audit log", pero el rollback total exige 0 filas del tenant (incluida `audit_log`, que además es append-only con FK a `tenants`): los eventos de rollback por-tenant son físicamente imposibles sin violar el requisito de 0 huérfanos. Implementación: el fallo se devuelve en el envelope con `reason=provisioning_failed`, el `message` nombra el paso exacto que falló y las compensaciones ejecutadas, y el orquestador lo registra en su log estructurado. El audit del tenant registra el evento `tenant.provisioned` con payload de pasos SOLO en el camino exitoso. Desviación de spec 001 con razón explícita (constitution II). | REQ-ONB-003, constitution II |
| D-ONB-10 | **Lecturas cross-tenant del proveedor vía `trust_migrator`** (BYPASSRLS) SOLO para el lookup por slug (RLS oculta `tenants` a `trust_app` sin scope); toda verificación/healthcheck posterior corre como `trust_app` con scope del tenant — mismo patrón que el claim de jobs (D-CP-10). | D-CP-10, D-DB-02 |
| D-ONB-11 | **Healthcheck post-commit sin rollback**: el healthcheck corre tras el commit y es de solo-lectura; si falla, se reporta (la data está consistente; el fallo es del entorno) — no existe camino de borrado post-commit (audit append-only). En el camino feliz debe dar `ok`. | REQ-ONB-001 AC |
| D-ONB-12 | **Keycloak 26**: todo realm creado lleva `unmanagedAttributePolicy=ENABLED` (sin ello el atributo `tenant_id` del usuario se descarta en silencio y el mapper no emite el claim — hallazgo crítico verificado en spec 003, skill `identity/keycloak-admin-oidc`). | skill keycloak-admin-oidc, ADR-015 |
| D-ONB-13 | **Bootstrap Admin REST**: el cliente Admin de Keycloak obtiene su token con `admin-cli` + `grant_type=password` contra el realm `master` — la única concesión ROPC-like del sistema, interna del proveedor, documentada en ADR-015 y fuera del API de usuarios finales. Credenciales desde env (`TRUST_KEYCLOAK_ADMIN_USER/PASSWORD`); sin password → error explícito (fail-closed). | ADR-015, REQ-CP-026 |

## 3. Requisitos (formato EARS)

Convenciones: plantilla EARS **Cuando** `<trigger>` **el sistema** `<respuesta>`; prioridad MoSCoW; cada REQ con AC verificable con evidencia real. Trazabilidad: `[001-REQ-…]` = spec maestra, `[ARQ-…]` = arquitectura, `[003-…]` = spec control plane.

### 3.1 Operación de aprovisionamiento

**REQ-ONB-001** (M) [001-REQ-ONB-001, ARQ-§2.4] — Cuando el proveedor ejecuta el aprovisionamiento con los datos mínimos (`organization_name`, `admin_email`, `initial_budget_usd`, `model_plan`, `branding` opcional, `departments` opcional, `slug`/`idempotency_key` opcional), el sistema crea en una única operación orquestada: el tenant con realm/issuer OIDC propios, el usuario `org_admin` inicial (Keycloak + DB), el branding (default del proveedor si no se provee), el presupuesto mensual de organización, las políticas default, los departamentos seed con sus plantillas de perfil y el catálogo de modelos aprobado según `model_plan` — y responde `201` con `tenant_id`, estado y credenciales one-time del admin.
> **AC-ONB-001**: e2e real (Keycloak 26 + PostgreSQL 17): `POST /api/admin/tenants` → 201; verificación SQL: 1 tenant, 4 departamentos con `profile_template`, 1 user `org_admin`, 1 presupuesto org con `amount_cents` correcto, 4 políticas org activas, filas `model_access` aprobadas; el realm existe (discovery 200) y el usuario admin existe en el realm con `requiredActions=["CONFIGURE_TOTP"]`.

**REQ-ONB-002** (M) [001-REQ-ONB-002, D-ONB-04] — Cuando el proveedor re-ejecuta el aprovisionamiento con el mismo slug/`idempotency_key`, el sistema devuelve el estado del tenant existente (`200`, `created=false`) sin crear duplicados ni re-entregar credenciales.
> **AC-ONB-002**: dos ejecuciones con el mismo slug → exactamente 1 tenant, 1 org_admin, 1 presupuesto (conteo SQL); la segunda respuesta no contiene `one_time_activation`.

**REQ-ONB-003** (M) [001-REQ-ONB-003, D-ONB-03/09] — Cuando un paso del aprovisionamiento falla, el sistema revierte todo lo ejecutado (transacción DB atómica + compensación del realm Keycloak en orden inverso), no deja recursos huérfanos y devuelve un error que nombra el paso exacto que falló y las compensaciones ejecutadas.
> **AC-ONB-003**: inyección de fallo real (`model_plan` con un `model_name` inexistente → FK/422 a mitad de la transacción DB, tras crear el realm) → conteo SQL de tenants/users/budgets/departments igual al estado inicial, `GET /admin/realms/{realm}` → 404 (realm compensado), y el envelope de error nombra el paso fallido. (Desviación del audit de rollback: D-ONB-09.)

**REQ-ONB-004** (M) [001-REQ-ONB-004, D-ONB-05] — Cuando el aprovisionamiento termina, el sistema entrega credenciales **one-time** del admin inicial (password temporal + URL de activación del realm) exactamente una vez; el primer login obliga a establecer contraseña nueva y enrolar MFA (TOTP).
> **AC-ONB-004**: la respuesta 201 contiene `one_time_activation` (password temporal, issuer, client_id); el usuario Keycloak queda con password temporal y `requiredActions=["CONFIGURE_TOTP"]` (verificado por Admin REST); la re-ejecución idempotente no lo devuelve; el password temporal no aparece en ninguna tabla de la DB (búsqueda = 0).

**REQ-ONB-005** (M) [001-REQ-ONB-005, D-ONB-07] — Cuando se crea un tenant, el sistema aplica las políticas default: las claves `invariable=true` del catálogo (`require_mfa`, `approved_models_only`, `explicit_shared_agent_grants`, `scoped_credentials`) con su `default_value`, como políticas org activas.
> **AC-ONB-005**: SQL del tenant nuevo: 4 filas en `policies` nivel org, activas, con los valores default del catálogo (leídos de `catalog.policy_keys`, no constantes del test).

**REQ-ONB-006** (M) [001-REQ-ONB-006] — Cuando el proveedor define `initial_budget_usd`, el sistema crea el presupuesto mensual de organización con `amount_cents = initial_budget_usd × 100`, `period='monthly'`, `level='org'`.
> **AC-ONB-006**: la fila `budgets` del tenant tiene `amount_cents` exacto y `period='monthly'`.

**REQ-ONB-007** (M) [001-REQ-ONB-007] — Cuando no se provee `branding`, el sistema aplica el branding default del proveedor (Trust Agent: nombre, colores); el branding provisto se fusiona encima del default.
> **AC-ONB-007**: sin branding → `tenants.branding` contiene el default (`product_name="Trust Agent"`); con branding parcial → el default persiste para las claves no provistas.

**REQ-ONB-008** (M) [001-REQ-ONB-008] — Cuando el proveedor aprovisiona, el sistema NO requiere intervención manual en servicios: todo paso es API (Admin REST de Keycloak, SQL) o script versionado en el repositorio; el runbook completo es un único comando CLI con exit 0.
> **AC-ONB-008**: `trust-provision provision …` (subproceso real) termina exit 0 y deja un tenant verificable (SQL + healthcheck `ok`); `trust-provision verify <slug>` → exit 0 con todos los checks en `ok`.

**REQ-ONB-009** (M) [001-REQ-ONB-009] — Cuando el proveedor aprovisiona, el sistema registra en el audit log del tenant los eventos con actor `provider`: `tenant.provisioned` (payload con pasos y parámetros) más `department.created`/`user.created`/`role.assigned`/`budget.updated`/`policy.updated`/`model.approved` por recurso creado.
> **AC-ONB-009**: el audit log del tenant nuevo contiene `tenant.provisioned` con `actor_role='provider'` y los eventos por recurso; la cadena verifica `ok` (`audit_verify_chain`).

### 3.2 Perfiles y departamentos seed

**REQ-ONB-020** (M) [001-REQ-PRF-001, D-ONB-06] — Cuando el proveedor publica las plantillas de perfil, el sistema ofrece mínimo 4 pre-diseñadas — **IT Operations**, **Cloud Operations**, **Finance**, **Customer Operations** — cada una declarando: skills incluidas, conectores sugeridos, acciones rápidas, políticas base sugeridas y presupuesto mensual sugerido (USD).
> **AC-ONB-020**: el registro de plantillas expone las 4 con todos los campos declarados y claves únicas; test unitario contra el registro.

**REQ-ONB-021** (M) [001-REQ-ONB-001, D-ONB-06] — Cuando el proveedor no especifica `departments`, el sistema siembra un departamento por cada plantilla estándar con `profile_template` poblado; si especifica, siembra exactamente esas (validadas contra el registro).
> **AC-ONB-021**: default → 4 departamentos con las claves de plantilla correctas; `departments=["finance"]` → 1 departamento Finance; clave inválida → 422 antes de tocar servicios.

### 3.3 Superficie admin del proveedor

**REQ-ONB-030** (M) [001-REQ-ONB-001, D-ONB-02] — Cuando una ruta `/api/admin/tenants*` recibe una request, el sistema exige el token de proveedor (`Authorization: Bearer` contra `TRUST_PROVIDER_TOKEN`, constant-time) y el permiso `tenants.provision` de la matriz del catálogo (rol `platform_admin`); sin token configurado → 503; token ausente/incorrecto → 401 `invalid_token`; permiso ausente de la matriz → 403 `permission_denied`.
> **AC-ONB-030**: sin `Authorization` → 401; token incorrecto → 401; token correcto → pasa; con la fila `platform_admin→tenants.provision` borrada del catálogo → 403 (data-driven).

**REQ-ONB-031** (M) [D-ONB-10] — Cuando el proveedor consulta el estado de un tenant (`GET /api/admin/tenants/{slug}`), el sistema resuelve el slug vía conexión `trust_migrator` (cross-tenant) y devuelve el estado leído como `trust_app` con scope del tenant; slug inexistente → 404.
> **AC-ONB-031**: GET del tenant recién aprovisionado → 200 con campos canónicos; slug aleatorio → 404.

**REQ-ONB-032** (M) [D-ONB-11] — Cuando el aprovisionamiento termina (o el proveedor lo pide), el sistema ejecuta el **healthcheck** del tenant: fila tenant activa, discovery + JWKS del realm alcanzables, usuario admin presente/habilitado en el realm, presupuesto org presente, políticas default activas, departamentos seed presentes, `model_access` aprobado, cadena de audit íntegra — y devuelve `{status, checks[]}`.
> **AC-ONB-032**: tras el alta, `GET /api/admin/tenants/{slug}/healthcheck` → 200 con `status=ok` y todos los checks `ok`; el CLI `verify` sale 0.

### 3.4 Aislamiento y no-regresión

**REQ-ONB-040** (M) [constitution III, 001-REQ-TEN-002] — Cuando el aprovisionamiento crea un tenant, el sistema mantiene el aislamiento: ningún principal de otro tenant ve sus recursos (RLS + scope por request del núcleo), y el nuevo admin no obtiene sesión sin pasar por el flujo OIDC del realm propio.
> **AC-ONB-040**: sesión del tenant A (seed) → referencia a IDs del tenant nuevo → 404; el tenant nuevo no es visible en ninguna ruta del tenant A.

**REQ-ONB-041** (M) [003-REQ-CP-026, D-ONB-13] — Cuando el aprovisionamiento maneja credenciales, el sistema jamás transporta passwords de usuarios finales por el API de negocio: el único password grant es `admin-cli` del bootstrap Admin REST (interno del proveedor); el password one-time del admin se entrega una sola vez y no se persiste en DB ni logs.
> **AC-ONB-041**: `grep -r "grant_type=password" platform/app/` → solo el bootstrap Admin REST documentado; búsqueda del valor del password one-time en todas las tablas del tenant → 0.

## 4. Contrato de superficie v1

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| POST | `/api/admin/tenants` | Provider token + `tenants.provision` | Aprovisiona un tenant completo (201 creado / 200 idempotente) |
| GET | `/api/admin/tenants/{slug}` | Provider token + `tenants.provision` | Estado del tenant (lookup cross-tenant vía migrator) |
| GET | `/api/admin/tenants/{slug}/healthcheck` | Provider token + `tenants.provision` | Healthcheck post-alta del tenant |
| CLI | `trust-provision provision --org --admin-email --budget-usd [--model-plan] [--slug] [--branding] [--departments]` | env (operador local) | Aprovisionamiento desde consola (misma capa de servicio) |
| CLI | `trust-provision verify <slug>` | env | Healthcheck del tenant desde consola |

Request de provisioning (JSON):
```json
{
  "organization_name": "Acme Corp",
  "admin_email": "admin@acme.com",
  "initial_budget_usd": 500.00,
  "model_plan": "standard" | ["qwen2.5-72b-instruct", "deepseek-v3"],
  "branding": {"product_name": "Acme AI"},      // opcional, se fusiona sobre el default
  "departments": ["it_operations", "cloud_operations"],  // opcional, default: las 4
  "slug": "acme-corp",                          // opcional = idempotency_key
  "admin_display_name": "Acme Admin"            // opcional
}
```

Respuesta 201 (extracto):
```json
{
  "created": true,
  "tenant": {"id", "slug", "name", "status", "oidc_realm", "oidc_issuer_url", "branding"},
  "admin": {"email", "display_name", "department_id"},
  "one_time_activation": {"issuer_url", "client_id", "username", "one_time_password", "instructions"},
  "provisioned": {"departments": [...], "models": [...], "budget_cents": 50000, "policies": [...]},
  "healthcheck": {"status": "ok", "checks": [{"name", "status", "detail"}]}
}
```

## 5. Checklist de convergencia

- [x] AC-ONB-001…041 verificados con evidencia real (tests e2e contra PostgreSQL 17 + Keycloak 26 reales, CLI como subproceso real, proceso uvicorn live + curl).
- [x] Suite `platform/tests/` en verde: **149 passed** (2026-10-23) — núcleo 003 sin regresión + suites hermanas en vuelo + 13 tests nuevos de spec 004.
- [x] Rollback verificado con inyección de fallo real (`model_plan` con modelo inexistente → 422 a mitad de la transacción DB, tras crear el realm): conteos SQL idénticos al estado inicial (0 huérfanos) + `GET /admin/realms/{realm}` → 404 (compensado) + el envelope nombra el paso (`db.validation_failed`) y la compensación (`realm '…' deleted`). Verificado por API y por CLI.
- [x] Idempotencia verificada por conteo SQL (2 ejecuciones → 1 tenant, 1 admin, 1 presupuesto; la 2ª respuesta no trae credenciales).
- [x] Password one-time: entregado 1 vez, ausente de la DB (búsqueda en tenants/users/audit_log/policies = 0); el login real con él NO entrega code — Keycloak redirige a required-action (UPDATE_PASSWORD + CONFIGURE_TOTP), verificado con el baile PKCE real en la corrida live.
- [x] `grep` de seguridad: `verify=False` en `platform/app/` → 0; `grant_type=password` SOLO en el bootstrap Admin REST documentado (`app/tenants/keycloak_admin.py`, D-ONB-13/ADR-015).
- [x] Trazabilidad REQ↔AC: cada REQ-ONB tiene sus AC referenciados y cada test de `tests/test_provision.py` / `tests/test_profiles_templates.py` nombra su AC en el docstring/comentario.
- [x] tasks.md cerrado; desviaciones documentadas: D-ONB-09 (audit de rollback imposible por diseño del schema — razón explícita), D-ONB-01 (CLI `trust-provision`, no `harper-provision` — placeholder retirado por DECISIONES.md).
- [x] Suite `db/` intacta tras los cambios: **171 passed** (ningún cambio en db/; paridad schema↔Alembic sin tocar).

Evidencia de la corrida de convergencia (2026-10-23):

- `pytest tests/ -q` (platform) → **149 passed**: flujo completo de
  provisioning (201 + conteos SQL exactos + realm real + admin KC con
  `requiredActions=[CONFIGURE_TOTP, UPDATE_PASSWORD]` + audit con
  `tenant.provisioned` y eventos por recurso + cadena íntegra), idempotencia
  (200/created=false, sin duplicados, sin credenciales), rollback total con
  realm compensado, matriz de auth provider (401/403 data-driven/503
  fail-closed), estado + healthcheck (10 checks ok), aislamiento (job del
  tenant nuevo → 404 como tenant A), higiene de secretos, CLI real
  (`trust-provision provision` exit 0 + `verify` exit 0 + fallo con rollback
  exit 1), y 4 tests del registro de plantillas.
- Verificación live (proceso real, no TestClient): `uvicorn app.main:app` en
  127.0.0.1:8000 contra DB de dev creada con el proyecto Alembic canónico →
  `POST /api/admin/tenants` con token provider → created=true + healthcheck
  `ok` (10 checks); re-ejecución → HTTP 200 `created=false`;
  `GET /api/admin/tenants/live-corp` y `/healthcheck` → 200; sin token y con
  token incorrecto → 401; baile PKCE real con el password one-time →
  **sin `code=`** en la redirección, `login-actions/required-action`
  (obligación de cambio de contraseña + TOTP en el primer login).
- `pytest tests/ -q` (db) → **171 passed**.
- Limpieza post-corrida: realms de test eliminados del Keycloak local
  (fixture de cleanup + borrado manual verificado), DBs efímeras dropeadas.
