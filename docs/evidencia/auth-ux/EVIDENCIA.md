# Evidencia en vivo — Spec 015 Auth UX (t_1844fb06, feedback Miller #10)

Fecha: 2026-10-26 (14:00–14:50 CST). Stack real: Keycloak 26.2.5
(`trust-agent-idp`, 127.0.0.1:8080), API (`trust-agent-api`, imagen
`trust-agent/platform:local` rebuild 14:31), PostgreSQL (`trust-agent-db`),
console (`trust-agent-console`, 127.0.0.1:3100). Navegador real (Chromium vía
CDP) + verificación DB/audit directa. **Nada simulado.**

Tenant de evidencia: `authux-ev` (id 2079624b-98f0-42ce-a184-32bee55b7d6c),
aprovisionado con `trust-provision` (CLI, mismo service layer que
POST /api/admin/tenants) a las 14:09 CST. Nace con `require_mfa=false`
(default del catálogo tras la migración 0014) y
`allow_self_registration=true`.

## AC-AUTH-001 — Keycloak invisible (tema white-label)

- Realm `authux-ev`: `loginTheme='trust-agent'`, `displayName='Trust Agent'`
  (verificado vía Admin REST con el código de la plataforma).
- Página de login del realm (authorize PKCE): header "TRUST AGENT" (marca CSS
  propia sobre PatternFly v5, paleta del console), título "Sign in to Trust
  Agent", **0 menciones de Keycloak** en el DOM (`document.body.innerText`).
- El HTML servido por KC es limpio: `<title>Sign in to Trust Agent</title>`
  (curl). El emoji 🐴 que muestra la pestaña del navegador de tooling proviene
  del favicon, no del HTML.
- Tema montado por volume `./platform/idp-themes/trust-agent
  → /opt/keycloak/themes/trust-agent:ro`; binding idempotente en
  provisioning (`ensure_login_theme`) + retro (script).
- Screenshots: `screenshots/03-kc-themed-login.png`,
  `screenshots/05-totp-enrollment.png` (enrolamiento TOTP también themed),
  `screenshots/01-app-login.png`, `screenshots/02-register.png`.

## AC-AUTH-002 — Auto-registro con contraseña desde la app

Flujo real por UI (navegador): `/register?org=authux-ev` → formulario
(org, email, nombre, password ≥12) → "Account created".

- Usuario `evidence@authux-ev.test` (+ `evidence2`, `evidence3` en corridas
  posteriores): fila `users` (status active, mfa_enrolled=false,
  oidc_subject=KC uuid) + `user_roles` (rol `user`) — verificado por SQL.
- Audit: `user.self_registered` (actor_role=user) en `audit_log` del tenant.
- Login posterior con esa contraseña por el baile PKCE normal: OK (ver
  AC-AUTH-003). Sin ROPC en ningún punto (constitution IV).

## AC-AUTH-003 — Org con require_mfa=false: password-only + banner

- `GET /api/auth/org-config?org=authux-ev` →
  `{"require_mfa":false,"allow_self_registration":true,"google_login_enabled":false}`
  (público, rate-limited).
- El login del console pide `acr_values=1` (derivado de org-config,
  verificado en la URL del authorize).
- Login password-only completa: code → callback → sesión console operativa
  (página /overview; los endpoints de negocio responden 200 — el 403
  `permission_denied` visible para budgets es RBAC de rol `user`, correcto).
- Sesión en DB: `mfa_verified=false`, status active.
- **Banner MFA persistente** visible en el shell ("Protect your account —
  Add multi-factor authentication (TOTP)… It takes less than a minute" +
  botón "Set up MFA"). Screenshot `screenshots/04-console-mfa-banner.png`
  (DOM verificado en la misma captura).

## AC-AUTH-004 — Set up MFA → enrolamiento inline → banner fuera

