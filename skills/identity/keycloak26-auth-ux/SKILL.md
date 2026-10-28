---
name: keycloak26-auth-ux
description: Keycloak 26 invisible (tema white-label), brokering Google, auto-registro y MFA opcional vía LoA/acr — semántica verificada del ConditionalLoaAuthenticator y patrones Admin REST.
category: identity
---

# Keycloak 26 — Auth UX: tema white-label, brokering Google, MFA opcional

Investigación para el feedback Miller #10 (trust-agent, spec 015). Fuente:
Keycloak 26.2.5 (contenedor vivo `trust-agent-idp`, quay.io/keycloak/keycloak:26.2)
+ fuente oficial `ConditionalLoaAuthenticator.java` (tag 26.2.5) + Server Admin
Guide 26 (flows/LoA, identity brokering) + UI Customization Guide (themes).

## 1. Semántica del `ConditionalLoaAuthenticator` (verificada contra fuente 26.2.5)

`matchCondition()` (services/.../conditional/ConditionalLoaAuthenticator.java):

```java
int currentLoa = acrStore.getLevelOfAuthenticationFromCurrentAuthentication();
int requestedLoa = acrStore.getRequestedLevelOfAuthentication(topLevelFlow);
if (currentLoa < Constants.MINIMUM_LOA) {   // MINIMUM_LOA = 1
    return true;                            // ← INCONDICIONAL
}
if (requestedLoa < configuredLoa) return false;
boolean previouslyAuthenticated = acrStore.isLevelAuthenticatedInPreviousAuth(configuredLoa, maxAge);
if (previouslyAuthenticated && currentLoa < configuredLoa) {
    acrStore.setLevelAuthenticatedToCurrentRequest(configuredLoa);  // step-up por SSO
}
return !previouslyAuthenticated;
```

