# Trust Agent — Admin Console

Spec: [`specs/009-admin-console/spec.md`](../../specs/009-admin-console/spec.md) ·
Mockup: `upload_iaadt_lh.png` (tema claro, acento `#c90104`) ·
Paridad de stack con [`platform/provider-console/`](../provider-console/) (Next 16 + React 19 + Tailwind v4).

Panel de administración del tenant: **Overview, Organization, Departments,
Users & Roles, Budgets, Model Access, Shared Agents, Devices, Credentials,
Audit Log, Branding** — todo contra la API REAL del control plane (specs
003–008), sin datos simulados (constitution I).

## Arquitectura (spec 009 D-AC-01/02)

```
Navegador ──(cookies httpOnly del console)──> Next.js (:3100)
   │  /login → /auth/login → PKCE S256 contra el realm del tenant
   │  /auth/callback → canje code → POST /api/auth/session (kind=admin)
   │                    → sesión como Set-Cookie → custodia en cookies locales
   └─ /api/proxy/* (BFF) ──> Control plane FastAPI (:8000)
        cookie trust_session + X-CSRF-Token (mutaciones) + refresh-on-401
```

- El navegador **jamás** ve URLs del control plane ni tokens (httpOnly).
- El CSRF lo valida el **control plane** (REQ-SEC-010): el proxy solo
  transporta el header `X-CSRF-Token` (double-submit estricto, sin fallback).
- Sin MFA verificada en el token, el control plane responde 403
  `mfa_required` y el shell renderiza la puerta MFA dedicada.

## Runbook local

```bash
# 0) Prerrequisitos: control plane arriba (platform/README.md paso 3) y
#    Keycloak en 127.0.0.1:8080. El tenant debe tener registrada la redirect
#    URI del console (TRUST_OIDC_REDIRECT_URIS del control plane).

# 1) deps
cd /root/trust-agent/platform/console
npm install

# 2) build + start (puerto 3100: el 3000 canónico puede estar ocupado)
npm run build
TRUST_API_BASE_URL=http://127.0.0.1:8000 \
TRUST_CONSOLE_REDIRECT_URI=http://127.0.0.1:3100/auth/callback \
npx next start --port 3100
# dev: npm run dev (ya apunta al 3100)

# 3) smoke real (flujo de login Keycloak completo, requiere un tenant
#    recién aprovisionado y su one-time password):
cd /root/trust-agent/platform
.venv/bin/python console/scripts/smoke_console.py <one_time_password>

# 3b) smoke del camino autorizado (sesión admin real + proxy + CSRF +
#     11 páginas + logout; usa el IdP de test de la suite e2e para el
#     claim amr — ver nota abajo):
.venv/bin/python console/scripts/smoke_authorized.py
```

Env (ver `.env.example`): `TRUST_API_BASE_URL` (:8000), `TRUST_IDP_BASE_URL`
(:8080), `TRUST_OIDC_CLIENT_ID` (`trust-agent-web`), `TRUST_CONSOLE_REDIRECT_URI`.

## Nota MFA (hallazgo real, 2026-10-24)

Keycloak 26 **no emite claim `amr`** en el id_token tras un login real
password+OTP (verificado: `amr: None, acr: 1`). La puerta MFA del control
plane (`_amr_mfa`) por tanto rechaza todo login real de KC26 — bug del
control plane trackeado como tarea dev (ver spec 009 §6). Mientras tanto:

- `smoke_console.py` verifica el flujo de login REAL completo hasta la
  puerta MFA (PKCE + one-time password + enrolamiento TOTP + mint + cookies).
- `smoke_authorized.py` verifica el camino autorizado completo con el MISMO
  patrón de IdP de test que usa la suite e2e del plataforma (JWKS real por
  HTTP, JWT RS256 con `amr=["mfa"]`); todo lo demás es real (sesión en
  PostgreSQL, RLS, datos del tenant, CSRF).

## Estructura

```
src/
├── app/
│   ├── login/                  org code → PKCE
│   ├── auth/login|callback|logout   route handlers del baile OIDC
│   ├── api/proxy/[...path]/    BFF proxy (D-AC-01)
│   └── (console)/              shell + 11 páginas (spec §3.2)
├── components/                 ui.tsx (kit) + shell/ + icons
├── hooks/use-api.ts            useApi/useMutation (loading/error reales)
└── lib/                        config, oidc, session, control-plane,
                                types (derivados del código del control
                                plane, citan fuente), format, api-client
scripts/
├── dump_openapi.py             snapshot openapi.json (referencia de tipos)
├── smoke_console.py            smoke E2E del flujo de login real
└── smoke_authorized.py         smoke E2E del camino autorizado
```

## Degradación honesta (D-AC-06)

`GET /api/users`, `GET/PUT /api/branding`, `POST /api/departments` y el
listado de grants NO existen hoy en el control plane (contrato en spec 001).
Las páginas correspondientes muestran el estado REAL (404) con la
explicación del contrato pendiente — cero datos inventados. Tarea dev con
el contrato exacto: ver spec 009 §6.
