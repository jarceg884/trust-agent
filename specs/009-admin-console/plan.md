# Spec 009 — Plan técnico

| Campo | Valor |
|---|---|
| **Spec** | specs/009-admin-console/spec.md |
| **Stack** | Next.js 16 (App Router, Turbopack) + React 19 + TypeScript 5 (strict) + Tailwind CSS v4 |
| **Ubicación** | `/root/trust-agent/platform/console/` |
| **Paridad** | Versiones y patrones alineados con `platform/desktop-client/` (spec 010) para mantenimiento conjunto |

## 1. Arquitectura

```
Navegor ──(cookies httpOnly del console)──> Next.js console (:3000)
   │                                            │
   │  /auth/login /auth/callback /auth/logout    │ BFF proxy server-side
   │  (PKCE S256 contra el realm del tenant)     │ (cookie trust_session + X-CSRF-Token
   │                                             │  + refresh-on-401 una vez)
   └──────────── UI (RSC shell + client pages) ──┴──> Control plane FastAPI (:8000)
```

- **Shell** (RSC, server): `(console)/layout.tsx` resuelve la sesión server-side
  (`/api/me` directo con el token de la cookie) → gate: 401 → `/login`;
  403 `mfa_required` → pantalla MFA; ok → Sidebar + Topbar + contenido.
- **Páginas** (client components): fetch vía `/api/proxy/*` con hook `useApi`
  (loading/error/refetch) + `mutate` para acciones. Skeletons por región.
- **Proxy** (`app/api/proxy/[...path]/route.ts`): reenvía método/query/body,
  adjunta `Cookie: trust_session=<ta_console_session>` y `X-CSRF-Token` (mutaciones);
  sobre 401 con `ta_console_refresh` → `POST /api/auth/refresh` upstream, cookies
  nuevas en la respuesta, retry único. Pasa el envelope §5.2 tal cual.
- **Auth**: `/auth/login?org=` genera PKCE (cookies 10 min) → 302 authorize;
  `/auth/callback` valida state, canjea code, `POST /api/auth/session
  {kind:"admin"}`, extrae `trust_session` de `Set-Cookie` (el body trae
  `session_token:null` en sesiones admin), guarda `ta_console_session/ta_console_refresh/
  ta_console_csrf` (csrf legible por JS para el header — el control plane valida el
  hash), limpia PKCE, redirect `/overview`.

## 2. Módulos

| Archivo | Contenido |
|---|---|
| `src/lib/config.ts` | env: apiBase, idpBase, clientId, redirectUri, scope |
| `src/lib/oidc.ts` | PKCE S256 + authorize/token del realm (paridad desktop) |
| `src/lib/session.ts` | custodia de cookies (ta_console_session/refresh/csrf/pkce — prefijo por app, F2) |
| `src/lib/control-plane.ts` | cliente server-side (RSC/auth) con envelope §5.2 |
| `src/lib/types.ts` | tipos derivados del control plane (cada uno cita fuente) |
| `src/lib/api-client.ts` | cliente navegador vía proxy (ApiError con reason/request_id) |
| `src/lib/format.ts` | USD (centavos → `Intl.NumberFormat`), fechas relativas |
| `src/hooks/use-api.ts` | `useApi<T>` (loading/error/data/refetch) + `useMutation` |
| `src/components/ui/*` | button, card, badge, table, input, select, dialog, skeleton, empty/error-state, toast, stat, progress, pagination, confirm, one-time-secret, copy |
| `src/components/shell/*` | sidebar, topbar, notifications, user-menu, mfa-gate |
| `src/app/(console)/*` | 11 páginas (spec §3.2) |

## 3. Tema (mockup `upload_iaadt_lh.png`, D-AC-04)

Tailwind v4 `@theme inline`: background `#f8f9fb`, surface `#ffffff`,
border `#e5e8ee`, foreground `#111827`, muted `#6b7280`, accent `#c90104`,
accent-soft `rgba(201,1,4,.08)`, success `#15803d`, warning `#b45309`,
danger `#b91c1c`. Geist Sans/Mono. Radios `rounded-lg`, sombras mínimas,
densidad enterprise (tablas compactas, jerarquía tipográfica clara).

## 4. Orden de implementación (tasks.md)

1. Scaffold + configs (paridad desktop-client) + `npm install`.
2. lib (config/oidc/session/control-plane/types/format/api-client) + hooks.
3. Auth routes (login/callback/logout) + proxy BFF.
4. UI kit + shell (sidebar/topbar/mfa-gate/notifications).
5. Páginas con datos reales: overview → budgets → organization → departments →
   models → devices → credentials → agents → audit → users → branding.
6. `npm run build` limpio + fixes.
7. Smoke real (control plane + Keycloak arriba): proxy, gates, login redirect,
   datos reales de un tenant de prueba.
8. Converge: checklist de spec + evidencia + tarea dev de contratos pendientes.

## 5. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Puerto 3000 ocupado en el host por contenedores ajenos | `TRUST_CONSOLE_REDIRECT_URI` override + smoke en :3100 con realm de prueba que registre esa URI (D-AC-09) |
| Sesión admin: `session_token:null` en el body | el callback lee `Set-Cookie` del response (D-AC-02) |
| CSRF: el proxy NO debe relajar el check del control plane | sesión reenviada como cookie + header X-CSRF-Token; test negativo en smoke (mutación sin header → 403 real) |
| Endpoints inexistentes (users/branding/grants) | degradación honesta D-AC-06 + tarea dev con contrato exacto |
| Deriva de tipos vs control plane | spot-check en converge citando archivo:línea por tipo |
