# QA Console — Repro real de bugs en Budgets y Devices (feedback Miller #5 y #8)

> Tarea kanban `t_877b0cb5` (perfil QA) · 2026-10-26 · Nada simulado: todo lo documentado
> se ejecutó contra el stack vivo con navegador real (Chromium via browser automation),
> login PKCE S256 + TOTP RFC 6238 contra Keycloak 26.2 real, sin curl para los flujos de UI
> (curl solo como sonda de diagnóstico).
>
> **Stack vivo**: control plane :8000 (healthy) · Keycloak 26.2 :8080 · PostgreSQL 17
> (`trust-agent-db`) · Admin Console :3100 (contenedor `trust-agent-console`,
> `npx next start --port 3100`, red host) · desktop-client :9090 (contenedor
> `trust-agent-desktop-client`, `npx next start --port 9090`, red host).
>
> **Tenant de prueba**: `qa-console-b8` (`9d9bf103-b41d-4306-8b18-83c5c89cf10e`),
> aprovisionado real vía `POST /api/admin/tenants` (proveedor), admin
> `admin@qa-console-b8.test`, presupuesto org inicial $500.00, 4 departamentos seed.
> Login del admin hecho DESDE LA UI: org code → KC (one-time password del
> aprovisionamiento) → CONFIGURE_TOTP real (secret capturado del formulario KC, código
> generado por HMAC-SHA1) → UPDATE_PASSWORD → sesión admin con MFA.

---

## Veredicto resumido

| # | Área | Prueba | Veredicto |
|---|---|---|---|
| B1 | Budgets | Editar presupuesto org desde la UI ($500→$450) + persistencia (reload + DB) | **PASS** |
| B2 | Budgets | Editar presupuesto de departamento (IT Operations → $200) | **PASS** |
| B3 | Budgets | Validación Σ deptos > org → error visible y claro | **PASS** |
| B4 | Budgets | Reflejo de presupuestos en Overview | **PASS** |
| B5 | Budgets | Editar presupuesto **por usuario** desde la UI | **FAIL — F3** (no existe en la UI) |
| D1 | Devices | Enrolar desde desktop-client (WebCrypto RSA-2048, 202 pending) | **PASS** |
| D2 | Devices | Approve desde la console UI → active + canje de token en el desktop | **PASS** |
| D3 | Devices | Reject desde la console UI → rejected + invalidación en el desktop | **PASS** (con bugs F4/F6) |
| D4 | Devices | Revoke desde la console UI → revoked + reflejo en dashboard desktop | **PASS** |
| D5 | Devices | Login del desktop → dashboard → pantalla de enrolamiento | **FAIL — F1** (loop de login; el dashboard es inalcanzable por el flujo normal) |
| D6 | Devices | Convivencia console + desktop en el mismo navegador/host | **FAIL — F2** (colisión de cookies) |
| D7 | Devices | Enroll con sesión de >10 min | **FAIL — F5** (id_token caducado, 401) |

