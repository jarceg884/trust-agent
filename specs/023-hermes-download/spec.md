# Spec 023 — Download Hermes por tenant: bundle white-label + endpoint público de descarga

| Campo | Valor |
|---|---|
| **Estado** | ✅ CONVERGIDA (implementada y validada con evidencia real 2026-10-26 — docs/hermes-download.md; AC-HDL-001..010 con evidencia en vivo: navegador anónimo, bundle real, instalación, login OIDC real, auto-enroll zero-UI, veto exacto; suite 512 passed) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-26 |
| **Autor** | dev (tarea t_526030f3, seam detectado en la revisión maestra t_ef306f32) |
| **Padre** | specs/022-hermes-real-bridge (plugin trust-agent-governance 0.4.0 convergido); specs/019-governance-over-hermes §4 (patrón binding app→org); specs/007-devices (enroll/approve/exchange); specs/028-branding-versioning (branding publicado) |
| **Contratos externos** | Admin Console sección "Hermes Download" (t_360a752e, ya cableada al contrato §5.3); ui-standard.md (página pública) |
| **Idioma** | Español (docs) / inglés (código, UI copy) / USD centavos (montos) |

---

## 1. Problema (seam link→app)

La revisión maestra (t_ef306f32) detectó un seam sin dueño: el Admin Console del cliente
genera y envía el **link de descarga** por tenant (corrección Miller 2026-10-26: lo envía el
ADMIN DEL CLIENTE a sus empleados, no el proveedor), y la spec 022 construyó el plugin
`trust-agent-governance` que enrola al Hermes REAL en una organización — pero **nadie
construye ni sirve el artefacto descargable que hay en medio**. Regla "nada simulado"
(constitution I): el link debe servir algo real y completo.

Esta spec es dueña de:

1. **El endpoint público** `GET /download/{tenant_slug}` (sin autenticación — punto de
   entrada del flujo app→organización): página con branding del tenant, rate-limited,
   que sirve el bundle con el binding de la org.
2. **El bundle real** (dev-local): tarball + `install.sh` que instala un Hermes COMPLETO
   (no un cliente ligero) con el plugin trust-agent activado y el `org_code` del tenant
   preconfigurado, con white-label aplicado en el primer arranque.
3. **El E2E con evidencia**: admin del cliente envía el link → navegador anónimo descarga
   el bundle → instalación → primera ejecución reconoce el binding → enrola el dispositivo
   (API existente spec 007) → el agente queda gobernado por la policy de la org.

## 2. Alcance y delimitación

| Componente | Spec / tarea dueña | Estado |
|---|---|---|
| Plugin `trust-agent-governance` (enroll/enforcement/bundle_client/attestation) | spec 019/022 (t_c875626f, padre) | **convergido** — esta spec lo EMPAQUETA, no lo re-implementa |
| Auto-enroll zero-UI (lectura de `org-code.txt` / `TRUST_ORG_CODE`) | spec 022 `enroll.py::org_binding()` | **convergido** — esta spec hornea el archivo que ya espera |
| API de enrolamiento `POST /api/devices/enroll` + approve + exchange | spec 007 | **existente, sin cambios** |
| Branding versionado draft/publish + assets | spec 028 (t_d2b65bcf) | **existente** — esta spec lee lo PUBLICADO |
| UI Admin Console "Hermes Download" (link copiable, toggle, invite) | t_360a752e (ui) | **cableada al contrato §5.3** — el toggle se activa solo cuando el endpoint exista |
| Página pública `/download/{slug}` + bundle + gestión del link | **esta spec** | esta corrida |
| Instalador + helpers del bundle (install.sh, trust-login) | **esta spec** | esta corrida |
| Mudanza del botón "Send download link" del Provider Console | tarea aparte (comentario Miller 2026-10-26) | fuera de alcance |
| desktop-client :9090 (double de desarrollo) | — | **explícitamente fuera del flujo** (mandato Miller 2026-10-26 12:39) |

