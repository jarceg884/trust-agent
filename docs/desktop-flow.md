# Flujo del desktop white-label — login, enrolamiento y sesión gobernada

> Spec 010 (`specs/010-desktop-enrollment/`). Implementación cliente:
> `platform/desktop-client/` (Next.js App Router + TS + Tailwind, puerto **9090**).
> Contratos de API: specs 003 (sesiones/device token), 005 (gobernanza), 007 (dispositivos).
> El desktop **es Hermes Agent configurado** — ver §6 y `platform/desktop-client/hermes/README.md`.

## 1. Visión general

El usuario final descarga el desktop white-label (marca de su organización).
La primera experiencia tiene tres momentos:

```
┌────────────┐   1. LOGIN OIDC/PKCE   ┌────────────┐   2. ENROLLMENT      ┌─────────────┐
│  Login     │ ─────────────────────► │ Enroll     │ ───────────────────► │ Aprobación  │
│ (org code) │   SSO+MFA del realm    │ (keypair   │   admin de la org    │ (consola    │
└────────────┘                        │  local)    │   aprueba el device  │  admin)     │
                                      └────────────┘                      └─────────────┘
                                                                                │
┌──────────────────────────────────────┐   3. SESIÓN GOBERNADA                 │
│ Dashboard: presupuesto, políticas,   │ ◄──────────────────────────────────────┘
│ modelos aprobados, dispositivos      │   device token + políticas/perfil/skills de la org
└──────────────────────────────────────┘
```

## 2. Login — OIDC authorization code + PKCE S256 (REQ-DTB-001)

El usuario solo escribe su **código de organización** (`org_code` =
`tenants.slug` = realm Keycloak). El desktop construye el issuer como
`{idp_base}/realms/{org_code}` (D-DTB-01) — el `org_code` desambigua el realm,
pero **no es raíz de confianza**: el tenant siempre sale del claim firmado
`tenant_id` del `id_token` verificado por JWKS (D-CP-03).

Secuencia (todo el baile criptográfico ocurre **server-side** en el servidor
Next.js local; el navegador jamás ve el verifier ni los tokens):

```
Navegador                  Next.js :9090                    Keycloak :8080              Control plane :8000
   │ POST / (org_code)          │                                │                          │
   │ ─────────────────────────► │ genera verifier + state        │                          │
   │                            │ (cookies httpOnly ta_pkce_*)   │                          │
   │ 302 → /realms/{org}/…/auth │                                │                          │
   │ ◄───────────────────────── │                                │                          │
   │ login UI (password + TOTP) ────────────────────────────────► │                          │
   │ 302 → /auth/callback?code&state                             │                          │
   │ ──────────────────────────► │ canje code→id_token (verifier) │                          │
   │                            │ ─────────────────────────────► │                          │
   │                            │ POST /api/auth/session {id_token, kind:"desktop"} ───────► │
   │                            │                                 │  JWKS + rate-limit +     │
   │                            │ ◄── session/refresh/csrf + expires_at ──────────────────  │  sesión PostgreSQL
   │ 302 → /dashboard           │ cookies httpOnly ta_session/ta_refresh/ta_csrf/ta_idtoken │  + audit auth.login
```

- Cliente público `trust-agent-web`; redirect URI del desktop:
  `http://127.0.0.1:9090/auth/callback` (registrada por el aprovisionamiento,
  `oidc_redirect_uris`).
- **MFA obligatorio** (`require_mfa`, catálogo `policy_keys`): el IdP exige
  TOTP (required action `CONFIGURE_TOTP` del aprovisionamiento — Keycloak 26
  NO permite enrolar TOTP vía Admin REST, pitfall #9 del skill
  `identity/keycloak-admin-oidc`; lo enrola el propio usuario en su primer
  login, flujo real sin scraping).
