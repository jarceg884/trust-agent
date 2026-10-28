# Super-user del Provider Console (spec 025, mandato Miller)

El Provider Console ya NO autentica con un secreto estático de entorno: existe
un **operador de proveedor** (super-user) quemado en la base de datos, con
usuario + contraseña y MFA TOTP opcional una vez adentro. Es el **único actor
del sistema fuera del multi-tenant** (sin `tenant_id`, plano `ops`, sin RLS
por diseño — D-DB-01, misma razón que `ops.login_throttle`).

Spec: `specs/025-provider-superuser/spec.md` · Migración:
`db/migrations/versions/20261026_0016_provider_superuser.py` (paridad
`db/schema.sql` §15).

## Modelo

| Objeto | Rol |
|---|---|
| `ops.provider_operators` | El operador: username único, password **argon2id** (cadena PHC — jamás plaintext), `status` active/disabled, `must_change_password` (required action del primer login), secret TOTP **cifrado en reposo** (envelope AES-256-GCM del vault, KEK fuera de la BD) nullable. |
| `ops.provider_sessions` | Sesiones en PostgreSQL (misma infra que `public.sessions`): token opaco `pst1.<id>.<secret>`, en BD solo `sha256(secret)` (`jti`), `mfa_verified`, `expires_at`. Nada en memoria → **sobreviven reinicios del API**. |
| `ops.provider_audit` | Audit append-only del plano proveedor: hash chain GLOBAL + trigger que rechaza UPDATE/DELETE. Eventos: `provider.login`, `provider.login_denied`, `provider.password_changed`, `provider.mfa_enrolled`, `provider.mfa_disabled` (FK a `catalog.audit_actions`). |

## Primer arranque (patrón OTA)

El bootstrap del API (lifespan) seedea el operador `provider-admin`
(`TRUST_PROVIDER_OPERATOR_USERNAME`) si no existe y loguea la contraseña
one-time **EXACTAMENTE una vez** en el log de arranque:

```
PROVIDER OPERATOR ONE-TIME PASSWORD (shown ONLY once, at bootstrap):
username=provider-admin password=… — sign in and change it immediately
(first login forces the change).
```

Para regenerarla (p. ej. entorno de test): borrar las filas del operador y
reiniciar el API — el bootstrap re-seedea con una OTA nueva.

- El primer login **fuerza el cambio de contraseña** (required action,
  D-PS-08): el login mints sesión, pero toda ruta de negocio responde
  `403 password_change_required` hasta completar
  `POST /api/provider/auth/password` (nueva ≥ 12 chars; revoca las demás
  sesiones).
- **Fail-closed** (REQ-PS-010): sin operador seedeado, TODA autenticación
  provider (sesión y break-glass) responde 503. Recuperación: reiniciar el
  API (re-seedea).

## Login y MFA

1. `POST /api/provider/auth/login` con `username` + `password`
   (throttle persistido en `ops.login_throttle`, claves
   `acct:provider:<sha256(username)>` e `ip:<ip>` — 5 fallos/15 min → 429).
2. Si el operador tiene MFA activa, el login exige `totp_code`: sin código →
   `401 mfa_code_required` (prompt de UX, no cuenta como fallo); código
   malo → 401 + throttle + `provider.login_denied`.
3. La sesión viaja al navegador SOLO dentro de la cookie **httpOnly**
   `ta_provider` que fija el BFF del Provider Console — el token jamás
   llega a JS.
4. Sección **Security** (`/security`): banner que sugiere MFA siempre
   (mientras no esté activa), enrolamiento TOTP en dos pasos — secret
   one-time + `otpauth://` (`POST /api/provider/mfa/enroll-start`) y
   **verificación con un código real antes de activar**
   (`/mfa/enroll-confirm`) — y desactivación que exige password + código
   (`/mfa/disable`). RFC 6238 (SHA-1, 6 dígitos, 30 s, ventana ±1),
   verificado contra los vectores del RFC.

## `provider_token`: DEPRECADO — solo break-glass (D-PS-06)

