# Tasks 007 — Dispositivos: enrolamiento de primera clase

Spec: `specs/007-devices/spec.md` · Plan: `specs/007-devices/plan.md`

- [x] T1. Spec 007 (specify): requisitos EARS REQ-DEV-001…010 + ACs + decisiones D-DEV-01…11.
- [x] T2. Plan técnico (secuencia, transacciones, riesgos).
- [x] T3. Evolución de capa de datos: migración `0003_devices_enrollment_token` (ALTER TABLE devices ADD COLUMN enrollment_token_hash text; re-apilada sobre `0002_login_throttle` de la lane hermana) + paridad en `db/schema.sql` + test de versión de Alembic calcula el head con `alembic heads`. (AC-DEV-001/007 vía infra)
- [x] T4. `app/devices/service.py`: enroll (verificación JWKS+MFA, org_code, SPKI, upsert por fingerprint, enrollment_token one-time con hash), exchange (202/200/410 uniforme), renew, list/detail con scope, approve/reject/revoke con invalidación de sesiones y audit emit. (AC-DEV-001/001a/002/004/005/007/008/009)
- [x] T5. `app/devices/router.py` (9 rutas del contrato §4) + `last_seen_at` con throttle en `gate.py` + wire en `main.py`. (AC-DEV-003/010)
- [x] T6. `tests/test_devices_flow.py`: flujo completo e2e (enroll→approve→exchange→gate→revoke→401), negativos (org_code, MFA, pubkey, 409s, 410s), RBAC (org_admin/dept_admin/user), aislamiento cross-tenant, audit con device_id, last_seen. (todos los AC)
- [x] T7. Converge: suite platform/ verde 149/149 (spec 003 intacta + devices), suite db/ verde 171/171 (paridad + head `0003`), greps de seguridad limpios, arranque live uvicorn + smoke del flujo completo (SMOKE PASS), trazabilidad REQ↔AC.
