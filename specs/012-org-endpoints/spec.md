# Spec 012 — Endpoints de organización: directorio users, branding, grants-list y departments CRUD

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-23 |
| **Autor** | dev (tarea t_f72dc691, creada por el lane Admin Console t_5ce74df8) |
| **Padre** | specs/001-platform/spec.md §4 (REQ-ORG-001…005, REQ-BRD-001…005, AC-ORG-002, AC-ONB-007, AC-BRD-005), specs/003-control-plane/spec.md (base transversal: sesiones, RLS, jobs), specs/005-governance/spec.md (agentes compartidos), specs/008-audit/spec.md §5 (contrato de eventos), specs/009-admin-console/spec.md §4.2 D-AC-06 (contratos pendientes) |
| **Idioma** | Español (docs) / inglés (código, UI copy) |

---

## 1. Alcance

Esta spec implementa los **contratos que el Admin Console (spec 009) consume con
degradación honesta (D-AC-06)** y que no existían en el control plane. El console
ya tiene el cliente tipado y la UI completas contra estos shapes
(`platform/console/src/lib/types.ts` §"Contratos PENDIENTES"); en cuanto estos
endpoints existen, las páginas Users & Roles y Branding se activan **sin cambios
de frontend**.

**Dentro de alcance**:

1. `GET /api/users` — directorio de usuarios del tenant con scope por rol
   (org_admin todo; dept_admin sus departamentos; user solo sí mismo — mismo
   patrón de scoping que audit D-AU-02).
2. `POST /api/users` — invitación asíncrona vía job queue (`job_id` consultable
   por `GET /api/jobs/{id}`, pending→succeeded; el alta real en Keycloak va por
   Admin REST del lado del worker, spec 001 AC-ORG-002).
3. `PUT /api/users/{user_id}` y `POST /api/users/{user_id}/disable` — edición y
   baja con audit `user.updated` / `user.disabled` (spec 008 §5).
4. `GET/PUT /api/branding` — white-label del tenant (REQ-BRD-001/005,
   AC-ONB-007/AC-BRD-005): GET devuelve el default del proveedor con
   `source="provider"` cuando el tenant no definió branding; PUT exige
   `branding.manage`.
5. `GET /api/agents/{agent_id}/grants` — listado de grants activos de un agente
   (revocación sin conocer el grant_id; extensión natural de spec 005).
6. `POST /api/departments` — alta de departamento (REQ-ORG-001) con
   `profile_template` opcional y audit `department.created`.

**Fuera de alcance** (se derivan como follow-up):

- Revocación completa de REQ-ORG-005 al desactivar (bloqueo de virtual keys
  LiteLLM del usuario y suspensión de grants salientes): v1 desactiva el
  usuario, revoca sesiones y dispositivos en la misma transacción; el bloqueo
  de keys del gateway requiere orquestación con el worker de spend (D-GOV-03) y
  se deriva a tarea propia.
- Upload de logos (REQ-BRD-001 valida tamaño/tipo de archivo): v1 trata
  `logo_url` como dato (URL); la validación de binarios llega con el upload.
- Envío de la invitación por email (conector): v1 entrega el one-time password
  al admin invitador vía el resultado del job (ver D-ORG-02).
- Edición de roles de un usuario existente (`role.assigned`/`role.revoked` vía
  API): el contrato del console lo lista como pendiente; esta spec solo asigna
  el rol inicial en la invitación.
- DELETE/Rename de departamentos (CRUD completo): solo alta (lo que el
  console consume hoy).