## 3. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| **D-023-01** | **Link estable por tenant en el origen del control plane**: `GET /download/{tenant_slug}` servido por la API (`:8000`). `tenant_slug` = `org_code` (= `tenants.slug`), exactamente como ya lo construye y evidencia el Admin Console (`{download_base}/download/{org_code}`, tenant real `hermes-dl`). `TRUST_DOWNLOAD_BASE_URL` sigue siendo overrideable para servir el bundle desde otro origen. | Contrato t_360a752e, docs/evidencia/hermes-download-admin-console/ |
| **D-023-02** | **Binding = org_code embebido** (link público + horneado en el bundle). org_code es identificador público, NO secreto; la seguridad del enrolamiento está en la aprobación admin (spec 007). Sin token one-time para el link. | Decisión previa del body (no re-decidida) |
| **D-023-03** | **La aprobación admin del device SE MANTIENE** como control de seguridad para dispositivos desconocidos (mandato Miller ofrece "o policy key de auto-aprobación" — se decide NO implementar auto-aprobación en esta spec: con org_code público, auto-aprobar convertiría el link en un enrolamiento sin control; queda documentada como extensión futura vía policy key `device_auto_approval`). El flujo del usuario invitado es ZERO-UI: sin paneles ni comandos — el auto-enroll de la spec 022 hace submit y poll automáticamente; el admin aprueba desde la console y el agente queda activo y gobernado sin intervención del empleado. | Mandato Miller 2026-10-26 12:39 (decisión pedida y documentada) |
| **D-023-04** | **El tarball NO embebe el binario de Hermes**: `install.sh` instala un Hermes COMPLETO vía el instalador oficial (`curl -fsSL https://hermes-agent.nousresearch.com/install.sh`) si no hay `hermes` en PATH; si existe, lo reutiliza verificando versión. El tarball lleva: plugin `trust-agent-governance` (copia exacta del árbol del padre), `install.sh`, `trust-login`, `README.md` y `manifest.json` (metadatos + checksums). Dev-local: el servidor ya tiene Hermes git-instalado (`/usr/local/lib/hermes-agent`). | Constitution VII (dev-local), "Hermes COMPLETO, no cliente ligero" |
| **D-023-05** | **White-label en el primer arranque vía skin oficial de Hermes**: `install.sh` genera `<home>/skins/<slug>.yaml` desde el branding PUBLICADO del tenant (`accent_color` → `ui_accent`/`banner_accent`, `product_name` → `branding.agent_name`, tagline → `welcome`) y fija `display.skin` en config. Es el mecanismo oficial de theming (un skin tema CLI+TUI+GUI a la vez, sin parches); es el análogo del mecanismo branding-desktop de la spec 013 (D-013-06) para las superficies de Hermes. El branding viaja horneado en el bundle (snapshot de lo publicado al momento de la descarga). | ui-standard.md, skill hermes-agent (themes), spec 013 |
| **D-023-06** | **Config del perfil instalado** (home fresco, primario en la máquina del empleado): `plugins.enabled: [trust-agent-governance]`, `platform_toolsets.cli: [hermes-cli, trust_agent]`, `gateway.standalone: true` (hallazgo crítico spec 022: un perfil servido por el gateway multiplex no dispara hooks; el instalador distribuido JAMÁS depende del multiplex), `model` → gateway LiteLLM del tenant (`base_url` horneado de `settings.litellm_base_url`, modelo `glm-5.2`), `api_key` provista por `trust-login --issue-key`. `.env` lleva `TRUST_AGENT_API` (origen del control plane, tomado del request de descarga) y `TRUST_ORG_CODE`. | Handoff t_c875626f (hallazgo crítico), spec 019 D-GOV-15 |
| **D-023-07** | **Rate limit público reutiliza `ops.login_throttle`** (familia org-config, D-DB-14: plano provider sin RLS, contadores en PostgreSQL que sobreviven restarts). Keys: `dl:page:{ip}` (ventana generosa) y `dl:bundle:{slug}:{ip}` (más estricta). Slug desconocido registra fallo en el contador de IP → lockout progresivo anti-enumeración, idéntico a `GET /api/auth/org-config`. | Constitution IV, auth/throttle.py, spec 015 D-AUTH-08 |
| **D-023-08** | **Estado del link en `tenants`**: columnas `download_enabled boolean NOT NULL DEFAULT true` y `download_updated_at timestamptz NOT NULL DEFAULT now()`. Link habilitado por defecto (el admin lo crea habilitado y lo deshabilita a propósito). Migración Alembic serializada vía `migration_guard.py` (ADR 0001) + paridad `db/schema.sql`. | ADR 0001, hotspot db/migrations |
| **D-023-09** | **Gestión del link**: `GET /api/download-link` → `{url, enabled, updated_at}` y `PUT /api/download-link {enabled}` con sesión MFA + permiso NUEVO `download_link.manage` (solo `org_admin` en la matriz RBAC; `dept_admin` NO lo recibe — coherente con el gate de rol de la UI). Audit `download.link.enabled` / `download.link.disabled`. Contrato EXACTO acordado con la UI (types.ts `DownloadLinkStatus`). | Comentario ui 2026-10-26 11:13, constitution V |
| **D-023-10** | **Audit `download.issued` por descarga del bundle** (no por vista de página — ruido): `actor_role="system"`, payload `{slug, ip, user_agent, bundle_sha256}`. Nuevas acciones en `catalog.audit_actions`. | Body de la tarea, spec 008 |
| **D-023-11** | **Página pública = HTML autocontenido** servido por el control plane (el origen del link no tiene runtime Node). Aplica el estándar ui-standard.md en su lenguaje visual: iconografía **lucide** (SVGs oficiales inline, `stroke-width 1.75` — no SVG casero), entrada escalonada `ta-rise`, transiciones en todos los estados, `focus-visible`, `prefers-reduced-motion`, skeleton-free (server-rendered), branding publicado inline (logo como data URI desde `branding_assets`). UI copy en inglés. | Mandato Miller t_7cd4f40e, comentarios ui 2026-10-26 |
| **D-023-12** | **Estados honestos de la página**: slug desconocido → 404 estándar (anti-enumeración); tenant suspendido → página de rehusado (fail-closed, igual que enroll); link deshabilitado → página 200 con estado "downloads disabled" (branding visible, sin botón) y `GET /download/{slug}/bundle` → 403 `link_disabled`. | Constitution I/IV |
| **D-023-13** | **`trust-login`**: helper del bundle que hace el flujo OIDC **authorization-code + PKCE** (S256) contra el realm del tenant abriendo el navegador, con callback en `http://127.0.0.1:8765/callback` (servidor local efímero). Escribe `$HERMES_HOME/trust-agent/id-token.txt` (0600) — el archivo que el auto-enroll de la spec 022 ya consume. `--issue-key`: canjea el id_token por sesión (`POST /api/auth/session`), auto-emite la virtual key del usuario (`POST /api/models/keys`, self, one-time) y la guarda con `hermes config set model.api_key`. Jamás maneja passwords ni códigos MFA (los teclea el usuario en el IdP). | Constitution IV (PKCE, no ROPC), spec 022 D-HRB-01, spec 005 REQ-MDL-002 |

