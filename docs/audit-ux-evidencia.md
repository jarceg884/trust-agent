# Evidencia — Spec 021: Audit UX (feedback Miller #9)

| Campo | Valor |
|---|---|
| **Fecha** | 2026-10-26 |
| **Tarea** | t_c14d6703 (ui) |
| **Stack vivo** | control plane :8000 · Keycloak 26.2 :8080 · PostgreSQL 17 (trust-agent-db) · Admin Console :3100 (contenedor `trust-agent-console`, build de producción reconstruido) |
| **Spec** | `specs/021-audit-ux/` (spec.md, plan.md, tasks.md) |

Todo lo documentado abajo se ejecutó contra el stack real, sin dobles ni datos
inventados. Los artefactos crudos están en `docs/evidencia/audit-ux/`.

## 1. Flujo real que alimenta el log

1. **Provisioning real** (proveedor, `POST /api/admin/tenants`): tenant
   `audit-ux` (`33e92df9-e952-4925-821a-483e0bb45dd8`), admin
   `admin@audit-ux.test`, one-time password de 24 chars entregada una vez.
   → audit `tenant.provisioned` + 14 eventos de seeding (departamentos,
   presupuesto, políticas, modelos, usuario, rol).
2. **Login REAL del admin**: baile PKCE S256 contra Keycloak 26 con
   `UPDATE_PASSWORD` + enrolamiento TOTP RFC 6238 real (secret capturado,
   código generado por HMAC-SHA1) → `id_token` con **acr=2**.
   → audit `auth.login` (`payload: via=oidc, kind=admin, mfa_verified=true`).
3. **Sesión admin MFA-verificada** minteada en el control plane
   (`POST /api/auth/session` → cookie `trust_session`, `mfa_verified=true`).
4. **Gobernanza real con esa sesión** (cada acción auditada):
   - `POST /api/departments` {"name": "Evidence Operations"} → **201**
     → audit `department.created` (payload `name=Evidence Operations`).
   - `PUT /api/budgets/dept/{id}` {"amount_cents": 12000} → **200**
     → audit `budget.updated` (level=dept, period=monthly).
   - `PUT /api/policies/org` {"key": "max_daily_sessions", "value": 20} → **200**
     → audit `policy.updated` (version=1).
   - `POST /api/admin/audit/verify` → **200 `{"status":"ok"}`**
     → audit `audit.verified` (source=api).

## 2. Verificación desde la UI real (navegador contra :3100)

Sesión inyectada como cookies del BFF (`ta_console_session` httpOnly +
`ta_console_csrf`),
las mismas que produce el login OIDC del console. Página `/audit` del build de
producción reconstruido.

