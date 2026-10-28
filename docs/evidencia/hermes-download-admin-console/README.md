# Evidencia — Mudanza del Hermes download link al Admin Console (corrección Miller, t_360a752e)

| Campo | Valor |
|---|---|
| **Fecha** | 2026-10-26 |
| **Tarea** | t_360a752e (ui) — corrección de Miller: el link de descarga lo genera y envía el ADMIN DEL CLIENTE desde SU Admin Console; el proveedor solo crea tenants |
| **Stack vivo** | control plane :8000 (`trust-agent-api`) · Keycloak 26.2 :8080 · PostgreSQL 17 (`trust-agent-db`) · Admin Console :3100 (`trust-agent-console`) · Provider Console :3200 (`trust-agent-provider-console`, imagen reconstruida con el código corregido) |
| **Specs** | `specs/009-admin-console/` (D-AC-11, REQ-AC-033) · `specs/014-provider-console/` (D-PC-09/REQ-PC-017/AC-PC-017 revocados) · contrato pendiente: spec 023 (t_526030f3, lane dev) |

Todo lo documentado abajo se ejecutó contra el stack real, sin dobles ni datos
inventados. Los screenshots tienen verificación DOM programática + OCR
(`tesseract`).

## 1. Flujo real que alimenta la evidencia

1. **Provisioning real** (proveedor, `POST /api/admin/tenants` con el provider
   token del `.env`): tenant `hermes-dl`
   (`eda9d785-ef2d-43d3-bce7-23242f655907`), admin `admin@hermes-dl.test`,
   one-time password de 24 chars entregada una vez.
2. **Login REAL del admin**: baile PKCE S256 contra Keycloak 26 con
   `UPDATE_PASSWORD` + enrolamiento TOTP RFC 6238 real (secret capturado del
   formulario, código HMAC-SHA1) → `id_token` con **acr=2** → sesión admin
   minteada en el control plane (`POST /api/auth/session`,
   `mfa_verified=true`).
3. **Estado REAL del contrato spec 023**: `GET /api/download-link` con la
   sesión admin → **HTTP 404** (el endpoint de gestión aún no existe —
   t_526030f3 en vuelo). La UI degrada con el banner honesto, sin toggles
   simulados.
4. **Sesión member real** (para el gate de rol): usuario
   `invited-probe@final-e2e.test` (rol `user`, SIN `org_admin`) del tenant
   `final-e2e` creado por el E2E 79/79; su one-time password de invitación se
   lee del `jobs.result` (mecanismo de entrega D-ORG-02). Primer login real:
   `UPDATE_PASSWORD` + enrolamiento TOTP → acr=2 → sesión minteada.

## 2. Verificación desde la UI real (navegador contra :3100 y :3200)

Sesiones inyectadas como cookies del BFF (`ta_console_session` httpOnly +
`ta_console_refresh` + `ta_console_csrf`; `ta_provider` para el operador) —
las mismas que producen los logins OIDC de cada console.

| # | Check (AC) | Resultado real | Artefacto |
|---|---|---|---|
| 1 | **AC-AC-033.1** Admin ve el link estable con su `tenant_slug` real | Página `/hermes-download` renderiza `http://127.0.0.1:8000/download/hermes-dl` (construido server-side desde `/api/me`), badge `org: hermes-dl`, sidebar con el item "Hermes Download" | `01-admin-console-hermes-download.png` (+ OCR) |
| 2 | **AC-AC-033.1** "Copy link" deja el link en el clipboard | Click REAL (evento de ratón CDP = user activation) → botón pasa a **"Copied"** y `navigator.clipboard.readText()` devuelve exactamente `http://127.0.0.1:8000/download/hermes-dl` | `02-copy-link-copied.png` (+ OCR) |
| 3 | Enviar al empleado | "Copy invite message" → clipboard con el mensaje de invitación real (contiene el link y el org code); "Open email draft" abre borrador `mailto:` desde el equipo del admin — el console no envía email | verificación DOM (§3, script de la corrida) |
| 4 | **AC-AC-033.3** Contrato spec 023 pendiente = degradación honesta | `GET /api/download-link` → 404 real; la tarjeta "Link availability" muestra el banner **"Link management API not deployed yet — read-only status"** con NINGÚN toggle simulado; el link sigue copiable | `01-…png` (tarjeta inferior) |
| 5 | **AC-AC-033.2** Sin rol admin | Sesión real rol `user`: el item "Hermes Download" NO aparece en el sidebar ni el botón "Download desktop app" en el topbar; la página muestra **"Organization admins only"** sin exponer el link | `04-member-gate-organization-admins-only.png` (+ OCR) |
| 6 | **AC-PC-017 revocado** Provider Console sin download link | Detalle del tenant `hermes-dl` con sesión de operador real: verificación DOM — sin bloque "Hermes download link", sin "Copy link", sin URL `/download/{slug}`; la sección "Entry points" solo ofrece el deep-link a la **Admin Console del cliente** | `03-provider-console-tenant-detail-sin-download-link.png` (+ OCR) |

Nota OCR: en los screenshots 01/02 la única aparición de la palabra
"download" fuera de la sección nueva es el nombre de la organización del
tenant de evidencia ("Hermes Download Evidence Corp", nombre elegido al
provisionar) — no es UI de descarga del proveedor.

## 3. Builds y calidad

- `npm run build` (console, host) → **exit 0**, 19 rutas incluida
  `/hermes-download`; `npm run lint` → **0 problems**.
- `npm run build` (provider-console, host) → **exit 0**, 8 rutas;
  `npm run lint` → **0 problems**.
- Imagen `trust-agent-provider-console:local` **reconstruida** y contenedor
  reiniciado: el `tenant-detail.tsx` del contenedor coincide (md5) con el
  código corregido del árbol — el contenedor anterior aún servía el build con
  el bloque de download link (hallazgo de esta corrida: el intento anterior
  editó el código pero no reconstruyó la imagen).

## 4. Wiring pendiente (contrato spec 023, t_526030f3)

La UI ya consume el contrato acordado; cuando el endpoint exista, el toggle
enable/disable se activa solo (sin cambios de UI):

- `GET /api/download-link` → `{url, enabled, updated_at}` (sesión org_admin)
- `PUT /api/download-link` `{enabled}` (auditado: `download.link.enabled` /
  `download.link.disabled`)
- Link estable público: `GET /download/{tenant_slug}` (sin auth, branding del
  tenant, rate-limited, audit `download.issued`)

## 5. Hallazgo para el lane dev (fuera de alcance de esta tarea)

`docker compose up -d` (sin `--no-deps`) relanza el servicio one-shot
`migrate` y hoy **falla con exit 255**: `Can't locate revision identified by
'0013_device_rejected_audit'` — la BD vive en una revisión que no está en
`db/migrations/versions/` del árbol actual (historial de ramas paralelas
Alembic, hotspot ya anunciado en t_526030f3). Sin efecto sobre el schema
(Alembic aborta antes de tocar nada; API/DB sanos), pero bloquea cualquier
`compose up` completo. Requiere dueño del lane dev.
