# Demo Miller — VIVIR Trust Agent con tus manos (contra el stack real)

> **Qué es esto**: el script paso a paso para que **Miller** experimente el
> producto completo en SU laptop: instalar Trust Agent, usarlo con un modelo
> aprobado, ver SU consumo subir en vivo en SU panel admin, intentar algo
> bloqueado, ver un bloqueo NUEVO caer en vivo, y comprobar que el mismo
> bloqueo aplica en otra superficie (CLI) como el mismo usuario.
>
> **Nada simulado**: cada paso tiene su ✓ Verificación objetiva. Lo que hoy
> no funciona está marcado como **GAP** con su tarea — no se maquilla.
> Especificación completa: `specs/042-product-experience/spec.md`.
>
> Stack real de este servidor: api `:8000` · Keycloak `:8080` · gateway
> LiteLLM `:4000` · Admin Console `:3100` · Provider Console `:3200`
> (9 servicios verificados healthy el 2026-10-29).

---

## PREP — lo que preparamos NOSOTROS en el servidor (antes de la sesión)

### PREP-0 · Acceso de red (GAP-42-D, funciona HOY vía túnel SSH)

Todo el stack escucha en `127.0.0.1` del servidor. Para que la laptop de
Miller lo alcance tal cual (los instaladores hornean `api_base =
http://127.0.0.1:8000`), Miller abre un túnel SSH desde su laptop:

```bash
# En la laptop de Miller (Windows: PowerShell/WSL2; macOS/Linux: terminal):
ssh -N <usuario>@<servidor> \
  -L 8000:127.0.0.1:8000 \
  -L 8080:127.0.0.1:8080 \
  -L 4000:127.0.0.1:4000 \
  -L 3100:127.0.0.1:3100 \
  -L 3200:127.0.0.1:3200
```

Con el túnel abierto, `http://127.0.0.1:8000` en SU laptop ES el control
plane del servidor. (Hostname público + TLS = lane de despliegue, tarea
t_b62edff2.)

✓ Verificación PREP-0: en la laptop, `curl -s -o /dev/null -w '%{http_code}'
http://127.0.0.1:8000/download/demo-miller` → **200**.

### PREP-1 · Stack levantado

```bash
cd /root/trust-agent && docker compose ps
# 9 servicios Up/healthy: api, db, idp, console, provider-console,
# litellm-double, spend-worker, jobs-worker, attestation-worker
```

### PREP-2 · Tenant de demo aprovisionado desde la UI (idiom-proof)

1. Abrir Provider Console `http://127.0.0.1:3200` → login super-user.
2. "New tenant" → **slug `demo-miller`**, nombre "Miller Demo Corp",
   budget inicial, modelos aprobados (p. ej. `glm-5.2`), políticas default.
3. El provisioning crea realm KC vivo + 4 deptos + budget + policies + audit
   `tenant.provisioned` (flujo 1 del Pilar 1, PASS e2e).

✓ Verificación PREP-2: `http://127.0.0.1:8080/realms/demo-miller/.well-known/openid-configuration` → **200**.

### PREP-3 · Branding publicado (white-label)

Admin Console `http://127.0.0.1:3100` (login admin del tenant) → Branding:
`product_name = "Trust Agent"`, tagline, accent, logo → **Publish**.

✓ Verificación PREP-3: la página `http://127.0.0.1:8000/download/demo-miller`
muestra "Trust Agent" y el accent elegido.

### PREP-4 · Instaladores construidos para el tenant

```bash
cd /root/trust-agent && python3 scripts/build_desktop_installers.py \
  --slug demo-miller --platforms windows,linux
# (~10 min con wine; hornea org-binding + branding DENTRO del instalador)
```

✓ Verificación PREP-4: `ls installer-artifacts/demo-miller/*/` →
`trust-agent-setup-demo-miller-*-win-x64.exe` + AppImage +
`installer-manifest.json` (sha256). Y en vivo:
`curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/download/demo-miller/installer/windows` → **200**.
(macOS: botón ofrece vía CLI — gap honesto, requiere runner macOS.)

### PREP-5 · Usuario empleado invitado

Admin Console → Users & Roles → invitar
`empleado@demo-miller.local` al depto elegido, con budget de usuario.
El empleado seteará su password y enrolará TOTP en su PRIMER login (inline).