El bearer estático `TRUST_PROVIDER_TOKEN` **ya no es el camino de la UI**;
queda como break-glass documentado para scripts de emergencia
(`trust-provision`, evidence scripts) y SOLO funciona si
`TRUST_PROVIDER_TOKEN` está explícitamente configurado en el entorno. No
está sujeto al required action de cambio de contraseña, pero sí al
fail-closed (sin operador seedeado → 503). La UI del Provider Console usa
exclusivamente usuario+contraseña(+TOTP).

Variables nuevas: `TRUST_PROVIDER_OPERATOR_USERNAME` (default
`provider-admin`), `TRUST_PROVIDER_OPERATOR_INITIAL_PASSWORD` (opcional —
pinea la inicial en vez de generarla; para entornos desatendidos).

## Evidencia de verificación en vivo (2026-10-26, BD live 0017)

Flujo real ejecutado contra `trust-agent-api` + `trust-agent-db`
(contenedores de `docker-compose.yml`), sin nada simulado:

| # | Comprobación | Resultado real |
|---|---|---|
| 1 | Borrado del operador + reinicio del API → bootstrap re-seedea | Log OTA una sola vez; fila `provider-admin` con `password_hash` argon2id (`$argon2id…`), `must_change_password=t` |
| 2 | Login con OTA | `200`, `must_change_password: true`, token `pst1.3890f253-…` |
| 3 | Ruta de negocio con required action pendiente | `403 password_change_required` |
| 4 | Password malo | `401 invalid_token` (sin oracle: mismo error para usuario inexistente) |
| 5 | Cambio de password | `200 {"ok":true,"must_change_password":false}` |
| 6 | Re-login con la nueva + ruta de negocio | `200` / `200` |
| 7 | `mfa/enroll-start` | `200` secret base32 one-time + `otpauth://` |
| 8 | Confirm con código inventado | `422 validation_failed`, `totp_active` sigue false |
| 9 | Confirm con código real (RFC 6238) | `200 {"ok":true,"mfa_enrolled":true}` |
| 10 | Login sin código con MFA activa | `401 mfa_code_required` |
| 11 | Login con código malo | `401` |
| 12 | Login con código real | `200`, sesión con `mfa_verified=true` |
| 13 | `docker compose restart api` → misma sesión | `GET /auth/me` → `200` (la sesión vive en PostgreSQL) |
| 14 | SQL: audit + throttle | `provider.login`×5, `provider.login_denied`×4, `provider.mfa_enrolled`×1, `provider.password_changed`×1; 2 filas `acct:provider:%` en `ops.login_throttle` |
| 15 | `UPDATE ops.provider_audit …` | `ERROR: ops.provider_audit is append-only: UPDATE is prohibited` |
| 16 | BFF `:3200/security` sin sesión | `307 → /login` |
| 17 | BFF `:3200/security` con cookie de sesión | `200`, sección "Two-factor authentication" + badge "MFA active" |
| 18 | Break-glass `TRUST_PROVIDER_TOKEN` | `200` contra `/api/admin/tenants` (deprecado, documentado) |
| 19 | Throttle en vivo | `429 rate_limited` al acumular fallos (verificado dos veces: lockout progresivo 30 s desde el 5.º fallo, contador en `ops.login_throttle` — claves `acct:provider:*` e `ip:127.0.0.1` con `locked_until`) |
| 20 | Flujo completo vía BFF (`:3200`) | login OTA → `200` + `Set-Cookie: ta_provider=pst1…; HttpOnly; SameSite=Lax`; cambio de password vía proxy con cookie → `200`; enroll-start/confirm vía proxy → `200`; login password+TOTP vía BFF → `200` + cookie httpOnly nueva; `/security` renderiza "MFA active" con la cookie del BFF; disable (password+código) → `200`; logout → `200` |

**Estado final del entorno dev**: operador `provider-admin` activo, password
cambiada durante la verificación, MFA desactivada (estado limpio). Para una
OTA fresca (p. ej. validación del gate QA): borrar las filas del operador y
reiniciar el API — procedimiento canónico arriba.

Tests: `platform/tests/test_provider_superuser.py` (25 tests, AC-PS-001…012,
incluye vectores RFC 6238 y fail-closed) + paridad `db/schema.sql`
(`db/tests/`, 182 en verde).
