# Plan técnico — Spec 025 (super-user del Provider Console)

## 1. Capa de datos (migración `0015_provider_superuser` + paridad `schema.sql`)

- `ops.provider_operators`: `id uuid PK`, `username text UNIQUE NOT NULL`,
  `password_hash text NOT NULL` (PHC argon2id), `status text CHECK
  (active|disabled) DEFAULT 'active'`, `must_change_password boolean NOT NULL
  DEFAULT true`, `totp_secret_enc bytea`, `totp_dek_enc bytea`,
  `totp_nonce bytea`, `totp_key_id text`, `totp_active boolean NOT NULL
  DEFAULT false`, `totp_enrolled_at timestamptz`, `created_at`, `updated_at`
  (trigger `set_updated_at` no aplica: está en `ops`, el DO-loop solo barre
  `public` → trigger explícito).
- `ops.provider_sessions`: `id uuid PK`, `operator_id uuid FK
  provider_operators`, `jti text UNIQUE` (sha256), `status CHECK
  (active|revoked|expired)`, `mfa_verified boolean`, `ip inet`, `user_agent`,
  `expires_at`, `last_seen_at`, `created_at`, `updated_at`.
- `ops.provider_audit`: `id bigserial PK`, `seq bigint`, `action text FK
  catalog.audit_actions`, `operator_id uuid`, `username text`, `ip inet`,
  `user_agent text`, `payload jsonb`, `ts timestamptz`, `hash_prev char(64)`,
  `hash char(64)`; trigger chain global (`pg_advisory_xact_lock` propia) +
  trigger de inmutabilidad + `provider_audit_verify_chain()`.
- Catálogo: 5 acciones `provider.*` en `catalog.audit_actions`.
- Sin RLS en las tres (plano proveedor, D-PS-01).

## 2. Backend (`platform/app/provider/`)

- `totp.py`: RFC 6238 (SHA-1, 6 dígitos, 30 s, ±1 ventana) con stdlib
  `hmac`; `generate_secret()` (160 bits base32), `verify()`
  (`hmac.compare_digest`), `otpauth_url()`.
- `passwords.py`: envoltura argon2-cffi (`hash_password`, `verify_password`
  → rehash si params viejos).
- `audit.py`: `emit()` a `ops.provider_audit` + `verify_chain()`.
- `operator.py` (servicio): `ensure_provider_operator()` (bootstrap
  idempotente con advisory lock + log one-time), `authenticate()` (throttle
  → argon2id → TOTP si activo → mint sesión), `lookup_session()`,
  `change_password()`, `enroll_start()/enroll_confirm()/disable_mfa()`,
  cifrado TOTP con `app/vault/crypto.py`.
- `router.py` (`/api/provider/…`): login/logout/me/password +
  mfa/enroll-start|enroll-confirm|disable. Registro en `app/main.py`.
- `app/auth/service.py::get_provider_principal`: (1) fail-closed si no hay
  operador, (2) Bearer `pst1…` → sesión, (3) break-glass deprecado si
  `provider_token` configurado. `Principal.via = "provider"` en ambos.
- `app/config.py`: `provider_operator_username` (default `provider-admin`),
  `provider_operator_initial_password` (default vacío → genera).
- Lifespan en `create_app()`: `ensure_provider_operator()` con try/except
  (log de error y sigue; login fail-closea después).
- `errors.py`: reason `password_change_required` (403) y
  `mfa_code_required` (401).

## 3. Frontend (`platform/provider-console/`)

- `lib/provider-session.ts`: cookie `ta_provider_session` (token `pst1…`).
- `auth/provider/login|logout/route.ts`: llaman al control plane; login
  devuelve `must_change_password` para redirigir.
- `login/login-form.tsx`: username + password (+ campo TOTP que aparece con
  `mfa_code_required`).
- `(provider)/layout.tsx`: gate con `GET /api/provider/auth/me`; si
  `must_change_password` → redirect `/change-password`.
- Páginas nuevas: `change-password` y `security` (estado MFA, banner,
  enrolamiento con secret one-time + verificación, desactivar).
- `shell.tsx`: nav "Security".

## 4. Tests (`platform/tests/test_provider_superuser.py`)

Con la BD efímera real del conftest + `TRUST_PROVIDER_OPERATOR_INITIAL_PASSWORD`
fijado en el fixture `env`: AC-PS-001…012 (login ok, 401+throttle, MFA
exigido, sesión sobrevive "reinicios" con `reset_engine()` + nueva app,
primer login fuerza cambio, fail-closed sin operador, vectores RFC 6238,
no-secretos en respuestas, cadena audit, inmutabilidad audit).

## 5. Verificación live (evidencia)

1. Reconstruir imagen (`docker compose build migrate api`) + `up -d`.
2. Reinicio del `api` con tabla vacía → capturar la OTA del log (una vez);
   segundo reinicio sin línea.
3. curl: login → cambio password → tenants → enroll MFA → login con TOTP
   (código generado con el secret) → login sin código → 401.
4. Reinicio del `api` → el mismo Bearer sigue vivo.
5. SQL: filas en `provider_operators`/`provider_sessions`/`provider_audit`
   + verificación de cadena.
6. Navegador real: login → change-password → Security → enrol → banner.
7. `docs/provider-superuser.md` con toda la evidencia.
