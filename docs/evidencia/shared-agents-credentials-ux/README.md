# Evidencia E2E — Spec 037: UX Shared Agents + Credentials (repro Miller)

**Tarea**: t_13b461a8 · **Fecha**: 2026-10-28 · **Stack**: local vivo (`api` + `console` recreados con las imágenes rebuild-eadas tras el cambio)

## Método (nada simulado)

- Sesión admin REAL minteada con el baile PKCE S256 + enrolamiento TOTP real
  contra Keycloak 26 (`platform/console/scripts/smoke_console.py`, tenant
  `console-smoke2` "UX037 Smoke Co" aprovisionado con
  `POST /api/admin/tenants`). Cookies del BFF inyectadas en Chromium real vía
  CDP (patrón t_e03bba6c / skill `trust-agent-pkce-login-dance`).
- Flujo completo ejecutado en el browser contra el build de PRODUCCIÓN
  desplegado (`trust-agent-console`, `npx next start`), no dev server.
- Usuario segundo real creado por auto-registro (`mara@console-smoke2.test`,
  "Mara López") para el Share con selector de usuario.

## Flujo 1 — Shared Agents (/agents)

| Paso | Resultado real | Screenshot |
|------|----------------|------------|
| Vista inicial (usuario sin agentes) | Pestaña default **"Default profiles" (4)** + **"My agents" (0)**; strip inline **"Your rules — applied to every agent you adopt or run: Approved models: glm-5.2 · Budget: within budget"**; 4 tarjetas con **"Adopt — make it mine"**; hint "New here? Adopt a ready-made agent…" | `01-agents-default-profiles-adopted.png` |
| Adopt (1 click, perfil Analyst) | Toast: *"\"Analyst\" is now your agent. Find it under \"My agents\"…"*; la tarjeta pasa a **"Adopted"** + "Manage in My agents" | `01` (estado post-adopt) |
| Pestaña "My agents" | Tabla: Analyst · **OWNER "You"** · "You own it" · "Nobody yet" · acciones Try it / Share / Manage access | `02-my-agents.png` |
| Share (REQ-037-005) | Dialog con **selector de usuario real** ("Mara López — mara@console-smoke2.test", buscable por nombre/email) + permiso en lenguaje llano ("Can use it (run the agent)" / "Can view it"). **Cero pasteo de UUID.** Tras "Give access": columna SHARED WITH → "1 person" | `03-share-user-picker.png` |
| Manage access (REQ-037-006) | Lista de grants reales: "Mara López — Can use it · since Oct 28, 2026, 3:31 PM" + **Revoke** por fila. Revoke probado → "Nobody has access yet" | `04-manage-access-grants.png` |

## Flujo 2 — Credentials (/credentials)

| Paso | Resultado real | Screenshot |
|------|----------------|------------|
| Vista inicial | 3 secciones separadas con copy llano: **"Catalog connections"** (6 apps con badge de policy y hint "Ask an admin to allow this app for your team" en las bloqueadas), **"My custom connections"** (tabla + "New connection"), **"My secrets"** ("API keys and passwords for your own tools…") | `05-credentials-sections.png` |
| Wizard paso 1 — Type | Tarjetas grandes: Database / API key / Other, con indicador 1-2-3 | `06-wizard-step1-type.png` |
| Wizard paso 2 — Details | Labels sin jerga: Name, Database type, Host, Port, Database, Username | `07-wizard-step2-details.png` |
| Wizard paso 3 — Secret | "Last step: the secret… shown back to you exactly once" | `08-wizard-step3-secret.png` |
| Crear | **"Connection created"** + secreto one-time visible UNA vez con botón Copy y "I stored it safely" | `09-connection-created-onetime.png` |
| Tabla final | "Analytics DB" → badge **"Allowed by your organization"** (tras `PUT /api/policies/org` `allowed_connections=[<uuid>, "email"]`); "Reporting DB" → badge **"Blocked by policy"**. Ambos estados del badge en pantalla, derivados del campo `allowed_for_caller` computado por el BACKEND | `10-custom-connections-table.png` |

## Verificaciones de API (AC-037-3, sesión real por el proxy BFF)

```
GET /api/proxy/api/vault/connections →
  [('4f7ce1b3-…', 'Analytics DB', False)]           # antes de la policy
PUT /api/proxy/api/policies/org {allowed_connections:[uuid,"email"]} → 200
GET /api/proxy/api/vault/connections →
  [('Analytics DB', True)]                          # allowed_for_caller flip
GET /api/proxy/api/connectors →
  [(email,False),(slack,False),(teams,False),(telegram,False),(vmware,False),(whatsapp,False)]
```

## Checks de aceptación

| AC | Estado | Evidencia |
|----|--------|-----------|
| AC-037-1 build+lint | `npm run build` ✓ (31/31 páginas); `npm run lint` ✓ para TODOS los archivos del diff 037 (eslint scoped: 0 problemas). Fix adicional de un error preexistente en `mfa-setup-client.tsx` (mismo lane, commit c636a1f). Queda 1 error en `structure-wizard.tsx` — archivo UNTRACKED de la lane concurrente 038 (departments-onboarding), no tocado para evitar colisión | Output real de comandos |
| AC-037-2 tests | `pytest tests/test_catalogs.py tests/test_custom_connections.py -q` → **12 passed** (incl. asserts nuevos de `allowed_for_caller`) | Output real |
| AC-037-3 stack vivo sirve los campos | curl con sesión real muestra `allowed_for_caller` en connectors y connections (arriba) | Response real |
| AC-037-4 E2E adopt + wizard | Ambos flujos completos sin errores ni toasts de error (el único 422 intermedio fue artefacto de la automatización: el harness seteó el select a un valor inexistente; corregido, la segunda corrida del wizard fue limpia end-to-end) | Screenshots 01-10 + transcripción |
| AC-037-5 usuario nuevo en 10 s | La vista inicial de /agents muestra tarjetas "Adopt — make it mine" + policy efectiva inline sin abrir nada | `01-agents-default-profiles-adopted.png` |

## Nota sobre el 422 intermedio

Durante la primera pasada del wizard, la automatización seteó el select
"Database type" al valor `postgres` (inexistente; las opciones son
`postgresql`, …) → el estado React quedó `""` → el API respondió 422
"Database connections require metadata 'type'." con toast de error visible.
Es comportamiento CORRECTO del producto (validación real + feedback real);
la segunda pasada sin interferir el select completó sin errores. No hay
cambio de código por esto.
