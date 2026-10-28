---
name: hermes-saas-governance
description: "Use when integrating Hermes Agent into a multi-tenant SaaS governance platform (trust-agent). Mecanismos oficiales de integración verificados (plugins, profiles, profile distributions, gateway) y reglas de gobernanza derivadas de la auditoría AIDLC."
version: 1.0.0
category: hermes-integration
---

# Integración Hermes Agent en una plataforma SaaS de gobernanza (trust-agent)

Hallazgos verificados contra docs oficiales (hermes-agent.nousresearch.com/docs) y la auditoría AIDLC (`/root/trust-agent/auditoria/AUDITORIA-AIDLC.md`). Úsalo antes de diseñar o implementar cualquier punto de integración con Hermes en la plataforma.

## 1. Mecanismos oficiales de integración (verificados 2026-10-23)

| Necesidad | Mecanismo oficial | Verificado en |
|---|---|---|
| Herramientas/hooks/comandos propios | Plugin: directorio en `~/.hermes/plugins/<name>/` con `plugin.yaml` + `register(ctx)`; `ctx.register_tool`, `ctx.register_hook`, `ctx.register_command`, `ctx.dispatch_tool` | docs `/user-guide/features/plugins` |
| Plataformas de mensajería custom | `ctx.register_platform(name, label, adapter_factory, check_fn, ...)` (gateway ya trae 19 nativas + IRC/Teams vía plugins: Telegram, Discord, Slack, WhatsApp, SMS, Matrix…) | ídem |
| Skills empaquetadas | `ctx.register_skill(name, path)` — namespaced `plugin:skill` | ídem |
| Distribución de plugins | pip entry-points `[project.entry-points."hermes_agent.plugins"]`, `hermes plugins install/enable`, catálogo con install-time security scanning | ídem |
| Aislamiento por usuario/agente | **Profiles**: cada perfil es un home independiente (config.yaml, .env, SOUL.md, memories, sessions, skills, cron, state.db). NUNCA dos procesos sobre el mismo home | docs `/user-guide/profiles` |
| Plantillas de agente versionadas | **Profile distributions**: publicar el perfil como repo git → `hermes profile install <repo> --alias` / `hermes profile update` (actualiza SOUL/config/skills/cron; credenciales, memorias y sesiones quedan por máquina). **Este es el mecanismo para los perfiles pre-diseñados por departamento** (REQ-PRF de specs/001-platform) | ídem |
| Plugins del gateway | El gateway carga plugins de usuario una vez por proceso; NO carga `./.hermes/plugins/` project-local en sesiones | ídem |
| Sección fija en el system prompt | `ctx.register_system_prompt_section(id, content, *, position="after_memory", max_chars≤4000)` — cache-safe: se renderiza UNA vez por sesión nueva y se congela en el prompt persistido; única posición válida hoy: `after_memory` | `hermes_cli/plugins.py::register_system_prompt_section` + `plugins_dispatch.py` (verificado 2026-10-26, spec 019) |
| Veto de tools | Hook `pre_tool_call` retornando `{"action": "block", "message": ...}` (el mensaje se convierte en el resultado de la tool); también `{"action": "approve", ...}` (escala al gate humano, fail-closed por diseño del core) y `{"action": "modify", ...}`. Precedencia block > approve | `hermes_cli/plugins.py::_get_pre_tool_call_directive_details` (verificado 2026-10-26) |
| Contexto por turno | Hook `pre_llm_call` retornando `{"context": ...}` o string — se inyecta en el mensaje del usuario en CADA turno; NO veto | `agent/turn_context.py` + docs hooks |
| Identidad/persona | `SOUL.md` = slot #1 del system prompt, cargado SOLO de `$HERMES_HOME`, inyectado verbatim tras scan anti-inyección; editable por el usuario — **es voz, no permisos: la gobernanza nunca debe depender de él** | docs `/user-guide/features/personality` + `/developer-guide/prompt-assembly` |

## 2. Reglas duras (lecciones C1–C6 de la auditoría AIDLC)

