# Spec 042 — Experiencia completa del producto: Miller debe VIVIR Trust Agent (no paneles — el producto)

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* (SDD) — definición completa de la experiencia verificada contra el stack real el 2026-10-29 (código, BD viva, servicios up, artefactos de installer en disco); gaps convertidos en tareas dev hijas de t_cc9eb24f |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-29 |
| **Autor** | product-owner (tarea t_cc9eb24f) |
| **Padre** | specs/022-hermes-real-bridge · specs/023-hermes-download · specs/027-live-policy-presence · specs/034-cli-governed · specs/040-platform-installers · specs/019-governance-over-hermes · specs/005-governance · specs/035-usage-analytics |
| **Demo** | `docs/demo-miller.md` (entregable final de esta spec — script paso a paso que Miller ejecuta con sus manos contra el stack real) |
| **Idioma** | Español (docs) / inglés (código y UI copy) |

---

## 1. Mandato (Miller, 2026-10-29)

> "Yo no he visto a Trust Agent funcionando — para mí sigue siendo un concepto.
> No he visto si realmente funciona. ¿Has considerado la visión completa de
> qué es el producto?"

Y la pregunta exacta que esta spec debe responder con evidencia:

> "Si admin bloquea por ejemplo Telegram, ¿igual me va a dejar hacer lo mismo
> en Mac o CLI?"

Dos días de correcciones de paneles web no resuelven esto: Miller jamás ha
(1) instalado la app en SU compu, (2) visto que dice "Trust Agent" y no
"Hermes", (3) sentido un bloqueo aplicado, ni (4) visto SUS tokens subiendo en
SU panel admin mientras él usa el agente. Esta spec define esa experiencia
completa, la verifica contra el estado REAL del servidor y convierte cada gap
en tarea dev con archivo:línea. **Nada simulado**: lo que no funciona hoy se
documenta como gap, no se maquilla en el demo.

## 2. Qué ES el producto (la visión completa)

El producto NO son los paneles. Los paneles son la cabina de mando. El
producto es el **ciclo vivo de gobernanza** con tres actores:

```
   PROVEEDOR                ADMIN (Miller)                    EMPLEADO
   aprovisiona       ┌──► ve el uso EN VIVO ◄───────────── usa el agente
   tenants           │     (tokens, requests,                │
   idiom-proof       │      presencia)                       ▼
                     │    bloquea X ──► el agente del empleado lo siente
                     └──◄ audit/vetos ◄── en el siguiente turno, SIN reiniciar
```

**La prueba de fuego del producto** (lo que Miller debe VIVIR en el demo):

1. Instalo Trust Agent en mi laptop → dice "Trust Agent", mi org, cero "Hermes".
2. Uso un modelo aprobado → en el panel admin MI consumo sube en vivo.
3. Intento algo bloqueado → lo veo bloqueado, con el mensaje de la org.
4. El admin (yo, otra pestaña) bloquea algo nuevo → lo veo caer EN VIVO.
5. Soy el mismo usuario en otra superficie (CLI) → el MISMO bloqueo aplica.

Si esos 5 momentos funcionan contra el stack real, el producto existe. Si
alguno no funciona, es un gap con tarea — no una demo maquillada.

## 3. Journey del empleado — definición detallada

### 3.1 Momento a momento

