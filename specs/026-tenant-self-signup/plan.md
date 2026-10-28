# Plan técnico — Spec 026

## Backend (platform/app)

1. **Migración 0015** (`db/migrations/versions/20261026_0015_tenant_self_signup.py`,
   down_revision = `0014_auth_ux_policies`):
   - `ALTER TABLE tenants DROP CONSTRAINT tenants_status_check` + re-CREATE con
     `('active','suspended','pending_activation')`.
   - INSERT `catalog.audit_actions`: `tenant.self_registered`,
     `tenant.activated`, `budget.assigned` (ON CONFLICT DO NOTHING).
   - Paridad `db/schema.sql` §5.1 (CHECK) y §3.5 (acciones).
2. **errors.py**: `tenant_pending_activation()` → 423 Locked, reason
   `tenant_pending_activation`.
3. **config.py**: `self_signup_enabled: bool = True` (TRUST_SELF_SIGNUP_ENABLED).
4. **auth/service.py**: `authenticate()` distingue pending (423) vs suspended
   (403). Nueva dependencia `get_principal_allow_pending` (logout).
5. **auth/router.py**: `POST /api/auth/signup-org` (público, rate-limited con
   clave `signup:ip:<ip>`; flag fail-closed 403 `self_signup_disabled`);
   `logout` pasa a `get_principal_allow_pending`.
6. **tenants/provision.py**: parameterizar `_provision_in_db` (status,
   amount_cents, summary_action, actor_role/actor_id) — defaults = comportamiento
   actual (cero cambio para spec 004/014).
7. **tenants/self_signup.py** (nuevo): `SelfSignupRequest` + `self_signup()`:
   validaciones → slug disponible (409) → KC (realm completo +
   `create_user_with_password`) → `_provision_in_db(status='pending_activation',
   amount_cents=0, summary_action='tenant.self_registered')` → compensación en
   fallo. Factory `_keycloak()` para el double de tests (patrón register.py).
8. **tenants/provider_service.py**: `activate_tenant(engine, migrator, slug,
   initial_budget_usd)` — UNA transacción: UPDATE status + upsert budget org +
   audit `tenant.activated`/`budget.assigned` + `notify_role(org_admin,
   'tenant_activated')`. `list_tenants` acepta filtro `status`.
9. **tenants/router.py**: `POST /api/admin/tenants/{slug}/activate` (body
   `initial_budget_usd`); `GET /api/admin/tenants?status=`.

## Frontend

10. **console**: `/signup` (page.tsx + signup-form.tsx, patrón register-form);
    `PendingActivationGate` (client, con sign out) + hook en
    `(console)/layout.tsx` para 423; link "Create your organization" en
    login-form.tsx + re-etiqueta "Join your organization".
11. **provider-console**: `StatusBadge` pending (warning) + filtro por estado
    en tenants page; panel Activate con budget en tenant-detail.tsx; types.

## Tests y evidencia

12. `platform/tests/test_tenant_self_signup.py` (AC-26-001..006,08 lógico) con
    double KC declarado + mock IdP (patrón test_auth_ux.py).
13. Script de evidencia viva `platform/scripts/evidence_tenant_self_signup.py`:
    journey REQ-SS-007 contra el stack real (signup → 423s → activate → login →
    spend) + screenshots del navegador para AC-26-007/008.
14. Rebuild de imágenes (api, console, provider-console, migrate) — el servicio
    migrate corre desde la imagen build-eada (regla 9).