## 4. Requisitos (formato EARS) — serie REQ-HDL

- **REQ-HDL-001** (página pública): Cuando un navegador anónimo solicite `GET /download/{tenant_slug}` con un slug existente y tenant activo y link habilitado, el sistema responderá 200 con una página HTML pública sin autenticación que muestra el branding publicado del tenant (product_name, tagline, accent_color, logo) y un botón de descarga del bundle.
- **REQ-HDL-002** (bundle real): Cuando se solicite `GET /download/{tenant_slug}/bundle` con el link habilitado, el sistema responderá 200 con un tarball gzip real que contiene el plugin `trust-agent-governance` (copia del árbol convergido de la spec 022), `install.sh` ejecutable, `trust-login`, `README.md` y `manifest.json` con el `org_code` horneado; el tarball NO contiene secretos.
- **REQ-HDL-003** (rate limit): Cuando un mismo IP exceda el límite de descargas de bundle en la ventana configurada, el sistema responderá 429 `rate_limited` con `Retry-After`; los contadores viven en PostgreSQL.
- **REQ-HDL-004** (audit de emisión): Cuando se complete una descarga del bundle, el sistema emitirá un evento de audit `download.issued` en el tenant correspondiente con payload `{slug, ip, user_agent, bundle_sha256}`.
- **REQ-HDL-005** (gestión del link): Cuando un principal con sesión MFA y permiso `download_link.manage` solicite `GET /api/download-link`, el sistema responderá `{url, enabled, updated_at}`; cuando envíe `PUT /api/download-link {enabled}`, el sistema actualizará el estado, lo auditará (`download.link.enabled`/`download.link.disabled`) y el endpoint público respetará el nuevo estado en la siguiente solicitud.
- **REQ-HDL-006** (anti-enumeración): Cuando se solicite `/download/{slug}` con slug desconocido, el sistema responderá 404 y registrará un fallo en el contador de IP (lockout progresivo tras N sondeos).
- **REQ-HDL-007** (tenant suspendido): Cuando el tenant esté suspendido, el endpoint público rehusará la descarga (fail-closed) sin filtrar branding.
- **REQ-HDL-008** (instalación real): Cuando el empleado ejecute `install.sh` del bundle, el resultado será un home de Hermes COMPLETO con: Hermes instalado (oficial o existente), plugin `trust-agent-governance` activo (`plugins.enabled` + `platform_toolsets`), `gateway.standalone: true`, binding `$HERMES_HOME/trust-agent/org-code.txt`, skin white-label `<slug>.yaml` activo (`display.skin`), y `.env` con `TRUST_AGENT_API`/`TRUST_ORG_CODE`.
- **REQ-HDL-009** (primera ejecución gobernada): Cuando el empleado abra la app por primera vez tras `trust-login`, el agente reconocerá el binding, se enrolará automáticamente (zero-UI, submit + poll), y tras la aprobación admin quedará con device token + governance bundle firmado y gobernado por la policy de la org (veto `pre_tool_call` por `allowed_tools`).
- **REQ-HDL-010** (aislamiento): El endpoint público solo expone datos del tenant identificado por el slug; jamás datos de otro tenant (verificable con tests).
- **REQ-HDL-011** (cero parches): La instalación no modifica ningún archivo del core de Hermes; todo por plugin oficial + config + skin (constitution IV / lección C6).

