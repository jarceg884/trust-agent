# Spec 010 — Desktop: flujo de enrolamiento white-label

| Campo | Valor |
|---|---|
| **Estado** | **RETIRADA (t_b8769ecb, mandato Miller, 2026-10-26)** — el desktop-client demo (:9090) fue un double de desarrollo, nunca el producto. El flujo real es link de descarga → bundle de Hermes real → enrolamiento automático zero-UI (specs 022 y 023, `docs/desktop-flow.md`). Este documento se conserva como registro histórico; los contratos de API de dispositivos (spec 007) que ejercitó siguen vigentes. |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-23 |
| **Autor** | ui (tarea t_d3768316) |
| **Padre** | specs/001-platform/spec.md §4.7 (REQ-DEV) + US-DTK (usuario final), specs/007-devices/spec.md (contrato de enrolamiento ya entregado), specs/003-control-plane/spec.md (sesiones + device token + DeviceGate), docs/arquitectura.md §2.2/ADR-013 |
| **Idioma** | Español (docs) / inglés (código, UI copy) / USD centavos (montos) |

---

## 1. Alcance

Esta spec define la **experiencia del usuario final que descargó el desktop white-label**: login OIDC (authorization code + PKCE S256) contra el realm de su organización, solicitud de enrolamiento del dispositivo (keypair local, spec 007), espera de aprobación admin, recepción del device token, y **sesión gobernada**: dashboard donde el usuario ve su presupuesto, las políticas activas, los modelos aprobados y sus dispositivos — como el mockup Desktop (`/root/.hermes/images/upload_lcw3pa1_.png`).

El desktop **es Hermes Agent configurado, no un fork**: la integración con Hermes se hace exclusivamente vía mecanismos oficiales (profiles / profile distributions, plugins `plugin.yaml` + `register(ctx)`, config por perfil), según el skill `hermes-integration/hermes-saas-governance` (t02) y la lección C6 (cero parches al core).

**Dentro de alcance**:

- Documento `/root/trust-agent/docs/desktop-flow.md`: flujo completo login → enrolamiento → aprobación → recepción de políticas/perfiles/skills → sesión gobernada, con secuencia, endpoints y manejo de tokens.
- Cliente `/root/trust-agent/platform/desktop-client/` (Next.js App Router + TypeScript + Tailwind):
  - **Login**: pantalla de entrada del `org_code` → redirección al authorize del realm (`{idp}/realms/{org_code}`) con PKCE S256 (cliente público `trust-agent-web`, redirect `http://127.0.0.1:9090/auth/callback`); el canje code→tokens y el mint de sesión (`POST /api/auth/session`, `kind="desktop"`) ocurren **server-side** (el verifier nunca llega al navegador).
  - **Enrolamiento**: generación de keypair asimétrico local (WebCrypto, SPKI PEM), fingerprint estable, `POST /api/devices/enroll`, persistencia local del keystore + `enrollment_token`, polling de `POST /api/devices/enroll/exchange` (202 pending → 200 device token → 410 gone) con estados de UI explícitos.
  - **Dashboard del usuario** (estilo mockup, dark): presupuesto propio (usuario/departamento/organización + corte), políticas efectivas activas, modelos aprobados (`/api/models/allowed-for-me`), dispositivos propios (`/api/devices/me`), notificaciones de gobernanza, identidad (quién soy, org, departamento, SSO+MFA).
  - Integración **real** con la API del control plane (puerto 8000): estados de carga/error reales, envelope de errores §5.2, cero datos mock presentados como reales.
- Artefactos de integración Hermes (oficiales, sin parches): plantilla de perfil (`config.yaml` apuntando al plano de control LiteLLM con la virtual key del usuario) + plugin `trust-agent-governance` (`plugin.yaml` + `__init__.py` con `register(ctx)`: tool de estado de gobernanza + hook `pre_llm_call` que rechaza modelos no aprobados).
- `npm run build` limpio.

**Fuera de alcance**:

- UI del Admin Console (lane hermana, spec propia).
- El chat/agente en sí: esa es la sesión de Hermes Agent (proceso propio con el perfil del usuario); el dashboard muestra el estado gobernado y lanza esa sesión.
- Empaquetado instalable (instalador/electron): v1 se entrega como app local en `127.0.0.1:9090`; el keystore local v1 es storage del navegador (documentado; en el empaquetado sería el keychain del SO).
- Aprovisionamiento de tenants (spec 004) y endpoints de admin de dispositivos (spec 007, ya entregados).