- "Set up MFA" → `/auth/login?org=…&acr=2` → el flujo LoA del realm dispara
  el required-action CONFIGURE_TOTP inline (página themed "Mobile
  Authenticator Setup" con QR).
- QR decodificado real (zbarimg):
  `otpauth://totp/Trust%20Agent:evidence%40authux-ev.test?secret=GNEWGNSWIRIGCODSKEYUEUCMII4FE3TX…`
  — código RFC6238 generado con el secret y aceptado por KC a la primera.
- Tras el enrolamiento: redirect al console, sesión re-minteada
  `mfa_verified=true` (DB) y **el banner desaparece** (DOM verificado).
- Repetido íntegro con `evidence2@authux-ev.test` (secret propio del QR):
  mismo resultado. Screenshot `screenshots/06-console-mfa-enrolled.png`.

## AC-AUTH-005 — Org con require_mfa=true: TOTP obligatorio (spec 011 intacto)

- Policy activada (fila org `require_mfa=true`, como lo haría el org_admin
  vía PUT /api/policies/org): `org-config` refleja `require_mfa:true` en
  caliente.
- El login del console pasa a pedir `acr_values=2` (verificado en la URL).
- Login fresco: password → **formulario OTP** (password-only NO completa, no
  hay code) → TOTP → console con sesión MFA. Verificado en navegador con
  `evidence@authux-ev.test` (que ya tenía TOTP enrolado).
- La puerta policy-aware del control plane (403 `mfa_required` con sesión sin
  MFA cuando la policy está true; 200 cuando false) está cubierta por el
  e2e `test_02_real_pkce_login_and_mfa_gate` (10/10 passed).

## AC-AUTH-006 — Login con Google (GAP EXPLÍCITO — dependencia externa)

**No verificable punta a punta sin credenciales del operador** (client
ID/secret de Google Cloud Console; redirect URI por realm:
`http://127.0.0.1:8080/realms/<org>/broker/google/endpoint`). Lo verificado:

- `ensure_google_idp` fail-closed: sin `TRUST_GOOGLE_CLIENT_ID/SECRET` el
  IdP no se agrega y `org-config` reporta `google_login_enabled=false`
  (visto en vivo).
- Mecánica de brokering confirmada en vivo contra KC 26.2.5 (probe B1/G1,
  `probe-loa-findings.txt`): botón "Sign in with Google" auto-renderizado,
  `kc_idp_hint=google` → redirect a accounts.google.com, y primer login
  brokered contra un doble OIDC local DECLARADO auto-crea el usuario
  ("Create User If Unique") con `acr:"1"` — el id_token vino SIN
  `tenant_id`, lo que motivó el mapper hardcode D-AUTH-03 (implementado:
  `ensure_tenant_id_hardcode_mapper` en provisioning y retro).
- Auto-provisión D-AUTH-05 implementada en el mint (gated por
  `allow_self_registration`, audit `user.auto_provisioned` con
  `via:"google"`) y cubierta por tests unit (`test_auth_ux.py`).

## AC-AUTH-007 — org-config por org + rate-limited

- Responde por org (arriba) y refleja cambios de policy en caliente
  (require_mfa false→true→false verificados en la misma sesión de prueba).
- Rate-limited por IP (`ops.login_throttle`); cobertura unit en
  `test_auth_ux.py` (429 tras el límite).

## AC-AUTH-008 — Suites

- db/: **182 passed** (`TRUST_DB_CONTAINER=trust-agent-db`; incluye paridad
  schema.sql↔migraciones y los 2 tests que la migración 0014 había dejado
  stale, ya corregidos).
- platform/: **478 passed** — incluye e2e `test_control_plane_flow`
  (10/10) actualizado a la semántica spec 015 (`acr_values` explícitos,
  gate policy-aware, require_mfa org-tunable) y `test_auth_ux.py` (nuevo).
- 2 fallos restantes son de lanes hermanas en vuelo, NO de spec 015:
  `test_provision.py::test_provider_token_unconfigured_is_fail_closed`
  (espera 503, recibe 401 — auth de operador spec 025 cambió la semántica)
  y `test_spend_cutoff.py::test_org_cutoff_and_tenant_isolation`
  (contaminación cross-test order-dependent; spend.py/budgets.py modificados
  por la lane de budgets/HRB). Ambos pasan standalone.

## AC-AUTH-009 — Skill actualizado

`skills/identity/keycloak26-auth-ux/SKILL.md` — probe S1–S8/G1/B1 congelado
(D-AUTH-06) + pitfalls nuevos de esta implementación (ver § "Lecciones").

## Regresión factory-wide resuelta (colateral de esta lane, 13:39–13:55 CST)

El primer intento de `_restructure_loa_flow` borraba el formulario
username/password recién creado (el listing de executions es
nested-inclusive) → todo realm nuevo nacía sin login. Fix: filtrar por
ownership (ids de executions del subflow) antes del DELETE. Verificado con
el código real de la plataforma contra un realm desechable: authorize →
**200 con formulario** (antes 400). Además: la imagen estaba rota para todo
el factory por `argon2` sin declarar (spec 025) — corregido en
pyproject.toml. Detalle en los comentarios del card (comentarios #100/#101).

## Retro-fit de realms existentes (T5)

`platform/scripts/apply_auth_ux_realms.py` (idempotente): **62 realms
actualizados, 0 fallos** — incluye realms con el cableado viejo spec 011
(el alias `trust-loa-level-1` de su condición OTP colisionaba con la
condición nueva del Password LoA → 409; fix: aliases por subflow
`trust-loa-password-level-1` / `trust-loa-otp-level-2` + uniquificación en
conflicto, el alias es cosmético).

## Addendum 2026-10-27 — Favicon del realm (t_13f8b623, hallazgo QA re-rev Pilar 2 §10)

**Bug**: el realm servía el favicon-caballo de Keycloak por fallback al tema
padre (`platform/idp-themes/trust-agent/login/` no traía `img/favicon.ico`;
md5 servido `0980755aea543da1af848366656d1d9c` = byte-idéntico al de
`keycloak.v2`). White-label incompleto en la pestaña del navegador
(AC-AUTH-001).

**Fix (spec-first)**:
1. Spec 015 AC-AUTH-001 enmendada para nombrar el favicon explícitamente
   (`specs/015-auth-ux/spec.md`).
2. `platform/idp-themes/trust-agent/login/resources/img/favicon.ico` — PNG
   32×32, 641 bytes, md5 `c7c057a90fa1e51ed1fc4ec2f812ba93`: diamante rojo
   `#c90104` (la marca del header del tema, `trust.css`) sobre fondo
   transparente, anti-aliased (supersample 256→32).

**Verificación en vivo (KC 26.2.5 `trust-agent-idp`, sin restart — el bind
mount + resolución de recursos de KC lo tomó al vuelo)**:

```
$ curl -s "http://127.0.0.1:8080/realms/demo-org/protocol/openid-connect/auth?..." \
    | grep -o '<link rel="icon"[^>]*>'
<link rel="icon" href="/resources/8zyis/login/trust-agent/img/favicon.ico" />
$ curl -s http://127.0.0.1:8080/resources/8zyis/login/trust-agent/img/favicon.ico | md5sum
c7c057a90fa1e51ed1fc4ec2f812ba93   # = on-disk, ≠ caballo (0980755a…)
$ curl -s http://127.0.0.1:8080/resources/8zyis/login/keycloak.v2/img/favicon.ico | md5sum
0980755aea543da1af848366656d1d9c   # padre sigue siendo el caballo — ya no se usa
```

Navegador real (Chromium/CDP) sobre el login del realm: `link[rel=icon]` →
`/resources/8zyis/login/trust-agent/img/favicon.ico`, fetch desde el
contexto de página → 200, 641 bytes, PNG magic ✓ (el caballo era 627 bytes);
`<title>Sign in to Trust Agent</title>` (curl, HTML limpio); 0 menciones de
marca "Keycloak" en el DOM (las 4 minúsculas son ids técnicos tipo
`body#keycloak-bg`, hooks CSS del propio tema). El emoji 🐴 del título de
pestaña del tooling es el artefacto del harness documentado por QA
(script CDP que prefija el emoji del favicon a `document.title`), no el
producto.

## Cómo reproducirlo

```bash
# tenant de evidencia (ya existe; para uno nuevo):
docker exec trust-agent-api trust-provision provision \
  --org "Auth UX Evidence" --admin-email admin@authux-ev.test \
  --budget-usd 100 --slug authux-ev
curl -s "http://127.0.0.1:8000/api/auth/org-config?org=authux-ev"
# navegador: http://127.0.0.1:3100/register?org=authux-ev  → crear cuenta
#            http://127.0.0.1:3100/login?org=authux-ev     → login + banner
# retro realms existentes:
cd /root/trust-agent && TRUST_DATABASE_URL=… TRUST_DATABASE_MIGRATOR_URL=… \
  platform/.venv/bin/python platform/scripts/apply_auth_ux_realms.py
```

## Addendum (2026-10-27) — Descope AC-AUTH-006 del v0.1.0-pilot + re-verificación fail-closed en vivo

**Decisión**: AC-AUTH-006 queda descoped del v0.1.0-pilot como gap
documentado (enmienda spec 015 v1.2.0 §7). Decisión por default del
orquestador (worker `default`, t_f372f47f, 2026-10-27 13:28) ante ausencia
de respuesta del operador; reversible en cuanto lleguen
`TRUST_GOOGLE_CLIENT_ID`/`TRUST_GOOGLE_CLIENT_SECRET`. El gate final
(t_e8a28fce) lo trata como pass-with-documented-gap. Re-entra en v0.1.1.

**Re-verificación fail-closed en vivo (este run, stack real, 2026-10-27
~17:30 UTC-6)**:

- Precondición: `grep -c TRUST_GOOGLE_CLIENT /root/trust-agent/.env` → `0`
  (sin credenciales configuradas).
- `curl -s "http://127.0.0.1:8000/api/auth/org-config?org=pilar2-141938"` →
  `{"org":"pilar2-141938","require_mfa":true,"allow_self_registration":true,"google_login_enabled":false}`
- HTML de login del realm (authorize endpoint de KC 26.2 vivo,
  `http://127.0.0.1:8080/realms/pilar2-141938/protocol/openid-connect/auth?...`):
  `grep -o -i google | wc -l` → `0` — ningún botón/elemento Google.

**Veredicto**: PASS (fail-closed intacto). Ningún usuario ve un botón
Google roto; el brokering no está expuesto hasta que el operador aporte
credenciales. Coherente con la verificación de QA con navegador real del
2026-10-26 (docs/evidencia/pilar2).

## Addendum (2026-10-27) — GAP-10.2: sync de `users.mfa_enrolled` tras enrolamiento TOTP (t_edbcface, AC-AUTH-010 / D-AUTH-11, spec 015 v1.1.0)

**Hallazgo QA** (t_db7b2fa9, GAP-10.2): el usuario completaba el enrolamiento
TOTP inline (banner fuera, sesión `acr=2`) pero `users.mfa_enrolled` seguía
`false` — la columna solo se fijaba en el INSERT inicial
(register/invite/provision); el directorio podía mostrar "sin MFA" de un
usuario que la tenía activa.

**Fix (D-AUTH-11)**: sincronización lazy en los funnels de auth con claims
MFA verificados — `POST /api/auth/session` (mint), rama JWT stateless de
`authenticate()` y `POST /api/devices/enroll` hacen
`UPDATE users SET mfa_enrolled=true WHERE id=:uid AND NOT mfa_enrolled`
en la MISMA transacción tenant-scoped (RLS, constitution III). Monotónico
(high-water mark); des-inscripción fuera de alcance v1 (sin flujo de
producto). El directorio (`GET /api/users`, `PUT /api/users/{id}`) expone
`mfa_enrolled` y la página Users del console pinta el badge.

**Evidencia live (stack real, 2026-10-27 ~17:22 CST, tenant del repro
original `qa-p2rev-223359`)** — script
`platform/scripts/evidence_mfa_enrolled_sync.py` (flujo 100% real, nada
simulado):

```
[PASS] POST /api/auth/register → 201 (self-registration real)
[PASS] SQL tras INSERT: users.mfa_enrolled = false (estado pre-fix)
[PASS] GET /auth/login?org&acr=2 → redirect al authorize con acr_values=2
[PASS] login form del realm servido (tema trust-agent)
    [dance] enrolamiento TOTP inline: secret=[REDACTED-POST-GATE — semilla TOTP = credencial, fuera del repo (t_b62edff2)] code=497673
[PASS] baile KC con acr=2 → code (enrolamiento TOTP real completado)
[PASS] callback del console → 307 /overview (sesión mintada, acr=2)
[PASS] SQL tras el mint: users.mfa_enrolled = true (D-AUTH-11 sync)
[PASS] GET /api/proxy/users → entrada del usuario con mfa_enrolled=true
[PASS] GET /users → 200 HTML (página del directorio, badge MFA)
[PASS] segundo baile acr=1 (password-only) → authorize con acr_values=1
[PASS] password-only → code SIN formulario OTP (acr=1)
[PASS] callback del 2º baile → 307 /overview (mint sin MFA)
[PASS] SQL tras mint sin evidencia MFA: mfa_enrolled SIGUE true (monotónico)
[PASS] whoami del 2º baile: mfa_verified=false (sesión password-only)
EVIDENCE PASS — GAP-10.2 convergido con flujo real
```

SQL directo (PostgreSQL vivo, trust-agent-db):

```
               email                | mfa_enrolled | status |      slug
------------------------------------+--------------+--------+-----------------
 gap102-172141@qa-p2rev-223359.test | t            | active | qa-p2rev-223359
 gap102-172216@qa-p2rev-223359.test | t            | active | qa-p2rev-223359
```

**UI en navegador real** (Chromium, login password-only del usuario de
evidencia): la página `/users` renderiza la columna **MFA** con badge
**enrolled** en la fila del usuario (`USER | DEPARTMENT | ROLES | STATUS |
MFA` → `active | enrolled`), consistente con el banner "Protect your
account" de la sesión acr=1. Screenshot:
`screenshots/gap102-mfa-enrolled-directory.png`.

**Tests**: `tests/test_auth_ux.py` +5 (mint con evidencia sincroniza; mint
acr=1 no voltea; monotónico; JWT stateless sincroniza; directorio expone) —
17 passed. Adyacentes (devices, isolation, departments, audit_auth,
org_endpoints): 70 passed. Suite completa: 671 passed / 11 failed — los 11
fallos (provision, usage_analytics, model_access, jobs, dynamic_discovery)
son de lanes hermanas/ordering: `test_provision_full_flow` pasa aislado y
`test_auth_ux.py + test_provision.py` juntos dan 27 passed; ninguno toca
los módulos de este fix (verificado este run).

**Veredicto**: AC-AUTH-010 **VERIFICADO OK** con evidencia real (flujo
positivo + negativo monotónico + UI).