## 5. Contratos

### 5.1 Endpoint público (nuevo)

```
GET  /download/{tenant_slug}          → 200 HTML público (branding) | 404 | página disabled
GET  /download/{slug}/bundle          → 200 application/gzip (tarball) | 403 link_disabled | 429 | 404
```

El tarball: `trust-agent-{slug}-{plugin_version}.tar.gz`,
`Content-Disposition: attachment; filename="..."`, `X-Bundle-SHA256` header.

### 5.2 Gestión (nuevo — contrato EXACTO con la UI existente)

```
GET  /api/download-link   (sesión MFA + download_link.manage)
     → { "url": string, "enabled": boolean, "updated_at": string }
PUT  /api/download-link   { "enabled": boolean }   (auditado)
     → { "url": string, "enabled": boolean, "updated_at": string }
```

`url` se construye desde el origen del request (`request.base_url`): es el link que el
admin está usando. La UI del Admin Console activa su toggle automáticamente cuando estos
endpoints existan (cero cambios de UI).

### 5.3 Bundle (contenido del tarball)

```
trust-agent-{slug}/
  install.sh            # ejecutable; instala Hermes + plugin + binding + skin + config
  trust-login           # ejecutable; OIDC code+PKCE → id-token.txt; --issue-key
  README.md             # instrucciones del empleado (inglés)
  manifest.json         # {slug, org_code, api_base, litellm_base_url, model, branding, plugin_version, bundle_sha256}
  plugin/trust-agent-governance/   # copia exacta del árbol de spec 022 (sin __pycache__)
```

