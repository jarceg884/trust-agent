# Revisión E2E Pilar 2 — Puntos 1 (Departments CRUD), 2 (Usuarios y roles) y 10 (Auth UX)

> Tarea QA t_db7b2fa9 (revisión maestra t_ef306f32, Pilar 2). Fecha: 2026-10-26.
> Método: flujos reales contra el stack vivo (API :8000, Keycloak :8080, console :3100,
> PostgreSQL `trust_agent` :55432) + suites pytest reales + smoke vivo con servidor real.
> **Nada simulado.** Tenant de evidencia aprovisionado en vivo para esta revisión:
> **`qa-p2rev-223359`** (id `93f715c5-4ab2-4378-8f48-7fbb36a0073a`), admin
> `admin@qa-p2rev-223359.test`, usuario auto-registrado `miller@qa-p2rev-223359.test`,
> invitado `newhire@qa-p2rev-223359.test`.

## Resumen de veredictos

| Punto | Estado | Veredicto |
|---|---|---|
| 1. Departments CRUD | **VERIFICADO OK** | CREATE/READ/UPDATE/DELETE desde la UI + 409 real + audit completo |
| 2. Usuarios y roles | **VERIFICADO OK** | Alta (invite+self-signup), baja, roles con scope, permisos efectivos, audit |
| 10. Auth UX | **VERIFICADO OK con gaps declarados** | Keycloak invisible, auto-registro, MFA opcional sugerido verificados en vivo; **Google punta a punta es GAP por dependencia externa** (credenciales del operador) |

---

## Punto 1 — Departments CRUD (spec 016): VERIFICADO OK

### Evidencia ejecutada

**Suite real**: `pytest tests/test_departments_crud.py` → **14 passed** (PostgreSQL real,
RLS activa, dos tenants sembrados).

**UI en vivo** (login admin real PKCE, tenant `qa-p2rev-223359`, página `/departments`):

1. **READ** — la página lista los 4 departamentos default (Cloud/Customer Operations,
   Finance, IT Operations) con columnas monthly limit / used / members / utilization y
   acciones `Budget | Rename | Policies | Delete` por fila. Sin el aviso desactualizado
   del feedback #3.
