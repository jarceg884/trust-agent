# Plan técnico — Spec 010 (Desktop: flujo de enrolamiento white-label)

| Campo | Valor |
|---|---|
| **Spec** | specs/010-desktop-enrollment/spec.md |
| **Fecha** | 2026-10-23 |

## 1. Arquitectura del cliente

```
Navegador (usuario final)          Servidor Next.js (local, :9090)         Control plane (:8000)        Keycloak (:8080)
──────────────────────────         ───────────────────────────────         ─────────────────────        ───────────────
1. POST / (org_code)       ──────► server action: genera verifier+state,
                                   cookies httpOnly (pkce_verifier, state)
2.                          ◄───── redirect 302 → /realms/{org}/…/auth?code_challenge=S256
3. login UI (password+TOTP) ───────────────────────────────────────────────────────────────────────────────►  authorize
4.                          ◄───── 302 → http://127.0.0.1:9090/auth/callback?code=…&state=…
5. GET /auth/callback       ─────► route handler: canje code→tokens (server-side,
                                   con verifier de la cookie) ─────────────────────────────────────────────►  token endpoint
                                   POST /api/auth/session {id_token, kind:"desktop"} ──────────►  mint sesión
                                   set cookies httpOnly: ta_session, ta_refresh, ta_csrf, ta_idtoken
6. GET /dashboard           ◀───── RSC: fetch server-side /api/me, /api/me/budget,
                                   /api/me/policies, /api/models/allowed-for-me,
                                   /api/devices/me (Bearer desde la cookie)
```

- **Stack**: Next.js (App Router) + TypeScript + Tailwind CSS, Node 22, puerto 9090 (redirect URI registrado en el aprovisionamiento: `http://127.0.0.1:9090/auth/callback`).
- **Server-side siempre para la API**: el `session_token` nunca llega al JS del navegador; las páginas son RSC que reenvían `Authorization: Bearer`.
- **Enrolamiento (cliente)**: la generación del keypair es WebCrypto en el navegador (la clave privada no sale del dispositivo); el POST a `/api/devices/enroll` lo hace un route handler (`/api/desktop/enroll`) que inyecta el `id_token` desde la cookie httpOnly — el navegador jamás manipula el JWT.

## 2. Estructura de archivos (desktop-client/)

```
src/
  app/
    page.tsx                    # Login: org code → server action → redirect authorize
    auth/callback/route.ts      # GET: canje PKCE + mint sesión + cookies + redirect
    dashboard/page.tsx          # RSC dashboard gobernado (mockup style)
    enroll/page.tsx             # Enrolamiento (client component + route handlers)
    api/desktop/
      enroll/route.ts           # POST → control plane /api/devices/enroll
      exchange/route.ts         # POST → /api/devices/enroll/exchange (polling)
      state/route.ts            # GET → /api/devices/me + /api/me (estado para el cliente)
    layout.tsx, globals.css
  lib/
    config.ts                   # env: API base, IdP base, client_id, redirect_uri, port
    control-plane.ts            # fetch server-side con Bearer + envelope §5.2 + ApiError
    session.ts                  # cookies httpOnly (ta_session, ta_refresh, ta_csrf, ta_idtoken)
    oidc.ts                     # PKCE S256 (verifier/challenge), authorize URL, token exchange
    format.ts                   # USD centavos → display, fechas ISO → relativas
  components/
    ui/*                        # Card, Badge, Button, ProgressBar, EmptyState, ErrorState
    dashboard/*                 # BudgetCard, PoliciesCard, ModelsCard, DevicesCard,
                                # NotificationsCard, IdentityHeader, Sidebar
hermes/
  README.md                     # Cableado oficial (profile + plugin), sin parches
  profile-template/
    config.yaml                 # proveedor LiteLLM + virtual key + modelos aprobados
    SOUL.md
  plugins/trust-agent-governance/
    plugin.yaml                 # manifiesto oficial (name/version/provides_hooks)
    __init__.py                 # register(ctx): tool governance_status + hook pre_llm_call
```

## 3. Decisiones de implementación

1. **Puerto 9090** — redirect URI ya registrada por el aprovisionamiento (config.py `oidc_redirect_uris`); el Admin Console usa la 3000 (lane hermana).
2. **Cookies httpOnly** (`ta_*`, path=/, sameSite=lax, secure solo con HTTPS): el servidor Next actúa como custodio local de tokens (equivalente web del keychain del desktop empaquetado).
3. **Keystore del dispositivo** (`localStorage`): `ta_device_keystore` = { private_key_pkcs8_b64, public_key_pem, fingerprint, device_id, device_token, enrolled_at }. v1 documentada; el empaquetado migrará a keychain del SO.
4. **Polling del canje**: intervalo 5 s desde el cliente (`/api/desktop/exchange`), con backoff visual; 202 → sigue esperando; 200 → guarda device_token, va al dashboard; 410 → estado terminal "Enrollment no longer valid" (sin oráculo).
5. **Estados reales**: `loading` (skeleton), `error` (mensaje + request_id), `empty` ("No active budget set by your organization"). Cero cifras inventadas.
6. **Dark theme mockup**: fondo `#0e1318`, paneles `#1a1f25`/`#141920`, acento `#f91128`, texto `#e7ecf2`/`#8b98a8`. UI copy inglés. Tipografía system-ui.
7. **Hermes**: plugin `trust-agent-governance` usa `ctx.register_tool(name, toolset, schema, handler)`, `ctx.register_hook("pre_llm_call", cb)` (inyección de contexto de gobernanza por turno — verificado: `agent/turn_context.py::_collect_pre_llm_call_context`) y `ctx.register_hook("pre_tool_call", cb)` (veto de tools por política — verificado: `_dispatch_pre_tool_call_hooks` devuelve `block_message`). La config del profile apunta a LiteLLM (`base_url` del proxy + `api_key` = virtual key del usuario): **el gate de modelos/presupuesto es LiteLLM** (D-GOV-15); los hooks del plugin son defensa en profundidad y visibilidad.

## 4. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Keycloak 26 no enrola TOTP vía Admin REST (pitfall #9 del skill identity) | El TOTP lo enrola el propio usuario en su primer login del browser (required action CONFIGURE_TOTP del aprovisionamiento) — flujo real de usuario, no scraping |
| CORS del token endpoint desde el navegador | El canje es server-side (route handler); el navegador nunca llama al IdP por fetch |
| `cookie_secure` del control plane | El desktop usa `kind="desktop"` → la sesión llega en el body (Bearer), no depende de cookies del control plane |
| Sesión expirada en el RSC | 401 → redirect a login (y borrado de cookies locales) |
| Device token TTL 15 min | Renovación vía `POST /api/devices/{id}/token` documentada + botón/acción en la pantalla de dispositivos |

## 5. Orden de implementación (tasks.md)

1. Scaffold + config + lib (control-plane, session, oidc).
2. Login (org code → authorize → callback → sesión).
3. Dashboard RSC con datos reales.
4. Enrolamiento (WebCrypto + enroll + polling exchange).
5. Artefactos Hermes (profile template + plugin) + docs/desktop-flow.md.
6. `npm run build` + smoke en vivo (API real + Keycloak real + browser).
7. Converge (checklist §5 de la spec).
