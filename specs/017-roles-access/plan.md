# Plan técnico — Spec 017 (roles-access)

## 1. Migración y catálogo

- `db/migrations/versions/20261026_0010_roles_access.py`:
  `revision = "0010_roles_access"`, `down_revision = "12c08d4fb858"` (head
  actual, merge de las ramas 0009 paralelas). Solo INSERT de datos:
  `catalog.permissions ('roles.assign')` +
  `catalog.role_permissions ('org_admin','roles.assign')`, ambos `ON CONFLICT
  DO NOTHING`. Sección espejo en `db/schema.sql` §13 (paridad pg_dump,
  `db/tests/test_schema_parity.py`).
- Cero DDL nuevo: `user_roles` (con su unique index por scope) y las acciones
  `role.assigned`/`role.revoked` existen desde 0001.

## 2. Backend (`platform/app/org/`)

- **`roles.py` (nuevo)**: `assign_role()` / `revoke_role()`. Reutiliza
  `directory._get_user` (404 sin oráculo), `TENANT_ROLES` de `invite.py`,
  `audit.emit` + `top_role_key` de rbac. Guard de auto-modificación (D-17-02)
  en ambas. Duplicado exacto → 422; ambigüedad de scope → 422; no asignado →
  404. `granted_by = principal.user_id`.
- **`router.py`**: rutas nuevas `POST /users/{user_id}/roles` (201),
  `DELETE /users/{user_id}/roles/{role_key}` (204, query `department_id`),
  `GET /departments`. Puerta: `if "roles.assign" not in
  principal.permissions: raise permission_denied(...)` (mismo patrón
  inline que el resto del router). `UserUpdateBody` gana
  `status: Literal["active","disabled"] | None`.
- **`directory.py`**: `update_user()` gana `status` (D-17-05); el núcleo de
  revocación de `disable_user` se extrae a `_disable_core()` compartido —
  una sola ruta de código para POST /disable y PUT status=disabled.
- **`departments.py`**: `list_departments()` (SELECT id, name ORDER BY name;
  RLS hace el scope de tenant).

## 3. UI (`console/src/app/(console)/users/users-page.tsx`)

- Datos: `useApi<DirectoryList>("/api/users")`, `useApi<Whoami>("/api/me")`,
  `useApi<DepartmentsList>("/api/departments")`.
- Permisos → visibilidad: `roles.assign` (asignar/quitar), `users.manage`
  (depto/disable/reactivate), `users.invite` (invite). Fila propia: sin
  acciones de roles/disable.
- Asignar: diálogo (Select rol + Select depto si `dept_admin`) → POST →
  toast + refetch. Quitar: `ConfirmDialog` → DELETE → toast + refetch.
- Departamento: diálogo con Select → PUT `{department_id}` → toast + refetch.
- Disable: `ConfirmDialog` danger → POST `/disable`. Reactivate:
  `ConfirmDialog` → PUT `{status:"active"}`.
- Invite: diálogo (email, nombre, depto, rol) → POST `/api/users` → 202 →
  toast "invitation queued" (el job y su one-time password viven en
  `/api/jobs/{id}`, spec 012).
- `types.ts`: `DepartmentsList` + `Whoami` (si falta) — añadir SIN tocar
  bloques de otras specs (hotspot: archivo editado por lane paralela hoy).

## 4. Tests (`platform/tests/test_roles_access.py`)

Convención de `test_org_endpoints.py` (fixtures `client`/`idp`/`mint` de
conftest, `_admin` para inspección de DB, `_audit_count`). Casos: AC-RA-001
(happy + audit), AC-RA-002 (revocación + audit + 404 + ambiguo 422),
AC-RA-003 (bob dept_admin → 403, carol user → 403), AC-RA-004 (self 422 en
assign y revoke), AC-RA-005 (PUT status disable/active + invalid 422 +
self-disable 422), AC-RA-006 (GET /departments aislamiento tenant B),
duplicado 422, dept_admin sin scope 422, rol/depto desconocido 422, usuario
cross-tenant 404, check data-driven del catálogo (solo org_admin tiene
`roles.assign`).

## 5. Evidencia viva (`platform/scripts/smoke_roles_access.py`)

Patrón `console/scripts/smoke_authorized.py`: IdP de test con JWKS real por
HTTP (issuer arbitrario — el control plane verifica la firma de verdad),
mint de sesión admin vía `POST /api/auth/session` contra el API vivo
(:8000, host network), cookies `trust_session` + `X-CSRF-Token`. Tenant real
`final-e2e` (admin `admin@final-e2e.test` + usuario plain
`worker@final-e2e.test`):

1. GET /api/users → directorio real.
2. POST /api/users/{worker}/roles (dept_admin @ IT Operations) → 201 +
   reflejo en directorio + `role.assigned` en audit.
3. DELETE del rol → 204 + `role.revoked` en audit.
4. POST roles con sesión de `worker@final-e2e.test` (plain user) → 403
   (escalada viva).
5. PUT /api/users/{worker} status=disabled → sesiones revocadas; status=active
   → reactivado (estado final: worker activo, sin roles extra — sin residuo).
6. GET /api/departments → departamentos del tenant.

## 6. Riesgos

- **Hotspot `app/org/router.py`**: editado hoy 08:59 por lane paralela
  (spec 016). Mitigación: append de secciones nuevas al final del archivo +
  re-leer antes de parchear + flag `hotspot:` en el card.
- **`types.ts`**: mismo riesgo; solo append.
- Imagen `trust-agent/platform:local` debe rebuildarse para que el API vivo
  sirva los endpoints nuevos (compose bakea el código).
