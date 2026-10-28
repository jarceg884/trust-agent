# Plan técnico — Spec 015 Auth UX

Referencia: spec.md (mismo directorio). Base: platform/ spec 003/004,
keycloak_admin.py spec 004/011, console spec 009, desktop spec 010.

## 0. Probe en vivo (T2 — ANTES de tocar producto)

`platform/scripts/probe_loa_flow.py` (script desechable de investigación,
no producto): contra KC 26.2 local (credencial de dev de tests/e2e,
ADR-015), realm desechable `probe-loa`:

1. Crear realm + map `acr.loa.map={"1":1,"2":2}` + copia del flujo browser
   reestructurada (Password LoA nivel 1 con username+password; Conditional
   OTP nivel 2) + usuario con password sin TOTP.
2. Baile authorize/POST-credentials con `acr_values=1` → esperar code +
   `acr:"1"` SIN formulario OTP.
3. Ídem con `acr_values=2` → esperar CONFIGURE_TOTP inline (sin code).
4. Completar enrolamiento TOTP por el flujo real (navegador) → login con
   `acr_values=2` → `acr:"2"`; login con `acr_values=1` → `acr:"1"`.
5. Re-login SSO (cookie) con acr 1 y acr 2 → documentar comportamiento.
6. DELETE realm (limpieza).

Salida: hallazgos → skill +, y D-AUTH-06 se congela o se corrige.

## 1. Capa de datos

- Migración `db/migrations/versions/20261026_0006_auth_ux_policies.py`:
  - `UPDATE catalog.policy_keys SET invariable=false, default_value='false'
    WHERE key='require_mfa'`
  - `INSERT catalog.policy_keys ('allow_self_registration','boolean',false,
    'true','Allow self-registration and brokered auto-provisioning.')`
  - `INSERT catalog.audit_actions ('user.self_registered',
    'user.auto_provisioned') ON CONFLICT DO NOTHING`
  - Sin DDL de tablas (users/user_roles ya existen).
  - Los tenants existentes NO se tocan (conservan su fila org
    `require_mfa=true` — decisión D-AUTH-07).

## 2. Backend (`platform/app`)

```
app/
├── config.py                    # + google_client_id, google_client_secret (fail-closed "")
├── tenants/keycloak_admin.py    # + LOGIN_THEME="trust-agent": ensure_login_theme(realm),
│                                #   ensure_google_idp(realm), ensure_tenant_id_hardcode_mapper(realm, tenant_id),
│                                #   rework ensure_mfa_acr() → ensure_auth_ux_flow() (D-AUTH-06):
│                                #   map {"1":1,"2":2}, Password-Loa subflow, OTP a nivel 2;
│                                #   ensure_admin_user/ensure_user_with_one_time_password:
│                                #   required_actions parametrizado (CONFIGURE_TOTP solo si require_mfa)
├── tenants/provision.py         # llama a los ensure_* nuevos; pasa require_mfa del catálogo
├── auth/router.py               # + GET /api/auth/org-config, POST /api/auth/register;
│                                #   mint: auto-provisión brokered (D-AUTH-05)
├── auth/service.py              # require_mfa dependency → policy-aware (lee policy org efectiva)
├── auth/register.py             # NUEVO: validación + Admin REST + users/user_roles + audit
└── governance/service.py        # helper org_policy_value(conn, key) (lectura org-level + default catálogo)
```

Contratos:

- `GET /api/auth/org-config?org=<slug>` → 200 `{org, require_mfa,
  allow_self_registration, google_login_enabled}` | 404 org desconocido.
  Rate-limited por IP (throttle). Sin secretos.
- `POST /api/auth/register` body `{org, email, password, display_name}` →
  201 `{user_id, org}` | 404 org | 403 self_registration_disabled |
  409 email_exists | 429 throttled. Password: mín. 12 chars, sin
  normalización (literal_preservation).
- Auto-provisión en mint: solo cuando `allow_self_registration` efectiva;
  lookup KC por `sub` → `federatedIdentities` → `via`; INSERT users
  (status active, oidc_subject, email, display_name) + user_roles ('user') +
  audit `user.auto_provisioned`.

## 3. Tema Keycloak (`platform/idp-themes/trust-agent/`)

```
idp-themes/trust-agent/login/
├── theme.properties             # parent=keycloak.v2, import=common/keycloak, styles=css/trust.css
├── template.ftl                 # copia del v2 con header de marca Trust Agent (sin loginTitleHtml de KC)
├── footer.ftl                   # vacío (sin "Powered by Keycloak")
└── resources/css/trust.css      # fondo/colores del console, tipografía, logo
```

docker-compose (servicio `idp`): volume
`./platform/idp-themes:/opt/keycloak/themes/trust-agent-external:ro`…
(mejor: montar el directorio del tema directamente en
`/opt/keycloak/themes/trust-agent`). `start-dev` no cachea temas → sin
reinicio. Binding: `ensure_login_theme` en provisioning + retro para realms
existentes (script `scripts/apply_auth_ux_realms.py` o CLI tenants).

## 4. Console (`platform/console`)

```
src/lib/oidc.ts                  # buildAuthorizeUrl({acrValues}) parametrizable
src/lib/control-plane.ts         # + orgConfig(org), register(body)
src/app/auth/login/route.ts      # ?acr=2 opcional; acr por org-config (fetch server-side)
src/app/auth/callback/route.ts   # step-up solo si policy require_mfa (org-config cacheada en cookie pkce)
src/app/login/login-form.tsx     # + link "Create your account", copy sin KC, hint Google
src/app/register/page.tsx        # NUEVO: formulario de auto-registro (org, email, password, name)
src/components/shell/mfa-banner.tsx  # NUEVO: banner persistente !mfa_verified + botón Set up MFA
src/components/shell/shell.tsx   # renderiza MfaBanner
```

## 5. Desktop (`platform/desktop-client`)

`src/lib/oidc.ts` + `src/app/auth/login/route.ts`: acr_values por org-config
(mismo contrato). Callback: sin step-up forzado si policy false.

## 6. Tests

- `tests/test_auth_ux.py` (unit, doble KC declarado como en spec 012):
  org-config, register (happy + policy off + email exists + throttle),
  auto-provisión brokered (policy on/off), require_mfa policy-aware
  (403 con policy true y sesión sin MFA; 200 con policy false).
- e2e `tests/e2e/test_control_plane_flow.py`: `_pkce_login(acr_values=...)`;
  test_02 se actualiza: (c) password-only con acr 2 → CONFIGURE_TOTP (igual
  que hoy pero explícito); nuevo caso acr 1 → code con acr "1".
- Verificación live con navegador real (evidencia AC-AUTH-001..005).

## 7. Riesgos

| Riesgo | Mitigación |
|---|---|
| D-AUTH-06 falla en el probe (SSO cookie dispara OTP incondicional) | Plan B: subflow "Password LoA" con condición LoA 1 + authenticator inofensivo; Plan C: mantener flujo actual para require_mfa=true y flujo sin OTP para require_mfa=false vía dos flows + client-level `authenticationFlowBindingOverrides` (un cliente por modo). Se decide con datos del probe. |
| Google sin credenciales del operador | AC-AUTH-006 queda como dependencia externa explícita; el resto converge. |
| e2e cambia semántica | Actualización de tests es parte de la spec (D-AUTH-10), documentada en converge. |
| tenants existentes quedan require_mfa=true | Decisión D-AUTH-07: conservador; el admin puede apagarlo vía policies API. |
