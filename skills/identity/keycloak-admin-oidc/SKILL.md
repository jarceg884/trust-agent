---
name: keycloak-admin-oidc
description: "Keycloak Admin REST API + OIDC authorization-code con PKCE + verificación JWKS de JWT. Comandos probados contra Keycloak 26.2 local. Use when aprovisionar realms/clientes/usuarios por API o implementar auth OIDC."
version: 1.0.0
author: trust-agent dev
metadata:
  hermes:
    tags: [keycloak, oidc, oauth2, pkce, jwks, jwt, admin-rest, identity, auth]
---

# Keycloak — Admin REST + OIDC authorization-code/PKCE + JWKS

**Comandos PROBADOS** contra Keycloak 26.2 (contenedor local `keycloak`,
127.0.0.1:8080, 23-oct-2026). Script de prueba e2e reutilizable:
`/root/trust-agent/scripts/test_keycloak_pkce.py`. Docs:
https://www.keycloak.org/docs/26.7/securing_apps/ — Admin REST:
https://www.keycloak.org/docs-api/26.7.1/rest-admin/index.html

## 1. Admin REST API

Bootstrap: token con `admin-cli` + password grant (solo para bootstrap interno;
ROPC prohibido para usuarios finales — constitution IV):

```bash
KC=http://127.0.0.1:8080
TOKEN=$(curl -s -X POST $KC/realms/master/protocol/openid-connect/token \
  -d 'grant_type=password&client_id=admin-cli&username=admin&password=<pass>' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
```

Operaciones probadas (todas devolvieron el código indicado):

```bash
# Crear realm — HTTP 201
curl -X POST $KC/admin/realms -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"realm":"trust-agent-test","enabled":true}'

# Crear cliente público con PKCE S256 — HTTP 201
curl -X POST $KC/admin/realms/trust-agent-test/clients -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "clientId":"trust-agent-web","enabled":true,"protocol":"openid-connect",
    "publicClient":true,"standardFlowEnabled":true,"directAccessGrantsEnabled":false,
    "redirectUris":["http://127.0.0.1:8765/callback"],
    "attributes":{"pkce.code.challenge.method":"S256"}}'

# Crear usuario — HTTP 201
curl -X POST $KC/admin/realms/trust-agent-test/users -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo","enabled":true,"email":"demo@test.local","firstName":"Demo","lastName":"User"}'

# Reset password — HTTP 204 (buscar id antes con GET .../users?username=demo)
curl -X PUT $KC/admin/realms/trust-agent-test/users/$USER_ID/reset-password \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"type":"password","value":"...","temporary":false}'
```

Endpoints clave (base `http://<host>:<port>/admin/realms/{realm}`):

| Método | Path | Uso |
|---|---|---|
| GET/POST | `/admin/realms` | listar/crear realms |
| GET/PUT/DELETE | `/admin/realms/{realm}` | leer/actualizar/borrar realm |
| GET/POST | `/admin/realms/{realm}/users` | listar (`?username=X` exact match)/crear |
| PUT | `/admin/realms/{realm}/users/{id}/reset-password` | setear password |
| GET/POST | `/admin/realms/{realm}/clients` | listar (`?clientId=X`)/crear |
| GET/POST | `/admin/realms/{realm}/roles` | roles de realm |
| POST | `/admin/realms/{realm}/users/{id}/role-mappings/realm` | asignar roles |
| GET/POST | `/admin/realms/{realm}/groups` | grupos |
| GET | `/admin/realms/{realm}/clients/{uuid}/client-secret` | secret de cliente confidencial |

Para aprovisionar tenants de Trust Agent: **1 realm por tenant** (aislamiento
máximo, coste de operación alto) o **1 client por tenant en un realm compartido**
(más ligero; los claims `groups`/attributes llevan el tenant). El provider
self-hosted OIDC de Hermes acepta `org_id`/`organization` o `groups` como org.

## 2. OIDC endpoints del realm

```bash
# Discovery (probado — responde issuer, authorization/token/jwks_uri endpoints)
curl -s $KC/realms/<realm>/.well-known/openid-configuration

# JWKS (probado — 2 claves: RS256 para firmar, RSA-OAEP para cifrar)
curl -s $KC/realms/<realm>/protocol/openid-connect/certs

# Userinfo con access token (probado HTTP 200)
curl -s $KC/realms/<realm>/protocol/openid-connect/userinfo -H "Authorization: Bearer $ACCESS"
```

## 3. Authorization code + PKCE (S256) — flujo completo probado