1. **Cero parches al core de Hermes** (C6): prohibido sobrescribir archivos de `hermes_cli/` o plugins core. Todo va por `plugin.yaml` + `register(ctx)` o config por perfil. El delta útil del prototipo (campo TOTP en login) debe ir upstream, no parcheado.
2. **Nunca `verify=False`** (C6): toda conexión TLS verifica certificados.
3. **Secretos one-time** (C1/C4): jamás en cuerpos de respuesta persistentes ni logs; cifrado en reposo (envelope: DEK por registro, KEK fuera de la DB).
4. **Sesiones/jobs en PostgreSQL** (C2), nunca en dicts en memoria.
5. **JWT verificado con JWKS** (firma/exp/aud/iss), no base64-decode.
6. **Auth code + PKCE** para usuarios finales; ROPC solo bootstrap interno (deprecado en OAuth 2.1, bypasea MFA resistente a phishing).
7. **Multi-tenant**: `tenant_id` en toda tabla/query + RLS de PostgreSQL; el prototipo single-tenant (un realm KC, JSONs planos) es la anti-referencia (C3).
8. **Cada servicio = proceso propio con healthcheck**; nada de nohup dentro de otro proceso.
9. **Catálogos = datos por tenant** (modelos, conectores, skills), no constantes de código.
10. **LiteLLM como plano de control de modelos**: virtual key por usuario (modelos permitidos + `max_budget`/`budget_duration`), pricing sync runtime, spend agregado a un ledger propio para presupuestos jerárquicos org→depto→usuario con corte (LiteLLM solo presupuesta por key).

## 3. Pitfalls conocidos

