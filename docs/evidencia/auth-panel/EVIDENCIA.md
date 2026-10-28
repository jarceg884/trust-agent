# Evidencia — Auth UX: PANEL PROPIO (spec 015 v1.3.0, ADR-019) — t_3a4d8c60

**Fecha**: 2026-10-28 · **Stack**: real, nada simulado (docker compose local:
api :8000, console :3100, Keycloak 26.2.5 :8080, PostgreSQL 17 :55432).

## 1. Probes en vivo (pre-implementación) — decisiones verificadas

Ejecutados contra el Keycloak 26.2.5 real (realm desechable `probedg`):

| # | Hallazgo | Implicación de diseño |
|---|---|---|
| P1 | El flujo *direct grant* stock trae "Direct Grant - Conditional OTP" con "Condition - user configured" + OTP, pero emite `acr:"1"` incluso con OTP validado | Hay que cablear LoA para la evidencia MFA (D-AUTH-13) |
| P2 | Cableado `trust-direct-grant-loa` (una condición "LoA level 2" agregada al sub-flow Conditional OTP, user-configured conservado): sin OTP → `acr:"1"`; OTP faltante/malo → 401; OTP válido → **`acr:"2"`** | El gate `require_mfa` queda INTACTO (mismo `mfa_evidence`, acr≥2). El bug "LoA otorgado en skip" del browser flow NO reproduce en direct grant (fail-open descartado en vivo) |
| P3 | `requestedLoa` es siempre 0 en direct grant → una condición LoA con wrapper "Password LoA" evalúa false y desactiva el OTP (fail-closed que rompe el login) | El cableado correcto es SIN wrapper (P2) |
| P4 | Credenciales validadas ANTES del required-action check: password incorrecta → 401 "Invalid user credentials"; password correcta + acción pendiente → 400 "Account is not fully set up" | El 400 ES la prueba de conocimiento de la password actual (D-AUTH-15) |
| P5 | KC 26 SIN REST para crear credencial OTP o generar el secret (Account REST perdió `POST /account/totp`; Admin REST no crea credenciales OTP — verificado contra fuente 26.2.5) | El enrolamiento TOTP solo existe como required action → dance headless (D-AUTH-14) |
| P6 | El dance headless funciona: authorize (`acr_values=2`, PKCE) → login form → config-totp (QR PNG data-URI inline + `totpSecret` hidden) → POST del código → 302 con authorization code → canje → `acr:"2"` | El panel muestra el QR del producto; el estado vive en `ops.auth_dances` |
| P7 | **KC 26.2 rechaza el replay de código OTP dentro de la ventana de 30 s** (el código consumido por el dance no sirve para un direct grant en la misma ventana) | El confirm del panel canjea el authorization code del dance (no re-granta) — el diseño es inmune; los tests esperan la ventana siguiente |
| P8 | KC setea cookies `Secure` sobre http (127.0.0.1 es secure-context para el navegador, no para httpx) | El dance maneja cookies manualmente (jar propio server-side) |
| P9 | VERIFY_PROFILE se dispara con firstName/lastName vacíos y bloquea el direct grant ("Account is not fully set up" para usuarios SIN acciones) | El provisioning/invitaciones ya setean nombres; documentado |

## 2. Retro-fit de realms (AC-AUTH-012) — REAL, idempotente

`platform/scripts/apply_auth_ux_realms.py` (extendido con
`ensure_direct_grant_enabled` + `ensure_direct_grant_flow`):

- **Corrida 1**: `101 realm(s) updated, 0 failure(s)` — todos con
  `direct_grant=on flow=trust-direct-grant-loa loa=2`
  (log completo: `retrofit-run1.txt`, abajo).
- **Corrida 2 (idempotencia)**: `102 realm(s) updated, 0 failure(s)`
  (incluye el tenant del e2e creado entre ambas).

## 3. E2E con navegador real (AC-AUTH-011/013/014/015)

Tenant fresco provisionado vía la API del proveedor
(`panel-e2e-1793203917`, admin con password única temporal).
Navegador real (Chromium) contra el console :3100. **Todas** las URLs
visitadas fueron `127.0.0.1:3100/*` — cero navegaciones a Keycloak (:8080):

1. `/login` — panel propio: org + email + password (+ código OTP opcional).
   Login con la password única → **`/set-password`** (UPDATE_PASSWORD
   detectado por el estado del direct grant; la password quedó probada).