```python
# 1) Generar verifier (43-128 chars, b64url de 64 bytes aleatorios ≈ 86) y challenge
verifier  = base64url(os.urandom(64))                          # sin padding '='
challenge = base64url(sha256(verifier.encode()).digest())      # S256

# 2) GET authorize (el navegador del usuario en producción):
#    /realms/<realm>/protocol/openid-connect/auth?
#      response_type=code&client_id=<client>&scope=openid
#      &redirect_uri=<uri>&state=<random>&code_challenge=<challenge>&code_challenge_method=S256
# 3) Login del usuario -> 302 a redirect_uri?code=...&state=...
# 4) Exchange backend-side:
POST /realms/<realm>/protocol/openid-connect/token
  grant_type=authorization_code&client_id=<client>&redirect_uri=<uri>
  &code=<code>&code_verifier=<verifier>          # cliente público
  # cliente confidencial: + client_secret (o Basic auth)
```

Resultado real de la prueba (Keycloak 26.2, realm `trust-agent-test`):

```
[1] exchange scope=openid: HTTP 200, id_token len=1106, access len=1422, refresh=True
[2] JWT verificado: sub=997a278c... iss=http://127.0.0.1:8080/realms/trust-agent-test
    aud=trust-agent-web email=demo@test.local exp_en=300s
[3] verifier incorrecto: HTTP 400, error=invalid_grant, desc=PKCE verification failed
[4] refresh_token: HTTP 200, nuevo id_token=True, nuevo refresh=True (rotación)
[5] userinfo con access_token: HTTP 200, sub=..., email=...
```

## 4. Verificación JWKS del ID token (PyJWT) — patrón probado

```python
import jwt
from jwt import PyJWKClient

jwks_client = PyJWKClient(f"{issuer}/protocol/openid-connect/certs",
                          cache_keys=True, lifespan=300)   # cache 5 min
key = jwks_client.get_signing_key_from_jwt(id_token)       # match por kid del header
claims = jwt.decode(
    id_token, key.key,
    algorithms=["RS256", "ES256"],          # NUNCA incluir HS256 (algorithm confusion)
    audience=client_id,                     # aud pineado
    issuer=f"{kc_base}/realms/{realm}",     # iss pineado
    options={"require": ["exp", "iat", "aud", "iss", "sub"]},
    leeway=60,                              # tolerancia de reloj (0 = estricto)
)
```

Reglas: verificar **siempre** firma (contra JWKS), `exp`, `nbf`/`iat`, `aud`,
`iss`, `sub`. El `access_token` de Keycloak también es un JWT firmado por el
mismo JWKS (útil para `verify_session` por request); el ID token es el de
identidad. Mismo patrón que usa el provider self-hosted OIDC de Hermes
(`plugins/dashboard_auth/_shared.py::verify_jwt`).

## 5. Pitfalls (todos verificados en la prueba real)

0. **Keycloak 26: el User Profile declarativo DESCARTA atributos no declarados
   en silencio**. Crear/actualizar un usuario con `attributes` (p. ej. el
   claim `tenant_id` de un mapper `oidc-usermodel-attribute-mapper`) NO
   persiste y NO da error: el GET del usuario devuelve `attributes: null`.
   Fix verificado (Admin REST):
   ```bash
   # 1) habilitar atributos no gestionados en el realm
   GET  /admin/realms/{realm}/users/profile          # -> config actual
   PUT  /admin/realms/{realm}/users/profile          # con "unmanagedAttributePolicy": "ENABLED"
   # 2) recién ahora el PUT del usuario con attributes persiste (204 -> GET los devuelve)
   ```
   Sin este paso el token sale SIN el claim y el verificador lo rechaza.
1. **Sin `scope=openid` NO hay `id_token`** — el primer intercambio devolvió
   access+refresh pero `id_token` vacío. Siempre `scope=openid` (+ `profile
   email` para los claims de identidad).
2. **El POST al formulario de login necesita las cookies del GET authorize**
   (`AUTH_SESSION_ID`, `KC_RESTART`). En tests headless con http.client/curl:
   capturar TODOS los `Set-Cookie` (¡no colapsar en dict! varios headers con el
   mismo nombre) y reenviarlos.
3. **PKCE se configura en el client** vía attribute
   `pkce.code.challenge.method: "S256"`; con `publicClient: true` +
   `standardFlowEnabled: true` y `directAccessGrantsEnabled: false` (ROPC off).