| # | Check (AC) | Resultado real |
|---|---|---|
| 1 | **AC-21-004a** Pill de integridad auto-verificada al abrir la página (org_admin, 1ª vez en la sesión del navegador) | Pill verde **"Chain intact"** tras la llamada real `POST /api/admin/audit/verify` → `{"status":"ok"}`; el evento `audit.verified` (seq 18/20/22) aparece en el log — la verificación es real y auditada |
| 2 | **AC-21-004c** Verificación manual (botón "Verify chain") | Click → pill vuelve a verde; log refrescado con el `audit.verified` nuevo (total 22→23) |
| 3 | **AC-21-001a** Filtro por actor (admin `79cc6b73…`) | **"1 filter active · 4 matching events"** — exactamente los 4 eventos del admin (seq 16, 17, 19, 21); verificado contra conteo SQL/API |
| 4 | **AC-21-001b** Filtro por tipo de evento `auth.login` | **"1 filter active · 1 matching events"** (seq 16) |
| 5 | **AC-21-001c** Filtro por recurso `target_type=department` | **"1 filter active · 5 matching events"** (seq 1–4 del seeding + 17 del flujo) |
| 6 | **AC-21-001d** Rango de fechas | `From` = mañana → **"1 filter active · 0 matching events"**; `From` = ayer → **"1 filter active · 23 matching events"** (todo el tenant) |
| 7 | **AC-21-002** Búsqueda libre "Evidence Operations" | **"1 filter active · 1 matching events"** — matchea el payload de `department.created` (búsqueda server-side sobre payload, D-AU-11) |
| 8 | **AC-21-003** Timeline por actor/sesión ("View activity" en una fila del admin) | Cambia a la pestaña Timeline con el filtro de actor aplicado; grupo **org_admin 79cc6b73… · 4 events in range · chronological order** con **"SESSION 1 — OPENED BY LOGIN"**: `auth.login` 08:49:29 → `department.created` 08:49:55 → `policy.updated` 08:50:24 → `budget.updated` 08:50:52 (newest last), con target, payload resumido (`name=Evidence Operations`, `mfa_verified=true`) y hash corto por nodo (`#a3aeb82d`…). El grupo `provider` (19 eventos) muestra **"EARLIER ACTIVITY"** (sin login en el rango) |
| 9 | **AC-21-005** Export CSV/JSON del rango filtrado desde la UI | Botones muestran el total del rango (**CSV · 4 / JSON · 4** con filtro de actor). Clicks → toasts "Exported CSV/JSON of the filtered range." y descargas reales del navegador. **CSV: 4 filas de datos + cabecera, columnas `hash_prev`/`hash` completas; JSON: 4 items, todos con hash** (archivos en `docs/evidencia/audit-ux/audit-events-actor-filter.{csv,json}`) |
| 10 | **AC-21-006** Estados reales | Error real de validación del API (fecha mal formada por tipeo) renderizado con `ErrorState` + mensaje exacto del control plane y botón Retry; skeleton de carga en tabla y timeline; 403 sin `audit.view` → empty state (código, mismo patrón verificado en spec 009) |

## 3. Build y calidad

- `npm run build` → **exit 0**, "✓ Compiled successfully" (18 rutas).
- `npm run lint` (eslint) → **0 problems**.
- Contenedor `trust-agent-console` reconstruido (`docker compose build console`)
  y reiniciado: `Up (healthy)`, `/login` 200, `/audit` 307→login sin sesión.
- La página sigue siendo 100% lectura sobre la API de la spec 008: la única
  escritura indirecta es el `audit.verified` que emite el verificador del
  backend (por diseño, D-AU-05). Inmutabilidad intacta.

## 4. Artefactos

| Archivo | Contenido |
|---|---|
| `docs/evidencia/audit-ux/audit-table.png` | Screenshot: tabla con pill verde, filtros completos, export con totales (23 eventos reales) |
| `docs/evidencia/audit-ux/audit-timeline.png` | Screenshot: timeline por actor/sesión del admin + grupo provider |
| `docs/evidencia/audit-ux/audit-events-actor-filter.csv` | Export CSV real descargado del navegador (filtro actor, 4 filas, hashes) |
| `docs/evidencia/audit-ux/audit-events-actor-filter.json` | Export JSON real (4 items) |

## 5. Hallazgo para dev (reportado como tarea)

`PUT /api/budgets/dept/{id}` con un id no-UUID responde **500** (error interno
de PostgreSQL al hacer CAST) en lugar de **422** de validación —
`platform/app/governance/router.py` (ruta de budgets de depto). Repro real:
`PUT /api/budgets/dept/not-a-uuid` → 500 `internal_error`
(request_id `req_c5698d02a8d541e5b0dc`). Reportado en tarea kanban al perfil dev.

> **RESUELTO (t_378503a1, 2026-10-26)**: validación `require_uuid` en todas las
> rutas con param UUID + red de seguridad `DataError`→422. El repro exacto ahora
> responde 422 `validation_failed` (26/26 checks en vivo PASS). Evidencia:
> `docs/evidencia/uuid-validation-fix.md`.
