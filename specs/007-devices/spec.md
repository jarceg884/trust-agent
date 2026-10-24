# Spec 007 — Dispositivos: enrolamiento de primera clase, aprobación y revocación

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-23 |
| **Autor** | dev (tarea t_256e5e87) |
| **Padre** | specs/001-platform/spec.md §4.7 (REQ-DEV), docs/arquitectura.md §2.2/ADR-013/ADR-006 (contratos NORMATIVOS), specs/002-data-layer/spec.md (schema físico), specs/003-control-plane/spec.md (device token + DeviceGate ya entregados) |
| **Idioma** | Español (docs) / inglés (código, UI copy) / USD centavos (montos) |

---

## 1. Alcance

Esta spec define el **flujo de enrolamiento de dispositivos de primera clase** (ADR-013): el desktop genera un par de claves local, solicita enrolamiento, un admin aprueba desde la consola, el servidor liga dispositivo↔usuario↔org y emite el device token firmado. **Nada de scraping** (lección C5): el control plane jamás parsea HTML de ningún sistema; la identidad del dispositivo la aporta el propio desktop con su clave pública.

**Dentro de alcance**:

- `POST /api/devices/enroll` — solicitud de enrolamiento con `id_token` verificado vía JWKS (con MFA), `org_code`, `public_key` (SPKI PEM) y `fingerprint`; crea `devices(status=pending)` ligado a (usuario, tenant) y devuelve un `enrollment_token` one-time.
- `POST /api/devices/enroll/exchange` — canje one-time del `enrollment_token` por el device token firmado cuando el admin aprobó (segundo uso → 410, ADR-006); sirve de polling mientras `pending`.
- `POST /api/devices/{device_id}/token` — renovación del device token con sesión + MFA + propiedad (el TTL de 15 min exige renovación).
- Admin Console: `GET /api/devices` (vista REQ-DEV-005 con usuario, departamento, estado, fechas, último acceso y contador de sesiones), `GET /api/devices/{id}`, `POST /api/devices/{id}/approve`, `POST /api/devices/{id}/reject`, `POST /api/devices/{id}/revoke` — permiso `devices.manage`, scope de `dept_admin` limitado a sus departamentos.
- Revocación con **invalidación inmediata**: `status=revoked` + revocación de las sesiones del dispositivo en la misma transacción (REQ-DEV-004 ≤ 60 s por construcción).
- `GET /api/devices/me` — dispositivos propios del usuario (dashboard del desktop).
- `last_seen_at` por dispositivo, actualizado por `DeviceGate` con throttle.
- Integración con `audit_log`: `device.enrolled` / `device.approved` / `device.revoked` con `device_id` (REQ-DEV-006).
- **Evolución de la capa de datos**: columna `devices.enrollment_token_hash` (migración Alembic `0003_devices_enrollment_token` — re-apilada sobre `0002_login_throttle` de la lane hermana de rate limiting, única cabeza; paridad `schema.sql` ↔ migración intacta, D-DB-12).

**Fuera de alcance**:

- UI del desktop y del Admin Console (→ spec 010-desktop-enrollment, t_d3768316).
- Certificados de dispositivo gestionados por MDM (ADR-013: fuera de v1).
- Cache de estado del dispositivo ≤ 30 s (D-CP-13: sin cache en v1; la revocación es inmediata).
- El device token JWT y el `DeviceGate` en sí (ya entregados por spec 003, D-CP-13); esta spec solo añade la actualización de `last_seen_at` al gate.