## 2. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| D-DTB-01 | **El org_code resuelve el realm**: el desktop construye el issuer como `{idp_base}/realms/{org_code}` (realm = slug del tenant, D-ONB). El `org_code` NO es raíz de confianza: el tenant siempre sale del claim firmado `tenant_id` del `id_token` (D-CP-03, D-DEV-02). IdP base y URL de la API son configuración del cliente (env), no datos del usuario. | D-DEV-02, config.py `idp_base_urls` |
| D-DTB-02 | **PKCE S256 con canje server-side**: el `code_verifier` se genera y vive en una cookie httpOnly del servidor Next.js; el navegador solo ve el redirect al authorize y de vuelta el `code`. El canje code→id_token contra el IdP y el mint de sesión contra `POST /api/auth/session` los hace el route handler del cliente. Sin ROPC, sin contraseñas en el cliente (REQ-SEC-001). | constitution IV, e2e `_pkce_login` |
| D-DTB-03 | **Tokens del desktop en cookies httpOnly del servidor local**: `session_token` (Bearer hacia la API), `refresh_token`, `csrf_token` y el `id_token` (necesario para `POST /api/devices/enroll`, que exige JWT con MFA) viven en cookies httpOnly de `127.0.0.1:9090`; el navegador jamás los lee. Las llamadas del dashboard a la API se hacen server-side (RSC/route handlers) reenviando el Bearer. | ADR-006, spec 003 |
| D-DTB-04 | **Keystore del dispositivo en el cliente**: el keypair lo genera WebCrypto en el navegador (RSA-2048, SPKI PEM export); la clave privada (PKCS#8 base64) y el `device_token` se guardan en el almacenamiento local del navegador (v1; keychain del SO en el empaquetado). El `enrollment_token` one-time se guarda igualmente local y se canjea exactamente una vez. | ADR-013, D-DEV-03/04 |
| D-DTB-05 | **El polling del canje ES la espera de aprobación**: la pantalla de enrolamiento muestra "waiting for approval" y repite `POST /api/devices/enroll/exchange` (202 no consume) hasta 200 (device token) o 410 (rechazado/revocado/consumido — sin oráculo, mensaje único "enrollment no longer valid"). | D-DEV-04, REQ-DEV-007 |
| D-DTB-06 | **Sesión gobernada visible**: el dashboard muestra SOLO datos reales de la API (`/api/me`, `/api/me/budget`, `/api/me/policies`, `/api/models/allowed-for-me`, `/api/devices/me`, `/api/me/notifications`). Sin datos: estados vacíos reales ("No active budget set"), nunca cifras inventadas. Corte de presupuesto → banner de corte real (`cutoff: true`). | constitution I/V, REQ-GOV-012 |
| D-DTB-07 | **El desktop es Hermes configurado**: el agente corre como Hermes Agent con un **profile** por usuario (`~/.hermes/profiles/<user>/`), cuya `config.yaml` apunta el proveedor de modelos al proxy LiteLLM del tenant con la virtual key del usuario — **el gate real de modelos/presupuesto es LiteLLM** (D-GOV-15: la key solo acepta los modelos aprobados y aplica `max_budget`). Un **plugin** oficial `trust-agent-governance` (`plugin.yaml` + `register(ctx)`) añade: la tool `governance_status` (presupuesto/políticas/modelos desde la API), el hook `pre_llm_call` (inyecta el contexto de gobernanza vigente en cada turno — verificado en `agent/turn_context.py`: es un hook de contexto, no de veto) y el hook `pre_tool_call` (sí puede bloquear: `_dispatch_pre_tool_call_hooks` devuelve `block_message` — defensa en profundidad para políticas de tools). Cero parches a `hermes_cli/`. | skill t02, lección C6, constitution IV, D-GOV-15 |
| D-DTB-08 | **Branding white-label por tenant**: nombre/logo del producto y acento de color se toman del branding del tenant cuando la API lo exponga; v1 usa el branding por defecto del mockup ("Harper Agent", acento rojo) constante del cliente, documentado como pendiente de conectar al endpoint de branding. | CONTEXTO-TAREAS (white-label) |
| D-DTB-09 | **UI copy en inglés, estándar enterprise**: dark theme según mockup (`#0e1318` fondo, paneles `#1a1f25`, acento `#f91128`), tipografía sans del sistema, componentes accesibles (labels, focus visible, contraste), responsive. | constitution VI, mockup |

## 3. Requisitos (formato EARS)

Trazabilidad: spec 001 §4.7 (REQ-DEV) y US-DTK; spec 007 (contrato de enrolamiento); spec 003 (sesiones). Serie nueva REQ-DTB.

**REQ-DTB-001** (M) [constitution IV, D-DTB-01/02] — Cuando el usuario inicia sesión en el desktop con su `org_code`, el cliente lo redirige al endpoint authorize del realm de su organización con authorization code + PKCE S256 (cliente público `trust-agent-web`), completa el canje y el mint de sesión server-side, y guarda sesión/refresh/csrf/id_token en cookies httpOnly; jamás maneja contraseñas.
> **AC-DTB-001**: flujo real contra Keycloak: org code → redirect al authorize → login del usuario (con MFA) → callback con code → sesión mintada (`POST /api/auth/session` kind=desktop) → redirección al dashboard con identidad real (`/api/me`).

**REQ-DTB-002** (M) [spec 007 REQ-DEV-001, D-DTB-04] — Cuando el usuario solicita enrolar este dispositivo desde el desktop, el cliente genera un keypair local (WebCrypto), exporta la pública como SPKI PEM, compone un fingerprint estable, y llama a `POST /api/devices/enroll` con el `id_token` (MFA) y el `org_code`; guarda `device_id` + `enrollment_token` localmente.
> **AC-DTB-002**: enroll real → 202 con `device_id`/`enrollment_token`; el estado del dispositivo aparece `pending` en `/api/devices/me`; la clave privada permanece solo en el almacenamiento local del navegador.

**REQ-DTB-003** (M) [spec 007 REQ-DEV-007, D-DTB-05] — Cuando el dispositivo está pendiente de aprobación, el desktop muestra el estado de espera y repite el canje del `enrollment_token` hasta recibir el device token (200), o termina con el mensaje único de enrolamiento inválido (410) sin revelar la causa.
> **AC-DTB-003**: con el dispositivo `pending`, la UI muestra "waiting for approval" y el canje responde 202; tras la aprobación admin, el siguiente canje devuelve 200 con `device_token` (guardado local) y el dashboard marca el dispositivo `active`; tras un rechazo, el canje termina en 410 con mensaje único.

**REQ-DTB-004** (M) [spec 001 US-DTK, D-DTB-06] — Cuando el usuario abre su dashboard en sesión gobernada, el sistema le muestra su identidad (nombre, org, departamento, SSO+MFA), su presupuesto por nivel con restante y corte, las políticas efectivas activas, los modelos aprobados disponibles y sus dispositivos con estado — todo desde la API real, con estados de carga/error/vacío reales.
> **AC-DTB-004**: dashboard con sesión real del usuario: presupuesto = `GET /api/me/budget` (user/dept/org, remaining, cutoff banner si `cutoff`), políticas = `GET /api/me/policies`, modelos = `GET /api/models/allowed-for-me`, dispositivos = `GET /api/devices/me`; sin sesión → redirección a login; error de API → estado de error con `request_id`.

**REQ-DTB-005** (M) [skill t02, D-DTB-07, constitution IV] — Cuando la organización entrega la experiencia de agente al usuario, el desktop corresponde a Hermes Agent configurado vía mecanismos oficiales: profile por usuario con `config.yaml` hacia el plano de control LiteLLM (virtual key del usuario, modelos aprobados) y plugin `trust-agent-governance` (`plugin.yaml` + `register(ctx)`) que registra la tool `governance_status` y el hook `pre_llm_call` (rechazo de modelos no aprobados); sin parches al core.
> **AC-DTB-005**: artefactos entregados (`desktop-client/hermes/`): plantilla de perfil con config.yaml válida, plugin con `plugin.yaml` + `__init__.py::register(ctx)` que usa `ctx.register_tool`/`ctx.register_hook` (API verificada en `hermes_cli/plugins.py`), y documentación del cableado; `grep` sin parches a `hermes_cli`.

**REQ-DTB-006** (M) [constitution II/VI] — Cuando se construye el cliente, `npm run build` compila limpio sin errores ni warnings de tipos.
> **AC-DTB-006**: `npm run build` exit 0 en `/root/trust-agent/platform/desktop-client/`.

**REQ-DTB-007** (M) [constitution I] — Cuando la API no está disponible o responde con error, el desktop muestra estados de error reales (con `request_id` del envelope §5.2 cuando exista) y jamás presenta datos inventados como reales.
> **AC-DTB-007**: con la API caída, el dashboard muestra estado de error (no cifras falsas); con sesión expirada, redirige a login.

## 4. Contrato de API (consumo del desktop)

| Método + ruta | Uso en el desktop | Auth |
|---|---|---|
| `POST /api/auth/session` | mint de sesión tras PKCE (`kind="desktop"`) | id_token en body |
| `POST /api/auth/refresh` | rotación de sesión | refresh_token |
| `POST /api/auth/logout` | cierre de sesión | Bearer |
| `GET /api/me` | identidad del dashboard | Bearer + MFA |
| `GET /api/me/budget` | tarjeta de presupuesto + corte | Bearer + MFA |
| `GET /api/me/policies` | políticas efectivas activas | Bearer + MFA |
| `GET /api/me/notifications` | inbox de gobernanza | Bearer + MFA |
| `GET /api/models/allowed-for-me` | modelos aprobados | Bearer + MFA |
| `GET /api/devices/me` | mis dispositivos | Bearer + MFA |
| `POST /api/devices/enroll` | solicitud de enrolamiento | id_token (MFA) en body |
| `POST /api/devices/enroll/exchange` | polling/canje one-time | enrollment_token en body |
| `POST /api/devices/{id}/token` | renovación del device token | Bearer + MFA |

Errores: envelope §5.2 (`error.code/reason/message/request_id`); sin oráculos (410 uniforme en el canje).

## 5. Checklist de convergencia

- [x] `npm run build` limpio en `platform/desktop-client/` (AC-DTB-006). **Evidencia: `next build` exit 0 — "Compiled successfully", TypeScript OK, 8 rutas (/, /auth/callback, /dashboard, /enroll + 3 route handlers).**
- [x] Flujo real ejercitado contra el control plane + Keycloak reales (AC-DTB-001…004). **Evidencia (smoke en vivo, 2026-10-23, browser real):**
  - Tenant real aprovisionado (`demo-org`): realm KC 26 real, healthcheck 10/10 ok, budget $500, 4 políticas invariables, 2 modelos aprobados.
  - **AC-DTB-001 (login)**: browser → org code → redirect PKCE S256 al authorize del realm real → login con one-time password del aprovisionamiento → required actions reales (CONFIGURE_TOTP con QR decodificado + UPDATE_PASSWORD) → callback con code → sesión mintada (201) → dashboard. El baile PKCE+TOTP contra KC real también se ejercitó por HTTP directo (probe): code emitido y canjeado OK.
  - **Limitación encontrada (plataforma, no desktop)**: Keycloak 26 NO emite `amr` en id_token ni access_token (solo `acr:"1"` — probe real) → `_amr_mfa` del control plane rechaza todo login real de KC con 403 `mfa_required`. Tarea dev creada (ver metadata). Para el smoke, los claims MFA vinieron del doble JWKS declarado (mismo patrón que la lane QA en t_974c6a5c); el resto 100% real.
  - **AC-DTB-002 (enroll)**: WebCrypto generó keypair RSA-2048 en el navegador (privada local, SPKI PEM al server) → `POST /api/devices/enroll` 202 → dispositivo `pending` visible en `/api/devices/me`; keystore local con privada + enrollment_token.
  - **AC-DTB-003 (aprobación + canje)**: approve vía API (200 active) → el polling del desktop (5 s) canjeó el enrollment_token one-time → 200 con device token guardado local → UI "Device enrolled / active"; renovación `POST /api/devices/{id}/token` → 200 (expires_in 900).
  - **AC-DTB-004 (dashboard)**: datos 100% reales del control plane: Alex Morgan / demo-org / org_admin, presupuesto org $500.00 de $500.00 (user/dept "No budget set" — estados vacíos reales), 4 políticas "Provider floor", 2 modelos aprobados, dispositivo `active` con "last seen", notificaciones vacías reales. Screenshot: `scratch` del perfil ui (copiado a adjuntos de la tarea).
  - **Audit**: `device.enrolled` + `device.approved` con `device_id` no nulo en `audit_log` del tenant (verificado por DB), más `auth.login` de ambas sesiones.
- [x] Artefactos Hermes oficiales entregados y documentados (AC-DTB-005). **Evidencia: `desktop-client/hermes/` — plugin.yaml + `__init__.py::register(ctx)` (ctx.register_tool/register_hook, API verificada en hermes_cli/plugins.py), profile-template/config.yaml, README del cableado. Cero parches: ningún archivo de `hermes_cli/` tocado.**
- [x] `docs/desktop-flow.md` completo (flujo, secuencia, tokens, integración Hermes, hallazgo KC26/amr, cómo ejecutar).
- [x] Cero datos mock presentados como reales; estados de carga/error/vacío reales (AC-DTB-007). **Evidencia: dashboard muestra "No budget set"/"No governance notifications" cuando la API no tiene datos; error de API muestra request_id (verificado: 403 mfa_required con request_id req_02423368afd84a0ea70d).**
- [x] Trazabilidad REQ↔AC completa y tasks.md cerrado.