2. **CREATE** — diálogo "Add department" (nombre + budget inicial opcional en USD, copy
   honesta: *"created atomically — if the budget is invalid the department is not
   created"*): creado `QA Research` con $100 → toast
   `Department “QA Research” created with a $100.00 monthly budget.` y fila nueva con
   `$100.00` de monthly limit.
3. **UPDATE** — `Rename` → diálogo con validación documentada (único case-insensitive) →
   `QA Research Renamed` → toast `Department renamed to “QA Research Renamed”.` y fila
   actualizada.
4. **DELETE (camino 409)** — intento de borrar `IT Operations` (1 miembro) → confirm
   dialog → el API responde 409 y la UI muestra el mensaje REAL al operador:
   `Department "IT Operations" is not empty: 1 assigned user(s) — move them first via
   PUT /api/users/{user_id}. Resolve them and try again.` (REQ-16-103).
5. **DELETE (éxito)** — `QA Research Renamed` (vacío) → confirm → toast
   `Department “QA Research Renamed” deleted.` y fila fuera de la lista.

**Persistencia y audit (SQL directo a `trust_agent`)**:

```
department.created | {"name": "QA Research"}
department.renamed | {"new_name": "QA Research Renamed", "old_name": "QA Research"}
department.deleted | {"name": "QA Research Renamed", "removed": {"budgets": 1, "policies": 0, "model_access": 0}}
```

Los tres eventos con el contrato de payload de D-16-05; `removed.budgets: 1` confirma el
borrado en cascada del presupuesto de nivel dept en la misma transacción (D-16-02).

### Observaciones
- Sin hallazgos. REQ-16-100..104 (API), REQ-16-110 (UI) y REQ-16-120 (convergencia con
  evidencia UI viva) se cumplen con evidencia propia de esta revisión.

---

## Punto 2 — Usuarios y roles (spec 017): VERIFICADO OK

### Evidencia ejecutada

**Suite real**: `pytest tests/test_roles_access.py` → passed (27 passed combinado con
`test_auth_ux.py`).

**Smoke vivo** (`scripts/smoke_roles_access.py`, servidor uvicorn real + DB efímera real
con migración 0010, IdP de test declarado que firma JWT RS256 real): **SMOKE PASS**, 28
checks — asignación `dept_admin` con scope → 201, revocación → 204, **escalada de user
plain → 403 permission_denied**, **auto-asignación → 422**, DELETE ambiguo (2 scopes sin
`department_id`) → 422, `PUT status=disabled` revoca la sesión del usuario en la misma
transacción, `GET /api/departments` lista solo el tenant, `role.assigned`/`role.revoked`/
`user.disabled` en audit y `audit_verify_chain → ok`. Catálogo real: `roles.assign`
otorgado SOLO a `org_admin` (dato de la migración 0010, no hardcode).

**UI en vivo** (página `/users`, sesión admin real):

1. **Directorio** con USER / DEPARTMENT / ROLES / STATUS; la **fila propia del admin no
   tiene acciones de auto-modificación** (solo Policies) — D-17-02 reflejado en UI.
2. **Asignar rol** — `+ role` → diálogo (role + department scope para dept_admin) →
   asignado `dept_admin @ IT Operations` → toast
   `Role dept_admin assigned to miller@qa-p2rev-223359.test.` y el chip
   `dept_admin · IT Operations` aparece en la fila con su ✕.
3. **Revocar rol** — ✕ del chip → confirm (`The user immediately loses the permissions
   of that role. Audited as role.revoked.`) → toast `Role dept_admin removed from …`.
4. **Desactivar** — `Disable` → confirm (`Their sessions and devices are revoked
   immediately in the same transaction.`) → status `disabled` + botón pasa a
   `Reactivate`.
5. **Reactivar** — confirm → status `active` (audit `user.updated {status: active}`).
6. **Mover de departamento** — `—` → diálogo → IT Operations → audit
   `user.updated {department_id}`.
7. **Alta de usuario (invite)** — `Invite user` → `newhire@…` → toast `Invitation
   queued…` → el **jobs worker real** aprovisionó al usuario (~4 s): fila activa en el
   directorio y audit `user.invited` + `user.created`.
8. **Permisos efectivos** — el usuario plain (rol `user`) ve los paneles de budgets y
   policies bloqueados con `permission_denied` real del API
   (`GET /api/budgets/overview · permission_denied`); el smoke vivo verificó 403 en los
   endpoints de roles para user plain; el gating de la UI usa los permisos reales de
   `/api/me` (`roles.assign`/`users.manage`/`users.invite`).

**Audit (SQL directo)** — todas las acciones UI dejaron evento real:

```
role.assigned | {"role_key": "dept_admin", "department_id": "ac4b664c…"}
role.revoked  | {"role_key": "dept_admin", "department_id": "ac4b664c…"}
user.disabled | {"email": "miller@…"}
user.updated  | {"email": "miller@…", "department_id": "ac4b664c…"}
user.updated  | {"email": "miller@…", "status": "active"}
user.invited  | {"email": "newhire@…", "role_key": "user"}
user.created  | {"email": "newhire@…"}
```

### Observaciones
- Sin hallazgos. AC-RA-001..008 se cumplen (suites + smoke vivo + UI viva).

---

## Punto 10 — Auth UX (spec 015): VERIFICADO OK con gaps declarados

### Evidencia ejecutada (todo en vivo, navegador real)

**Keycloak invisible (AC-AUTH-001)**:
- Páginas del IdP (login, UPDATE_PASSWORD, CONFIGURE_TOTP) renderizan con tema
  white-label: header `TRUST AGENT`, `Sign in to your account`, **cero apariciones de
  "Keycloak" en el cuerpo** (verificado por DOM: `/keycloak/i → false`).
  Screenshot: `01-idp-login-themed.png`.
- `loginTheme=trust-agent` en el realm (Admin REST) y tema desplegado en
  `/opt/keycloak/themes/trust-agent` del contenedor `idp`.
- Las pantallas propias del console (`/login`, `/register`) no mencionan Keycloak.

**Auto-registro (AC-AUTH-002)**:
- `/register` (página propia de la app): org + email + nombre + contraseña →
  `Account created` → login inmediato con esa contraseña (acr=1, sin OTP forzado) →
  sesión operativa en `/overview` con rol `user` y panels gated por permisos.
- Audit real: `user.self_registered {"mfa": "optional", "email": "miller@…",
  "initial_role": "user"}`; fila `users` + `user_roles` (rol `user`) en DB.

**MFA opcional sugerido (AC-AUTH-003/004)**:
- `org-config` del tenant nuevo: `{"require_mfa": false,
  "allow_self_registration": true, "google_login_enabled": false}` → el login usa
  `acr_values=1` (verificado en la URL de authorize) → password-only completa.
- **Banner persistente visible** en el shell (overview) para el usuario sin MFA:
  `Protect your account — Add multi-factor authentication (TOTP)…` + botón
  `Set up MFA` (componente `mfa-banner.tsx`, `me.mfa_verified=false`).
- `Set up MFA` → baile con `acr=2` → formulario de **enrolamiento TOTP inline themed**
  (CONFIGURE_TOTP con `totpSecret` en la página) → código RFC 6238 real calculado y
  aceptado → vuelta al shell → **el banner desaparece** (sesión `mfa_verified=true`).
- ⚠️ **Corrección a la corrida API hermana** (`qa-pilar2-api-e2e.log` P10e FAIL): el
  chequeo buscaba el banner en `GET /api/notifications` — sonda equivocada. El banner
  de la spec D-AUTH-08 es un componente del shell UI y **existe y funciona** (evidencia
  en vivo arriba).

**org-config rate-limited (AC-AUTH-007)**: ráfaga de orgs inexistentes →
`404 404 404 404 404 429 429 429` (5 fallos → lockout por IP, anti-enumeración,
contador en `ops.login_throttle`). Los éxitos no queman el contador (por diseño).

**UPDATE_PASSWORD themed**: primer login del admin con la one-time activation →
página de cambio de contraseña con marca Trust Agent → sesión admin operativa.

### GAPS (con evidencia)

**GAP-10.1 — Login con Google punta a punta (AC-AUTH-006): NO VERIFICABLE por
dependencia externa (consistente con spec 015 §6.1)**.
- Evidencia: el contenedor `api` NO tiene `TRUST_GOOGLE_CLIENT_ID/SECRET`
  (fail-closed correcto por D-AUTH-02); el realm no tiene instancias de identity
  provider (`GET /admin/realms/qa-p2rev-223359/identity-provider/instances → []`);
  `org-config → google_login_enabled: false`; el botón "Sign in with Google" no se
  renderiza (DOM: `hasGoogleBtn: false`).
- El mecanismo (`ensure_google_idp`, auto-provisión D-AUTH-05, mapper `tenant_id`
  D-AUTH-03) está implementado y cubierto por tests, pero **punta a punta requiere
  credenciales Google Cloud Console del operador** — gap EXTERNO, no un defecto del
  código. Coincide con el estado "Converged" declarado en la spec (AC-AUTH-006
  explícitamente condicionado). **Ya convertido en tarea: t_f372f47f** (@dev,
  bloqueada por decisión humana: ¿credenciales o descope con enmienda a spec 015?),
  enlazada al gate t_e8a28fce.
- Observación menor de copy: la página `/register` promete *"or with your Google
  account from the sign-in screen"* aunque el botón no existe mientras el IdP no esté
  configurado — inconsistencia cosmética.

**GAP-10.2 (menor) — `users.mfa_enrolled` no se sincroniza tras el enrolamiento
TOTP inline**.
- Evidencia: `miller@…` enroló TOTP por el flujo inline (banner desapareció, sesión con
  acr=2) pero la columna sigue `mfa_enrolled=false` (SQL: solo se fija en el INSERT de
  provision/invite/register — `app/auth/register.py:132/190`, `invite.py:168`,
  `provision.py:344`; no hay UPDATE posterior). La spec 017 dejó fuera de alcance la
  edición del campo, pero el cambio de mecanismo de enrolamiento (D-AUTH-06: inline en
  vez de required action) dejó la columna sin sincronizar — el directorio puede mostrar
  "sin MFA" de un usuario que la tiene.

**GAP-10.3 (cosmético, YA CUBIERTO por tarea existente) — favicon del realm = caballo
de Keycloak (B4 / t_13f8b623)**.
- Evidencia: el HTML servido NO declara `rel="icon"` (grep del authorize → 0
  resultados) → el navegador hace fallback a `/favicon.ico` del host KC, que es el
  caballo de Keycloak. El tema `trust-agent` es CSS-only (`theme.properties` +
  `trust.css`, sin override de `template.ftl` como planeaba D-AUTH-01) y no trae
  `img/favicon.ico` propio.
- DESCARTADO como gap nuevo lo del emoji 🐴 en el `<title>` visto con el navegador de
  tooling: el título SERVIDO es limpio — verificado por curl:
  `<title>Sign in to Trust Agent</title>` (0 apariciones de Keycloak/emoji). El 🐴
  en `document.title` del harness es un artefacto del script CDP inyectado por la
  tooling de browser — mismo hallazgo que el run 219 de la tarea padre (t_1c65ff67),
  atrapado allí con MutationObserver. No es un gap del producto.
- El favicon residual ya está convertido en tarea: **t_13f8b623** (@dev), enlazada
  al gate t_e8a28fce.

---

## Cómo reproducirlo

1. Aprovisionar tenant: `POST /api/admin/tenants` con `Authorization: Bearer
   test-provider-token` (payload: organization_name, admin_email,
   initial_budget_usd=500, model_plan=["glm-5.2"], slug).
2. Suites: `cd /root/trust-agent/platform && .venv/bin/python -m pytest
   tests/test_departments_crud.py tests/test_roles_access.py tests/test_auth_ux.py -q`.
3. Smoke vivo: `.venv/bin/python scripts/smoke_roles_access.py`.
4. UI: navegador contra `http://127.0.0.1:3100/login` (org code del tenant) — flujos
   descritos arriba; screenshots `01/02/03-*.png` en este directorio.
5. SQL de audit: `docker exec trust-agent-db psql -U postgres -d trust_agent` con el
   `tenant_id` del tenant de evidencia.

## Artefactos
- `01-idp-login-themed.png` — login del IdP con tema Trust Agent (sin Google btn).
- `02-departments-page.png` — Departments con acciones CRUD por fila.
- `03-users-roles-page.png` — Users & Roles con roles/estados y acciones.