## 2. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| D-ORG-01 | **Scope por rol sin permiso de entrada nuevo**: `GET /api/users` no añade `users.view` al catálogo — replica el patrón de audit (D-AU-02): todo principal autenticado+MFA puede llamar y el alcance se aplica en la MISMA transacción tenant-scoped (org_admin → todo el tenant; dept_admin → usuarios de sus departamentos; user → solo sí mismo). Razonamiento: un permiso `users.view` otorgado a los tres roles para que "user vea solo sí mismo" no filtraría nada (todos lo tendrían) y añadiría una fila de catálogo sin efecto; el scoping por rol es el mecanismo real. La tarea dejaba la decisión a esta spec. | D-AU-02, tarea t_f72dc691 §1 |
| D-ORG-02 | **Invitación asíncrona con entrega one-time vía job result**: `POST /api/users` valida (email, departamento en scope, rol del catálogo, email no existente en el tenant), emite `user.invited` y encola el job `user.invite` (idempotency_key `invite:<email>`) — **ambos en la MISMA transacción del request** (el insert del job comparte la conexión del endpoint: la cadena de audit toma un advisory lock por tenant que se retiene hasta commit, así que una segunda conexión emitiendo `job.created` se autointerbloquearía; una sola tx además hace la invitación atómica — sin job huérfano si el request revienta) — y responde `202 {job_id, status}`. El worker crea la identidad en Keycloak (Admin REST: usuario + atributo `tenant_id` + `CONFIGURE_TOTP` + password temporal one-time) y la fila `users`+`user_roles` en una `tenant_tx`, con audit `user.created`+`role.assigned` (actor `system`, `actor_id` = invitador). El **one-time password viaja en `jobs.result`** (visible solo al tenant vía RLS + sesión MFA): es la misma entrega one-time de spec 004 (`one_time_activation` en la respuesta de provisioning, ADR-015 adjacente) y no otorga ninguna capacidad que el org_admin no tenga ya (re-invitar resetea el password). Es temporal: Keycloak lo invalida en el primer login (cambio forzado + TOTP). El envío por email queda como follow-up del conector. | AC-ORG-002, spec 004 one_time_activation, constitution IV |
| D-ORG-03 | **Edición/baja con `users.manage` y scope dept_admin**: `PUT /api/users/{id}` acepta `display_name`/`department_id` (mover de departamento); `POST /{id}/disable` marca `status='disabled'`, revoca TODAS sus sesiones activas y sus dispositivos en la MISMA transacción (efecto inmediato, no ≤60 s), y es idempotente (re-disable → 200 sin re-audit). dept_admin solo opera sobre usuarios de sus departamentos (fuera de scope → 404, sin oráculo de existencia — mismo criterio que budgets/policies). Auto-disable → 422 (un admin no puede bloquearse a sí mismo). Audit `user.updated`/`user.disabled` al contrato §5 (`{email, department_id?}`). | REQ-ORG-004/005, D-CP-09, spec 008 §5 |
| D-ORG-04 | **Branding con `source` derivado por comparación**: shape canónico `{product_name, tagline?, accent_color?, logo_url?, source}` (el de types.ts del console). `GET` lo puede llamar cualquier principal autenticado (el desktop también lo lee en runtime, REQ-BRD-002/003); `PUT` exige `branding.manage` (org_admin). El default del proveedor es `{product_name: "Trust Agent", tagline: "AI governance for your organization", accent_color: "#4F46E5", logo_url: null}` — `accent_color` hereda el índigo del `primary_color` que spec 004 estampa al provisionar (mapeo legacy `primary_color`→`accent_color` al leer). `source="provider"` cuando `tenants.branding` está vacío **o es exactamente el default estampado por provisioning** (un tenant recién provisionado cumple AC-ONB-007/AC-BRD-005); cualquier otra cosa → `source="tenant"` con los cuatro campos rellenos por el default solo si el tenant no los definió. `PUT` valida: `product_name` 1–80, `tagline` ≤140, `accent_color` `^#[0-9a-fA-F]{6}$`, `logo_url` http(s) ≤2048 (v1 sin upload); preserva claves desconocidas del jsonb, audita `branding.updated` (acción nueva del catálogo, D-ORG-07) y devuelve el branding vigente con `source="tenant"`. | REQ-BRD-001/005, AC-ONB-007, AC-BRD-005, D-AC-06 |
| D-ORG-05 | **Grants-list con el mismo gate que gestionar grants**: `GET /api/agents/{agent_id}/grants` usa `_can_manage` de spec 005 (dueño, dept_admin del depto del agente u org_admin); quien no puede gestionar → 404 (no revela existencia, REQ-AGT-003). Respuesta `{grants: [{id, grantee_user_id, permission, granted_at, granted_by}]}` con solo grants vigentes (`revoked_at IS NULL`), ordenados por `granted_at`. Habilita la revocación sin conocer el grant_id a priori. | REQ-AGT-002/003, D-GOV-13 |
| D-ORG-06 | **Alta de departamento con `departments.manage`** (org_admin, matriz §2.1): `POST /api/departments` valida `name` (1–80, sin duplicados por tenant → 422) y `profile_template` opcional contra el registro de plantillas (422 si la clave no existe), inserta y audita `department.created` (`{name, profile_template?}`). Respuesta `201 {department_id, name}`. No crea presupuesto ni dept_admin (eso llega por budgets/policies/invitación existentes). | REQ-ORG-001, REQ-PRF-002 |
| D-ORG-07 | **Evolución de datos mínima**: una sola fila nueva de catálogo — `branding.updated` en `catalog.audit_actions` (FK de `audit_log.action`) vía migración `0005_org_endpoints` (encadenada sobre `0004_governance`, única cabeza) Y en `db/schema.sql` (sección 10). Cero DDL nuevo: todas las tablas/RLS/grants ya existen (users, user_roles, departments, tenants.branding, agent_grants, jobs). La paridad pg_dump de spec 002 no se ve afectada (INSERT de datos, no DDL). | D-DB-12, spec 008 §5 |
| D-ORG-08 | **Cero secretos en respuestas, con la excepción documentada del one-time**: el directorio, branding, grants y departments no exponen material sensible (ni hashes de session, ni subjects OIDC, ni keys). La única credencial que cruza el API es el one-time password de la invitación (D-ORG-02), entregado una vez al admin invitador por un canal tenant-scoped+MFA. `users.oidc_subject` NO se expone en el directorio. | constitution IV, tarea §Reglas |