| # | Momento | Qué ve exactamente | Estado hoy (verificado 2026-10-29) |
|---|---|---|---|
| E1 | Recibe el link | El admin comparte `https://<control-plane>/download/<org_code>` desde SU Admin Console (sección Hermes Download). El link es del CLIENTE, no del proveedor (corrección Miller t_360a752e). | ✅ `GET /download/pi-e2e` → 200 en vivo |
| E2 | Abre la página de descarga | Página con **branding publicado del tenant** (nombre de producto, tagline, accent, logo). Tres vías: botón **Windows** (`.exe`), botón **Linux** (AppImage), botón **macOS** (hoy ofrece vía CLI — gap honesto, sin runner), comando **CLI** copiable (`curl … \| bash`) y bundle tarball como opción avanzada. Detección de OS que resalta SU botón. | ✅ página 200; instaladores `windows`/`linux` → 200 en vivo (`installer-artifacts/pi-e2e/0.6.0/`: exe 102.9 MB + AppImage 117 MB, sha256 en manifest) |
| E3 | Instala | Doble-clic en **"Trust Agent Setup"** (nombre del instalador = branding del tenant). Wizard zero-config: instala el agente completo por la vía oficial de la plataforma, baja el bundle canónico (sha256 verificado), aplica plugin + binding de org + skin white-label + gateway del tenant. | ✅ wizard real (Electron, spec 040); ⚠️ el LOG del wizard muestra líneas "Hermes" → **GAP-42-B** |
| E4 | Primer arranque / sign-in | El wizard abre el sign-in **embebido** contra el realm de SU org (authorization-code + PKCE S256, `acr_values=2`): SSO + MFA. En Windows el empleado NO necesita Python. Si es su primer login: setea su password y enrola TOTP inline. Keycloak queda invisible (tema trust-agent: título "Sign in to Trust Agent", favicon del tenant). | ✅ verificado (Pilar 2/3: 0 menciones de Keycloak en DOM, título limpio) |
| E5 | Enrolamiento del dispositivo | **Cero UI de enrolamiento**: el plugin enrola solo (device PENDING → el admin aprueba desde el Admin Console → device token + bundle JWS firmado). El empleado solo experimenta "mi admin aprobó mi laptop". | ✅ auto-enroll zero-UI verificado e2e (Pilar 3, 66 checks) |
| E6 | Día a día | El agente corre con el **skin white-label** del tenant: nombre de agente = `product_name` del branding (p. ej. "Trust Agent"), accent del tenant, prompt `❯`. La tool `governance_status` le muestra SU estado gobernado (modo, versión de policy, presupuesto restante por nivel, modelos aprobados, tools/conectores permitidos). Lo bloqueado llega con el mensaje exacto `BLOCKED by organization policy: …`. | ✅ skin (bundle.py `_skin_yaml`: `branding.agent_name = product_name`); ✅ governance_status; ⚠️ comando `hermes`, home `~/.hermes` y textos del instalador exponen "Hermes" → **GAP-42-B** |

### 3.2 Regla white-label (vinculante)

**El empleado NUNCA ve la palabra "Hermes"** en ninguna superficie que toque:
instalador, wizard, página de descarga, CLI/TUI, mensajes de bloqueo, docs de
su bundle. "Hermes" es un detalle de infraestructura del proveedor, igual que
nadie compra "Linux" cuando compra un Chromebook.

**Fugas actuales (GAP-42-B, con archivo:línea):**

| Fuga | Dónde |
|---|---|
| Log del wizard: "==> Hermes not found — installing the official Hermes Agent", "Hermes install failed", "Hermes found: …" | `platform/installer-app/main.js:166,172,176,181,185` |
| `install.sh` del bundle imprime "Hermes not found — installing…", "Hermes found" | `platform/app/download/bundle.py:129-139` (plantilla `_INSTALL_SH`) |
| Instalador CLI imprime lo mismo | `platform/app/download/cli.py:69-79` |
| README del bundle enlaza "a complete [Hermes Agent](…)" | `platform/app/download/bundle.py:413,424` |
| El comando es `hermes` y el home `~/.hermes` (instalador oficial, D-023-04) | diseño actual; requiere alias `trust-agent` (wrapper, NO parche al core) |

## 4. Journey del admin en vivo — lo que Miller ve MIENTRAS el empleado usa

