# Spec 026 — Self-signup de organizaciones (mandato Miller)

## 0. Contexto y mandato

El Admin Console, visto al final, **ES el SaaS**: usuarios NUEVOS llegan a la
entrada pública y crean su cuenta (organización) por sí mismos. Esa cuenta nace
**SIN budget y APAGADA** (`pending_activation`). El **proveedor** la activa y le
asigna budget inicial; recién entonces todo funciona. El proveedor conserva el
flujo existente de provisioning (spec 004/014) para crear cuentas por su cuenta.

### Seam con spec 015 (auth UX, t_1844fb06) — NO duplicar
- **Spec 015 D-AUTH-04/07** = self-registration de **USUARIO dentro de una org
  existente** (`POST /api/auth/register`, gated por policy
  `allow_self_registration`). Queda intacto.
- **Esta spec (026)** = signup de **ORGANIZACIÓN NUEVA** (crea el tenant).
- La entrada pública distingue los dos caminos:
  - "Entrar a mi organización" → `/login` (baile OIDC) y "Join your
    organization" → `/register?org=` (spec 015, usuario dentro de org).
  - "Create your organization" → **`/signup`** (nuevo, esta spec).

## 1. Requisitos (EARS)

### REQ-SS-001 — Signup público de organización
El sistema DEBE exponer `POST /api/auth/signup-org` público: crea la
organización completa (realm KC real, schema de datos, admin user) en estado
`pending_activation`, con budget org 0¢. **ENMIENDA (spec 038 §6, D-38-01)**:
la org nace con **CERO departamentos** (fin del seed de 4 — causa raíz del
feedback Miller, t_b76456e5); la estructura real se declara post-activación
con el wizard "Organization structure" de spec 038. El resto del flujo queda
intacto.

- Body: `organization_name`, `slug` (opcional, default = slugify del nombre),
  `admin_email`, `admin_password` (≥ 12 caracteres, permanente, sin required
  actions — MFA opcional inline como spec 015), `admin_display_name`.
- Validaciones: slug `^[a-z0-9][a-z0-9-]{2,48}$` único (409 si existe), email
  válido, password ≥ 12, nombre no vacío.
- Google: el realm se crea con `ensure_google_idp` (fail-closed sin
  credenciales) igual que el provisioning — el admin puede luego entrar con
  Google si el proveedor las configuró.
- Rate-limited por IP (familia `ops.login_throttle`, clave `signup:ip:<ip>`);
  cada rechazo quema el contador.
- Flag `TRUST_SELF_SIGNUP_ENABLED` (default true) para que el proveedor pueda
  cerrar la puerta (403 `self_signup_disabled`).
- Audit: `tenant.self_registered` (actor = el admin que se registra), más los
  eventos de seed existentes (`department.created`, `user.created`,
  `role.assigned`, `budget.updated` con 0¢, `policy.updated`, `model.approved`).
- Respuesta 201: `{org, status: "pending_activation", message}` — **sin**
  credenciales (la contraseña la eligió el usuario; nada one-time que entregar).

### REQ-SS-002 — Tenant apagado = fail-closed (423)
Mientras `tenants.status = 'pending_activation'`, TODO endpoint del tenant
DEBE rechazar con **423 `tenant_pending_activation`** — sin agents, models,
devices, vault, spend, org, governance. Excepciones mínimas de ciclo de sesión:
`POST /api/auth/refresh` y `POST /api/auth/logout` (el usuario debe poder
salir). El login (mint de sesión) se PERMITE: el usuario entra y **ve su
estado** ("Your organization is pending activation by the provider") en una
pantalla dedicada del Admin Console — nada más. Suspensión (403
`tenant_suspended`) no cambia.

### REQ-SS-003 — Activación por el proveedor
El Provider Console DEBE mostrar los tenants `pending_activation` (badge +
filtro) y ofrecer **Activate** con **budget inicial en USD**.

- `POST /api/admin/tenants/{slug}/activate` (provider token +
  `tenants.provision`): body `{initial_budget_usd}` (> 0). Solo desde
  `pending_activation` (409 si ya active; suspended usa reactivate existente).
- UNA transacción: status → `active` + upsert del budget org + audit
  `tenant.activated` (payload previous/new/initial_budget_usd) + audit
  `budget.assigned` (payload level/amount_cents/period) + notificación.
- El proveedor sigue pudiendo suspender (existe) y ajustar budget después
  (existe, spec 012).

### REQ-SS-004 — Notificación al admin del cliente
Al activar, el sistema DEBE crear notificaciones (canal existente
`notifications`, type `tenant_activated`) para cada `org_admin` de la org, con
payload `{initial_budget_usd, activated_by}`.

### REQ-SS-005 — UI Admin Console
- `/signup` público (estándar UI pro): form org name + slug (auto-derivado,
  editable) + admin email + nombre + password; éxito → pantalla "Organization
  created — pending activation" con CTA a `/login?org=`.