### 5.4 Permisos y audit (nuevo en catálogos)

- `catalog.permissions`: `download_link.manage` — "Enable/disable the public Hermes download link."
- `catalog.role_permissions`: `org_admin` → `download_link.manage` (SOLO org_admin).
- `catalog.audit_actions`: `download.issued`, `download.link.enabled`, `download.link.disabled`.

## 6. Criterios de aceptación (AC-HDL) — verificación E2E en docs/hermes-download.md

| AC | Criterio | Evidencia |
|---|---|---|
| **AC-HDL-001** | Navegador anónimo abre `GET /download/{slug}` → página 200 con branding real del tenant (nombre, color de acento en el HTML) | transcript HTTP + screenshot |
| **AC-HDL-002** | La descarga entrega un tarball real; `tar -tzf` lista plugin/install.sh/trust-login/manifest; `manifest.json` lleva el org_code del tenant | salida de tar + jq |
| **AC-HDL-003** | `install.sh` en un home fresco produce: plugin activo, `gateway.standalone: true`, `org-code.txt` con el slug, skin del tenant activo (`hermes config get display.skin`), `.env` con TRUST_AGENT_API/TRUST_ORG_CODE | salidas de instalación + config get |
| **AC-HDL-004** | Audit `download.issued` en la DB del tenant por cada descarga (payload con ip/sha256) | psql |
| **AC-HDL-005** | `PUT /api/download-link {enabled:false}` (admin) → audit `download.link.disabled` → el bundle responde 403 y la página muestra estado disabled; re-habilitar restaura la descarga | curl + psql |
| **AC-HDL-006** | Rate limit: ráfaga de descargas desde la misma IP → 429 con Retry-After | transcript |
| **AC-HDL-007** | Slug desconocido → 404; sondeos repetidos → 429 anti-enumeración | transcript |
| **AC-HDL-008** | Primera ejecución con binding + id_token: auto-enroll zero-UI (evento `auto_enrolled` en plugin.log) → device pending → admin approve → device token 0600 → governance bundle verificado | plugin.log + psql + plugin.log |
| **AC-HDL-009** | El agente instalado queda gobernado: tool bloqueada por policy (`allowed_tools`) con el mensaje exacto de veto; `governance_status` NORMAL con policy del tenant | transcript de sesión real |
| **AC-HDL-010** | Tests de aislamiento: la página/bundle de un tenant no exponen datos de otro; suite de tests del control plane en verde | pytest |

## 7. Trazabilidad

| Requisito | Implementación | Test / evidencia |
|---|---|---|
| REQ-HDL-001/006/007/012 | `app/download/router.py` (página) + `page.py` | test_hermes_download.py + E2E |
| REQ-HDL-002/004 | `app/download/bundle.py` + router | test_hermes_download.py + E2E |
| REQ-HDL-003 | `app/auth/throttle.py::register_hit` (extensión genérica) | test_hermes_download.py |
| REQ-HDL-005 | `app/download/router.py` (admin) + migración catálogos | test_hermes_download.py |
| REQ-HDL-008 | `install.sh` generado en `bundle.py` | E2E AC-HDL-003 |
| REQ-HDL-009 | plugin spec 022 (padre) + `trust-login` | E2E AC-HDL-008/009 |
| REQ-HDL-010/011 | diseño (sin datos cross-tenant; cero parches) | tests + git status del core |

## 8. Fuera de alcance

- Auto-aprobación de devices por policy key (documentada como extensión futura, D-023-03).
- Botón "Send download link" en el Provider Console (mudado a tarea aparte por Miller).
- desktop-client :9090 y su flujo BFF (double de desarrollo).
- Distribución multi-plataforma firmada (notarización, instaladores .msi/.dmg) — dev-local.
- Re-emisión de branding en caliente para bundles ya instalados (el branding viaja horneado; el desktop Next lo lee en runtime por spec 013).