| # | Qué ve | Mecanismo | Presupuesto de tiempo | Estado hoy |
|---|---|---|---|---|
| A1 | **Tokens consumiéndose en tiempo real** — página Usage: totales del período, una fila por usuario (requests, tokens in/out, costo USD), drill-down request-a-request, export CSV. Toda cifra sale de `spend_ledger` (una fila por request real vía LiteLLM). | spend-worker ingesta logs de LiteLLM cada **2 s** (`platform/app/jobs/worker.py:100`, verificado en logs del contenedor: ciclo activo con cursor vivo) | request → fila en ledger ≤ ~4 s; UI: refresco manual hoy | ✅ datos reales; ⚠️ **la página no se auto-refresca** (solo botón/refetch: `usage-page.tsx:207,346,443`) → **GAP-42-C** |
| A2 | **Requests apareciendo** — drill-down `/usage` con modelo, modo (central/byok), tokens y costo por request | `GET /api/usage/requests` (spec 035) sobre `spend_ledger` | ídem A1 | ✅ |
| A3 | **Presencia conectada** — franja "X online / Y active · live" en Devices con auto-refresco **cada 15 s**: quién, depto, dispositivo (badge CLI/Desktop), última señal, versión de policy aplicada vs vigente, uptime | heartbeat del plugin (spec 027) → `devices.last_heartbeat_at` | ventana online 600 s | ✅ (auto-refresh 15 s verificado en spec 027) |
| A4 | **UN BLOQUEO APLICÁNDOSE EN VIVO** — el admin cambia una policy → el badge del device pasa a "→ vN pending" → el device re-baja el bundle JWS → **el agente del empleado aplica el nuevo efectivo en el siguiente turno, SIN reiniciar** → audit `policy.propagated {from,to}` | heartbeat (default 300 s, configurable `TRUST_SYNC_INTERVAL`) + `BundleSyncer` (spec 027, smoke 21/21 PASS) | ≤ `TRUST_SYNC_INTERVAL` + 1 turno (demo: interval 15 s) | ✅ mecanismo verificado; el demo lo baja a 15 s vía env (mecanismo real, no hack) |
| A5 | **Audit de lo que pasó** — vetos del agente llegan como `device.attestation` con `veto_counts`, timeline con filtros, pill de integridad de cadena | spec 008/021 | — | ✅ (re-verificado en BD viva) |

## 5. Gobernanza ENTRE superficies — la pregunta exacta de Miller

> "Si admin bloquea por ejemplo Telegram, ¿igual me va a dejar hacer lo mismo
> en Mac o CLI?"

**Respuesta de producto (vinculante): NO — un bloqueo es del USUARIO, no de la
app.** El mismo usuario no puede evadir un bloqueo cambiando de superficie.

### 5.1 La semántica: governance-bundle por USUARIO, no por app

- El bundle JWS se compila con `effective_policy(user_id, department_id)` —
  **por usuario** — y se firma con `aud = device_id` (uno por superficie):
  `platform/app/governance/bundle.py:225-229` (`compile_bundle_payload`) y
  `:249-258`. Cada superficie del mismo usuario (desktop Windows/Mac/Linux,
  CLI, gateway de mensajería) baja el MISMO efectivo firmado.
- La virtual key del usuario (modelos ∩ policy) también es por usuario: el
  gateway LiteLLM del tenant rechaza modelos no aprobados en TODAS las
  superficies (403 `key_model_access_denied`).
- El presupuesto es por usuario → depto → org: el corte bloquea la key del
  usuario, no una app.
- Por lo tanto: **admin bloquea X → TODAS las superficies del usuario sienten X
  en su siguiente sync + turno** (≤ `TRUST_SYNC_INTERVAL` + 1 turno cada una).

### 5.2 Superficies del mismo usuario

| Superficie | Cómo se enrola | Cómo recibe el bloqueo |
|---|---|---|
| Desktop (Windows exe / Linux AppImage / macOS vía CLI hoy) | wizard → auto-enroll (`device_type=desktop`) | bundle JWS → veto del plugin / 403 del gateway |
| CLI (Linux/macOS/WSL2) | `curl …/cli \| bash` → trust-login → auto-enroll (`device_type=cli`, spec 034) | idéntico — mismo plugin, mismo bundle por usuario |
| Mensajerías (telegram, whatsapp, slack, teams…) del agente del usuario | gateway del propio Hermes enrolado del usuario | **así debe ser**; hoy NO hay enforcement → GAP-42-A |

### 5.3 Estado REAL de la pregunta (verificado en código, 2026-10-29)

**La arquitectura ya es por usuario** (bundle, key, budget) — el bloqueo de
tools y modelos YA cruza superficies porque el veto vive en el plugin que
corre en cada superficie del usuario. **PERO** el caso concreto que Miller
preguntó — bloquear una plataforma de mensajería — tiene un gap real:

- `messaging_permissions` (map plataforma → `read_only|reply|full|false`) y
  `allowed_connectors` (lista de slugs: telegram, whatsapp, slack, email…)
  **sí se compilan al bundle** (catálogo tipado, combinator per-platform
  deny-overrides: `platform/app/governance/combinator.py` §map;
  `bundle.py:264`) y el plugin las **conoce** (`policy_contract.py:59,124`,
  default restrictivo `{}` / `[]`).