## 2. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| D-DEV-01 | **Primera clase, sin scraping**: el desktop genera un keypair asimétrico local y envía la `public_key` (PEM SubjectPublicKeyInfo) + `fingerprint` (huella estable p. ej. hostname/OS). El servidor valida que la clave parsea como SPKI (cualquier algoritmo aceptado por `cryptography`); jamás consulta HTML de Keycloak ni de ningún otro sistema (C5). | ADR-013, lección C5 |
| D-DEV-02 | **`org_code` requerido en el enrolamiento, pero NO es raíz de confianza**: el código de la org (`tenants.slug`) viaja en el body para desambiguar el realm en el desktop; el tenant SIEMPRE sale del claim firmado `tenant_id` del `id_token` (D-CP-03). `org_code` ≠ slug del tenant verificado → 422 `validation_failed`. | tarea t_256e5e87, D-CP-03 |
| D-DEV-03 | **`enrollment_token` opaco one-time**: `enr1.<tenant_id>.<device_id>.<secret>` (secreto aleatorio 256 bits); en DB solo `sha256(secret)` en la columna nueva `devices.enrollment_token_hash`. Canje único: el canje que emite device token consume el hash (`NULL`); segundo uso → 410 `gone` sin oráculo (fila inexistente, hash consumido y hash distinto responden igual). `reject`/`revoke` limpian el hash; `approve` lo conserva para el canje. | ADR-006, D-CP-04 (patrón de token opaco con hash) |
| D-DEV-04 | **El canje es el polling del desktop**: `pending` → 202 `{device_id, status:"pending"}` (NO consume); `active` + hash válido → 200 `{device_token, expires_in}` (consume); hash ausente/inválido → 410; `rejected`/`revoked` → 410 (el token de enrolamiento muere con el dispositivo). | ADR-013 §2.2, ADR-006 |
| D-DEV-05 | **Renovación de device token**: `POST /api/devices/{device_id}/token` con sesión + MFA + propiedad del dispositivo + `status=active` → device token fresco. Necesaria porque el TTL es corto (15 min) y el canje one-time ya se consumió; también cubre la pérdida del token tras un canje (fallback con sesión). | D-CP-13 |
| D-DEV-06 | **Re-enrolamiento soportado** (pérdida del keystore, ADR-013): fingerprint con `revoked`/`rejected` → reset a `pending` con la nueva `public_key` y un `enrollment_token` nuevo (mismo `device_id`, audit `device.enrolled`); fingerprint `active` del mismo usuario → 409 `conflict`; fingerprint ocupado por OTRO usuario → 409 `conflict`. | ADR-013 |
| D-DEV-07 | **Revocación sincrónica e inmediata**: `revoke` hace `UPDATE devices SET status='revoked'` + `UPDATE sessions SET status='revoked' WHERE device_id=...` + audit en LA MISMA transacción; el siguiente request con ese device token → 401 `device_not_authorized` (sin cache, D-CP-13). REQ-DEV-004 (≤ 60 s) se cumple por construcción. | REQ-DEV-004, D-CP-13 |
| D-DEV-08 | **RBAC de administración**: permiso `devices.manage` (org_admin + dept_admin, matriz §2.1 en datos). `dept_admin` solo administra dispositivos de usuarios de SUS departamentos (fuera de scope → 404, D-CP-09); cross-tenant → 404. La aprobación registra `approved_by` + `approved_at`. | REQ-DEV-002, D-CP-08/09 |
| D-DEV-09 | **`last_seen_at` con throttle**: `DeviceGate` actualiza `devices.last_seen_at` solo si el valor almacenado tiene > 60 s (máx. una escritura por minuto por dispositivo; el gate sigue siendo lectura de estado en cada request). | REQ-DEV-005 |
| D-DEV-10 | **Audit con `device_id`**: `device.enrolled` (actor = usuario que enrola), `device.approved` (actor = admin que aprueba), `device.revoked` (reject y revoke); `actor_role` = rol del principal que actúa, `target_type='device'`, `target_id=device_id`, `device_id` en el evento (REQ-DEV-006). | REQ-AUD-003, catálogo `audit_actions` |
| D-DEV-11 | **Evolución de capa de datos por la línea canónica**: nueva columna `devices.enrollment_token_hash text` (nullable) vía migración Alembic `0003_devices_enrollment_token` en `db/migrations` (única línea, D-CP-11; re-apilada sobre `0002_login_throttle` — lane hermana t_4da60ada — para mantener una única cabeza) + edición de `db/schema.sql` manteniendo la paridad pg_dump (test db/tests/test_schema_parity.py en verde). El test de versión de Alembic calcula el head con `alembic heads` (sin hardcodear). | D-DB-12, D-CP-11 |

## 3. Requisitos (formato EARS)

Trazabilidad con spec 001 §4.7 (REQ-DEV-001…006); los requisitos nuevos amplían la serie.

