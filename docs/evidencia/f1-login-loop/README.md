# Evidencia F1 — Login loop: redirect post-callback a `localhost` (t_38ce1e82)

Fix del fallo CRÍTICO F1 de `docs/qa-console-repro.md` (t_877b0cb5): tras el
callback OIDC, Next.js 16 reconstruía `request.url` con el hostname del banner
de arranque (`localhost`, `next start` sin `-H`), ignorando el `Host` header —
el redirect final mandaba al navegador de `127.0.0.1` a `localhost` (o
viceversa), donde las cookies host-only de sesión no aplican → login loop.

## Fix

Origin derivado del `Host`/`X-Forwarded-Host` header del request (con
`X-Forwarded-Proto`), con fallback a `request.url`:

- `platform/console/src/lib/origin.ts` (nuevo) — `requestOrigin(request)`.
- `platform/desktop-client/src/lib/origin.ts` (nuevo) — ídem.
- `platform/console/src/app/auth/callback/route.ts` — `fail()` (:34) y redirect
  final a `/overview` usan `requestOrigin`; el `redirect_uri` del canje y del
  step-up MFA también.
- `platform/console/src/app/auth/login/route.ts` — `redirect_uri` del authorize
  derivado del Host header.
- `platform/console/src/app/auth/logout/route.ts` — redirect a `/login`.
- `platform/desktop-client/src/app/auth/callback/route.ts` — `fail()` y redirect
  final a `/dashboard` + `redirect_uri` del canje/step-up.
- `platform/desktop-client/src/app/actions.ts` — inicio del baile desktop.
- `docker-compose.yml` — `TRUST_OIDC_REDIRECT_URIS` registra el callback en
  AMBOS nombres del loopback (127.0.0.1 y localhost, :3100 y :9090).
- `platform/app/tenants/keycloak_admin.py` — `_web_client_uuid` fusiona las
  redirect URIs configuradas en clientes pre-existentes;
  `ensure_web_client_redirect_uris` (heal público).
- `platform/app/tenants/provision.py` — re-provisión idempotente healea realms
  creados antes del registro de ambos nombres de loopback.

## Verificación (stack vivo, 2026-10-26)

Imágenes `trust-agent/console:local` (11:50) y `trust-agent/desktop-client:local`
(11:26) reconstruidas DESPUÉS del fix; contenedores healthy.

### Sonda de redirect (curl, la misma de QA §F1)

```
$ curl -si http://127.0.0.1:3100/auth/callback | grep -i '^location'
location: http://127.0.0.1:3100/login?error=Sign-in%20callback%20is%20missing%20its%20authorization%20code.
$ curl -si http://localhost:3100/auth/callback   → location: http://localhost:3100/login?error=...
$ curl -si http://127.0.0.1:9090/auth/callback   → location: http://127.0.0.1:9090/?error=...
$ curl -si http://localhost:9090/auth/callback   → location: http://localhost:9090/?error=...
```

El host del request se conserva en los 4 casos (antes: siempre `localhost`).

### Login real por UI (navegador, tenant `f1-verify` aprovisionado en vivo)

Primer login real completo: org code → Keycloak 26.2 (password one-time) →
CONFIGURE_TOTP (secret capturado del formulario, código RFC 6238 generado con
HMAC-SHA1) → UPDATE_PASSWORD → callback → sesión minteada.

| # | Pantalla inicial | URL final tras el callback | Sesión | Resultado |
|---|---|---|---|---|
| 1 | `http://127.0.0.1:3100/login` | `http://127.0.0.1:3100/overview` | Overview con budget $500, admin@f1-verify.test | **PASS** |
| 2 | `http://localhost:3100/login` | `http://localhost:3100/overview` | Overview activo | **PASS** |
| 3 | `http://127.0.0.1:9090/` | `http://127.0.0.1:9090/dashboard` | Dashboard con "Enroll this device" visible | **PASS** |
| 4 | `http://localhost:9090/` | `http://localhost:9090/dashboard` | Dashboard activo | **PASS** |

El punto letal de F1 (el dashboard del desktop inalcanzable por el flujo normal
→ el usuario nunca llega a "Enroll this device" → "Devices no funciona") queda
resuelto: el botón de enrolamiento es alcanzable por el flujo normal en ambos
hosts.

Cliente OIDC del realm con las 4 redirect URIs registradas (verificado vía
Admin REST): `127.0.0.1:3100`, `localhost:3100`, `127.0.0.1:9090`,
`localhost:9090` (todas `/auth/callback`).

## Nota para QA

La verificación usó el tenant `f1-verify` (6025e845-b858-4d28-93dc-fd0537cf7367),
admin `admin@f1-verify.test`. El realm se healeó manualmente (wiring LoA del
browser flow) porque el contenedor `trust-agent-api` corre una imagen stale
previa a los cambios de spec 015 en `keycloak_admin.py` — ver comentario en la
tarjeta t_38ce1e82 (hotspot `platform/app/tenants/keycloak_admin.py`).