- **NADIE las enforce**: en todo el plugin, `messaging_permissions` solo
  aparece en `policy_contract.py`; `allowed_connectors` solo se muestra en
  `governance_status` (`__init__.py:153`) — el veto de `enforcement.py`
  (`on_pre_tool_call`, líneas 403-425) solo consulta `allowed_tools`.

**Conclusión honesta**: si el admin bloquea hoy `terminal` o un modelo, el
empleado lo siente en TODAS sus superficies (verificado). Si bloquea
**Telegram**, hoy no lo siente en NINGUNA — la policy se compila y firma, pero
el agente no la obedece. → **GAP-42-A (ALTO)**, tarea dev hija. El E2E
explícito de 2 superficies (REQ-PE-012) queda como criterio de aceptación de
esa tarea y paso (e) del demo.

## 6. Lo que NO es el producto — "un Hermes con otra cara"

El producto no es cosmético. Cada afirmación con su verificación REAL de hoy:

| Afirmación | Mecanismo real | Evidencia viva (2026-10-29) |
|---|---|---|
| **Tokens contados de verdad** | Cada request LLM pasa por el gateway LiteLLM del tenant con la virtual key del usuario → log de spend → `spend_ledger` **una fila por request** (idempotente por request_id, milli-centavos, margen MaaS solo en central) → counters de budget user/depto/org en la MISMA transacción | `spend_ledger`: 20 filas reales, última HOY 20:22 UTC; spend-worker ciclando cada 2 s con cursor vivo (logs del contenedor) |
| **Bloqueos enforced de verdad** (no ocultos en UI) | El veto vive DENTRO del agente del empleado (`pre_tool_call` del plugin) con mensaje exacto `BLOCKED by organization policy`; deny-by-default (key ausente = restrictivo, `policy_contract.py` REQ-GOH-023); los modelos se cortan en el gateway (403 real, no filtro de UI); la org gana sobre SOUL.md/config local (REGLA DE ORO, bundle JWS inforgeable) | REGLA DE ORO PASS 11/11 en vivo (soul.md contradictorio → terminal SIGUE vetada); vetos auditados con `veto_counts` en BD viva |
| **Budget cortado de verdad** | El sweep del spend-worker sincroniza la virtual key con el gateway: cutoff → key `blocked` → 401 "Key is blocked" en el siguiente turno del agente; restauración idempotente | Verificado en vivo (Pilar 3, 3c: budget $0.01 → keys blocked → 401 → restauración); audit `budget.cutoff`/`model.key_blocked` en BD |
| **El empleado no puede evadir** | Sin `fallback_model`, cero gateway→gateway, plugin dentro de la app, fail-closed a RESTRICTED si el bundle no verifica/expira | Punto 5 del Pilar 3 PASS; revocación integral (device → keys → conectores en la misma transacción) |

## 7. Gaps definición ↔ realidad (todos con tarea dev hija de t_cc9eb24f)

| Gap | Severidad | Qué falta | Archivo:línea | Tarea |
|---|---|---|---|---|
| **GAP-42-A** | ALTO | Enforcement en el plugin de `messaging_permissions` + `allowed_connectors` (el bloqueo de Telegram/mensajerías/conectores debe morder en TODAS las superficies del usuario) + E2E cross-surface (mismo usuario, 2 superficies, bloqueo, ambas bloqueadas) | `platform/hermes/plugins/trust-agent-governance/policy_contract.py:59,124` (conocidas, no enforced) · `enforcement.py:403-425` (solo allowed_tools) · `__init__.py:153` (solo display) · `platform/app/governance/combinator.py` (merge per-platform ya existe) | dev (hija de t_cc9eb24f) |
| **GAP-42-B** | MEDIO | White-label total: eliminar "Hermes" de todo lo que el empleado toca (log del wizard, install.sh, instalador CLI, README del bundle) + alias de comando `trust-agent` (wrapper, cero parches al core) | `platform/installer-app/main.js:166-185` · `platform/app/download/bundle.py:100-139,413,424` · `platform/app/download/cli.py:36-79` | dev (hija de t_cc9eb24f) |
| **GAP-42-C** | BAJO | Usage en vivo: auto-refresco de la página Usage (hoy refresco manual) para que Miller vea SUS tokens subir sin F5 | `platform/console/src/app/(console)/usage/usage-page.tsx:207,346,443` (sin setInterval; patrón a copiar: presencia de Devices, 15 s) | dev/ui (hija de t_cc9eb24f) |
| **GAP-42-D** | Prereq de demo (no código) | El stack escucha en 127.0.0.1: para que Miller instale desde SU laptop hace falta túnel SSH (documentado en el demo, funciona HOY) o hostname público+TLS (piloto de despliegue t_b62edff2) | `/root/trust-agent/.env:20` (`TRUST_DOWNLOAD_BASE_URL=http://127.0.0.1:8000`); instaladores hornean api_base 127.0.0.1 → el túnel los hace funcionar tal cual | documentado en `docs/demo-miller.md` §PREP-0; hostname público = lane deploy |

