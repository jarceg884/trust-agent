# Tasks 004 — Onboarding de tenant idiom-proof

Spec: `specs/004-tenant-onboarding/spec.md` · Plan: `specs/004-tenant-onboarding/plan.md`

- [x] T1. Spec 004 (specify): requisitos EARS + ACs + decisiones vinculantes (D-ONB-01…13).
- [x] T2. Plan técnico (estructura, flujo del orquestador, riesgos).
- [x] T3. Probe RLS: INSERT en `tenants` como `trust_app` con id pre-generado + `set_config` — validado con DB efímera real vía Alembic canónico (probe_rls.py; el INSERT pasa, audit en la misma tx asigna seq 1; setting ≠ id cae por InsufficientPrivilege, confirmando el WITH CHECK).
- [x] T4. `app/profiles/templates.py`: 4 plantillas REQ-PRF-001 + registro/validación. (AC-ONB-020/021)
- [x] T5. `app/config.py`: settings nuevos (provider_token, keycloak_admin_*, oidc_redirect_uris) fail-closed.
- [x] T6. `app/tenants/keycloak_admin.py`: cliente Admin REST (bootstrap admin-cli, realm/client/mapper/user-profile/usuario/password temporal, DELETE realm). (D-ONB-12/13)
- [x] T7. `app/tenants/provision.py`: orquestador idempotente con transacción DB única + compensación. (AC-ONB-001…007)
- [x] T8. `app/tenants/healthcheck.py`: verificación post-alta (10 checks). (AC-ONB-032)
- [x] T9. `app/auth/service.py` + `app/tenants/router.py`: principal provider + rutas `/api/admin/tenants*`. (AC-ONB-030/031)
- [x] T10. `app/tenants/cli.py` + console script `trust-provision` (D-ONB-01: nombre canónico; "harper" retirado por DECISIONES.md). (AC-ONB-008)
- [x] T11. Tests e2e: conftest (env provider/KC-admin), test_profiles_templates (4), test_provision (9: flujo completo, idempotencia, rollback, auth matrix + fail-closed, estado/healthcheck, aislamiento, no-fuga de secretos, CLI real con rollback). (todos los AC)
- [x] T12. Converge: suite completa `platform/tests/` **149 passed** (núcleo 003 + hermanas + 13 nuevas, 0 regresiones); suite `db/` **171 passed** (intacta); greps `verify=False`=0 y `grant_type=password` solo en el bootstrap admin-cli documentado; verificación live con proceso real (uvicorn :8000 + curl: provision → healthcheck ok 10 checks, re-run idempotente 200/created=false, 401 sin/mal token, y baile de login real que demuestra que el password one-time NO entrega code — redirige a required-action UPDATE_PASSWORD+CONFIGURE_TOTP); realms y DBs de test limpiados.