**Hallazgo crítico**: mientras la autenticación en curso no haya alcanzado
LoA ≥ 1, la condición evalúa `true` **sin mirar el `acr_values` solicitado**
(coincide con la nota oficial: "during the first authentication, the first
configured subflow with the Conditional LoA is always executed regardless of
the requested level"). El LoA solo lo sube `onParentFlowSuccess()` del propio
subflow condicional (o el step-up por sesión previa dentro de `maxAge`).

**Implicación**: un realm con UN solo subflow "Conditional OTP" con condición
LoA (el diseño de `ensure_mfa_acr`, spec 011) fuerza el formulario OTP en TODO
login fresco, pida o no `acr_values`. MFA "opcional" es imposible con ese
cableado — hay que rediseñar el flujo (ver §2).

Otras reglas útiles:
- `requestedLoa < configuredLoa` → el subflow se salta (una vez que
  currentLoa ≥ 1). Es el mecanismo real de "pedir menos".
- `previouslyAuthenticated` (nivel alcanzado en una autenticación previa
  dentro de `loa-max-age`) → la condición devuelve `false` y **otorga** el
  nivel a la petición actual sin re-autenticar (step-up sobre sesión SSO).
- El `acr` emitido es el reverse-mapping del LoA alcanzado según
  `acr.loa.map` del realm (atributo real: `acr.loa.map`, NO
  `acr-to-loa-map` — pitfall #9 del skill keycloak-admin-oidc).

## 2. Diseño de flujo para MFA opcional + obligatorio por policy

Mapa nuevo: `acr.loa.map = {"1":1,"2":2}` (acr "1" → LoA 1 = password;
acr "2" → LoA 2 = password+OTP). Flujo browser copiado (`browser` built-in
no se puede modificar):

```
browser-copy
├── Cookie / Kerberos / Identity Provider Redirector   (como built-in)
└── forms [ALTERNATIVE]
    ├── *subflow condicional "Password LoA" — condición LoA nivel 1*
    │     └── Username Form + Password
    └── *subflow condicional "Conditional OTP" — condición LoA nivel 2*
          └── OTP form (OTPFormAuthenticator)
```

### 2.1 CONFIRMADO EN VIVO (probe scripts/probe_loa_flow.py, KC 26.2.5,
### hallazgos en docs/evidencia/auth-ux/probe-loa-findings.txt)

- S1 acr_values=1 sin TOTP → login → code `acr:"1"` SIN formulario OTP. ✓
- S2 acr_values=2 sin TOTP → redirect inline a required-action
  CONFIGURE_TOTP (mecanismo OTPFormAuthenticator.setRequiredActions). ✓
- S3 completar enrolamiento TOTP → code `acr:"2"`. ✓
- S4 acr_values=2 con TOTP → login → otp → code `acr:"2"`. ✓
- S5 acr_values=1 con TOTP enrolado → password-only → `acr:"1"`
  (el OTP se salta: requestedLoa(1) < configuredLoa(2)). ✓
- S6a SSO (cookie, sesión LoA 1) + acr=1 → code directo `acr:"1"` sin
  password (el riesgo "OTP incondicional sobre SSO" NO se materializa). ✓
- S6b SSO + acr=2 → formulario OTP (step-up REAL) → `acr:"2"`. ✓
- S6c SSO + acr=2 + prompt=login → página de RE-AUTENTICACIÓN (input
  password SIN input username — pitfall de scraping: los divs
  `id="input-error-container-*"` están siempre presentes y un detector
  de error basado en ellos misfirea) → OTP → `acr:"2"`. ✓
- S7 authorize SIN acr_values → password-only → `acr:"1"` — el bare
  authorize YA NO FUERZA OTP (impacto directo: la suite e2e debe pedir
  acr_values explícitos). ✓
- S8 SSO sesión LoA 2 + acr=2 → code directo `acr:"2"` (step-up por
  loa-max-age, sin re-pedir OTP). ✓

### 2.2 Pitfalls de Admin REST para construir el flujo (verificados)

- Crear subflow: `POST /admin/realms/{r}/authentication/flows/{aliasPadre}`
  `/executions/flow` con `providerId="basic-flow"`,
  `requirement="CONDITIONAL"` (la creación IGNORA requirement y priority;
  se fijan después con `PUT .../flows/{FLOW}/executions` usando la
  entrada del listing).
- **CRÍTICO — orden por prioridad**: dentro de `forms`, los subflows
  CONDITIONAL se procesan POR PRIORIDAD; "Password LoA" debe tener
  priority MENOR que "Conditional OTP" (p. ej. 15 vs 20) — si el OTP Form
  corre primero evalúa con `authUser==null` y el authorize muere con
  `400 UNKNOWN_USER`.
- **CRÍTICO — el listing de executions es NESTED-INCLUSIVE**:
  `GET /flows/{alias}/executions` devuelve el árbol PLANO (incluye los
  executions de subflows anidados, marcados con `level`>0). Un loop que
  itere ese listing para BORRAR executions (p. ej. "mover" el
  username/password form al subflow nuevo borrando la copia vieja bajo
  `forms`) se come TAMBIÉN la copia recién creada dentro del subflow →
  el realm nace sin login (regresión real: todo tenant nuevo en 400 sin
  formulario). Fix verificado: capturar los ids de executions del subflow
  (`GET /flows/{subflow}/executions`) y excluirlos del DELETE por
  membership (los authenticator executions llevan `flowId=None`; la
  membership es lo fiable).
- **Alias de config LoA**: el alias del
  `AuthenticationExecutionConfig` es COSMÉTICO (los reads resuelven por el
  `authenticationConfig` id del execution) pero ÚNICO por realm. En realms
  con el cableado viejo (spec 011) ya existe un config `trust-loa-level-1`
  en la condición del OTP → crear el config del Password LoA con ese alias
  da **409**. Fix verificado: aliases distintos por subflow
  (`trust-loa-password-level-1` / `trust-loa-otp-level-2`) + retry con
  alias uniquificado (sufijo execution-id) en 409.
- El alias de un subflow creado/copiado ES su displayName (el listing
  plano de ejecuciones de la copia lo expone como displayName; los
  subflows anidados no aparecen en GET /authentication/flows).
- Páginas intermedias de KC con input `authenticationExecution` se
  auto-envían por JS; un cliente headless debe POSTear el form.
- Código intermedio del broker: tras autenticar contra el IdP externo el
  redirect lleva un `code` que aterriza en `…/broker/<idp>/endpoint` —
  NO es el code final del cliente; solo debe canjearse el code que
  aterriza en el redirect_uri registrado.

Mantener `loa-max-age` alto (36000) para conservar el step-up por SSO que la
consola ya usa (spec 009/011 verificados).

## 3. Tema white-label (KC 26: el tema default es `keycloak.v2`)

- Estructura: `themes/<nombre>/login/theme.properties` con
  `parent=keycloak.v2` + `import=common/keycloak`. El padre aporta
  PatternFly v5 vía propiedades `kc*` (kcButtonPrimaryClass, etc.).
- `template.ftl` de keycloak.v2: la marca del header es
  `${msg("loginTitleHtml",(realm.displayNameHtml!''))}` — override del
  template para poner marca propia; `footer.ftl` es un macro vacío
  (punto de override documentado por KC).
- `social-providers.ftl` renderiza AUTOMÁTICAMENTE el botón "Sign in with
  Google" (SVG de Google incluido) para cada IdP habilitado en el realm —
  no hay que tocar la plantilla para el botón.
- Otros templates heredan del padre: login-otp, login-config-totp (QR),
  error, register… basta con CSS propio + template override para que TODO
  parezca la app.
- Despliegue: directorio montado en `/opt/keycloak/themes/<nombre>` (volume
  en docker-compose). En `start-dev` el caché de temas está desactivado
  (`--spi-theme-cache-themes=false` implícito) → lo toma sin reinicio
  (verificar en vivo). En producción: JAR en `providers/` + restart.
- Binding por realm (Admin REST): `PUT /admin/realms/{realm}`
  `{"loginTheme":"<nombre>"}`. Hacerlo idempotente en el provisioning y
  retroactivo para realms existentes.

### 3.1 Overrides de textos (messages) — verificado en vivo 26.2.5 (t_e68687ee)

Dos capas, ambas sin fork de templates:

1. **Bundles del tema** `themes/<nombre>/login/messages/messages_{en,es}.properties`:
   solo las keys cuyos defaults de KC leen como jerga (TOTP:
   `loginTotpTitle`="Mobile Authenticator Setup", `configureTotpMessage`,
   `loginTotpStep1..3`, `loginTotpStep3DeviceName`, `loginTotpDeviceName`,
   `loginTotpManual*`, `loginTotpUnableToScan`, `loginTotpScanBarcode`).
   Las keys ausentes caen al padre — el archivo se mantiene mínimo a
   propósito. Se toman al vuelo junto con el resto del tema (sin restart).
   PITFALLS: (a) los .properties del tema se leen **UTF-8 crudo** (como los
   del padre — "Configura tu aplicación…"); (b) los valores se formatean
   estilo **MessageFormat**: un apóstrofe literal exige `''` ("Can''t scan
   the code?" — con uno solo se renderiza "Cant", verificado en vivo).
2. **Localization texts por realm** (para textos con el product_name del
   tenant — los bundles no pueden interpolar el displayName en keys que el
   template llama sin argumentos, p.ej. `msg("loginTotpTitle")`):
   `GET/POST /admin/realms/{r}/localization/{locale}` (POST **mergea
   por-key**; `DELETE .../localization/{locale}/{key}` limpia). PITFALLS
   verificados en vivo: (a) el `PUT /admin/realms/{r}` **silencia**
   `localizationTexts` — NO persiste por ahí, solo por el endpoint dedicado;
   (b) funciona con `internationalizationEnabled=false` (estado default de
   los realms — no hay que habilitar i18n); (c) el `title` de pestaña SIEMPRE
   es `msg("loginTitle", realm.displayName)` → "Sign in to <displayName>",
   ya lleva el product name por dato del realm. Implementación de referencia:
   `KeycloakAdminClient.ensure_login_theme` +
   `platform/scripts/apply_auth_ux_realms.py` (retro-fit idempotente que lee
   `tenants.branding->>'product_name'`).

### 3.2 El emoji 🐴 del título de pestaña NO es del producto

Verificado dos veces (QA t_13f8b623 y t_e68687ee): el HTML del servidor trae
`<title>Sign in to <displayName></title>` SIN emoji y ningún script de la
página toca `document.title`. El "🐴 " aparece solo en navegadores manejados
por harness CDP (tooling de QA y de dev lo reproducen): un script del
tooling prefija el emoji del favicon al título. Usuario real: título limpio
+ favicon del tema (verificar `link[rel=icon]` → `/resources/<v>/login/
<nombre-tema>/img/favicon.ico`, no `keycloak.v2`).

### 3.3 TOTP del enrolamiento por HTTP (para probes/e2e)

El input oculto `totpSecret` NO es base32: es un string ALPHANUM random
(`SecretGenerator.randomString`, alfabeto A-Za-z0-9). La clave HMAC son sus
bytes UTF-8 directos (las apps llegan a lo mismo decodificando el QR, que es
Base32(raw.getBytes())). TOTP stock: SHA1/6 dígitos/30s. En el form de
entrada OTP (`login-otp.ftl`) el input es `#otp` (no `#totp`) y NO hay que
mandar `tryAnotherWay` ni `selectedCredentialId` vacíos — `tryAnotherWay`
presente dispara la pantalla "Select login method". El `action` de los
forms viene HTML-escapado (`&amp;`) — aplicar `html.unescape` antes del
POST.

## 4. Google identity provider (brokering) vía Admin REST

```
POST /admin/realms/{realm}/identity-provider/instances
{
  "alias": "google", "providerId": "google", "enabled": true,
  "trustEmail": true, "clientId": "...", "clientSecret": "...",
  "firstBrokerLoginFlowAlias": "first broker login"
}
```

- Redirect URI a registrar en Google Cloud Console (por realm):
  `{kc-base}/realms/{realm}/broker/google/endpoint`. Google acepta
  `http://127.0.0.1` / `http://localhost` con puerto para desarrollo.
- Scopes default de KC para google: `openid profile email`.
- `first broker login` (default) trae "Create User If Unique" → auto-crea el
  usuario en el realm en el primer login con Google. Si el email YA existe
  (p. ej. invitado), va al flujo "confirm link existing account" (el usuario
  debe autenticarse con su cuenta local para vincular) — seguro por defecto.
- **Pitfall**: el usuario brokered NO recibe atributos de usuario → el
  mapper `tenant_id` (oidc-usermodel-attribute-mapper) no emite el claim y
  el mint de sesión falla. Solución: mapper `oidc-hardcode-claim` en el
  cliente web del realm con `claim.value = <tenant_id>` (el valor es
  constante por realm — cada tenant tiene su propio realm). Coexiste con el
  mapper de atributo (mismo valor).
- `trustEmail: true` → emailVerified automáticamente (Google prueba el
  email). Requiere `unmanagedAttributePolicy=ENABLED` ya configurado
  (D-ONB-12) para poder escribir atributos post-hoc si hiciera falta.
- **CONFIRMADO EN VIVO (probe B1/G1)**: el botón "Sign in with Google"
  aparece automáticamente en la página de login en cuanto el IdP está
  habilitado; `kc_idp_hint=google` en el authorize redirige a
  `https://accounts.google.com/o/oauth2/v2/auth?scope=openid+profile+email`.
  El primer login brokered contra un doble OIDC local DECLARADO auto-creó
  el usuario ("Create User If Unique") con `federatedIdentities` correcto
  y emitió `acr:"1"` — y el id_token vino SIN `tenant_id` (el usuario
  brokered no lleva atributos → confirma la necesidad del mapper
  hardcode D-AUTH-03).
- **Pitfall de cookies para dobles locales**: dos realms KC en el mismo
  host:puerto comparten el nombre de cookie AUTH_SESSION_ID y la sesión
  del realm real se pierde al volver del broker ("Restart login cookie
  not found"). Con Google real no pasa (accounts.google.com es otro
  host); para probar brokering local, el doble debe vivir en otro
  hostname (p. ej. localhost vs 127.0.0.1).
- El login brokered NO pasa por username/password: con el diseño §2 y
  `acr_values=1` produce `acr:"1"` (sin OTP) → sesión sin MFA → el banner
  sugiere enrolar; con acr 2 se comporta igual que password.

## 5. Required actions / auto-registro

- CONFIGURE_TOTP como required action al aprovisionar = enrolamiento FORZADO
  en el primer login (comportamiento actual, spec 004/012). Para MFA
  opcional: NO fijarlo (dejar que el enrolamiento ocurra vía el formulario
  OTP inline cuando la app pida acr 2).
- Auto-registro con contraseña desde la app (sin ROPC, constitution IV):
  formulario propio → `POST /api/auth/register` → Admin REST crea el usuario
  (`POST /users` + `reset-password` NO temporal) → fila `users`+`user_roles`
  (rol mínimo) → el usuario firma con el flujo authorize normal.
- Auto-provisión en el primer login brokered: si el id_token verifica
  (firma/iss/tenant) pero no existe fila `users`, crearla con rol mínimo
  gated por policy `allow_self_registration` (nueva clave de catálogo,
  default true, variable). Detectar "vía google" con
  `GET /admin/realms/{realm}/users/{sub}` → `federatedIdentities`.

## 6. Panel propio (ADR-019, spec 015 v1.3.0) — direct grant + dance headless

Decisión de producto Miller (2026-10-28): el usuario JAMÁS ve páginas de
Keycloak. Hallazgos probe-verified contra KC 26.2.5 (todo en
docs/evidencia/auth-panel/EVIDENCIA.md §1):

- **Direct grant stock**: trae "Direct Grant - Conditional OTP"
  (user-configured + `direct-grant-validate-otp`) pero emite `acr:"1"`
  incluso con OTP validado — sin condiciones LoA no hay evidencia MFA.
- **Cableado `trust-direct-grant-loa`** (el que usa el producto): copia del
  flujo "direct grant" con UNA condición "LoA level 2" AGREGADA al sub-flow
  Conditional OTP (user-configured conservado, OTP REQUIRED), bind como
  `directGrantFlow`. Matriz: sin OTP → `acr:"1"`; OTP faltante/malo → 401;
  OTP válido → `acr:"2"`. `requestedLoa` es SIEMPRE 0 en direct grant:
  una condición LoA con wrapper "Password LoA" evalúa false y DESACTIVA el
  OTP (fail-closed) — no envolver. El bug "LoA granted on skip" del browser
  flow NO reproduce aquí (verificado: usuario sin OTP queda en acr 1).
- **Credenciales antes que required actions**: password incorrecta → 401
  "Invalid user credentials"; correcta + acción pendiente → 400 "Account
  is not fully set up" — ese 400 ES la prueba de la password (habilita el
  panel de set-password con proof-handle).
- **KC 26 NO tiene REST para TOTP**: Account REST perdió `POST
  /account/totp` (405) y `credentials-password` requiere audiencia
  `account`; Admin REST no crea credenciales OTP. El ÚNICO mecanismo real
  es el required action CONFIGURE_TOTP → el control plane lo conduce
  headless (authorize `acr_values=2` + PKCE → login form → config-totp →
  QR PNG data-URI inline (`id="kc-totp-secret-qr-code"`, con ESPACIO tras
  la coma) + `totpSecret` hidden (secret RAW, key TOTP = bytes UTF-8) →
  POST del código → 302 con code → canje → `acr:"2"`).
- **Cookies del dance**: KC setea `Secure;SameSite=None` sobre http — el
  navegador lo acepta en 127.0.0.1 (secure context) pero httpx NO: manejar
  el jar a mano (echo de Cookie header).
- **REPLAY DE OTP**: KC 26.2 RECHAZA reusar un código dentro de su ventana
  de 30 s (el código consumido por el dance no valida en un direct grant
  de la misma ventana). El confirm del panel canjea el authorization code
  del dance en vez de re-grantar — inmune por diseño.
- **VERIFY_PROFILE**: usuarios sin firstName/lastName disparan la acción y
  el direct grant da "not fully set up" SIN acciones listadas — siempre
  setear nombres al crear usuarios por Admin REST.
- **Flujos por sub-flow alias**: el listing de ejecuciones es
  nested-inclusive y `/executions/{id}/executions` 404 para sub-flows
  creados por API — los hijos se listan con
  `GET /flows/{subflow-displayName}/executions` (el displayName va
  PREFIJADO con el nombre del flujo copiado).

## 7. Referencias

- Server Admin Guide 26: authentication flows / step-up LoA
  (keycloak.org/docs/latest/server_admin — "Creating a browser login flow
  with step-up mechanism", nota del primer subflow).
- Fuente: `ConditionalLoaAuthenticator.java` tag 26.2.5 (GitHub keycloak/keycloak).
- UI Customization: keycloak.org/ui-customization/themes.
- Identity brokering: keycloak.org/docs/latest/server_admin — "Identity
  Brokering" (Google provider, redirect URI, first login flow).
- Skills hermanos: `identity/keycloak-admin-oidc` (Admin REST, pitfalls
  #1-#12), `identity/keycloak26-amr-mfa` (KC26 nunca emite `amr`).