**Conclusión**: el backend sí funciona (los E2E 79/79 no mentían), pero **desde la UI el
flujo real está roto por el redirect post-login a `localhost` (F1)** — un usuario que entra
por `http://127.0.0.1:...` (las URLs de todos los runbooks y del redirect URI registrado en
Keycloak) completa el login SSO+MFA y **acaba de vuelta en la pantalla de login**, sin
sesión aparente. Para el desktop esto es letal: el dashboard es inalcanzable por el flujo
normal → nadie puede llegar a "Enroll this device" → **"Devices no funciona"** (feedback #8).
Para el console el usuario puede recuperarse navegando manualmente a `127.0.0.1:3100`, pero
el flujo se ve roto/aleatorio → contribuye a **"Budgets no tiene funcionalidad real"**
(feedback #5), que además tiene un gap real de UI: **no existe edición de presupuesto por
usuario** (F3).

---

## FALLOS CONFIRMADOS (con repro exacto)

### F1 — [CRÍTICO] Redirect post-callback a `localhost` pierde la sesión: el login de console y desktop acaba… en el login

- **Pantalla**: `http://127.0.0.1:3100/login` (console) y `http://127.0.0.1:9090/` (desktop).
- **Repro (console, ejecutado 2 veces, 2ª en sesión de navegador limpia)**:
  1. Entrar a `http://127.0.0.1:3100/login`, escribir el org code, "Continue to sign-in".
  2. Keycloak real: usuario + password → one-time code (TOTP) → callback.
  3. **Real**: el navegador acaba en `http://localhost:3100/login` — la pantalla de login
     de nuevo, como si el login hubiera fallado. (Observado: `URL final:
     http://localhost:3100/login` tras login PKCE+password+OTP completo.)
  4. **Esperado**: `http://127.0.0.1:3100/overview` con la sesión activa.
  5. La sesión SÍ se creó: navegando manualmente a `http://127.0.0.1:3100/overview` se
     entra sin re-login (verificado). El problema es puramente el redirect final.
- **Repro (desktop, ejecutado 2 veces)**: mismo baile en `http://127.0.0.1:9090/` →
  tras el callback el navegador acaba en `http://localhost:9090/` (login del desktop).
  El dashboard (`/dashboard`) **no es alcanzable por el flujo normal**: en `localhost:9090`
  no hay cookies (se setearon para `127.0.0.1`) → la página redirige a `/` → login. Si el
  usuario reintenta desde `localhost:9090`, el authorize vuelve SIEMPRE al callback
  registrado en `127.0.0.1:9090/auth/callback` → cookies en `127.0.0.1` → redirect a
  `localhost` → **loop infinito**. Solo navegando manualmente a
  `http://127.0.0.1:9090/dashboard` se entra. **Efecto: el usuario nunca ve el botón
  "Enroll this device" → "Devices no funciona".**
- **Root cause (verificado con sonda)**: Next.js 16 reconstruye `request.url` con host
  `localhost` (el "Local:" del banner de `next start` sin `-H`), ignorando el `Host` header
  del request. Prueba ejecutada:

  ```
  $ curl -si 'http://127.0.0.1:3100/auth/callback' | grep -i '^location'
  location: http://localhost:3100/login?error=Sign-in%20callback%20is%20missing%20its%20authorization%20code.
  ```

  El `fail()` del console construye el redirect con `new URL(request.url).origin`
  (`platform/console/src/app/auth/callback/route.ts:34`) y el origin resultante es
  `http://localhost:3100` aunque el request llegó a `127.0.0.1:3100`.
- **Código implicado**:
  - `platform/console/src/app/auth/callback/route.ts:176` —
    `return NextResponse.redirect(new URL("/overview", url.origin));` (éxito) y `:34` —
    `const origin = new URL(request.url).origin;` (fail).
  - `platform/desktop-client/src/app/auth/callback/route.ts:154` —
    `const res = NextResponse.redirect(new URL("/dashboard", url.origin));`.
  - Ambos contenedores arrancan `npx next start --port N` sin hostname explícito
    (`docker-compose.yml` servicios `console` y `desktop-client`).
- **Fix sugerido (decisión de dev)**: construir el origin desde el `Host` header del
  request (`request.headers.get("host")`) en vez de `request.url`, o arrancar Next con
  hostname explícito, o redirigir con path relativo (`NextResponse.redirect(new URL("/overview", "http://" + request.headers.get("host")))`).

### F2 — [ALTO] Colisión de cookies console ↔ desktop: sesiones que se pisan y se destruyen entre apps

- **Código**: ambas apps usan los MISMOS nombres de cookie —
  `platform/console/src/lib/session.ts:19-21` (`ta_session`, `ta_refresh`, `ta_csrf`) y
  `platform/desktop-client/src/lib/session.ts:16-23` (`ta_session`, `ta_refresh`,
  `ta_csrf`, `ta_idtoken`, …). Ambas corren en el mismo host `127.0.0.1` (puertos 3100 y
  9090) y **las cookies no distinguen puertos** → un solo cookie jar compartido.
- **Repro (todo ejecutado en vivo)**:
  1. Login en el console `:3100` → abrir `http://127.0.0.1:9090/` → **el desktop muestra
     el dashboard SIN haber hecho login en él** (usa la `ta_session` del console;
     verificado: dashboard renderizado con datos reales del tenant).
  2. En ese estado, ir a `/enroll` → "Generate key & request enrollment" → **"Sign in
     first."** (el enroll exige la cookie `ta_idtoken` que solo pone el login del desktop:
     `platform/desktop-client/src/app/api/desktop/enroll/route.ts:14-18`). Callejón sin
     salida: el usuario "está dentro" pero no puede enrolar.
  3. "Sign out" en el desktop → **la sesión del console muere**: navegar a
     `http://127.0.0.1:3100/overview` → redirect a `/login` (verificado; el sign-out borra
     las cookies compartidas).
  4. Login en el desktop → abrir el console `:3100/login` → **auto-login con la sesión del
     desktop** (verificado: redirige directo a `/overview` sin pedir credenciales).
- **Esperado**: sesiones independientes por app (o al menos nombres de cookie distintos).
- **Impacto**: en el setup local single-node (constitution VII) es 100% reproducible y hace
  el flujo admin-console + desktop impredecible. En un despliegue real (desktop en la
  máquina del usuario) no colisiona, pero todo se valida local.
- **Fix sugerido**: prefijar las cookies por app (`ta_console_*` / `ta_desktop_*`) o
  distinguir por `Path`.

### F3 — [MEDIO] Budgets: no existe edición de presupuesto por usuario en la UI (gap UI↔API)

- **Pantalla**: Admin Console → Budgets (`/budgets`).
- **Repro**: abrir `/budgets` — solo hay botones "Edit" para la organización y para cada
  departamento. La tarjeta "Your own budget" es de solo lectura. En `/users` tampoco hay
  edición de presupuesto. **No existe ninguna pantalla que invoque
  `PUT /api/budgets/user/{user_id}`** (grep de `budgets/user` en
  `platform/console/src/` = 0 usos).
- **Esperado** (feedback Miller #5, tarea): "editar presupuesto org/depto/usuario desde la
  UI". El endpoint backend existe y está probado:
  `platform/app/governance/router.py:77-100` (`PUT /api/budgets/user/{user_id}`), y el
  propio cliente tipado del console lo documenta:
  `platform/console/src/lib/types.ts:68` — `PUT /api/budgets/{org|dept/{id}|user/{id}}`.
- **Código implicado**: `platform/console/src/app/(console)/budgets/budgets-page.tsx`
  — `EditTarget` (línea 30) solo admite `kind: "org" | "dept"`; el submit (líneas 61-64)
  solo construye `/api/budgets/org` o `/api/budgets/dept/{id}`; la tarjeta "Your own
  budget" (líneas 179-214) es read-only.
- **Nota**: `GET /api/budgets/history` (spec 013, `router.py:103`) tampoco tiene UI —
  gap menor adicional.

### F4 — [MEDIO] Audit: rechazar un dispositivo se audita como `device.revoked` (no `device.rejected`)

- **Pantalla**: Admin Console → Devices → "Reject" (con diálogo de confirmación).
- **Repro**: rechazar un enrolamiento pending desde la UI (funciona: toast "…rejected",
  fila pasa a `rejected`, el desktop recibe la invalidación) y consultar el audit log:
  ```
  SELECT action FROM audit_log WHERE tenant_id='9d9bf103-…' AND action LIKE 'device.%';
  → device.enrolled, device.approved, device.enrolled, device.revoked, device.revoked
  ```
  **Cero eventos `device.rejected`**: el reject del device `1ac72eb8…` quedó auditado como
  `device.revoked` (payload `{"outcome": "rejected"}`).
- **Esperado**: acción de audit `device.rejected` (o un nombre distinto de revoke); el
  trail debe distinguir ambas acciones de gobernanza (filtrar por `device.rejected` hoy
  devuelve 0 eventos).
- **Código**: `platform/app/devices/service.py:429` — dentro de `reject()` (línea 412),
  `audit_service.emit(..., action="device.revoked", …)`.
- **El estado del dispositivo en DB sí es correcto** (`devices.status='rejected'`) y la UI
  lo muestra bien; es únicamente el evento de audit el que lleva el nombre equivocado.

### F5 — [MEDIO] Desktop: el enrolamiento usa el id_token caducado de la cookie → 401 "Invalid or expired token"

- **Pantalla**: desktop `/enroll` → "Generate key & request enrollment".
- **Repro**: login en el desktop, esperar >10 min con la sesión abierta, luego pulsar
  "Generate key & request enrollment" → **"Invalid or expired token."** con
  `request_id: req_ca1753a2b27b427286aa`. Log del API: `POST /api/devices/enroll 401`.
  Con un login fresco el mismo paso da 202 pending (verificado con 4 enrolamientos).
- **Esperado**: el desktop refresca el id_token (o re-autentica) antes de enrolar; el
  usuario no debería hacer sign-out + sign-in para recuperarse.
- **Código**: `platform/desktop-client/src/app/api/desktop/enroll/route.ts:14-15` — lee
  `getSessionToken()` + `getIdToken()` de las cookies y reenvía el id_token tal cual al
  control plane; nunca lo renueva.
- **Atenuante**: la pantalla de error muestra el `request_id` (REQ-DTB-007 ✓) pero solo
  ofrece "Back" — no guía al re-login.

### F6 — [BAJO] Desktop: rechazo de enrolamiento sin feedback tras recargar la página

- **Pantalla**: desktop `/enroll`.
- **Repro (ambos ramas ejecutadas)**:
  - Admin rechaza MIENTRAS el usuario mira la página de "Waiting for approval" → el
    polling recibe el 410 en ≤5 s y muestra **"This enrollment is no longer valid. Start
    again or contact your organization admin."** con botón "Try again" — CORRECTO
    (verificado en vivo con dos sesiones de navegador separadas).
  - Admin rechaza mientras el usuario NO mira (o el usuario recarga `/enroll` después) →
    la página arranca en fase `idle`: muestra el formulario inicial "Enroll this device to
    your organization" **sin ninguna mención al rechazo** (verificado 2 veces).
- **Código**: `platform/desktop-client/src/app/enroll/enroll-flow.tsx:82-107` — el estado
  inicial solo distingue `active`/`pending` del device; un device `rejected`/`revoked`
  cae al `else` → `setPhase("idle")`. El mensaje "gone" (líneas 349-360) solo existe en el
  camino del polling en vivo.
- **Esperado**: distinguir `rejected` en el estado inicial y mostrar el mismo mensaje
  "no longer valid" (o similar) en vez del formulario vacío.

---

## Lo que SÍ funciona (evidencia de cada paso, todo en vivo)

### Budgets (Admin Console `/budgets`, sesión admin MFA real)

1. **Editar org** ($500.00 → $450.00): botón Edit de "Organization" → diálogo →
   "Save budget" → toast **"Organization budget updated."** → la fila muestra
   `Limit $450.00`. Persistencia verificada con reload completo de la página
   (`Organization — Limit $450.00`) y en DB:
   `SELECT amount_cents FROM budgets WHERE level='org' → 45000`.
2. **Editar depto** (IT Operations → $200.00): mismo flujo → toast
   **"IT Operations budget updated."** → `Limit $200.00` en la fila.
3. **Validación Σ deptos > org**: con org $450 e IT $200, intentar Cloud Operations
   $300 (Σ $500 > $450) → toast de error claro y accionable:
   **"Department budgets would exceed the organization budget by 5000 cents
   (Σ departments = 50000 > org = 45000)."** — el diálogo permanece abierto y el valor
   NO se persista (`Limit —`).
4. **Reflejo en Overview** (`/overview`): "Monthly organization budget $450.00 · Used
   $0.00 · Remaining $450.00", tabla de departamentos con IT Operations $200.00, y
   **"$250.00 remains unallocated."** (450−200 ✓).

### Devices (desktop :9090 + console :3100, sesiones reales separadas)

1. **Enroll desde el desktop**: `/enroll` → "Generate key & request enrollment" →
   keypair WebCrypto RSA-2048 en el navegador → **202 pending** con `device_id`
   (4 dispositivos enrolados: `c1c1e608…`, `1ac72eb8…`, `082b78f4…`, `539ffe0d…`),
   polling de aprobación cada 5 s.
2. **Approve desde la console UI**: fila pending → "Approve" → diálogo de confirmación →
   toast **"Device Trust Agent on Linux x86_64 approved."** → fila `active` con botón
   "Revoke". En el desktop (≤5 s): **"Device enrolled — active. This device is now part
   of your organization's governed session."** — el canje del enrollment_token one-time
   entregó el device token (guardado en el keystore local).
3. **Reject desde la console UI**: fila pending → "Reject" → confirmación → toast
   **"…rejected."** → fila `rejected`. Con la página del desktop abierta: mensaje
   **"This enrollment is no longer valid…"** en ≤5 s (ver F6 para el caso reload).
4. **Revoke desde la console UI**: fila active → "Revoke" → confirmación → toast
   **"…revoked."** → fila `revoked`. El dashboard del desktop refleja el dispositivo
   como `revoked` en "Your devices".
5. **Audit**: `device.enrolled` ×4, `device.approved` ×1, `device.revoked` ×2 en el
   trail del tenant (con el matiz de F4: los reject aparecen como revoked).

---

## Notas de proceso QA (no son bugs de producto)

- La sesión de navegador **por defecto** del harness de browser automation es compartida
  entre workers concurrentes del board: durante esta corrida otro worker inyectó
  navegaciones a un tenant ajeno (`dept-crud-ux`). Todo el flujo Devices se re-ejecutó en
  sesiones aisladas (`session="qa-t877b-devices"`, `"qa-t877b-console2"`). Regla para QA
  en este host: **usar siempre sesiones con nombre propio**.
- `capture_screenshot` del daemon de browser quedó colgado (timeout 60 s); la evidencia
  de este reporte es textual (innerText de cada pantalla, headers de respuestas, logs de
  contenedores y queries SQL), toda reproducible con los pasos arriba.
- Artefacto de sonido del harness: `fill_input` no dispara el evento `input` de React en
  inputs controlados (el primer intento de "Save budget" falló con el mensaje de
  validación del propio formulario hasta despachar el evento nativo). No es bug del
  producto: con el evento correcto el guardado funcionó.

## Tareas dev derivadas (regla 8 del CONTEXTO-TAREAS)

Una tarea dev hija por fallo confirmado, creadas con `--assignee dev --parent t_877b0cb5`:

| Fallo | Tarea dev |
|---|---|
| F1 — Login loop redirect `localhost` (CRÍTICO) | `t_38ce1e82` |
| F2 — Colisión de cookies console↔desktop (ALTO) | `t_6f8a24a5` |
| F3 — Budgets UI sin edición por usuario (MEDIO) | `t_6b40c758` |
| F4 — Audit reject→device.revoked (MEDIO) | `t_c076152d` |
| F5 — id_token caducado en enroll (MEDIO) | `t_9fddcb75` |
| F6 — Rechazo sin feedback tras reload (BAJO) | `t_d3fd75db` |
