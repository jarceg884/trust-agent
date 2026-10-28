---
name: keycloak26-amr-mfa
description: "Use when a gated endpoint requires MFA evidence from a Keycloak-issued OIDC token. KC26 never emits `amr`; how to wire the realm so `acr` carries the LoA, the pitfalls of the Admin REST API, and how to verify it live."
version: 2.0.0
category: identity
---

# Keycloak 26 no emite `amr` — evidencia MFA vía `acr`/LoA (resuelto, D-MFA-01)

Hallazgo verificado en vivo (2026-10-23, spec 010/011): un login REAL de
Keycloak 26 produce id_token y access_token **sin claim `amr`** (solo
`acr:"1"` contra un realm por defecto), incluso con TOTP enrolado y
verificado. La puerta MFA del control plane acepta desde entonces DOS
señales (`platform/app/auth/mfa.py::mfa_evidence`, spec 011 D-MFA-01):

1. `amr` (OIDC estándar) con `mfa`/`otp` — lo emite el doble de tests y
   cualquier IdP que lo implemente.
2. `acr` numérico >= `TRUST_MFA_MIN_ACR` (pineado a 2, acoplado al mapa LoA).

## Reglas

1. **No asumir `amr` en tokens de Keycloak 26** — jamás lo emite. Cualquier
   puerta que solo mire `amr` rechaza (403 `mfa_required`) a TODO usuario
   real de KC26; los tests pasan solo si el doble lo emite.
2. **El wiring del realm lo produce `ensure_mfa_acr()`**
   (`platform/app/tenants/keycloak_admin.py`, llamado por el provisioning):
   atributo `acr.loa.map` = `{"1":0,"2":1}` + condición LoA nivel 1 en el
   sub-flujo "Conditional OTP" de una copia del flujo `browser`, ligada como
   `browserFlow`. Resultado: password+TOTP → `acr:"2"`; password-only →
   `CONFIGURE_TOTP` inline, sin code.
3. **Verificar con probe antes de culpar al cliente**: replicar el baile
   PKCE S256 por HTTP (patrón `tests/e2e/test_control_plane_flow.py::
   _pkce_login` o el probe del workspace de t_c43906d1) y decodificar AMBOS
   tokens. `amr:None, acr:"1"` = realm sin wiring; `acr:"2"` tras OTP = OK.

## Pitfalls del wiring por Admin REST (todos verificados en vivo, KC 26.2)

1. **El atributo de realm es `acr.loa.map`** (`Constants.ACR_LOA_MAP`), NO
   `acr-to-loa-map` (la etiqueta de la consola engaña). Un nombre erróneo se
   ignora EN SILENCIO y el `acr` pedido se interpreta como LoA numérico crudo
   → error "Forced level of authentication did not meet the requirements.
   Requested level: 2, Fulfilled level: 1".
2. **El flujo `browser` built-in no admite cambios** ("illegal to add
   execution to a built in flow"). Copiar con
   `POST /authentication/flows/browser/copy` body `{"newName": ...}`
   (el path `/authentication/copy` NO existe en KC26) y ligar la copia con
   `PUT /admin/realms/{realm}` `{"browserFlow": "<copia>"}`.
3. **Al agregar la ejecución LoA** (`POST /authentication/executions?flow=
   {subflowId}`): el campo es `authenticator` (no `provider` — 400), el
   `requirement` es obligatorio (sin él → 500 NPE) y `parentFlow` debe
   llevar el id del sub-flujo (sin él → 400).
4. **El config va envuelto**: `POST /authentication/executions/{id}/config`
   con `{"alias": "...", "config": {"loa-condition-level": "1",
   "loa-max-age": "36000"}}` (claves sueltas → 400).
5. **El listing de ejecuciones expone el config como `authenticationConfig`**,
   no `authenticatorConfig` — leer el nombre equivocado hace creer que el
   config no se aplicó.
6. **CRÍTICO — eliminar la condición copiada "Condition - user configured"**
   (`DELETE /authentication/executions/{id}`, permitido porque la copia no
   es built-in). Con AMBAS condiciones en el mismo sub-flujo condicional,
   Keycloak dispara el callback LoA cuando la condición LoA apenas COINCIDE
   — aunque el flujo luego se salte por user-configured=false — y un login
   password-only sin TOTP obtiene `acr:"2"` (fail-open verificado en vivo).
   Con la condición LoA sola, el único camino a LoA 1 es el OTP Form.
7. **Nadie queda lockeado sin TOTP**: `OTPFormAuthenticator.
   setRequiredActions()` añade `CONFIGURE_TOTP` inline cuando el OTP Form
   corre sin credencial TOTP — el usuario es mandado a enrolar DURANTE el
   login (redirect `login-actions/required-action?execution=CONFIGURE_TOTP`),
   sin code hasta completarlo.
8. **El cliente debe pedir `acr_values=2`** en el authorize (desktop y
   console ya lo hacen): sobre una sesión SSO previa sin OTP, sin el pedido
   la condición LoA no dispara el step-up y el token vuelve con `acr:"1"`.
   Un `claims` con acr ESSENTIAL que no puede cumplirse falla el login entero
   ("Forced level of authentication…") — preferir `acr_values` no-esencial y
   dejar que la puerta del control plane sea el enforcement point.
9. **El campo del formulario OTP es `otp`** (no `totp`) en KC26; el de
   CONFIGURE_TOTP es `totp` + `userLabel` + botón `saveTOTPBtn`.
10. **TOTP no reutilizable**: KC rechaza el mismo código dentro de la ventana
    de 30 s — esperar la ventana siguiente en probes repetidos.
11. **Enrolar TOTP de usuario real**: NO vía Admin REST (404, pitfall del
    skill hermano `keycloak-admin-oidc`). La vía real es el flujo de usuario
    (required action CONFIGURE_TOTP → QR PNG data URI → decodificar QR →
    secret base32 → TOTP RFC6238 SHA1/30s/6 dígitos → submit `#totp` +
    `#userLabel` → `#saveTOTPBtn`; después `UPDATE_PASSWORD`).

## Referencias cruzadas

- Spec vinculante: `specs/011-mfa-acr-evidence/spec.md` (D-MFA-01…06).
- Gate: `platform/app/auth/mfa.py` (amr OR acr>=2, fail-closed).
- Wiring: `platform/app/tenants/keycloak_admin.py::ensure_mfa_acr` +
  `mfa_acr_state` (usado por el healthcheck de provisioning).
- Clientes con step-up: `platform/console` y el helper `trust-login` del
  bundle de descarga (`platform/hermes/`, spec 023)
  (`acr_values=2` + re-intento único de authorize si `mfa_verified=false`).
- Doble de tests: `tests/conftest.py::_IdP` (emite `amr`; `extra_claims`
  permite pinnar la forma real KC26 `amr:null, acr:"2"`).
- Skill hermano: `identity/keycloak-admin-oidc` (pitfalls #9–#12).