- Semántica de hooks verificada en el código (2026-10-23, spec 010): `pre_llm_call` es un hook de CONTEXTO (su retorno `{"context": ...}` se inyecta al mensaje del usuario; `agent/turn_context.py::_collect_pre_llm_call_context`) — NO puede vetar una llamada. El veto de tools es `pre_tool_call` (`_dispatch_pre_tool_call_hooks` consume `block_message`). Para vetar MODELOS no hay hook: el gate correcto es LiteLLM con la virtual key del usuario (D-GOV-15), nunca un parche.
- `register_tool` exige `toolset` (namespace libre, p. ej. `"trust_agent"`); el idiom del manifiesto es `plugin.yaml` (name/version/description/provides_hooks) + `__init__.py::register(ctx)`.
- **El toolset de un plugin es opt-in por plataforma** (verificado en vivo 2026-10-26, spec 019 Inc. 1): `plugins.enabled` NO pone la tool en la superficie del agente; hace falta `platform_toolsets.<plataforma>: [hermes-cli, <toolset_plugin>]` en config.yaml (`hermes_cli/tools_config.py::_enabled_plugin_toolsets`: first-class solo en lista explícita guardada).
- **register() debe ser rápido** (verificado en vivo): el ensamblado de la sesión (catálogo de tools + system prompt) corre en paralelo a la carga de plugins de usuario y NO espera un register lento — un sync de red o un import pesado (PyJWT/cryptography) ANTES de `register_tool` deja la tool fuera del catálogo de esa sesión y la `register_system_prompt_section` fuera del prompt persistido. Patrón: registrar tool/hooks/sección PRIMERO (puro), I/O local después, red en hilo daemon.
- **En el flujo `hermes -z`/gateway, el prompt y el catálogo se ensamblan ANTES de registrar los plugins de usuario** (bundled plugins sí llegan a tiempo): la tool del plugin no es llamable en la primera sesión de un proceso fresco, aunque los hooks (`pre_llm_call`/`pre_tool_call`) sí se disparan por turno. El system prompt queda cacheado por hash de contenido (tabla `system_prompts` en state.db) y se reusa idéntico entre sesiones.
- **El gateway del host no rutea `session.create` (`invalid_params`) para perfiles creados después de su arranque** — ejercer el flujo `chat` de un perfil nuevo exige reiniciar el gateway compartido (coordinar con las lanes que dependen de él) o usar el flujo `-z`.
- **⚠️ MULTIPLEX vs STANDALONE (hallazgo crítico spec 022, verificado en vivo 2026-10-26)**: en el modelo *un gateway por host que sirve todos los perfiles* (`hermes_cli/gateway_profile_lifecycle.py`), el turno del chat local de un perfil SERVIDO ejecuta contra el **plugin manager del scope default del gateway** (su propio home) — los plugins del home del perfil cargan (syncers, comandos del TUI) pero **sus hooks NO se disparan ni sus tools entran al catálogo del agente**. Además el config del perfil se congela al momento de servirlo (`bind_worker_policy`) y un plugin cargado a mitad de corrida deja tools "deferred to the next session" (`gateway/run_plugin_rewire.py`). **Solución oficial, cero parches**: `gateway.standalone: true` en el config.yaml del perfil (`hermes_cli/profiles.py::profile_is_standalone`) → el perfil corre su PROPIO gateway (socket en `$HERMES_HOME/gateway.sock`) y el plugin se descubre en el arranque, antes de ensamblar sesión. Es el modelo por defecto en la máquina de un usuario final (su perfil es el primario); en servidores multi-perfil hay que fijarlo explícito. Implicación spec 023: el instalador del desktop distribuido jamás debe depender de perfiles servidos por un gateway multiplex.
- **Los handlers de tools de plugin reciben UN argumento posicional** (el dict de args): `def handler(**_)` → `TypeError: takes 0 positional arguments but 1 was given` EN CADA LLAMADA (hallado en vivo con `governance_status`, spec 022). Firma correcta: `def handler(args: dict | None = None, **_)`.
- **Log de plugins multi-proceso: siempre `pid`** — en un host puede haber instancias del mismo plugin en el gateway host, el gateway standalone del perfil, el TUI y los managed workers; sin `pid` en cada evento de log es imposible atribuirlos (spec 022 añadió `pid` + evento `veto_eval` a `plugin.log`).
- `register_system_prompt_section` acepta `content` string O **callable** (`fn(session_info_mapping) -> str`), renderizado al ensamblar el prompt; solo `position="after_memory"`, `max_chars` ≤ 4000 (`hermes_cli/plugins.py::register_system_prompt_section`).
- Keycloak 26: flujo VERIFY_PROFILE roto (documentado en la auditoría); el enrolamiento MFA por scraping de HTML del prototipo (C5) es frágil — usar Admin API oficial.
- Mensajerías: un bot/token pertenece a UN solo perfil; dos gateways con el mismo token pelean el long-poll. Al clonar perfiles, las channels NO se clonan por defecto.
- mem0 u otros servicios que usen LITELLM_MASTER_KEY: su gasto evade virtual keys y presupuestos — darles su propia key con budget.
- Imágenes no fijadas (`:latest`) + `pip install` en arranque: no reproducible; fijar versiones.
- **Enforcement de policies: `or []`/truthiness colapsa un deny-all explícito** (lección t_f0d3122d, QA P4e): en gates que intersectan listas de allow (`policy.get("allowed_models") or []` + `if lista:`), una lista VACÍA significa "deny-all" (el valor más restrictivo que el upsert acepta), pero el fallback la convierte en "sin restricción". Regla: distinguir con `isinstance(valor, list)` (clave DEFINIDA → intersecta, aunque sea `[]`) de clave ausente (`None`/null → heredar). Ídem aprobaciones por nivel: un nivel con filas de CUALQUIER status (todas denegadas) participa en la intersección con conjunto vacío — una revocación jamás relaja. El default de catálogo de una clave lista gobernable debe ser `null`, nunca `[]`, o el combinator no puede distinguir "sin policy" de deny-all.
- **Auto-enroll zero-UI es multi-proceso por diseño** (hallazgo spec 023, fix t_dd2f5f65): con `gateway.standalone:true`, `hermes chat` arrastra DOS procesos con el plugin (gateway standalone + TUI) y el loop de auto-enroll corre en ambos sobre el mismo home. Cada `POST /api/devices/enroll` resetea el `enrollment_token_hash` del device pending → dos enroll concurrentes se pisan el token one-time (410s mutuos). Lección general: un watcher en plugin que persiste credenciales one-time en `$HERMES_HOME` DEBE (a) serializarse entre procesos con flock sobre el home (`enroll.py::_HomeLock` en `.autoenroll.lock`) y (b) reutilizar el token persistido antes de re-POSTear (reuse-first: mientras el device está pending, re-POSTear solo resetea el hash que otro proceso poll-ea). Regla: el recurso compartido implícito es el HOME, no solo los archivos — dos procesos del mismo plugin comparten estado aunque el código no lo sepa. Además: jamás arrancar un gateway explícito junto al chat standalone del mismo home — un gateway viejo "adueñado" del home bloquea el arranque del chat nuevo (`hermes gateway stop` primero).
- **TUI Hermes + tmux send-keys**: el primer mensaje tras arrancar el TUI no se envía si texto y Enter van en el MISMO `send-keys` (queda en el buffer del prompt) — enviar texto, esperar ~2 s, y ENTONCES `send-keys Enter`. El reply del modelo al resultado de una tool vetada se renderiza DESPUÉS del veto: para evidencia, pollar la transcripción canónica (`state.db` tabla `messages`) en vez del pane.
- **Gateway huérfano sobre un home e2e re-instalado** (hallazgo t_b8769ecb, 2026-10-26): si una corrida previa dejó `hermes chat`/gateway vivos sobre un home que `--phase install` borra y recrea, el gateway viejo "adueñado" del home hace fallar el arranque del chat nuevo (`Another gateway instance is already running` + `StateDbReplacedError`) y la fase governed queda sin veto. Antes de re-ejecutar el harness: matar los PIDs viejos de ese home (vía `/proc/<pid>/environ`, NUNCA `pkill -f HERMES_HOME=...` — el patrón matchea tu propio shell y te mata la sesión). El TOTP del realm se computa con la clave HMAC = bytes UTF-8 crudos del secret (NO base32-decode; verificado vs KC26, `e2e_final_live.py::totp`).
- **Realm LoA condicional (spec 015 D-AUTH-06)**: sin `acr_values=2` en el authorize, el realm emite `acr=1` SIN MFA aunque el usuario tenga TOTP enrolado. Todo flujo de login programático (bailes PKCE, helpers CLI) DEBE pedir `acr_values=2` explícitamente.