## 8. Requisitos (EARS) — REQ-PE

Trazabilidad: cada REQ lista sus criterios de aceptación (AC). El estado
"✅ hoy" significa verificado contra el stack real el 2026-10-29 por esta spec.

### Journey del empleado

**REQ-PE-001** (página de descarga brandeada) — Cuando el empleado abre
`/download/{org_code}`, el sistema muestra el branding publicado del tenant
(nombre de producto, tagline, accent, logo), un botón por plataforma
(Windows/Linux/macOS), el comando CLI copiable y el bundle avanzado, con el
botón de SU OS resaltado.
- AC-001-1: página 200 con `product_name` del branding publicado en el HTML servido. ✅ hoy
- AC-001-2: `GET /download/{slug}/installer/windows` y `/linux` sirven el artefacto real (200, `X-Installer-SHA256`); macOS responde 404 `installer_not_built` (honesto) hasta haber runner. ✅ hoy

**REQ-PE-002** (instalador white-label por tenant) — Cuando el empleado
descarga el instalador de su plataforma, el sistema le entrega un binario
"Trust Agent Setup" con `org-binding.json` (org, control plane, gateway,
OIDC) y `branding.json` horneados DENTRO, e íconos generados desde el
branding.
- AC-002-1: `installer-manifest.json` con sha256+size por plataforma en `installer-artifacts/{slug}/{plugin_version}/`. ✅ hoy (pi-e2e v0.6.0: exe 102.9 MB, AppImage 117 MB)
- AC-002-2: wizard `--status` imprime el binding horneado; `--install-only` instala sin UI. ✅ hoy

**REQ-PE-003** (regla white-label: NUNCA "Hermes") — Cuando el empleado
interactúa con cualquier superficie del producto (página de descarga,
instalador/wizard, CLI/TUI, mensajes de bloqueo, README del bundle), el
sistema le presenta únicamente el nombre de producto del tenant; la marca
"Hermes" no aparece en ningún texto visible al empleado.
- AC-003-1: grep case-insensitive de "hermes" en textos visibles del wizard, install.sh, instalador CLI y README del bundle → 0 resultados. ❌ GAP-42-B
- AC-003-2: el comando que el empleado teclea es `trust-agent` (alias/wrapper sobre el binario oficial; `hermes` sigue existiendo pero no es el camino mostrado). ❌ GAP-42-B
- AC-003-3: la TUI muestra `agent_name` = `product_name` del branding (skin). ✅ hoy

**REQ-PE-004** (primer arranque zero-config) — Cuando el empleado abre el
instalador por primera vez, el sistema lo guía sin decisiones técnicas:
instalación del agente → sign-in embebido SSO+MFA del realm de su org
(PKCE S256, `acr_values=2`) → enrolamiento TOTP inline si es primer login →
el agente arranca.
- AC-004-1: en Windows el empleado no instala Python ni nada externo al wizard. ✅ hoy
- AC-004-2: el realm sirve tema trust-agent (título "Sign in to Trust Agent", favicon del tenant, 0 menciones de Keycloak en DOM). ✅ hoy

**REQ-PE-005** (auto-enroll zero-UI + aprobación) — Cuando el empleado
completa el sign-in, el sistema enrola su dispositivo automáticamente
(PENDING, keypair local, enrollment_token one-time) sin ningún panel de
enrolamiento; cuando el admin lo aprueba, el dispositivo recibe su token y el
bundle JWS firmado.
- AC-005-1: flujo enroll→approve→exchange verificado e2e (202/200/410). ✅ hoy
- AC-005-2: el empleado nunca ve un formulario de "enrolar dispositivo". ✅ hoy