### PREP-6 · Política inicial del demo (lo que Miller bloqueará "en vivo")

Dejar al empleado CON `web_search` y SIN `terminal` en `allowed_tools`
(p. ej. asignar la política con esos valores a nivel usuario desde
/policies). El paso (d) del demo quitará `web_search` EN VIVO.

---

## DEMO — lo que ejecuta MILLER con sus manos

> Dos pestañas del navegador + una terminal en SU laptop:
> **[AGENTE]** = la app Trust Agent · **[ADMIN]** = `http://127.0.0.1:3100`
> (Admin Console, logueado como admin del tenant).

### (a) Instalo Trust Agent en MI laptop

1. **[ADMIN]** Hermes Download → copiar el link
   `http://127.0.0.1:8000/download/demo-miller`.
2. Abrir el link en el navegador → página con branding "Trust Agent" →
   botón **Windows** resaltado (detecta tu OS) → descargar el `.exe`.
3. Doble-clic en **"Trust Agent Setup"** → wizard zero-config:
   - instala el agente completo (vía oficial de la plataforma),
   - baja el bundle canónico del tenant (sha256 verificado),
   - aplica plugin de gobernanza + binding de org + skin white-label.
4. Sign-in **embebido** en el wizard: `empleado@demo-miller.local` →
   setear password (primer login) → **enrolar TOTP** (escanea con Google
   Authenticator) → entras con el código de 6 dígitos.
   - Lo que ves: "Sign in to **Trust Agent**" (tu realm, cero "Keycloak").
5. El agente arranca con el skin del tenant: nombre **Trust Agent**,
   accent elegido, prompt `❯`.
6. **[ADMIN]** Devices → aparece tu laptop **PENDING** → **Approve**
   (control de seguridad: con org_code público, la aprobación es la puerta).
7. **[AGENTE]** El dispositivo canjea su token solo (zero-UI) y queda
   enrolado. Pregunta al agente: *"¿cuál es mi estado de gobernanza?"* →
   usa `governance_status` y ves tu bundle firmado: policy version,
   budget restante (user/dept/org en USD), modelos aprobados, tools
   permitidas.

✓ Verificación (a):
- El instalador se llama "Trust Agent Setup" y el wizard muestra el
  branding del tenant.
- `governance_status` responde con **policy_version ≥ 1** y budget en USD.
- **[ADMIN]** Devices → tu laptop figura **active** con badge **Desktop**.
- ⚠️ **GAP-42-B**: hoy el log del wizard y el comando muestran "Hermes"
  (`hermes`) — tarea dev hija abierta; el skin y la TUI ya dicen Trust Agent.

### (b) Uso un modelo aprobado y veo MI consumo subir en vivo