- El mint de sesión (`POST /api/auth/session`) verifica el `id_token` con
  JWKS (firma/exp/aud/iss), aplica rate limiting de login y crea la sesión en
  PostgreSQL (nunca en memoria). Devuelve `session_token` (Bearer), 
  `refresh_token`, `csrf_token`, `expires_at`.
- Custodia local de tokens (D-DTB-03): cookies **httpOnly** del servidor
  Next.js local — equivalente web del keychain del desktop empaquetado.

## 3. Enrolamiento del dispositivo (REQ-DTB-002/003, spec 007)

Primera clase, **cero scraping** (D-DEV-01): la identidad del dispositivo la
aporta el propio dispositivo.

1. **Keypair local** (WebCrypto en el navegador, RSA-2048): la clave pública
   viaja como SPKI PEM; la **privada (PKCS#8) nunca sale del dispositivo**
   (v1 web: `localStorage`; empaquetado: keychain del SO).
2. **Fingerprint estable** por instalación (`web-<platform>-<uuid>`).
3. `POST /api/devices/enroll` — el route handler `/api/desktop/enroll`
   inyecta el `id_token` (cookie httpOnly; la API exige JWT con evidencia
   MFA: `amr` o `acr` ≥ 2, D-MFA-01) y
   el `org_code` (= slug del tenant verificado vía `/api/me`). Respuesta **202**
   `{device_id, status:"pending", enrollment_token}` — el `enrollment_token`
   es **one-time** (`enr1.<tenant>.<device>.<secret>`; en DB solo su sha256).
4. **Espera de aprobación** (D-DEV-04): el canje ES el polling. La pantalla
   muestra "waiting for approval" y repite `POST /api/devices/enroll/exchange`
   cada 5 s:
   - `pending` → **202** (no consume) — sigue esperando;
   - admin aprueba (consola admin, `POST /api/devices/{id}/approve`) →
     **200** `{device_token, expires_in}` (consume el hash) — el desktop
     guarda el device token en el keystore local;
   - rechazado/revocado/token consumido → **410** uniforme, sin oráculo
     (mensaje único "enrollment no longer valid").
5. Audit: `device.enrolled` (usuario) y `device.approved` (admin) con
   `device_id` (REQ-DEV-006).

El device token JWT (HS256, firmado por el control plane, TTL 15 min) es la
credencial de dispositivo: pasa el `DeviceGate`, actualiza `last_seen_at`
(throttle 60 s) y su revocación invalida las sesiones ligadas **en la misma
transacción** (≤ 60 s por construcción, D-DEV-07). Renovación: `POST
/api/devices/{device_id}/token` (sesión + MFA + propiedad).

## 4. Recepción de políticas / perfiles / skills de la organización

Con el dispositivo `active`, el usuario queda en **sesión gobernada**. Lo que
la organización le entrega:

| Qué | Mecanismo | Dónde se ve |
|---|---|---|
| Presupuesto jerárquico (org→depto→usuario, USD centavos) | `GET /api/me/budget` — gastado/restante/corte por nivel | Dashboard "Budget" |
| Políticas efectivas (combinador org∩depto∩usuario + piso del proveedor) | `GET /api/me/policies` | Dashboard "Organization policies active" |
| Modelos aprobados (intersección de model access + política `allowed_models`) | `GET /api/models/allowed-for-me` | Dashboard "Approved models" |
| Dispositivos propios | `GET /api/devices/me` | Dashboard "Your devices" |
| Inbox de gobernanza (cortes, umbrales) | `GET /api/me/notifications` | Dashboard "Governance notifications" |
| Perfil del agente (SOUL, config, skills del departamento) | **Profile distribution** de Hermes (`hermes profile install`) | Sesión del agente |
| Virtual key de modelos (modelos permitidos + `max_budget`) | Emisión one-time del control plane → `.env` del perfil | Gateway LiteLLM |

## 5. Sesión gobernada (dashboard, REQ-DTB-004)

El dashboard (`/dashboard`, RSC) muestra **solo datos reales** de la API
(constitution I — cero mocks): identidad (`/api/me`), presupuesto con banner
de corte real (`cutoff: true`), políticas activas (las invariables marcadas
"Provider floor — cannot be relaxed"), modelos aprobados, dispositivos con
estado y último acceso, notificaciones. Estados de carga/error/vacío reales;
el error muestra el `request_id` del envelope §5.2 (REQ-DTB-007). Sin sesión →
redirect a login; 401 de la API → sesión revocada → login.

Lo que la sesión gobernada **impone** (no solo muestra):

- **Solo modelos aprobados**: el tráfico LLM pasa por el gateway LiteLLM del
  tenant con la virtual key del usuario (D-GOV-15) — la key rechaza modelos no
  aprobados y aplica presupuesto. Sin `fallback_model` en el perfil: nada
  evade el gateway.
- **Presupuesto con corte**: al agotarse el presupuesto, el control plane
  bloquea la key (401), notifica al inbox y el dashboard muestra el corte.
- **Políticas activas**: MFA obligatorio, `approved_models_only`, grants
  explícitos para agentes compartidos, credenciales con scope mínimo —
  evaluadas server-side en cada operación.
- **Audit completo**: login, enrolamiento, uso de modelos, cortes — cadena
  append-only verificable (`audit_verify_chain`).

## 6. Integración con Hermes Agent (oficial, sin forks)

**El desktop es Hermes configurado** (D-DTB-07, constitution IV, lección C6).
Artefactos en `platform/desktop-client/hermes/`:

1. **Profile por usuario** (`profile-template/config.yaml`): Hermes corre con
   un perfil propio por usuario; su `model.base_url` apunta al proxy LiteLLM
   del tenant con `api_key = ${TRUST_USER_VIRTUAL_KEY}`. **El gate real de
   modelos/presupuesto es LiteLLM** — no hay parche posible que lo evite.
2. **Profile distribution**: la plantilla (SOUL/config/skills por
   departamento) se publica como repo git y se instala con
   `hermes profile install <repo> --alias <user>`; `hermes profile update`
   actualiza la plantilla sin tocar credenciales/memorias/sesiones de la
   máquina.
3. **Plugin `trust-agent-governance`** (`plugin.yaml` + `__init__.py::
   register(ctx)` — API verificada en `hermes_cli/plugins.py`):
   - tool `governance_status`: estado gobernado en vivo (presupuesto,
     políticas, modelos) desde la API con el device token;
   - hook `pre_llm_call`: **inyecta contexto** de gobernanza en cada turno
     (verificado: es un hook de contexto, no de veto — el veto de modelos lo
     hace LiteLLM);
   - hook `pre_tool_call`: **veto de defensa en profundidad** (p. ej.
     delegación de agentes con presupuesto en corte) — verificado:
     `_dispatch_pre_tool_call_hooks` consume `block_message`.

Cero parches a `hermes_cli/`; sin `verify=False`; secretos one-time; sin ROPC
para usuarios finales.

## 7. Cómo ejecutar el desktop (desarrollo local)

```bash
# 1) Control plane (platform/README.md): PostgreSQL :55432 + Keycloak :8080
#    y uvicorn :8000 con TRUST_* correspondientes.

# 2) Desktop client (puerto 9090 — redirect URI registrada)
cd /root/trust-agent/platform/desktop-client
npm install
npm run build && npm start        # http://127.0.0.1:9090
# (dev: npm run dev)

# Variables (defaults ya apuntan al stack local):
#   TRUST_API_BASE_URL=http://127.0.0.1:8000
#   TRUST_IDP_BASE_URL=http://127.0.0.1:8080
#   TRUST_DESKTOP_REDIRECT_URI=http://127.0.0.1:9090/auth/callback
```

Flujo de prueba: login con el `org_code` del tenant → SSO+MFA del realm →
dashboard (si el dispositivo no está enrolado, banner con "Enroll this
device") → enrolamiento → aprobación desde la consola admin (o API
`POST /api/devices/{id}/approve`) → el polling del desktop recibe el device
token → dashboard con el dispositivo `active`.

## 8. Pendientes conocidos (documentados, no bloqueantes)

- **Branding por tenant** (D-DTB-08): a la espera del endpoint de branding en
  la API; v1 usa el branding por defecto del mockup ("Harper Agent").
- **Keystore v1 en `localStorage`**: el empaquetado del desktop migrará a
  keychain del SO (documentado en el código).
- **Renovación automática del device token** (TTL 15 min): el endpoint existe
  (`POST /api/devices/{id}/token`, verificado en vivo: 200 con `expires_in`
  900); la automatización en el runtime del desktop queda para el empaquetado.
- **Sesiones del agente en el sidebar** (Sessions/Capabilities/Shared
  agents/Artifacts): corresponden al runtime de Hermes; el dashboard mínimo
  entrega el estado gobernado (alcance de esta tarea).
- **`require_device` (DeviceGate) sin rutas de negocio que lo monten aún**:
  el gate existe (`app/devices/gate.py`, spec 003/007) pero ninguna ruta de
  data plane lo exige todavía; corresponde a la lane de sesiones de agente.

## 9. Evidencia del smoke en vivo (2026-10-23) y hallazgo KC26

Smoke completo con browser real contra PostgreSQL 17 + Keycloak 26 + uvicorn
(:8000) + desktop client (:9090), tenant `demo-org` aprovisionado en vivo:

1. Login org code → redirect PKCE S256 al realm real → one-time password del
   aprovisionamiento → required actions reales (CONFIGURE_TOTP — QR
   decodificado, TOTP RFC 6238 — y UPDATE_PASSWORD) → callback → sesión
   mintada (201) → dashboard.
2. Enrolamiento: WebCrypto RSA-2048 en el navegador → enroll 202 pending →
   approve (200 active) → polling canjea el enrollment_token one-time →
   device token guardado local → renovación OK (expires_in 900).
3. Dashboard 100% real: presupuesto org $500.00/$500.00, 4 políticas
   "Provider floor", 2 modelos aprobados (deepseek-v3, qwen2.5-72b-instruct),
   dispositivo `active`. Audit DB: `device.enrolled`/`device.approved` con
   `device_id`.

**Hallazgo (plataforma) — RESUELTO (t_c43906d1, spec 011 D-MFA-01)**:
Keycloak 26 NO emite `amr` en id_token ni access_token con configuración
por defecto (solo `acr:"1"`; probe real con login password+TOTP). La puerta
MFA exigía `amr`, así que **todo login real de KC recibía 403
`mfa_required`**. Arreglo de raíz: la puerta acepta `amr` **O** `acr` ≥
`TRUST_MFA_MIN_ACR` (2) — `app/auth/mfa.py`; el aprovisionamiento cablea
cada realm con `ensure_mfa_acr()` (`acr.loa.map` + condición LoA en el
flujo browser); el desktop pide `acr_values=2` en el authorize y su
callback hace step-up (re-intento único) si el mint llega sin MFA.
Verificado en vivo (realm `demo-org`, KC 26.2 real): password+TOTP →
`acr:"2"` → sesión `mfa_verified=true` → `/api/me` 200; password-only sin
TOTP → `CONFIGURE_TOTP` inline, sin code. Detalles y pitfalls:
skill `identity/keycloak26-amr-mfa` y `specs/011-mfa-acr-evidence/`.

**Nota de entorno tras el smoke**: `tenants.oidc_issuer_url` de `demo-org`
quedó apuntando al doble (puerto 8908, ya apagado) porque el comando de
restauración fue bloqueado por el aprobador de comandos. Restaurar con:

```sql
UPDATE tenants SET oidc_issuer_url = 'http://127.0.0.1:8080/realms/demo-org'
WHERE slug = 'demo-org';
```