## 4. Gobernanza sobre el agente (spec 019, feedback Miller #2)

El patrón decidido para que las policies de la org queden POR ENCIMA del SOUL.md/config locales (spec `/root/trust-agent/specs/019-governance-over-hermes/spec.md`, ADR-016/017/018):

1. **Binding app→org** (no gateway→gateway): link de descarga por org (`/d/{org_code}`) → instalador hornea issuer/control plane/branding; el tenant de confianza es el claim firmado `tenant_id`, jamás el binding local.
2. **Governance bundle firmado**: policy efectiva compilada server-side viaja como JWS (RS256/ES256, `aud=device_id`, `jti` anti-replay, `exp` = gracia offline); el plugin verifica con clave pública pineada. HS256 prohibido.
3. **Enforcement local** (plugin `trust-agent-governance` v2): `pre_tool_call` veto por `allowed_tools`; `pre_llm_call` contexto de gobernanza + cláusula de precedencia por turno; `register_system_prompt_section` con identidad gobernada; tool `governance_status`; `on_session_start` → attestation al control plane.
4. **Gates duros remotos**: LiteLLM (modelos/presupuesto via virtual key), vault server-side (credenciales de conectores nunca locales), device token, audit.
5. **Fail-closed**: offline > `offline_grace_minutes` o device revocado → modo restringido (todas las tools externas bloqueadas). Jamás fail-open.
6. **Threat model honesto**: el home del perfil es editable por el usuario del SO; las garantías duras son remotas y la detección es por attestation. No prometer bypass imposible.

## 5. Referencia cruzada

- Spec maestra con requisitos EARS: `/root/trust-agent/specs/001-platform/spec.md` (REQ-PRF, REQ-SEC-008, REQ-DTK-008 son los que citan estos mecanismos).
- Docs Hermes: `/docs/user-guide/features/plugins`, `/docs/user-guide/profiles`, índice completo `/docs/llms.txt`.