1. **[AGENTE]** Pregunta algo que use el modelo (p. ej. *"resume en 3
   bullets qué es la gobernanza de IA"*). La respuesta pasa por el gateway
   del tenant con TU virtual key (modelo aprobado → 200).
2. **[ADMIN]** pestaña **Usage** → tu fila (`empleado@…`) sube: requests +1,
   tokens in/out, costo USD. Drill-down → el request de hace un momento
   con modelo y costo exacto.
3. Repite 2-3 preguntas y ve los números subir (refresca con el botón —
   ⚠️ GAP-42-C: auto-refresco en camino).

✓ Verificación (b):
- **[ADMIN]** Usage → drill-down muestra cada request con tokens y costo.
- En el servidor (lo corroboramos en pantalla si quieres):
  `docker exec trust-agent-db psql -U postgres -d trust_agent -c
  "SELECT count(*), max(occurred_at) FROM spend_ledger;"`
  → el count subió exactamente tus requests (una fila por request real,
  ingestada en ~2 s por el spend-worker). **Nada simulado**: toda cifra
  de Usage sale de este ledger.
- **[ADMIN]** Devices → franja "1 online · live" (auto-refresco 15 s).

### (c) Intento algo bloqueado → lo veo bloqueado

1. **[AGENTE]** Pide: *"ejecuta `ls -la` en la terminal"*.
2. El agente responde con el bloqueo de la organización:
   `BLOCKED by organization policy: tool 'terminal' is not allowed.
   Allowed tools: governance_status, web_search, …`
   — lo aplica el plugin DENTRO de tu agente (no es un filtro de UI), y
   tu SOUL.md o config local no lo evaden (REGLA DE ORO, verificada 11/11).
3. **[ADMIN]** Audit → `device.attestation` con `veto_counts`
   (`terminal: 1`) — tu intento quedó auditado en la org.

✓ Verificación (c):
- Mensaje exacto `BLOCKED by organization policy` en la respuesta.
- **[ADMIN]** Audit → el veto aparece con `veto_counts`.
- Contraste: pide *"busca en la web X"* → `web_search` SÍ corre (está
  permitida hoy — la bloquearemos en vivo en el paso (d)).

### (d) El admin (yo, otra pestaña) bloquea algo NUEVO → lo veo caer EN VIVO

> Preparación de 1 línea (PREP, mecanismo real del plugin): el `.env` del
> perfil del empleado lleva `TRUST_SYNC_INTERVAL=15` → tu agente sincroniza
> el bundle cada 15 s (default 300 s; env documentado del plugin).

1. **[AGENTE]** Pide: *"busca en la web las noticias de hoy"* → `web_search`
   corre normal. ✅
2. **[ADMIN]** Policies → editar la policy del usuario → quitar
   `web_search` de `allowed_tools` → guardar. (En Devices, tu dispositivo
   muestra el badge "→ vN pending": hay una policy nueva que aún no
   confirmas.)
3. Espera **≤ 15-20 s** (tu agente re-baja el bundle JWS firmado — SIN
   reiniciar, SIN re-enrolar).
4. **[AGENTE]** Pide de nuevo: *"busca en la web las noticias de hoy"* →
   **BLOCKED by organization policy: tool 'web_search' is not allowed.**
   — cayó en vivo, frente a tus ojos.
5. **[ADMIN]** Devices → tu dispositivo vuelve a "policy up to date"
   (confirmaste vN). Audit → `policy.propagated {from: vN-1, to: vN}`.

✓ Verificación (d):
- El bloqueo llegó **sin reiniciar** el agente, en ≤ 15 s + 1 turno.
- **[ADMIN]** Audit → `policy.propagated` con `{from_version, to_version}`.
- **[ADMIN]** Devices → badge "→ vN pending" → up to date (el ciclo
  completo de propagación en vivo).

### (e) Mismo usuario en CLI: MISMO bloqueo (gobernanza entre superficies)

> La pregunta exacta: *"si admin bloquea por ejemplo Telegram, ¿igual me va
> a dejar hacer lo mismo en Mac o CLI?"* — Respuesta: el governance-bundle
> es del **USUARIO**, no de la app. Aquí lo compruebas con una segunda
> superficie.

1. En una terminal de TU laptop (WSL2/macOS/Linux):
   ```bash
   curl -fsSL http://127.0.0.1:8000/download/demo-miller/cli | bash
   ```
   → instala el Hermes CLI ligado a tu org (mismo plugin, mismo binding).
2. `trust-login` → mismo navegador SSO+MFA → **el mismo usuario**
   `empleado@demo-miller.local`.
3. **[ADMIN]** Devices → aparece el segundo dispositivo **PENDING** con
   badge **CLI** → **Approve**.
4. En la terminal: `hermes` (próximamente `trust-agent`, GAP-42-B) →
   pregunta *"¿cuál es mi estado de gobernanza?"* → `governance_status`
   muestra **la MISMA policy_version** que tu desktop (bundle por usuario).
5. Pide: *"busca en la web las noticias de hoy"* → **BLOCKED by
   organization policy: tool 'web_search' is not allowed** — el bloqueo
   que el admin aplicó en (d) te sigue a esta superficie. Mismo usuario,
   otra app, mismo candado.

✓ Verificación (e):
- `governance_status` en CLI reporta la misma `policy_version` que en (d).
- `web_search` bloqueada en AMBAS superficies del mismo usuario.
- **[ADMIN]** Devices → 2 dispositivos del mismo usuario (badge Desktop +
  CLI), ambos online, ambos up to date.

#### (e.2) Bloquear Telegram — cross-surface real (GAP-42-A convergido)

> La pregunta literal de Miller: *"si admin bloquea por ejemplo Telegram,
> ¿igual me va a dejar hacer lo mismo en Mac o CLI?"* — **Respuesta: NO**.
> El governance-bundle es por USUARIO, y el plugin ahora enforce
> `allowed_connectors` y `messaging_permissions` en TODAS las superficies.

1. **[ADMIN]** Policies → `messaging_permissions = {"telegram": {"allowed": false}}`
   + `allowed_connectors = []` → Save → policy vN+1.
2. En ≤ 15 s (TRUST_SYNC_INTERVAL=15) ambas superficies (desktop + CLI)
   sincronizan el nuevo bundle → `governance_status` muestra
   `Allowed connectors: none` y `Policy version: N+1` en AMBAS.
3. Pide en desktop: *"envía un mensaje por Telegram"* →
   `connectors__telegram__send_message` → **BLOCKED by organization policy:
   messaging platform 'telegram' is blocked** (veto del plugin).
4. Pide en CLI: *"conecta mi cuenta de Gmail"* →
   `connectors__gmail__send` → **BLOCKED by organization policy: connector
   'gmail' is not allowed. Allowed connectors: none** (veto del plugin).
5. El gateway tiene `gateway.platforms.telegram.enabled=false` pineado por
   el managed scope (overlay GANA al config del usuario, M16) — el despacho
   por Telegram no procede aunque el agente lo intente.

✓ Verificación (e.2):
- `governance_status` en AMBAS superficies reporta `Allowed connectors: none`
  y la misma `policy_version` (bundle por usuario, cross-surface).
- `connectors__telegram__send_message` bloqueada en desktop y CLI con mensaje
  estándar `BLOCKED by organization policy`.
- `connectors__gmail__send` bloqueada en desktop y CLI (allowed_connectors=[]).
- `gateway.platforms.telegram.enabled: false` en config.yaml de ambas
  superficies (pin del managed scope, fail-closed).
- Intento auditado en `plugin.log` + `veto-counts.json` de ambas superficies.
- E2E automatizado: `python3 platform/scripts/evidence_cross_surface.py` → 0 FAIL.

### (f) Opcional — el presupuesto corta de verdad

1. **[ADMIN]** Budgets → budget del empleado a `$0.01`.
2. **[AGENTE]** una pregunta más → el sweep bloquea TU virtual key en el
   gateway → el turno falla con 401 "Key is blocked". Audit → `budget.cutoff`.
3. **[ADMIN]** restaurar budget → siguiente turno pasa (desbloqueo
   idempotente).

✓ Verificación (f): el corte es en el GATEWAY (401 real), no un aviso de UI;
audit `budget.cutoff` + `model.key_blocked` en la org.

---

## Resumen de gaps honestos (no se maquillan en el demo)

| Gap | Qué falta hoy | Tarea |
|---|---|---|
| ~~GAP-42-A (ALTO)~~ | ✅ Convergido: enforcement de `messaging_permissions`/`allowed_connectors` en el plugin + E2E cross-surface (evidence_cross_surface.py, 17 tests, 0 FAIL) | dev t_e455680f |
| GAP-42-B (MEDIO) | "Hermes" visible en wizard/installers/comando → white-label total + alias `trust-agent` | dev, hija de t_cc9eb24f |
| GAP-42-C (BAJO) | Usage sin auto-refresco (hoy botón manual) | dev/ui, hija de t_cc9eb24f |
| GAP-42-D (red) | Stack en 127.0.0.1 → túnel SSH para la laptop de Miller (funciona HOY); hostname público = piloto de despliegue t_b62edff2 | documentado aquí |
| macOS installer | Requiere runner macOS (workflow listo en el repo); el botón ofrece la vía CLI real | spec 040, documentado |

## Reproducir las verificaciones del servidor (para escépticos)

```bash
cd /root/trust-agent && docker compose ps                        # 9 Up
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/download/demo-miller
docker exec trust-agent-db psql -U postgres -d trust_agent -c \
  "SELECT action, ts FROM audit_log WHERE tenant_id=(SELECT id FROM tenants WHERE slug='demo-miller') ORDER BY ts DESC LIMIT 10;"
# → download.issued, device.enrolled, device.approved, device.attestation
#   (veto_counts), policy.propagated, budget.cutoff … todo lo que viviste
docker exec trust-agent-db psql -U postgres -d trust_agent -c \
  "SELECT count(*) FROM spend_ledger;"      # una fila por request real
```