2. `/set-password` — panel de nueva password (sin re-pedir la única: el
   proof-handle lleva la prueba) → sesión mintada → **`/overview`** con el
   banner "Protect your account".
3. Banner → `/mfa-setup` — panel propio: confirmar password → **QR del
   producto** (PNG data-URI) → código TOTP → sesión **MFA-verificada** →
   banner fuera.
4. Menú de usuario → `/change-password` — cambio voluntario (password
   actual + código OTP) → dashboard.
5. Logout → `/login` → re-login con la password NUEVA + código OTP →
   **dashboard MFA-verificado**.

Screenshots: `01-login-panel.png`, `03-dashboard-after-set-password.png`,
`05-dashboard-mfa-verified.png`, `07-final-dashboard-relogin.png`.

### Verificación de estado tras el e2e (SQL + Admin REST + audit)

- `users.mfa_enrolled = true` para el admin (SQL directo).
- Credencial `otp` presente en Keycloak (Admin REST).
- Audit del tenant (cadena completa, SIN material de credencial):

```
auth.login_failed      | via=panel
auth.password_changed  | via=panel            (first_login=true)
auth.login             | via=panel | mfa=false
auth.login             | via=panel | mfa=true
auth.totp_enrolled     | via=panel
auth.login_failed      | via=panel            (cambio sin OTP — hint)
auth.password_changed  | via=panel            (voluntario)
auth.logout
auth.login             | via=panel | mfa=true (re-login pw nueva + OTP)
auth.logout
```

## 4. Matriz negativa (tests live-KC, `platform/tests/test_auth_panel.py`)

16/16 en verde contra el Keycloak real (realm `trust-agent-panel`):

- Password incorrecta → 401 genérico "Invalid email or password." —
  **sin oracle** (email desconocido responde EXACTAMENTE lo mismo).
- OTP faltante → 401 `mfa_code_required` **solo** si la cuenta tiene
  credencial OTP; cuentas sin OTP nunca reciben el hint.
- OTP incorrecto → 401 (mismo hint — el mensaje cubre ambos casos).
- 5 fallos → lockout progresivo → 429 `rate_limited` (ops.login_throttle,
  persistido en DB; el lock de cuenta no se salta "acertando a la 6ª").
- Proof-handle del set-password: single-use (reuso → 410), y el reset
  fail-closed si la required action ya no está pendiente.
- `totp/setup` en cuenta ya enrolada → 409 `already_enrolled`.
- QR con handle inválido/expirado → 410.
- Password nueva < 12 chars → 422.
- Audit `auth.password_changed` sin la password (assert literal).

## 5. Suites

- **db** (`db/tests`): **207 passed** (paridad schema.sql ↔ migración 0030
  verificada por pg_dump; RLS coverage actualizado con `ops.auth_dances`
  como plano proveedor sin RLS).
- **platform** (`platform/tests`): 662+ passed, `test_auth_panel.py` 16/16.
  Los errores transitorios de `test_vault_connectors` solo aparecen en la
  corrida completa y pasan aislados (14/14) — se investigó si eran míos:
  la suite sin `test_auth_panel.py` reproduce los mismos errores (ver
  §7), es un order-effect pre-existente del entorno compartido, no un
  regressión del panel.
- **e2e browser**: §3 (arriba) — el journey completo del mandato.

## 6. Guard AC-CP-026 (amendado por ADR-019)

`test_no_password_grant_in_app_code` sigue prohibiendo `grant_type=password`
en TODO el app code con UNA superficie sancionada: `app/auth/panel.py`
(la decisión de producto del direct grant). Cualquier otro módulo que lo
intente rompe la suite.

## 7. Notas honestas

- `test_vault_connectors` (14 errores psycopg) y 1 fallo esporádico en la
  corrida completa del platform suite: **pre-existentes/order-dependent**
  — reproducen con `--deselect platform/tests/test_auth_panel.py`; en
  aislamiento pasan 14/14. No tocados por esta card (mismo patrón que el
  hallazgo test_provision de t_8f716f9f).
- El título de la pestaña mostró "🐴" en la PRIMERA navegación del
  navegador del tool: verificado que el HTML servido (SSR) y el DOM tras
  reload están limpios; artefacto del harness del navegador, no del
  producto. El console no tiene favicon propio (404, gap cosmético
  pre-existente, fuera de alcance).
- Desktop (spec 010): mantiene su baile PKCE — documentado fuera de
  alcance en la enmienda v1.3.0.
