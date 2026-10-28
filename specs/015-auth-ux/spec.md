# Spec 015 — Auth UX: Keycloak invisible + Google + auto-registro + MFA opcional

| Campo | Valor |
|---|---|
| **Estado** | Converged (2026-10-26) — AC-AUTH-001..005/007/008/009 verificados con evidencia real (docs/evidencia/auth-ux/EVIDENCIA.md); AC-AUTH-010 (sync `mfa_enrolled`, GAP-10.2) añadido en v1.1.0. **AC-AUTH-006 (Google punta a punta) DESCOPED del v0.1.0-pilot por enmienda v1.2.0 (§7): gap documentado, pass-with-documented-gap para el gate; re-entra en v0.1.1** |
| **Versión** | 1.2.0 |
| **Fecha** | 2026-10-27 |
| **Autor** | dev (tarea t_1844fb06, feedback Miller #10) |
| **Padre** | specs/003-control-plane (auth/JWT/sesiones), specs/004-tenant-onboarding (provisioning KC, D-ONB-12/13), specs/009-admin-console (login PKCE, D-AC-01/02, step-up), specs/011-mfa-acr-evidence (D-MFA-01, ensure_mfa_acr), specs/012-org-endpoints (invitas) |
| **Idioma** | Español (docs) / inglés (código, UI copy) |

---

## 1. Problema (feedback Miller #10)

Hoy el login muestra la página propia de Keycloak (el baile PKCE redirige al
authorize del realm y el usuario ve la UI de KC 26) y el TOTP es obligatorio
para todos (el realm fuerza el formulario OTP en todo login fresco — ver
hallazgo del skill `identity/keycloak26-auth-ux` §1). Requisitos del cliente:

1. **Keycloak 100% invisible** — el usuario nunca ve UI de KC.
2. **Login con Google** — identity brokering de KC 26, PRIMERA FASE.
3. **Auto-registro** — el cliente crea su usuario con contraseña desde la app,
   o inicia sesión con Google.
4. **MFA opcional al crear** con contraseña; la app SIEMPRE sugiere agregarlo
   (banner/reminder persistente); el obligatorio queda como opción de policy
   (`require_mfa` ya existe).

El IdP sigue siendo Keycloak por dentro (sesiones, JWKS, realms) — solo la
superficie es de la app.

## 2. Investigación previa (hecha, capturada como skill)

`/root/trust-agent/skills/identity/keycloak26-auth-ux/SKILL.md` — semántica
verificada contra la fuente de `ConditionalLoaAuthenticator` (KC 26.2.5): en
una autenticación fresca (LoA corriente < 1) la condición LoA evalúa `true`
**sin importar el `acr_values` solicitado**. Por eso el cableado actual
(spec 011) fuerza OTP para todos y hay que rediseñar el flujo (D-AUTH-06).

## 3. Decisiones de diseño (vinculantes)

### D-AUTH-01 — Keycloak invisible vía tema white-label `trust-agent`

Tema propio `parent=keycloak.v2` (default de KC 26): override de
`template.ftl` (marca propia en el header, título de producto), CSS propio
sobre PatternFly, `footer.ftl` sin marca KC. El botón "Sign in with Google"
lo renderiza `social-providers.ftl` del padre automáticamente cuando el IdP
está habilitado. Despliegue: volumen `idp-themes/` → `/opt/keycloak/themes/`
en el servicio `idp` del docker-compose; binding por realm
(`loginTheme`) idempotente en el provisioning y retroactivo para realms
existentes.

**Descartado**: BFF con pantallas propias para el password — autenticar
username/password sin UI de KC exigiría ROPC, prohibido por el constitution
IV para usuarios finales. El tema es la superficie white-label soportada
oficialmente; el baile sigue siendo authorization-code + PKCE S256 (D-AC-02).

### D-AUTH-02 — Google IdP por realm vía Admin REST

`ensure_google_idp(realm)`: `POST /admin/realms/{realm}/identity-provider/
instances` con `alias=google`, `providerId=google`, `trustEmail=true`,
credenciales desde `TRUST_GOOGLE_CLIENT_ID` / `TRUST_GOOGLE_CLIENT_SECRET`
(fail-closed: sin credenciales no se agrega el IdP y `org-config` reporta
`google_login_enabled=false`). Redirect URI por realm para Google Cloud
Console: `{kc-base}/realms/{realm}/broker/google/endpoint`. Flujo first
broker login default ("Create User If Unique" → auto-alta; email existente →
confirm-link). **Dependencia externa**: las credenciales de Google las
aporta el operador (no existen en el servidor).

### D-AUTH-03 — Claim `tenant_id` para usuarios brokered

El usuario creado por brokering no lleva atributos → el mapper de atributo
(spec 003) no emite `tenant_id`. Se agrega mapper `oidc-hardcode-claim` en el
cliente web del realm con `claim.value = <tenant_id>` (constante por realm;
coexiste con el mapper de atributo, mismo valor). Así todo token del realm
lleva el claim firmado.

### D-AUTH-04 — Auto-registro con contraseña desde la app (sin ROPC)

`POST /api/auth/register` (público, rate-limited con `ops.login_throttle`):
valida org code + policy `allow_self_registration`; crea el usuario en KC vía
Admin REST (`POST /users` + `reset-password` NO temporal, `emailVerified=true`
en dev local — sin verificación por email en v1, documentado) + filas
`users`/`user_roles` (rol mínimo `user`) + audit `user.self_registered`.
Después el usuario entra por el flujo authorize normal. La contraseña transita
una vez por el control plane hacia el Admin REST (TLS en producción); jamás
se almacena.

### D-AUTH-05 — Auto-provisión en el primer login brokered

En `POST /api/auth/session`, si el id_token verifica (firma/iss/tenant) pero
no existe fila `users`: gated por policy `allow_self_registration` → lookup
Admin REST del usuario (`federatedIdentities` para detectar google) → alta de
`users`+`user_roles` (rol `user`) + audit `user.auto_provisioned`
(payload `via: "google"`). Si la policy está apagada → `unknown_user` fail
closed (comportamiento actual).

### D-AUTH-06 — MFA opcional: rediseño del flujo browser LoA

Nuevo cableado (**CONFIRMADO EN VIVO** por `scripts/probe_loa_flow.py`
contra KC 26.2.5 — S1..S8 + G1 + B1, ver skill §2.1):

- `acr.loa.map = {"1":1,"2":2}` (antes `{"1":0,"2":1}`).
- Flujo copiado: subflow condicional **"Password LoA"** (condición LoA nivel
  1) que envuelve Username Form + Password; subflow **"Conditional OTP"**
  (condición LoA nivel 2, sin "Condition - user configured") con el OTP form.
- Resultado esperado: `acr_values=1` → password-only → `acr:"1"` (sin OTP);
  `acr_values=2` → OTP form → sin TOTP, enrolamiento inline CONFIGURE_TOTP
  (mecanismo verificado spec 011) → `acr:"2"`.
- `loo-max-age` 36000 conserva el step-up por SSO (spec 009/011).
- CONFIGURE_TOTP **deja de fijarse** como required action en provisioning e
  invitaciones (el enrolamiento ocurre vía el formulario OTP inline cuando la
  app pide acr 2).
- Riesgos a cubrir en el probe: login por cookie SSO con acr 1 (el subflow
  Password LoA no corre → el OTP podría dispararse incondicional), orden de
  ejecuciones dentro de `forms`, y comportamiento del login brokered.

### D-AUTH-07 — `require_mfa` pasa a policy variable; nueva `allow_self_registration`

Migración: `catalog.policy_keys` — `require_mfa` → `invariable=false`,
`default_value=false`; nueva clave `allow_self_registration` (boolean,
variable, default `true`). Los tenants existentes conservan su fila org
explícita (`require_mfa=true`) — el org_admin puede apagarla vía la API de
policies; los tenants nuevos nacen con MFA opcional + banner. La dependencia
`require_mfa` del control plane se vuelve **policy-aware**: lee la policy
org efectiva; `true` → 403 `mfa_required` sin MFA (como hoy); `false` → deja
pasar sesiones sin MFA.

### D-AUTH-08 — Console: org-config, acr condicional y banner MFA persistente

- `GET /api/auth/org-config?org=<slug>` (público, rate-limited): devuelve
  `{require_mfa, allow_self_registration, google_login_enabled}` — el login
  elige `acr_values` (2 si `require_mfa`, 1 si no) y muestra/hide el hint de
  Google/registro.
- Callback: si `!mfa_verified` y policy `require_mfa=false` → sesión válida,
  NO step-up forzado. Con `require_mfa=true` → step-up retry como hoy.
- **Banner persistente** en el shell (todas las páginas) mientras
  `mfa_verified=false`: "Protect your account — set up multi-factor
  authentication" + botón **Set up MFA** → `/auth/login?org=…&acr=2` →
  formulario OTP inline (themed) → enrolamiento + verificación → `acr:"2"`
  → banner desaparece.
- Página de login de la app: link "Create your account" (registro propio) y
  copy sin mención de Keycloak.

### D-AUTH-09 — Paridad desktop

El desktop replica la selección de `acr_values` vía `org-config` (mismo
contrato). Sin cambios de baile PKCE (D-DTB-02).

### D-AUTH-10 — Evidencia real (nada simulado)

- Probe en vivo del flujo LoA (T2) contra KC 26.2 antes de congelar D-AUTH-06.
- Verificación punta a punta con navegador real (browser tooling): registro
  → login → banner → enrolamiento TOTP → banner fuera; org con
  `require_mfa=true` → step-up forzado intacto.
- Login con Google punta a punta **requiere credenciales reales del
  operador** (dependencia externa explícita; sin ellas el entregable marca
  el gap honestamente — el brokering se verifica contra el mecanismo real
  con el IdP google configurado y, si es posible, con un doble OIDC local
  DECLARADO para la mecánica de brokering, nunca presentado como Google).
- Suites unit + e2e en verde; e2e actualizado donde la semántica cambia
  (bare authorize ya no fuerza OTP — ahora el e2e pide `acr_values`
  explícitos).

### D-AUTH-11 — Sincronización de `users.mfa_enrolled` tras el enrolamiento TOTP (GAP-10.2)

La columna `users.mfa_enrolled` solo se fijaba en el INSERT inicial
(`register.py`, `invite.py`, `provision.py`) y quedaba desalineada cuando el
usuario enrolaba TOTP por el flujo inline de D-AUTH-06: la sesión quedaba
`acr=2` (MFA verificada en el IdP) pero el directorio seguía diciendo "sin
MFA". Decisión (hallazgo QA t_db7b2fa9 / GAP-10.2, tarea t_edbcface):

- **Sincronización lazy en los funnels de auth con claims verificados.** El
  estado real de MFA vive en el IdP; la plataforma lo aprende en los puntos
  donde ya verifica criptográficamente un id_token y dispone de una
  transacción tenant-scoped:
  1. `POST /api/auth/session` (mint de sesión, console y desktop) — cuando
     `mfa_evidence(claims)` (D-MFA-01: `amr` mfa/otp o `acr >= 2`), la MISMA
     transacción hace `UPDATE users SET mfa_enrolled = true WHERE id = :uid
     AND NOT mfa_enrolled`. Es exactamente el momento en que converge el
     enrolamiento inline: el console re-baila con `acr_values=2` y re-minte.
  2. Rama JWT stateless de `authenticate()` (desktop/plugin usa el id_token
     como Bearer; nunca pasa por el mint) — mismo UPDATE guardado, solo
     cuando hay evidencia MFA.
  3. `POST /api/devices/enroll` — la evidencia MFA ya es OBLIGATORIA ahí
     (D-HRB-03); mismo UPDATE guardado.
- **Guardado y monotónico:** el UPDATE lleva `AND NOT mfa_enrolled` — es
  no-op (0 filas) cuando ya está en `true`, así la rama JWT no escribe en
  cada request. Un login password-only (`acr=1`) NUNCA baja la columna: no
  probar un segundo factor no prueba que el usuario se des-inscribió.
- **Des-inscripción: fuera de alcance v1** (ver §4): el producto no ofrece
  flujo de des-inscripción TOTP (solo un admin del IdP podría remover la
  credencial OTP por Admin REST). La semántica de la columna es "ha
  acreditado MFA ante la plataforma" (high-water mark).
- **Sin acción de audit nueva:** la transición ya queda auditada por
  `auth.login` con `payload.mfa_verified=true` (mismo evento, misma
  transacción); no se añade acción al catálogo ni migración.
- **Directorio:** `GET /api/users` y `PUT /api/users/{id}` exponen
  `mfa_enrolled` (lectura de la columna, sin tocar `oidc_subject` —
  D-ORG-08 intacto) y la página Users del console lo pinta como badge.
  El campo sigue NO editable por la API de directorio (spec 017: la
  identidad vive en el IdP).
- **Aislamiento multi-tenant (constitution III):** el UPDATE corre en la
  conexión ya scoped con `set_tenant` + RLS de `users`, igual que toda
  otra query del flujo; sin excepciones.

## 4. Fuera de alcance (v1)

- Verificación de email en el auto-registro (emailVerified=true en dev;
  follow-up con conector de email).
- WebAuthn/passkeys (deferred, t_c6318558).
- Otros IdPs sociales (el mecanismo es el mismo; solo google).
- Términos y condiciones / recaptcha en el registro.
- Des-inscripción TOTP y su sincronización (D-AUTH-11): no existe flujo de
  producto para remover la credencial OTP; `users.mfa_enrolled` es un
  high-water mark ("ha acreditado MFA ante la plataforma"). Si nace un flujo
  de des-inscripción, la columna deberá derivarse del IdP en ese momento.

## 5. Criterios de aceptación

| ID | Criterio |
|---|---|
| AC-AUTH-001 | Las páginas de login/OTP/enrolamiento de KC muestran marca Trust Agent (tema `trust-agent`), sin branding de Keycloak, con botón "Sign in with Google" visible cuando el IdP está configurado. Esto incluye el **favicon** del realm: `login/resources/img/favicon.ico` debe servirse desde el tema `trust-agent` (icono Trust Agent, no el caballo por fallback del tema padre `keycloak.v2`) — hallazgo QA t_13f8b623, white-label incompleto en la pestaña del navegador. |
| AC-AUTH-002 | Auto-registro con contraseña desde la app → login con esa contraseña → sesión admin operativa; fila `users`+`user_roles` (rol `user`) + audit `user.self_registered`. |
| AC-AUTH-003 | Org con `require_mfa=false`: login password-only completa (`acr:"1"`), la API de negocio responde 200 (sin 403 mfa_required) y el banner MFA persistente es visible. |
| AC-AUTH-004 | "Set up MFA" → enrolamiento TOTP inline (pantalla themed) → `acr:"2"` → sesión `mfa_verified=true` → banner desaparece. |
| AC-AUTH-005 | Org con `require_mfa=true`: password-only no alcanza (step-up/403 como hoy); TOTP requerido — comportamiento spec 011 intacto. |
| AC-AUTH-006 | Login con Google punta a punta contra KC 26.2 vivo: primer login → auto-provisión (rol `user`, claim `tenant_id`, audit `user.auto_provisioned` con `via:"google"`) → sesión operativa. **Requiere credenciales Google del operador. DESCOPED del v0.1.0-pilot (enmienda v1.2.0 §7): pass-with-documented-gap; re-entra en v0.1.1 con las credenciales.** |
| AC-AUTH-007 | `org-config` refleja por org: require_mfa, allow_self_registration, google_login_enabled; rate-limited. |
| AC-AUTH-008 | Suites unit + e2e en verde (e2e actualizado a la semántica nueva de acr). |
| AC-AUTH-009 | Skill `identity/keycloak26-auth-ux` actualizado con los resultados del probe (hipótesis D-AUTH-06 confirmada o corregida). |
| AC-AUTH-010 | Tras completar el enrolamiento TOTP (inline, D-AUTH-06) y la sesión resultante (`acr=2`), la columna `users.mfa_enrolled` queda `true` (SQL directo) y el directorio (`GET /api/users`, página Users del console) lo refleja. Un mint sin evidencia MFA (`acr=1`) no baja la columna (monotónico, D-AUTH-11). |

## 6. Dependencias externas (bloqueos conocidos)

1. **Credenciales Google OAuth** (client ID + secret de Google Cloud Console,
   redirect URI `http://127.0.0.1:8080/realms/<org>/broker/google/endpoint`)
   — las aporta el operador; sin ellas AC-AUTH-006 no es verificable punta a
   punta.
2. **Consentimiento para operaciones Admin REST en vivo** desde el worker
   (el gate del terminal bloqueó el bootstrap de credenciales admin; el
   proyecto ya usa el credencial de dev `KeycloakDemo2026` en `tests/e2e`).

## 7. Enmienda v1.2.0 (2026-10-27) — Descope de AC-AUTH-006 del v0.1.0-pilot

**Decisión**: AC-AUTH-006 (login Google punta a punta) queda **fuera del
alcance del v0.1.0-pilot** como gap documentado. El gate final (t_e8a28fce)
lo trata como **pass-with-documented-gap**. Re-entra en **v0.1.1** en cuanto
el operador aporte las credenciales.

- **Actor y trazabilidad**: decisión por default del orquestador (worker
  `default`, comentario en t_f372f47f, 2026-10-27 13:28 UTC-6) ante la
  ausencia de respuesta del operador (Miller) a la pregunta específica
  plantada en el bloqueo de t_f372f47f. **Reversible**: en cuanto lleguen
  `TRUST_GOOGLE_CLIENT_ID`/`TRUST_GOOGLE_CLIENT_SECRET`, se ejecuta la
  verificación punta a punta y esta enmienda se marca como resuelta (sin
  cambios de código — la implementación ya está completa).
- **Razón**: dependencia externa pura. El flujo requiere un OAuth client real
  de Google Cloud Console (client ID + secret) que solo el operador puede
  crear; no existen en el servidor. Ningún cambio de producto puede
  sustituirlas, y fabricar/simular credenciales viola la regla de evidencia
  real (D-AUTH-10).
- **Lo que SÍ está entregado y verificado** (no se descope, queda listo para
  v0.1.1):
  - `ensure_google_idp` (D-AUTH-02) — fail-closed: sin credenciales no se
    agrega el IdP.
  - Mapper hardcode `tenant_id` para usuarios brokered (D-AUTH-03,
    `ensure_tenant_id_hardcode_mapper`).
  - Auto-provisión en el mint (D-AUTH-05, gated por
    `allow_self_registration`, audit `user.auto_provisioned` con
    `via:"google"`) — cubierta por tests unit.
  - Mecánica de brokering probeada en vivo contra KC 26.2.5 con doble OIDC
    local DECLARADO (probe B1/G1, `probe-loa-findings.txt`): botón
    auto-renderizado, `kc_idp_hint=google`, primer login brokered
    auto-crea el usuario ("Create User If Unique").
  - Fail-closed verificado en vivo (re-verificado 2026-10-27, ver
    EVIDENCIA.md §Addendum): `org-config` reporta
    `google_login_enabled=false` y el HTML de login del realm contiene 0
    elementos "google" — ningún usuario ve un botón Google roto.
- **Riesgo aceptado**: el pilot arranca sin login social; el auto-registro
  con contraseña (AC-AUTH-002) y las invitaciones cubren el alta de
  usuarios. No hay degradación de seguridad: el brokering simplemente no
  está expuesto.
- **Criterio de re-entrada (v0.1.1)**: operador crea el OAuth client en
  Google Cloud Console con redirect URI por realm
  `http://<host>/realms/<org>/broker/google/endpoint`, setea
  `TRUST_GOOGLE_CLIENT_ID`/`TRUST_GOOGLE_CLIENT_SECRET` en `.env`,
  re-corre el provisioning (idempotente) y se ejecuta AC-AUTH-006 tal como
  está escrito en §5.
