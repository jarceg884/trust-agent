# Plan 007 — Dispositivos: enrolamiento de primera clase

Spec: `specs/007-devices/spec.md` · Base: `platform/` (spec 003) + `db/schema.sql` (spec 002) · Contratos: `docs/arquitectura.md` §2.2, ADR-006/013.

## 1. Arquitectura de la implementación

```
db/
├── schema.sql                          # + devices.enrollment_token_hash text (paridad)
└── migrations/versions/
    └── 20261023_0002_devices_enrollment_token.py   # ALTER TABLE devices ADD COLUMN

platform/app/devices/
├── tokens.py                           # (spec 003) device JWT HS256 pineado
├── gate.py                             # + update last_seen_at con throttle 60 s
├── service.py                          # NUEVO: enroll, exchange, renew, list,
│                                       #   approve/reject/revoke, audit emit
└── router.py                           # NUEVO: /api/devices/** (wired en main.py)

platform/tests/
└── test_devices_flow.py                # NUEVO: flujo completo + RBAC + aislamiento
```

## 2. Flujo de enrolamiento (secuencia normativa §2.2)

1. **Enroll**: `POST /api/devices/enroll {org_code, id_token, public_key, fingerprint, device_name?, platform?}`.
   - `verify_id_token` (JWKS, RS256/ES256, exp/aud/iss/tenant_id) → tenant del claim firmado.
   - MFA requerido (`mfa_evidence`: `amr` con `mfa`/`otp` O `acr` ≥ `TRUST_MFA_MIN_ACR`, D-MFA-01 spec 011) → si no, 403 `mfa_required`.
   - `org_code` == `tenants.slug` del tenant verificado → si no, 422.
   - Usuario por `oidc_subject`, `status='active'` → si no, 401.
   - `public_key` debe parsear como SPKI PEM (cryptography) → si no, 422.
   - Upsert por `(tenant_id, fingerprint)`: nuevo → INSERT pending; pending propio → nuevo enrollment_token; revoked/rejected propio → reset pending + nueva key (D-DEV-06); active propio → 409; ajeno → 409.
   - `enrollment_token = enr1.<tenant>.<device>.<secret>`; `sha256(secret)` → `enrollment_token_hash`.
   - Audit `device.enrolled`. Respuesta 202 `{device_id, status, enrollment_token}` (el token viaja UNA vez).
2. **Aprobación** (Admin Console): `POST /api/devices/{id}/approve` con `devices.manage` + scope dept_admin → `active`, `approved_at/by`, audit `device.approved`. El hash del enrollment_token SOBREVIVE para el canje.
3. **Canje** (desktop hace polling): `POST /api/devices/enroll/exchange {enrollment_token}` →
   - fila no existe / hash NULL / hash distinto → 410 `gone` (uniforme, sin oráculo);
   - `pending` → 202 `{device_id, status:"pending"}` (no consume);
   - `active` → consume hash (NULL) + `issue_device_token` → 200 `{device_token, device_id, status, expires_in}`;
   - `rejected`/`revoked` → 410.
4. **Renovación**: `POST /api/devices/{device_id}/token` (sesión + MFA + propietario + active) → device token fresco.
5. **Revocación**: `POST /api/devices/{id}/revoke` → en UNA transacción: `devices.status='revoked'`, hash NULL, `sessions.status='revoked' WHERE device_id`, audit `device.revoked`. El siguiente request → 401 (gate sin cache).

## 3. Transacciones y seguridad

- Toda operación de negocio: `engine.begin()` + `set_config('app.tenant_id', ..., true)` como `trust_app` (D-CP-02). RLS hace el aislamiento; cross-tenant → 404.
- El `enrollment_token` y el `device_token` jamás se persisten: solo `sha256(secret)` / JWT firmado. El enrollment_token aparece UNA vez en la respuesta 202 (ADR-006); grep de canarios en logs.
- `DeviceGate` (spec 003) sigue sin cache; añade `UPDATE devices SET last_seen_at=now() WHERE id=:id AND (last_seen_at IS NULL OR last_seen_at < now() - interval '60 seconds')`.
- Audit `emit()` en la MISMA transacción que el cambio de estado (hash chain atómico).

## 4. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Columna nueva rompe paridad schema.sql↔Alembic | La columna se añade al FINAL de la definición de `devices` en schema.sql (mismo orden que `ALTER TABLE ADD COLUMN`); test de paridad db/ en verde. |
| Test de versión de Alembic hardcodea `0001_initial` | Se actualiza al head `0002_devices_enrollment_token` (cambio documentado, D-DEV-11). |
| Canje como endpoint sin auth → oráculo de dispositivos | Token opaco 256 bits, respuesta uniforme 410 para desconocido/consumido/inválido; sin enumeración. |
| Write amplification del last_seen en el gate | Throttle: máx. 1 escritura/min por dispositivo. |
| Re-enrolamiento repetido como DoS de filas pending | Upsert por `(tenant_id, fingerprint)` — no se crean filas nuevas por reintento. |

## 5. Orden de implementación (tasks)

T1 spec → T2 plan → T3 migración+paridad → T4 service → T5 router+gate → T6 tests → T7 converge (suites + greps + live).