## 3. Requisitos (formato EARS)

Convenciones: plantilla EARS **Cuando** `<trigger>` **el sistema** `<respuesta>`; prioridad MoSCoW; trazabilidad `[001-…]` = spec maestra, `[008-…]` = spec audit, `[009-…]` = spec console.

### 3.1 Directorio de usuarios

**REQ-ORG-100** (M) [001-REQ-ORG-004, 009-D-AC-06] — Cuando un principal autenticado con MFA pide `GET /api/users`, el sistema devuelve los usuarios de su tenant dentro de su alcance de rol: org_admin todos; dept_admin los de sus departamentos; user solo sí mismo; cada usuario con `{user_id, email, display_name, department_id, status, roles: [{role_key, department_id}]}`.
> **AC-ORG-100**: org_admin ve a todos (incluidos disabled); dept_admin solo los de sus deptos; user solo sí mismo; un usuario del tenant B jamás aparece para el tenant A (RLS); `oidc_subject` no viaja en la respuesta.

**REQ-ORG-101** (M) [001-AC-ORG-002] — Cuando un admin con `users.invite` invita un usuario (`email`, `department_id?`, `role_key?`, `display_name?`), el sistema valida email/departamento/rol, rechaza emails ya existentes en el tenant (422), emite `user.invited` y responde `202 {job_id, status}`; el job `user.invite` es consultable por `GET /api/jobs/{id}` y pasa de `pending` a `succeeded` con el usuario consultable en el directorio.
> **AC-ORG-101**: invite válido → 202 + `job_id`; tras correr el worker, `GET /api/jobs/{id}` = `succeeded` y el usuario aparece en `GET /api/users`; email duplicado → 422; dept_admin invitando a un depto fuera de su scope → 404; rol fuera del catálogo → 422.

**REQ-ORG-102** (M) [001-REQ-ORG-004] — Cuando un dept_admin invita/edita/desactiva, el sistema limita su alcance a sus departamentos; `department_id` fuera de scope → 404 (sin oráculo); un dept_admin no puede invitar con rol `org_admin` (422).
> **AC-ORG-102**: dept_admin + depto propio → 202; depto ajeno → 404; rol `org_admin` por dept_admin → 422; org_admin con cualquier rol del catálogo → 202.

**REQ-ORG-103** (M) [001-REQ-ORG-005 parcial, 008-§5] — Cuando un admin con `users.manage` desactiva un usuario, el sistema marca `status='disabled'`, revoca sus sesiones activas y sus dispositivos en la misma transacción, audita `user.disabled` (`{email}`) y responde 200; desactivar a sí mismo → 422; re-desactivar → 200 idempotente sin nuevo evento.
> **AC-ORG-103**: disable → 200 + usuario `disabled` en el directorio + 0 sesiones activas + dispositivos `revoked` + evento `user.disabled`; self-disable → 422; segundo disable → 200 sin evento adicional.

**REQ-ORG-104** (M) [008-§5] — Cuando un admin con `users.manage` edita un usuario (`display_name?`, `department_id?`), el sistema aplica los cambios, audita `user.updated` (`{email, department_id?}`) y responde con el usuario actualizado; dept_admin moviendo a un usuario hacia un depto fuera de su scope → 403.
> **AC-ORG-104**: PUT display_name → 200 + valor nuevo + evento `user.updated`; dept_admin mueve usuario a depto ajeno → 403; usuario inexistente → 404.

### 3.2 Branding