**REQ-DEV-001** (M) [spec 001, I5] — Cuando un usuario autenticado con MFA solicita enrolar un dispositivo (keypair local + fingerprint + org_code), el sistema verifica el `id_token` vía JWKS, valida que `org_code` coincida con el slug del tenant del claim firmado, valida la clave pública, y crea/actualiza una fila `devices` con `status='pending'` ligada a (usuario, tenant), devolviendo `device_id`, `status` y un `enrollment_token` one-time.
> **AC-DEV-001**: enroll con JWT válido con evidencia MFA (`amr` con `mfa`/`otp` **O** `acr` ≥ `TRUST_MFA_MIN_ACR` — D-MFA-01, spec 011; Keycloak 26 no emite `amr`) + org_code correcto + SPKI PEM válido → 202 `{device_id, status:"pending", enrollment_token}`; existe fila `devices(status='pending')` con `user_id`/`tenant_id`/`fingerprint`/`public_key`; evento `device.enrolled` en `audit_log` con `device_id`.

**REQ-DEV-001a** (M) [D-DEV-02] — Cuando el `org_code` no coincide con el tenant del claim firmado, o el JWT no trae MFA, o la clave pública no parsea, el sistema rechaza el enrolamiento con 422 / 403 sin crear fila ni emitir token.
> **AC-DEV-001a**: org_code equivocado → 422 `validation_failed` y 0 filas nuevas; JWT sin evidencia MFA (sin `amr` con `mfa`/`otp` y `acr` < `TRUST_MFA_MIN_ACR` — D-MFA-01, spec 011) → 403 `mfa_required`; `public_key` inválida → 422; JWT con firma inválida → 401 `invalid_token`.

**REQ-DEV-002** (M) [spec 001, I5] — Cuando un admin con permiso `devices.manage` aprueba un dispositivo `pending` de su scope, el sistema lo pasa a `active` registrando `approved_at`/`approved_by` y emite (en el canje) un device token firmado; si lo rechaza o revoca, pasa a `rejected`/`revoked`, pierde el `enrollment_token` y todo acceso.
> **AC-DEV-002**: approve sobre pending → 200 `{status:"active"}` con `approved_at`/`approved_by` persistidos y evento `device.approved`; reject sobre pending → 200 `{status:"rejected"}` y evento `device.revoked`; approve sobre no-pending → 409 `conflict`.

**REQ-DEV-003** (M) [spec 001] — Cuando un dispositivo no enrolado, pendiente, rechazado o revocado envía una petición a una ruta con `DeviceGate`, el sistema la rechaza con 401 `reason=device_not_authorized` (ya implementado en spec 003; esta spec añade `last_seen_at`).
> **AC-DEV-003**: request con device token de dispositivo `active` → 200 y `devices.last_seen_at` avanza (throttle 60 s); dispositivo revocado → 401 en el request siguiente.

**REQ-DEV-004** (M) [spec 001] — Cuando se revoca un dispositivo con sesiones activas, el sistema invalida esas sesiones en la misma transacción (efectivo de inmediato, ≤ 60 s).
> **AC-DEV-004**: sesión activa ligada al dispositivo → tras `revoke` su `status='revoked'` en DB y su token recibe 401; el device token del dispositivo recibe 401 `device_not_authorized`.

**REQ-DEV-005** (M) [spec 001, I9, MOC-ADMIN "Devices"] — Cuando un admin consulta la vista Devices, el sistema lista los dispositivos de su scope con: usuario (email/nombre), departamento, estado, fecha de enrolamiento/aprobación, último acceso y contador de sesiones activas; filtros por estado; `dept_admin` solo ve los de sus departamentos.
> **AC-DEV-005**: org_admin lista todos los del tenant; dept_admin solo los de sus deptos (dispositivo de otro depto → ausente de la lista y 404 en detail); user sin `devices.manage` → 403; cross-tenant → 404/ausente.

**REQ-DEV-006** (M) [spec 001, I9] — Cuando se registra un evento de audit del flujo de dispositivos, el sistema incluye `device_id`, el actor y su rol.
> **AC-DEV-006**: tras el flujo completo existen eventos `device.enrolled`, `device.approved`, `device.revoked` con `device_id` no nulo y `actor_id` del principal actuante.

**REQ-DEV-007** (M) [ADR-006, D-DEV-03/04] — Cuando el desktop canjea el `enrollment_token`, el sistema responde según estado: `pending` → 202 sin consumir; `active` + hash válido → 200 con device token firmado (consume el hash); hash consumido/inválido o dispositivo `rejected`/`revoked` → 410 `gone`; nunca revela cuál fue la causa.
> **AC-DEV-007**: canje en pending → 202 `{status:"pending"}` (repetible); tras approve, canje → 200 `{device_token}`; segundo canje → 410 `gone`; canje con token de otro dispositivo → 410; el device token canjeado pasa el `DeviceGate`.

