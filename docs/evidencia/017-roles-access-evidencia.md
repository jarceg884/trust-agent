# Evidencia — Spec 017: Roles & Access (feedback Miller #4)

**Tarea**: t_5576ee94 · **Fecha**: 2026-10-26 · **Estado**: Converged (implementación == spec, ACs con evidencia real)

## Resumen

La página Users & Roles pasó de directorio de solo lectura a gestión completa:
asignar/quitar roles inline con scope de departamento, cambiar departamento,
desactivar/reactivar e invitar — todo contra el API vivo, con confirmación,
toasts, audit inmutable y sin camino de auto-escalada.

## Artefactos

| Pieza | Archivo |
|---|---|
| Endpoints | `platform/app/org/router.py` — `POST /api/users/{id}/roles` (201), `DELETE /api/users/{id}/roles/{role_key}` (204, `?department_id=`), `GET /api/departments`, `status` en `PUT /api/users/{id}` |
| Lógica de roles | `platform/app/org/roles.py` — guards D-17-02/03/04, audit `role.assigned`/`role.revoked` |
| Status vía PUT | `platform/app/org/directory.py` — `_disable_core()` compartido (POST /disable y PUT status=disabled, una sola ruta de código) |
| Departamentos | `platform/app/org/departments.py` — `list_departments()` (RLS) |
| Migración | `db/migrations/versions/20261026_0010_roles_access.py` — permiso `roles.assign` SOLO a `org_admin` (data-driven, cero DDL) + `db/schema.sql` §13 |
| Tests | `platform/tests/test_roles_access.py` — 15 casos (AC-RA-001..006) |
| Smoke dedicado | `platform/scripts/smoke_roles_access.py` — servidor real efímero + PostgreSQL real |
| Smoke compartido | `platform/scripts/smoke_roles_access_shared.py` — stack compose vivo (:8000 + Keycloak real + DB `trust_agent`) |
| UI | `platform/console/src/app/(console)/users/users-page.tsx` + `types.ts` (`DepartmentsList`) |
| Fix proxy BFF | `platform/console/src/app/api/proxy/[...path]/route.ts` — status 204/304 sin body (bug: `Response constructor: Invalid response status code 204` → 500) |
| Screenshot UI viva | `docs/evidencia/017-users-roles-ui-viva.png` |

## Evidencia real (nada simulado)

### 1. Tests (AC-RA-001..006)

```
tests/test_roles_access.py — 15 passed
tests/test_roles_access.py + test_org_endpoints.py + test_departments_crud.py — 48 passed
```

Cubren: asignación con scope + audit, revocación + audit + 404 + ambiguo 422,
escalada dept_admin/user → 403, auto-modificación → 422, status vía PUT
(disable revoca sesiones/dispositivos; active reactiva), aislamiento
cross-tenant de `GET /api/departments`, duplicado 422, catálogo data-driven
(solo org_admin tiene `roles.assign`).

### 2. Smoke dedicado (AC-RA-008) — `smoke_roles_access.py`

28/28 PASS contra servidor uvicorn real (socket real, HTTP real) + PostgreSQL
real efímera migrada con la línea Alembic canónica (0010 incluido — el permiso
`roles.assign` es dato real del catálogo), RLS activa, CSRF real, audit chain
verificada. Único doble declarado: IdP de test con JWKS real por HTTP (el
control plane verifica la firma de verdad). Desviación documentada en el
docstring del script (constitución II): el plan original pedía el tenant
compartido final-e2e; ver punto 3 — se cubrió AMBOS caminos.

### 3. Smoke compartido (stack vivo) — `smoke_roles_access_shared.py`

16/16 PASS contra el stack compose REAL:

- Login PKCE S256 + enrolamiento TOTP real contra el Keycloak real (acr=2).
- Sesión MFA minteada por el control plane vivo (`sts1.*`, `mfa_verified=true`).
- `GET /api/me` → org_admin con `roles.assign` (catálogo 0010 en la DB compartida).
- `POST /api/users/{worker}/roles` (dept_admin @ IT Operations) → **201**.
- `DELETE .../roles/dept_admin?department_id=...` → **204**.
- `PUT status=disabled` → 200 (revoca sesiones); `PUT status=active` → 200.
- `GET /api/departments` → 200 (4 deptos seed).
- Audit en la DB compartida: `role.assigned`, `role.revoked`, `user.disabled`;
  `audit_verify_chain` → ok.
- Sin residuo: worker activo con su rol original.

### 4. UI viva (AC-RA-007) — browser real contra :3100

Sesión admin real (login PKCE+TOTP descrito arriba). En `/users`:

- Directorio real con columnas USER / DEPARTMENT / ROLES / STATUS.
- Fila propia del admin SIN acciones de roles/disable (D-17-02; el servidor
  además rechaza auto-modificación con 422).
- **Assign**: diálogo con Select de rol + Select de departamento (datos vivos
  de `GET /api/departments`) → 201 → toast "Role dept_admin assigned to
  worker@final-e2e.test." → diálogo se cierra → fila refleja
  `dept_admin · IT Operations`.
- **Remove**: `✕` en el badge → ConfirmDialog ("Remove dept_admin (scope IT
  Operations)? … Audited as role.revoked.") → 204 → toast → diálogo se cierra
  → fila sin el rol.
- Audit de la sesión UI visible en la DB compartida (pares
  `role.assigned`/`role.revoked`).
- Screenshot: `docs/evidencia/017-users-roles-ui-viva.png`.

### 5. Console build/lint (AC-RA-007)

```
npm run lint → 0 errores
npm run build → ✓ Compiled successfully (19/19 páginas)
docker compose build console + up -d → healthy
```

## Bugs reales encontrados y corregidos durante la verificación

1. **Proxy BFF rompía todo 204** (`route.ts`): `new NextResponse(body,
   {status: 204})` lanza `TypeError: Response constructor: Invalid response
   status code 204` → el navegador veía HTTP 500 aunque el API había
   devuelto 204. Era el primer endpoint 204 que cruza el proxy, así que
   ninguna spec anterior lo había ejercitado. Fix: `proxyResponse()` con
   body `null` para 204/304 (también en el camino de refresh-retry).
2. **Diálogos no se cerraban tras éxito** (`users-page.tsx`): `run()`
   refetchaba y toasteaba pero nunca limpiaba el estado del diálogo — el
   diálogo de asignación quedaba abierto encima de la fila actualizada.
   Fix: callback `close` en `run()`, se invoca SOLO en éxito (en fallo el
   diálogo queda abierto para reintentar).

## Estado de la suite completa

`pytest tests/ --ignore=tests/e2e` → 372 passed, 3 failed. Los 3 fallos
(`test_provider_admin.py` ×2, `test_spend_cutoff.py::test_org_cutoff_and_tenant_isolation`)
se reproducen SIN los tests de esta spec (verificados con `--deselect`), pasan
aislados (13/13 y 8/8) y pertenecen a lanes paralelas editadas hoy
(08:34/09:54). Son interferencia de orden entre tests que comparten la DB
efímera, no una regresión de spec 017. Reportado en el card para seguimiento.

## Cómo verificarlo

```bash
cd /root/trust-agent/platform
.venv/bin/pytest tests/test_roles_access.py -q          # 15 passed
.venv/bin/python scripts/smoke_roles_access.py          # SMOKE PASS 28/28
.venv/bin/python scripts/smoke_roles_access_shared.py   # SMOKE SHARED PASS 16/16
# UI: http://127.0.0.1:3100 → org final-e2e → admin@final-e2e.test → Users & Roles
```