**REQ-PE-006** (sesión gobernada visible) — Cuando el empleado pregunta por
su estado, la tool `governance_status` muestra modo, versión de policy,
presupuesto restante por nivel (USD), modelos aprobados, tools/conectores
permitidos y gracia offline — todo leído del bundle FIRMADO (sin bundle →
RESTRICTED y sin cifras inventadas).
- AC-006-1: salida de governance_status refleja exactamente el bundle verificado. ✅ hoy

### Journey del admin en vivo

**REQ-PE-007** (consumo en tiempo real) — Cuando el empleado hace un request
LLM, el sistema lo registra como una fila de `spend_ledger` (request_id
idempotente, tokens in/out, costo milli-centavo, modo central/byok) y la
página Usage del admin la muestra — totales, fila por usuario y drill-down
por request — sin que el admin recargue la página.
- AC-007-1: request → fila en `spend_ledger` en ≤ 10 s (p95; ciclo del worker 2 s). ✅ hoy (datos)
- AC-007-2: la página Usage se auto-refresca (intervalo ≤ 15 s, mismo patrón que la presencia de Devices) y muestra el request nuevo sin F5. ❌ GAP-42-C
- AC-007-3: toda cifra de Usage sale de `spend_ledger` (cero datos sintéticos). ✅ hoy

**REQ-PE-008** (presencia) — Cuando el admin abre Devices, el sistema muestra
la franja "X online / Y active · live" (auto-refresco 15 s) con usuario,
depto, tipo de dispositivo (CLI/Desktop), última señal, versión de policy
aplicada vs vigente y uptime. ✅ hoy (spec 027)

**REQ-PE-009** (bloqueo aplicándose EN VIVO) — Cuando el admin cambia una
policy del usuario/depto/org, el sistema la propaga a cada superficie
enrolada del usuario en ≤ `TRUST_SYNC_INTERVAL` + 1 turno, sin reiniciar ni
re-enrolar: badge "→ vN pending" en Devices → re-baja del bundle JWS →
aplicación en el siguiente turno → audit `policy.propagated {from,to}`.
- AC-009-1: cambio de policy visible en el agente del empleado sin reinicio (smoke spec 027 21/21). ✅ hoy
- AC-009-2: el demo usa `TRUST_SYNC_INTERVAL=15` (env real del plugin) para una experiencia de segundos. ✅ hoy (mecanismo)

### Gobernanza entre superficies

**REQ-PE-010** (bundle por USUARIO) — Cuando cualquier superficie del mismo
usuario (desktop, CLI, mensajería) sincroniza, el sistema le entrega el
MISMO efectivo firmado: `effective_policy(user_id, department_id)` compilada
por usuario, `aud` por dispositivo.
- AC-010-1: dos dispositivos del mismo usuario reportan el mismo `policy_version` y las mismas keys efectivas en `governance_status`. ✅ hoy por arquitectura (bundle.py:225-229)

**REQ-PE-011** (bloqueo de mensajería/conectores en TODAS las superficies) —
Cuando el admin bloquea una plataforma de mensajería
(`messaging_permissions[<plataforma>] = false`) o un conector
(`allowed_connectors` sin el slug), el sistema lo hace cumplir en el agente
del usuario en TODAS sus superficies: el gateway de esa plataforma no
procesa mensajes del agente (o los procesa solo en el modo permitido) y la
tool/conector bloqueado es vetada con el mensaje estándar de la org —
independientemente de desde qué app/dispositivo opere el usuario.
- AC-011-1: con `messaging_permissions = {"telegram": false}` (y el resto de keys igual), el agente enrolado del usuario NO despacha por telegram en ninguna superficie; el intento queda auditado (veto/finding). ❌ GAP-42-A
- AC-011-2: con `allowed_connectors = []`, toda tool de conector del agente es vetada `BLOCKED by organization policy` en desktop y CLI del mismo usuario. ❌ GAP-42-A (hoy solo display)
- AC-011-3: el enforcement es fail-closed: key ausente en el bundle → default restrictivo del plugin (`{}`/`[]`), nunca relaja. (contrato ya lo garantiza para el valor; el enforcement nuevo debe leerlo vía `policy_value`). ❌ GAP-42-A

