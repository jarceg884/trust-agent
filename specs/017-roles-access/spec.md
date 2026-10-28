# Spec 017 — Roles & Access: endpoints de asignación/revocación + UI (feedback Miller #4)

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-26 |
| **Autor** | dev (tarea t_5576ee94, feedback Miller #4) |
| **Padre** | specs/012-org-endpoints/spec.md §1 (directorio users + invite/disable), specs/001-platform/spec.md §2.1 (matriz RBAC data-driven, I10), specs/009-admin-console/spec.md §4.2 D-AC-06, specs/008-audit/spec.md §5 (contrato `role.assigned`/`role.revoked`) |
| **Idioma** | Español (docs) / inglés (código, UI copy) |

---

## 1. Problema (feedback #4)

> "Roles y acceso no funciona."

La página **Users & Roles** del Admin Console es un directorio de SOLO LECTURA:
no existen endpoints para asignar/quitar roles ni UI para editarlos (la propia
página lo admite en su comentario de cabecera). `PUT /api/users/{id}` y
`POST /api/users/{id}/disable` existen (spec 012) pero la página no los usa, y
el botón "Invite user" muestra un aviso DESACTUALIZADO ("the control plane
does not expose…") cuando `POST /api/users` YA existe y está verificado.

## 2. Alcance

**Dentro de alcance**:

1. **`POST /api/users/{id}/roles`** — asignar rol (con scope de departamento
   para `dept_admin`). Solo `org_admin` (permiso nuevo `roles.assign`,
   data-driven). Sin auto-escalada: un principal jamás modifica sus propios
   roles. Audit `role.assigned` (ya existe en catálogo y se emite en
   provision/invite — mismo contrato).
2. **`DELETE /api/users/{id}/roles/{role_key}`** — quitar rol (con
   `?department_id=` para desambiguar scopes múltiples de `dept_admin`).
   Audit `role.revoked` (ya existe en catálogo).
3. **`PUT /api/users/{id}`** — extensión backward-compatible: campo opcional
   `status` (`active`|`disabled`). `disabled` delega en la MISMA lógica que
   `POST /disable` (revocación de sesiones + dispositivos en la misma
   transacción, audit `user.disabled`); `active` reactiva (audit
   `user.updated` con payload `status`).
4. **`GET /api/departments`** — lista mínima (`department_id`, `name`) del
   tenant para los selectores de la UI (hoy la única fuente con nombres es
   `budgets/overview`, que omite departamentos sin presupuesto).
5. **Migración `0010_roles_access`** + `db/schema.sql` §13: permiso nuevo
   `roles.assign` en `catalog.permissions` + grant SOLO a `org_admin` en
   `catalog.role_permissions`. Solo INSERT de catálogo, cero DDL (paridad
   pg_dump intacta).
6. **UI completa** en `users-page.tsx`: asignar/quitar roles inline con
   confirmación, cambiar departamento, desactivar/reactivar usuario, invitar
   usuario (endpoint spec 012 existente), todo con toasts + refetch; controles
   gated por los permisos reales de `/api/me` (`roles.assign`, `users.manage`,
   `users.invite`); fila propia sin acciones de auto-modificación.
7. **Tests** nuevos en `platform/tests/test_roles_access.py`: asignación,
   revocación, intento de escalada → 403, auto-modificación → 422, aislamiento
   cross-tenant → 404, status vía PUT, lista de departamentos.
8. **Evidencia real** contra el stack vivo (API :8000 + PostgreSQL
   `trust_agent` + tenant real): script
   `platform/scripts/smoke_roles_access.py` con sesión admin real (patrón
   `smoke_authorized.py`) + sesión de usuario plain para el 403.

**Fuera de alcance**:

- Asignación de roles por `dept_admin` (v1: solo `org_admin`; el `dept_admin`
  conserva `users.manage` para editar depto/nombre/desactivar DENTRO de su
  scope, pero no toca roles).
- Edición de `oidc_subject`, email o `mfa_enrolled` (identidad vive en
  Keycloak).
- UI de drenaje del job de invite con entrega de one-time password (el job
  queda visible en `/api/jobs/{id}`; la entrega one-time ya está cubierta por
  spec 012).

## 3. Decisiones (D-17-xx)

- **D-17-01 — Permiso data-driven `roles.assign`**: la puerta de los
  endpoints de roles es un permiso NUEVO del catálogo (`catalog.permissions`)
  otorgado SOLO a `org_admin` (`catalog.role_permissions`), verificado con el
  helper existente `require_permission`-style check del router (reutiliza
  `app/auth/rbac.py`, no duplica lógica). NO se hardcodea el rol en código
  (constitution III/I10: la matriz vive en la DB). `dept_admin` tiene
  `users.manage` pero NO `roles.assign` → no puede asignar roles ni
  auto-escalarse a `org_admin`.
- **D-17-02 — Sin auto-escalada ni auto-privación**: asignar o quitar roles
  sobre uno mismo → `422` (un `org_admin` no puede fabricarse scopes ni
  quitarse su propio `org_admin` y dejar el tenant sin admin operable). El
  servidor es la autoridad; la UI además oculta las acciones en la fila
  propia.
- **D-17-03 — Contrato de asignación**: `POST /api/users/{id}/roles`
  `{role_key, department_id?}`. Validaciones: rol en
  `('org_admin','dept_admin','user')`; `dept_admin` exige `department_id`
  (espejo del CHECK de `user_roles`); departamento existente → `422` si no;
  usuario inexistente/cross-tenant → `404` sin oráculo (D-CP-09); duplicado
  exacto (mismo rol+scope) → `422`. Respuesta `201` con la lista de roles
  fresca del usuario. Audit `role.assigned` payload
  `{role_key, department_id?}`.
- **D-17-04 — Contrato de revocación**: `DELETE /api/users/{id}/roles/{role_key}`
  con query param opcional `department_id`. Si el usuario tiene el rol en
  varios scopes y no se pasa `department_id` → `422` (ambiguo). Rol no
  asignado → `404`. Respuesta `204`. Audit `role.revoked` payload
  `{role_key, department_id?}`.
- **D-17-05 — `status` vía `PUT`**: reutiliza el código de `disable_user`
  (misma transacción: status + sesiones + dispositivos revocados, audit
  `user.disabled`); `status=active` solo cambia la columna + audit
  `user.updated` (el usuario debe re-autenticarse; Keycloak sigue siendo la
  autoridad de login). Auto-disable → `422` (guard existente).
- **D-17-06 — UI gated por permisos reales**: la página consulta `/api/me`
  (incluye `permissions`) y muestra los controles según
  `roles.assign`/`users.manage`/`users.invite`. Toda mutación pasa por el
  proxy BFF con CSRF (`api-client` existente), confirmación explícita
  (`ConfirmDialog`) para quitar rol / desactivar, toasts de resultado y
  refetch del directorio (cero cache de datos de gobernanza).

## 4. Requisitos (EARS)

- **REQ-RA-001**: Cuando un principal con permiso `roles.assign` hace
  `POST /api/users/{id}/roles` con un rol válido, el sistema debe asignar el
  rol (con scope de depto si aplica), emitir `role.assigned` en el audit log y
  responder `201` con los roles frescos.
- **REQ-RA-002**: Cuando un principal SIN `roles.assign` (dept_admin o user)
  llama a los endpoints de roles, el sistema debe responder `403`
  `permission_denied` sin revelar nada más.
- **REQ-RA-003**: Cuando un principal modifica los roles de su propia
  cuenta, el sistema debe responder `422`.
- **REQ-RA-004**: Cuando `DELETE /api/users/{id}/roles/{role_key}` targeting
  un rol asignado, el sistema debe revocarlo, emitir `role.revoked` y
  responder `204`; rol no asignado → `404`; ambiguo sin `department_id` →
  `422`.
- **REQ-RA-005**: Cuando `PUT /api/users/{id}` incluye `status`, el sistema
  debe transicionar el estado con la semántica de D-17-05 y audit; valor
  inválido → `422`.
- **REQ-RA-006**: Cuando cualquier principal autenticado hace
  `GET /api/departments`, el sistema debe devolver SOLO los departamentos de
  su tenant (RLS).
- **REQ-RA-007**: La UI de Users & Roles debe permitir asignar/quitar roles,
  cambiar departamento, desactivar/reactivar e invitar, con confirmación y
  feedback, y debe ocultar los controles que el principal no tiene permitido.
- **REQ-RA-008**: Toda la evidencia de verificación debe ser real (stack
  vivo), nada simulado.

## 5. Criterios de aceptación

- **AC-RA-001**: `POST /api/users/{id}/roles` asigna `dept_admin` con scope,
  responde 201, el directorio refleja el rol y `role.assigned` aparece en
  `audit_log` (test + smoke vivo).
- **AC-RA-002**: `DELETE /api/users/{id}/roles/{role_key}` responde 204, el
  rol desaparece del directorio y `role.revoked` aparece en `audit_log` (test
  + smoke vivo).
- **AC-RA-003**: dept_admin y user plain reciben `403` en ambos endpoints de
  roles (test + smoke vivo con sesión de usuario real).
- **AC-RA-004**: auto-asignación y auto-revocación → `422` (test).
- **AC-RA-005**: `PUT status=disabled` revoca sesiones/dispositivos y emite
  `user.disabled`; `status=active` reactiva y emite `user.updated` (test).
- **AC-RA-006**: `GET /api/departments` lista solo el tenant del principal
  (test de aislamiento).
- **AC-RA-007**: suite completa en verde (nueva + existentes sin regresión) y
  `npm run build` + `npm run lint` del console en verde.
- **AC-RA-008**: smoke vivo PASS documentado (script + salida real).