- `/login`: link "Create your organization" → `/signup` (el link existente
  "Create your account" de spec 015 se re-etiqueta "Join your organization"
  para desambiguar el seam).
- Gate del layout: 423 `tenant_pending_activation` → pantalla dedicada
  `PendingActivationGate` (estado + sign out), no el shell.

### REQ-SS-006 — UI Provider Console
- Lista de tenants: badge "Pending" (warning) para `pending_activation` +
  filtro por estado; contador de pendientes.
- Detalle: si pending → panel "Activate" con input de budget inicial (USD) y
  confirmación; resultado visible (status Active + budget).

### REQ-SS-007 — E2E del journey completo
self-signup → tenant pending → login muestra estado bloqueado (423 en toda la
superficie) → provider activa + asigna budget → admin entra y todo funcional →
spend descuenta del budget asignado.

## 2. Decisiones

- **D-26-01**: estado nuevo `pending_activation` en `tenants.status` (CHECK
  extendido, migración 0015). El tenant se provisiona COMPLETO (realm, deptos,
  policies, modelos) — solo el status y el budget lo mantienen apagado.
- **D-26-02**: el fail-closed vive en `authenticate()` (auth/service.py):
  `pending_activation` → 423 `tenant_pending_activation`; `suspended` → 403
  `tenant_suspended` (igual que hoy). El mint de sesión NO comprueba status
  (igual que hoy para suspended): el usuario autentica y luego toda la
  superficie le responde 423 — el estado se ve, lo demás no funciona.
- **D-26-03**: `logout` usa una dependencia que tolera pending (el usuario
  debe poder cerrar su sesión); `refresh` no pasa por `authenticate()`.
- **D-26-04**: el signup reutiliza el orquestador de provisioning (provision.py)
  parameterizado (`status`, `amount_cents`, acción de audit de resumen, actor):
  cero duplicación del leg de KC/DB; compensación (borrado de realm) idéntica.
- **D-26-05**: la activación es UNA transacción en provider_service.py
  (status + budget + 2 audits + notificación) — no reusa
  `budgets.set_org_budget` porque exige Principal de tenant y audita
  `budget.updated`; el evento de negocio es `budget.assigned` (proveedor).
- **D-26-06**: rate limit reutiliza `ops.login_throttle` con familia de claves
  `signup:ip:<ip>` (sin tabla nueva; el conteo pre-auth es cross-tenant por
  naturaleza — mismo criterio D-DB-14).

## 3. Criterios de aceptación

| AC | Criterio |
|----|----------|
| AC-26-001 | `POST /api/auth/signup-org` válido → 201; tenant `pending_activation`, realm KC creado, **0 deptos (ENMIENDA spec 038 §6: fin del seed de 4 — la estructura se declara post-activación)**, org_admin creado, budget org 0¢, policies invariables, modelos approved; audit `tenant.self_registered` presente. |
| AC-26-002 | Slug duplicado → 409; payload inválido (email/password/slug/nombre) → 422; flag off → 403 `self_signup_disabled`; abusos → 429 (contador IP). |
| AC-26-003 | Con tenant pending: mint de sesión 201; `GET /api/me` → 423 `tenant_pending_activation`; endpoints de negocio (agents/models/devices/budgets/org/…) → 423; `POST /api/auth/logout` → 204; tenant suspended sigue → 403 `tenant_suspended`. |
| AC-26-004 | `POST /api/admin/tenants/{slug}/activate` con budget → 200; status `active`; budget org = monto; audit `tenant.activated` + `budget.assigned`; notificación `tenant_activated` a cada org_admin. |
| AC-26-005 | Activar un tenant ya active → 409; budget ≤ 0 → 422; tenant suspended → 409 (usar reactivate); sin provider token → 401. |
| AC-26-006 | Tras activar: el admin del tenant autentica y `GET /api/me` → 200; un evento de spend real descuenta del budget org (budget_counters) y el corte sigue funcionando. |
| AC-26-007 | UI Admin Console: `/signup` crea la org desde el navegador; el login de un tenant pending muestra la pantalla de pending activation (no el shell); `/login` ofrece los tres caminos del seam. |
| AC-26-008 | UI Provider Console: pendientes visibles con badge + filtro; Activate con budget desde el detalle → status Active + budget visible. |
| AC-26-009 | Journey E2E completo (REQ-SS-007) con evidencia real contra el stack vivo. |

## 4. Fuera de alcance
- Cobro/facturación del budget inicial (el proveedor lo asigna manualmente).
- Email de verificación del admin (no hay SMTP en el stack local; el realm KC
  es la fuente de verdad de la identidad).
- Cambios al self-registration de usuario de spec 015 (seam, no duplicar).