**REQ-ORG-110** (M) [001-REQ-BRD-005, AC-ONB-007] — Cuando un principal autenticado pide `GET /api/branding`, el sistema devuelve el branding vigente `{product_name, tagline?, accent_color?, logo_url?, source}`; si el tenant no definió branding (jsonb vacío o exactamente el default estampado por provisioning), devuelve el default del proveedor con `source="provider"`.
> **AC-ORG-110**: tenant recién provisionado (sin customización) → default + `source="provider"`; tenant con branding propio → sus valores + `source="tenant"`; `accent_color` se mapea desde `primary_color` legacy si el tenant no definió `accent_color`.

**REQ-ORG-111** (M) [001-REQ-BRD-001] — Cuando un org_admin hace `PUT /api/branding` (`product_name?`, `tagline?`, `accent_color?`, `logo_url?`), el sistema valida (nombre 1–80, color hex, logo URL http(s)), persiste en `tenants.branding`, audita `branding.updated` y devuelve el branding vigente con `source="tenant"`; sin permiso `branding.manage` → 403; validación fallida → 422; cuerpo vacío → 422.
> **AC-ORG-111**: PUT válido → 200 + GET refleja los valores con `source="tenant"` + evento `branding.updated`; color inválido → 422; user sin permiso → 403.

### 3.3 Grants-list y departments

**REQ-ORG-120** (M) [005-REQ-AGT-002/003, 009-D-AC-06] — Cuando el dueño, el dept_admin del depto del agente o un org_admin pide `GET /api/agents/{agent_id}/grants`, el sistema devuelve `{grants: [{id, grantee_user_id, permission, granted_at, granted_by}]}` con solo grants vigentes; cualquier otro principal → 404.
> **AC-ORG-120**: dueño lista sus grants (los revocados no aparecen); grantee sin permiso de gestión → 404; agente inexistente → 404.

**REQ-ORG-121** (M) [001-REQ-ORG-001] — Cuando un org_admin crea un departamento (`name`, `profile_template?`), el sistema valida nombre único por tenant y plantilla existente, inserta el departamento, audita `department.created` (`{name, profile_template?}`) y responde `201 {department_id, name}`; duplicado → 422; plantilla desconocida → 422; sin `departments.manage` → 403.
> **AC-ORG-121**: alta válida → 201 + departamento visible; nombre duplicado → 422; `profile_template` inexistente → 422; dept_admin → 403.

## 4. Contrato de eventos de audit (spec 008 §5)

| Endpoint | Acción | target | payload |
|---|---|---|---|
| `POST /api/users` | `user.invited` | `user` (id nuevo aún no existe → target_id = email) | `{email, department_id?, role_key?, job_id}` |
| worker `user.invite` | `user.created` | `user:<id>` | `{email, department_id?}` |
| worker `user.invite` | `role.assigned` | `user:<id>` | `{role_key}` |
| `PUT /api/users/{id}` | `user.updated` | `user:<id>` | `{email, department_id?}` |
| `POST /api/users/{id}/disable` | `user.disabled` | `user:<id>` | `{email}` |
| `PUT /api/branding` | `branding.updated` (acción nueva, D-ORG-07) | `tenant` | `{keys: [...actualizadas]}` |
| `POST /api/departments` | `department.created` | `department:<id>` | `{name, profile_template?}` |

`user.invited` lleva `target_id` = email porque el user_id aún no existe (el job lo crea); el `job_id` en el payload cierra la trazabilidad invitación→job→`user.created`.

## 5. Verificación

- Suite unitaria sobre PostgreSQL real: `tests/test_org_endpoints.py` (scoping del directorio, invitación + worker con doble de Keycloak declarado, edición/baja, branding GET/PUT, grants-list, departments) + suite existente en verde.
- **Activación del console (D-AC-06)**: `platform/console/scripts/smoke_authorized.py` — los checks de `/users` y `/branding` pasan de 404 (degradación honesta) a 200 con datos reales; la página Users & Roles muestra el directorio real sin cambios de frontend.

## Evolución spec 013 (cierre de gaps de spec 001, oct-2026)

- **REQ-ORG-001 (overview)**: el overview LISTA departamentos sin presupuesto (`budget_cents: null`, D-013-07) en vez de crear un presupuesto 0 implícito; `console` tipa `budget_cents: number | null`.
- **REQ-BRD-002/003 (desktop)**: el desktop-client consume `GET /api/branding` en runtime tras el login (dashboard → Sidebar → Brand); branding estático del proveedor antes del login (D-013-06).
Detalle y ACs en `specs/013-spec001-gaps/spec.md`.