**REQ-DEV-008** (M) [D-DEV-05] — Cuando el propietario de un dispositivo `active` con sesión MFA solicita renovar su device token, el sistema emite uno nuevo sin consumir nada one-time.
> **AC-DEV-008**: POST token con sesión del propietario → 200 `{device_token}` válido para el gate; dispositivo ajeno → 404; dispositivo no-active → 409 `conflict`; sin MFA → 403.

**REQ-DEV-009** (M) [D-DEV-06, ADR-013] — Cuando un usuario re-enrola un fingerprint previamente `revoked`/`rejected`, el sistema resetea el dispositivo a `pending` con la clave nueva y un `enrollment_token` nuevo; si el fingerprint está `active` de su mismo usuario → 409; si pertenece a otro usuario → 409.
> **AC-DEV-009**: re-enroll tras revoke → 202 pending con nuevo enrollment_token y `public_key` actualizada; re-enroll de fingerprint active → 409; fingerprint de otro usuario → 409.

**REQ-DEV-010** (M) [spec 001 US-USR, D-DEV-05] — Cuando un usuario consulta sus dispositivos, el sistema devuelve solo los suyos (id, nombre, plataforma, fingerprint, estado, último acceso).
> **AC-DEV-010**: `GET /api/devices/me` con sesión de carol → solo dispositivos de carol; los de alice ausentes.

## 4. Contrato de API

| Método + ruta | Auth | Permiso | Efecto |
|---|---|---|---|
| `POST /api/devices/enroll` | id_token en body (JWKS + MFA) | — | 202 pending + `enrollment_token` one-time |
| `POST /api/devices/enroll/exchange` | `enrollment_token` en body | — | 202 pending \| 200 device token \| 410 |
| `POST /api/devices/{device_id}/token` | sesión Bearer + MFA | propietario | 200 device token renovado |
| `GET /api/devices/me` | sesión + MFA | — | dispositivos propios |
| `GET /api/devices?status=&department_id=` | sesión + MFA | `devices.manage` | lista con métricas (REQ-DEV-005) |
| `GET /api/devices/{device_id}` | sesión + MFA | `devices.manage` | detalle (fuera de scope → 404) |
| `POST /api/devices/{device_id}/approve` | sesión + MFA | `devices.manage` | pending → active |
| `POST /api/devices/{device_id}/reject` | sesión + MFA | `devices.manage` | pending → rejected |
| `POST /api/devices/{device_id}/revoke` | sesión + MFA | `devices.manage` | → revoked + sesiones revocadas |

Errores: envelope §5.2 con reasons existentes (`validation_failed`, `mfa_required`, `invalid_token`, `permission_denied`, `not_found`, `conflict`, `gone`, `device_not_authorized`). Sin oráculos: cross-tenant/cross-scope → 404; canje inválido → 410 uniforme.

## 5. Checklist de convergencia

- [x] Suite platform/ en verde (spec 003 intacta + suite nueva de devices) sobre PostgreSQL 17 real. **Evidencia: 149/149 passed** (incluye `tests/test_devices_flow.py`, 18 tests nuevos).
- [x] Suite db/ en verde (paridad schema.sql↔migración con la columna nueva; test de versión calcula el head con `alembic heads` → `0003_devices_enrollment_token`). **Evidencia: 171/171 passed.**
- [x] Flujo completo ejercitado por test e2e: enroll → pending → approve → exchange → gate OK → revoke → 401 inmediato + sesiones invalidadas. **Evidencia: `test_full_flow_enroll_approve_exchange_revoke` + smoke live sobre uvicorn real (SMOKE PASS).**
- [x] Audit verificado: eventos `device.enrolled/approved/revoked` con `device_id` y cadena íntegra (`audit_verify_chain` → ok).
- [x] Aislamiento multi-tenant verificado (tenant B no ve ni aprueba dispositivos de tenant A → 404/ausente).
- [x] RBAC verificado (`devices.manage`: org_admin sí, user no → 403; dept_admin acotado a sus deptos → 404 fuera de scope).
- [x] Greps de seguridad limpios (sin `verify=False`; el único `grant_type=password` del repo es el bootstrap admin-cli del lado proveedor de la spec 004, permitido por el constitution IV; secreto one-time jamás persistido en claro — verificado por test).
- [x] Trazabilidad REQ↔AC completa y tasks.md cerrado.