**REQ-PE-012** (E2E cross-surface explícito) — Cuando QA corre el escenario
cross-surface, el sistema demuestra: mismo usuario enrolado en 2 superficies
(desktop + CLI), admin aplica un bloqueo, y AMBAS superficies lo hacen
cumplir en su siguiente sync + turno, con la misma `policy_version`.
- AC-012-1: script de evidencia `evidence_cross_surface.py` (o extensión de los existentes) con el escenario completo en vivo contra el stack real, N FAIL = 0. ❌ GAP-42-A (AC de esa tarea)
- AC-012-2: el paso (e) del demo de Miller ejecuta este escenario con sus manos. → `docs/demo-miller.md` paso (e)

### Lo que no es cosmético

**REQ-PE-013** (ledger real) — Cuando el sistema reporta consumo, toda cifra
proviene de `spend_ledger` (una fila por request real, idempotente por
request_id). ✅ hoy — AC-013-1: `SELECT count(*) FROM spend_ledger` coincide
con los requests del período en Usage (paridad UI==API==SQL).

**REQ-PE-014** (enforcement real, no UI) — Cuando el empleado intenta algo
bloqueado, el bloqueo lo aplica el agente (veto del plugin con mensaje de la
org) o el gateway (403/401), no un filtro de UI; y la org gana sobre
cualquier config local o SOUL.md. ✅ hoy para tools/modelos/presupuesto
(REGLA DE ORO PASS 11/11); ❌ para mensajería/conectores → GAP-42-A.

**REQ-PE-015** (corte real) — Cuando el presupuesto del usuario/depto/org se
agota, el sistema bloquea la virtual key en el gateway (401 "Key is blocked"
en el siguiente turno) y lo audita (`budget.cutoff`); al restaurar, desbloquea
(idempotente). ✅ hoy (verificado en vivo, Pilar 3 3c).

### Demo

**REQ-PE-016** (demo reproducible) — Cuando Miller ejecuta
`docs/demo-miller.md` contra el stack real, cada paso (a)-(e) tiene una
verificación objetiva (URL, comando, salida esperada) y el demo no depende de
nada simulado; los pasos que hoy no se cumplen están marcados como gap con su
tarea, no ocultos.
- AC-016-1: el demo existe en `docs/demo-miller.md` con PREP (servidor) y DEMO (manos de Miller) separados. ✅ (esta spec)
- AC-016-2: cada paso del demo tiene su línea "✓ Verificación". ✅
- AC-016-3: el paso (e) (mismo usuario, 2 superficies, mismo bloqueo) pasa solo cuando GAP-42-A converja para mensajería; para tools/modelas pasa HOY (el demo lo ejecuta con bloqueo de tool, que sí cruza superficies, y documenta el caso mensajería como gap). ✅/❌ documentado

## 9. Demo reproducible

Ver **`docs/demo-miller.md`** — el entregable final de esta spec: script paso
a paso que Miller ejecuta con sus manos (instalar en SU laptop → usar → ver
consumo subir en SU admin → intentar lo bloqueado → verlo caer en vivo →
mismo usuario en CLI con el mismo bloqueo), contra el stack real de este
servidor, con túnel SSH como prereq de red (GAP-42-D) y cada paso verificable.

## 10. Cómo se verificó el estado REAL (reproducible, 2026-10-29)

```bash
cd /root/trust-agent && docker compose ps   # 9 servicios Up (api, db, idp,
#   console, provider-console, litellm-double, workers) — verificado este run
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/download/pi-e2e            # 200
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/download/pi-e2e/cli        # 200
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/download/pi-e2e/installer/windows  # 200
ls installer-artifacts/pi-e2e/0.6.0/   # exe + AppImage + manifest (sha256) reales
docker exec trust-agent-db psql -U postgres -d trust_agent \
  -c "SELECT count(*), max(occurred_at) FROM spend_ledger;"   # 20 filas, hoy 20:22 UTC
docker logs trust-agent-spend-worker 2>&1 | tail -2           # ciclo 2 s, cursor vivo

# Bundle por USUARIO (la pregunta de Miller):
grep -n "effective_policy" platform/app/governance/bundle.py        # :225 — por user_id
# Keys de mensajería compiladas pero SIN enforcement (GAP-42-A):
grep -rn "messaging_permissions" platform/hermes/plugins/trust-agent-governance/*.py
#   → solo policy_contract.py (conocida, default {}); ningún veto la lee
grep -rn "allowed_connectors" platform/hermes/plugins/trust-agent-governance/*.py
#   → solo display en governance_status (__init__.py:153)
```
