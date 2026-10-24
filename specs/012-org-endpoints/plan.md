# Plan técnico — Spec 012 Endpoints de organización

Referencia: spec.md (mismo directorio). Base: platform/ de spec 003, governance de spec 005, provisioning de spec 004.

## 1. Capa de datos (D-ORG-07)

- `db/schema.sql`: **sección 10** — `INSERT INTO catalog.audit_actions ('branding.updated') ON CONFLICT DO NOTHING`. Sin DDL nuevo.
- `db/migrations/versions/20261023_0005_org_endpoints.py`: embebe la sección 10 verbatim; `down_revision = "0004_governance"` (única cabeza).
- Paridad pg_dump intacta (INSERT de datos, no DDL).

## 2. Módulos

```
app/
├── org/                            # NUEVO — endpoints de organización (spec 012)
│   ├── __init__.py
│   ├── router.py                   # /api/users*, /api/branding, /api/departments
│   ├── directory.py                # list scoping (D-ORG-01), update, disable
│   ├── invite.py                   # enqueue + handler job "user.invite" (D-ORG-02)
│   ├── branding.py                 # GET/PUT con source derivado (D-ORG-04)
│   └── departments.py              # alta (D-ORG-06)
├── governance/
│   ├── agents.py                   # + list_grants (D-ORG-05)
│   └── router.py                   # + GET /api/agents/{agent_id}/grants
├── jobs/worker.py                  # importa app.org.invite (registra el handler)
├── tenants/keycloak_admin.py       # + alias ensure_user_with_one_time_password
└── main.py                         # + org_router
```

## 3. Contratos internos clave

- `directory.list_users(conn, principal)`: una query con `LEFT JOIN user_roles` agregando roles en Python; scope org_admin=TRUE / dept_admin=`department_id = ANY(:scope)` / user=`id = :uid`.
- `directory.disable_user`: `UPDATE users SET status='disabled'` + `UPDATE sessions SET status='revoked' WHERE user_id AND status='active'` + `UPDATE devices SET status='revoked' WHERE user_id AND status IN ('pending','active')` en la MISMA tx + audit solo en la transición.
- `invite.enqueue_invite(conn, principal, ...)`: audit `user.invited` en la tx del request; `jobs.service.enqueue(..., job_type="user.invite", idempotency_key=f"invite:{email}")` como ÚLTIMA operación (si enqueue falla, el rollback se lleva el audit).
- `invite.handle_invite(conn, job)`: lee `tenants.oidc_realm` en el scope; `_keycloak()` (factory monkeypatcheable en tests) crea el usuario KC (one-time password + `CONFIGURE_TOTP` + atributo `tenant_id`); inserta `users` (status `'active'`, como el org_admin de provisioning) + `user_roles`; idempotente por email (retry seguro); audit `user.created`+`role.assigned` con `actor_role="system"`, `actor_id=payload["invited_by"]`; result `{user_id, email, one_time_password, instructions}`.
- `branding.get_branding(conn)`: `source="provider"` si jsonb vacío **o == PROVISION_DEFAULT** (comparación canónica de las 4 claves); mapeo legacy `primary_color`→`accent_color`.
- `branding.put_branding`: validaciones pydantic + regex; merge sobre el jsonb existente preservando claves desconocidas; audit `branding.updated`.
- `agents.list_grants`: gate `_can_manage` → 404; solo `revoked_at IS NULL`.

## 4. Doble de tests (declarado)

- Keycloak Admin REST en la suite unitaria: **doble declarado** — el handler usa la factory `app.org.invite._keycloak()`; los tests la reemplazan por un fake con el mismo contrato (`ensure_user_with_one_time_password(realm, ...) -> (uuid, one_time_password)`). El E2E contra Keycloak real (127.0.0.1:8080) se ejerce en el smoke live del console (invitación real → login real). Mismo patrón que el IdP mock de spec 003 y LiteLLM fake de spec 005 (D-GOV-15).

## 5. Activación del console (verificación de extremo a extremo)

- `platform/console/scripts/smoke_authorized.py`: los dos checks de degradación honesta (`/users`, `/branding` → 404) se actualizan a **200 con datos reales** (el contrato ya existe; el estado 404 era el placeholder de D-AC-06).
- Sin cambios de frontend: `types.ts` §"Contratos PENDIENTES" ya tipa estos shapes (verificación: build del console sigue limpio).