4. **redirect_uri debe coincidir exactamente** con un `redirectUris` del client.
5. **Verifier incorrecto → `invalid_grant` HTTP 400** ("PKCE verification
   failed") — no un error genérico: distinguible para telemetría.
6. El `state` lo valida el CLIENTE (Keycloak lo echo de vuelta); el
   `code_verifier` lo valida Keycloak en el token endpoint. Ambos obligatorios.
7. Access tokens de Keycloak viven ~5 min por defecto (300s en la prueba);
   refrescar con `grant_type=refresh_token` (rota el refresh token).
8. `exp`/`iat` con reloj desincronizado: usar `leeway` (Hermes usa 60s por
   defecto, fail-closed si el valor configurado es inválido).
9. **Keycloak 26 NO permite enrolar TOTP vía Admin REST** (verificado con
   probe real, 2026-10-23): `POST /admin/realms/{r}/users/{u}/credentials`
   con `{"type":"otp","value":"<base32>"}` → **404** (el endpoint no existe;
   el enrolamiento de OTP solo va por el flujo web de `requiredActions`
   CONFIGURE_TOTP). Implicación para e2e: NO se puede producir un
   `id_token` con `amr=["mfa","otp"]` de forma automatizada sin scraping
   de HTML/QR (prohibido — hallazgo C5 de la auditoría AIDLC). Patrón
   honesto usado por la suite e2e de trust-agent
   (`platform/tests/e2e/test_control_plane_flow.py`): login PKCE REAL
   contra el realm (verifica JWKS real + puerta `mfa_required` 403), y para
   los pasos MFA-restantes un doble JWKS declarado (RSA real + PyJWKClient
   real) sustituyendo `tenants.oidc_issuer_url`, documentado en el test.
10. **El enrolamiento TOTP SÍ es scripteable por el flujo web** (verificado
    2026-10-24, lane UI / smoke del Admin Console): el required action
    CONFIGURE_TOTP sirve el secreto en un `<input type="hidden"
    name="totpSecret" value="…">` del HTML (sin scraping de QR: es un campo
    de formulario legítimo). El POST del código va a la `action` del form
    con `totp=<código>&totpSecret=<secreto>&mode=totp&userLabel=…`. El
    código se valida en ese mismo POST (código inválido → re-render con
    `id="input-error-otp-code"` "Invalid authenticator code." y el MISMO
    secreto). Script de referencia:
    `platform/console/scripts/smoke_console.py` (función `login_dance`).
11. **El secreto TOTP de KC26 NO es base32: la clave HMAC son los bytes
    UTF-8 raw del string** (verificado 2026-10-24): el `totpSecret` es
    alfanumérico mixto (p. ej. `of5MNau69u2vG7BVOGsk`, contiene 0/1/8/9 →
    `base64.b32decode` falla con "Non-base32 digit found"). El código
    válido se genera con `hmac.new(secret.encode("utf-8"), counter,
    SHA1)` — RFC 6238 estándar (6 dígitos, 30 s, política default del
    realm). OJO: `otpPolicyCodeReusable=false` — el código consumido en el
    enrolamiento NO sirve para el segundo factor del login siguiente; usar
    counter+1 (el `otpPolicyLookAheadWindow=1` lo acepta).
12. **KC26 NO emite claim `amr` en el id_token** (verificado 2026-10-24 con
    login real password+OTP: `amr: None`, `acr: 1`): cualquier verificación
    de MFA basada en `amr` (como `_amr_mfa` del control plane,
    `platform/app/auth/router.py` + `auth/service.py`) rechaza TODO login
    real de Keycloak. Bug trackeado como tarea dev (t_59afbe56); para e2e
    MFA-positivo usar el doble JWKS declarado del pitfall #9.

## 6. Artefactos de la prueba (reutilizables)

- Realm `trust-agent-test` creado en el Keycloak local (127.0.0.1:8080) con
  cliente `trust-agent-web` (público, PKCE S256) y usuario `demo` — disponible
  para las siguientes tareas del proyecto.
- Script e2e: `/root/trust-agent/scripts/test_keycloak_pkce.py` (flujo completo +
  verificación JWKS + casos negativos). Credenciales admin del contenedor:
  `docker inspect keycloak` (env `KEYCLOAK_ADMIN`/`KEYCLOAK_ADMIN_PASSWORD`).

## Fuentes

- https://www.keycloak.org/docs/26.7/securing_apps/ (OIDC, authorization code + PKCE)
- https://www.keycloak.org/docs-api/26.7.1/rest-admin/index.html (Admin REST)
- https://datatracker.ietf.org/doc/html/rfc7636 (PKCE), RFC 7519 (JWT), RFC 7517 (JWK)
- Código de referencia de verificación: `/usr/local/lib/hermes-agent/plugins/dashboard_auth/_shared.py`
- Skill relacionado: `devops/keycloak` (instalación/HA/federación) y
  `devops/keycloak-huawei-cloud` (despliegue en ECS+RDS)
