# Matriz de capacidades de Hermes Agent × mecanismos de limitación × policy keys de Trust Agent

> **Mandato Miller**: las policies de Trust Agent deben cubrir la superficie COMPLETA de capacidades de Hermes Agent. Este inventario es la base del pilar 3 (app ligada a gobernanza) y el criterio de la revisión maestra (t_ef306f32) y del gate final (t_e8a28fce).
>
> **Fecha**: 2026-10-26 · **Autor**: product-owner (t_d8ed9362) · **Método de regeneración**: skill `skills/hermes/capability-surface/`
>
> **Fuentes** (verificadas, no inventadas):
> - **[DOC]** Documentación oficial completa: `https://hermes-agent.nousresearch.com/docs/llms-full.txt` (5.97 MB, 246 páginas; índice curado en `/docs/assets/files/llms-d8572be0e192ab98a8bd158467f1e822.txt`). Citas por página: `[DOC: <ruta de página>]`.
> - **[SRC]** Código fuente real: `/usr/local/lib/hermes-agent/` — `toolsets.py`, `tools/connectors/`, `plugins/platforms/`, `agent/secret_scope.py`, `cli-config.yaml.example`. Citas por archivo.
> - **[TA]** Trust Agent: `db/schema.sql` (catálogo `catalog.policy_keys`), `platform/app/governance/policies.py`, specs 001/005/019/024.

---

## 0. Resumen ejecutivo

- **Capacidades inventariadas**: 62 (C-001..C-062), agrupadas en 10 dominios.
- **Policy keys existentes** en `catalog.policy_keys` (db/schema.sql L140-177): 18 — `require_mfa`, `allow_self_registration`, `approved_models_only`, `explicit_shared_agent_grants`, `scoped_credentials`, `allowed_models`, `allowed_connectors`, `shared_agent_grants_mode`, `vault_allowed_credential_types`, `session_timeout_minutes`, `artifact_sharing`, `messaging_permissions`, `max_daily_sessions`, `allowed_tools`, `offline_grace_minutes`, `unbind_mode`, `attestation_overdue_hours`, `allowed_connections`.
- **Gaps detectados**: 29 policy keys NUEVAS requeridas (§3) + 3 gaps de enforcement sobre keys existentes (§4). Deny-by-default: toda capacidad sin policy key es un gap.
- **Tareas dev creadas**: 4 (catálogo tipado, enforcement batch 1, enforcement batch 2, bundle JWS extensible) — ver §5.

---

## 1. Mecanismos nativos de limitación de Hermes (inventario previo)

Antes de la matriz: qué mecanismos ofrece Hermes para limitar una capacidad. Todo agente de gobernanza se construye sobre estos cimientos.

| # | Mecanismo | Qué hace | Fuente |
|---|-----------|----------|--------|
| M1 | **Toolsets** (`toolsets:`, `platform_toolsets:`, `agent.disabled_toolsets`, `custom_toolsets:`, `hermes tools`) | Activa/desactiva grupos de tools por plataforma y por sesión; sustracción estricta al final del pipeline | [SRC: toolsets.py L74-244, L458-464] · [DOC: reference/toolsets-reference.md] |
| M2 | **Aprobación de comandos peligrosos** (`approvals.mode: smart\|manual\|off`, `approvals.timeout`, `cron_mode`, `single_query_mode`, `unattended_mode`) | Human-in-the-loop para comandos destructivos; modos por contexto (cron/-q/webhook) | [DOC: user-guide/security.md §Dangerous Command Approval] |
| M3 | **Blocklist hardline** (`UNRECOVERABLE_BLOCKLIST`) | Piso no sobrepasable: `rm -rf /`, fork bombs, mkfs, dd a disco — ni YOLO ni `mode: off` lo saltan | [DOC: security.md §Hardline Blocklist] · [SRC: tools/approval.py] |
| M4 | **Deny rules de usuario** (`approvals.deny`, globs fnmatch) | Bloqueo incondicional de patrones de comandos ANTES de YOLO/mode:off | [DOC: security.md §User-Defined Deny Rules] |
| M5 | **Allowlist permanente** (`command_allowlist`) | Patrones aprobados "always" que se auto-aprueban | [DOC: security.md §Permanent Allowlist] |
| M6 | **YOLO** (`--yolo`, `/yolo`, `HERMES_YOLO_MODE=1`, `approvals.mode: off`) | BYPASS total de aprobaciones por sesión/perfil — el riesgo que una policy debe poder vetar | [DOC: security.md §YOLO Mode] |
| M7 | **File write safety** (denylist de rutas protegidas + `HERMES_WRITE_SAFE_ROOT`) | `write_file`/`patch` bloqueados fuera del sandbox y en rutas de credenciales; sin prompt de aprobación | [DOC: security.md §File Write Safety] |
| M8 | **Container isolation** (`terminal.backend: docker\|modal\|daytona\|vercel_sandbox`, flags `--cap-drop ALL`, `no-new-privileges`, `pids-limit`, tmpfs noexec; recursos `container_cpu/memory/disk`) | El contenedor ES la frontera: los checks de comando peligroso se omiten dentro | [DOC: security.md §Container Isolation] · [DOC: features/tools.md §Terminal Backends] |
| M9 | **Autorización de gateway** (allowlists por plataforma `TELEGRAM_ALLOWED_USERS` etc., `GATEWAY_ALLOWED_USERS`, `GATEWAY_ALLOW_ALL_USERS`, DM pairing, `unauthorized_dm_behavior`) | Quién puede hablarle al bot; default deny | [DOC: security.md §User Authorization (Gateway)] |
| M10 | **Filtrado de entorno** (`_sanitize_subprocess_env`; MCP env allowlist `PATH,HOME,USER,...`; `terminal.env_passthrough`, `docker_forward_env`, `required_environment_variables` de skills) | Secretos fuera de subprocesos por defecto; passthrough explícito | [DOC: security.md §Environment Variable Passthrough, §MCP Credential Handling] |
| M11 | **Website blocklist + SSRF** (`security.website_blocklist`, `security.allow_private_urls`, `security.fake_ip_ranges`) | Bloqueo de dominios en todas las tools con URL; redes privadas/metadata bloqueadas fail-closed | [DOC: security.md §Website Access Policy, §SSRF Protection] |
| M12 | **Escaneo de inyección** (context files, memory, SOUL.md de distribuciones) | Patrones de prompt injection/exfiltración bloqueados antes del system prompt | [DOC: security.md §Context File Injection Protection] · [DOC: features/memory.md] |
| M13 | **MCP: trust tiers + filtering + spend limit** (`trust: full\|untrusted`, `tools.include/exclude`, `spend_limit`, env filtrado, `elicitation`, `sampling`) | Servidores no confiables piden aprobación por cada tool de escritura; presupuesto por chat | [DOC: reference/mcp-config-reference.md L53-70] |
| M14 | **Plugins opt-in** (`plugins.enabled` allowlist, `plugins.disabled` deny-list, catálogo con aprobación; plataformas bundled auto, user-installed opt-in) | Código de terceros no carga sin consentimiento explícito | [DOC: features/plugins.md L169-182, L270-282] |
| M15 | **Hooks** (plugin hooks `pre_tool_call` con `block>approve>modify`; shell hooks `hooks:` con aprobación first-use/`hooks_auto_accept`; gateway hooks trusted-by-placement en `~/.hermes/hooks/`) | Punto de veto programable — **el mecanismo que usa el plugin trust-agent** | [DOC: features/hooks.md L11-15, L448] · [DOC: security.md §Trusted-by-placement] |
| M16 | **Managed scope** (directorio managed admin-pinned, config/env inmutable por el usuario) | Config y secretos fijados por el administrador, ignorados ruidosamente si el usuario los edita | [DOC: user-guide/managed-scope.md] |
| M17 | **Secret scope por perfil** (`agent/secret_scope.py`, fail-closed en gateway multiplexado) | Las keys de un perfil nunca sangran a otro | [SRC: agent/secret_scope.py L1-14] |
| M18 | **Egress isolation** (redes Docker segmentadas + proxy con allowlist Squid; iron-proxy con inyección de credenciales) | El sandbox solo reacha hosts allowlisted | [DOC: egress/network-isolation.md] · [DOC: egress/iron-proxy.md] |
| M19 | **Perfiles** (`~/.hermes/profiles/<name>/`, HERMES_HOME aislado; NO son sandbox) | Aislamiento de config/memoria/skills/credenciales por instancia | [DOC: user-guide/profiles.md L229-241] |
| M20 | **Tool search / tools diferidas** (`tools.tool_search.enabled`, lista `defer`) | Tools frías reemplazadas por un puente de búsqueda; cada carga pasa por hooks/aprobación | [DOC: features/tool-search.md L34-42, L127-133] |

---

## 2. Matriz de capacidades (C-001..C-062)

Convención por fila: **Capacidad** · mecanismos nativos (con cita) · **Policy key Trust Agent** (EXISTENTE / NUEVA / — con justificación) · estado (✅ cubierta / ⚠️ gap).

### Dominio A — Tools del núcleo (toolsets estáticos, [SRC: toolsets.py L74-244])

| ID | Capacidad | Mecanismos nativos de limitación | Policy key Trust Agent | Estado |
|----|-----------|----------------------------------|------------------------|--------|
| C-001 | `web_search`/`web_extract` (toolset `web`/`search`) | M1 toolset; M11 website blocklist + SSRF; `web.backend` (proveedor, SearXNG self-hosted); `web.extract_char_limit`/`extract_timeout` | `allowed_tools` (existente) + `web_domain_policy` (NUEVA P-19) | ⚠️ gap dominios |
| C-002 | `x_search` (X/Twitter vía xAI) | M1; off por default; requiere credenciales xAI; filtros por llamada `allowed_x_handles` | `allowed_tools` (existente) | ✅ |
| C-003 | `terminal` + `process_manage` (toolset `terminal`) | M2/M3/M4/M5/M6 aprobaciones + blocklists; M8 backends contenedor; guard de gateway supervisado no sobrepasable; `terminal.cwd`, `timeout` | `allowed_tools` + `approval_mode` (P-1), `allowed_terminal_backends` (P-2), `denied_command_patterns` (P-3), `allowed_command_patterns` (P-4) | ⚠️ 4 gaps |
| C-004 | `read_file`/`write_file`/`patch`/`search_files` (toolset `file`) | M7 rutas protegidas + `HERMES_WRITE_SAFE_ROOT`; `.env` read-denied; verificador de mutaciones | `allowed_tools` + `require_write_sandbox` (P-5) | ⚠️ gap sandbox |
| C-005 | Browser automation (`browser_exec`, `browser_cdp`, `browser_dialog`, …; toolset `browser`) | M1; backends local CDP/cloud (Browserbase, Browser Use, Nous); M11 blocklist/SSRF aplica a browser | `allowed_tools` + `web_domain_policy` (P-19) | ⚠️ gap dominios |
| C-006 | `browser_vault` (vault de credenciales del browser: logins, tarjetas) | M1 + M20 (diferida por default); prompts de fill de tarjeta que YOLO no salta | `allow_credential_vault` (P-16) + `allow_payment_autofill` (P-17) | ⚠️ 2 gaps |
| C-007 | `computer_use` (control de escritorio vía cua-driver) | M1; `computer_use.permission_mode: bounded\|unrestricted` + capability manifest aprobada; combos/patrones hard-blocked; en sandbox corre dentro del sandbox | `allowed_tools` (+ enforcement: bounded cuando permitida — ver §4 E-2) | ✅ con nota |
| C-008 | `vision_analyze` (toolset `vision`; `video_analyze` opt-in) | M1; `vision.embed_target_bytes`, `max_calls_per_image`; SSRF en URLs | `allowed_tools` | ✅ |
| C-009 | `image_generate` / `video_generate`+`xai_video_*` (toolsets `image_gen`/`video_gen`) | M1; proveedor (`image_gen.provider`, plugins de video); presupuesto de concurrencia | `allowed_tools` (+ budget existente) | ✅ |
| C-010 | `text_to_speech` (toolset `tts`; 10+ proveedores) | M1; `tts.provider` | `allowed_tools` | ✅ |
| C-011 | `execute_code` (toolset `code_execution`; kernel Python con RPC a tools) | M1; `code_execution.mode: project\|strict`, `timeout`, `max_tool_calls`, `kernel_idle_timeout`; M10 env scrubbing (KEY/TOKEN/SECRET/...); aprobación de script entero en gateway; `terminal()` foreground-only | `allowed_tools` + `allowed_env_passthrough` (P-18) | ⚠️ gap env |
| C-012 | `delegate_task` (toolset `delegation`; subagentes) | M1; hereda toolsets del padre (no puede escalar); `delegate_task` bloqueado en hojas; `max_spawn_depth`, `max_iterations`, `child_timeout_seconds`; `delegation.model/provider` | `allowed_tools` + `max_delegation_depth` (P-12) | ⚠️ gap profundidad |
| C-013 | `cronjob_manage` (toolset `cronjob`) | M1; `cron.allow_agent_scheduling` (default false); sesiones cron no pueden crear más cron; `approvals.cron_mode: deny` default; preflight de config | `allow_scheduled_tasks` (P-9) | ⚠️ gap |
| C-014 | Tools kanban (`kanban_show`…`kanban_unblock`; toolset `kanban`) | M1; opt-in explícito (`all` no lo activa); solo workers dispatcher (`HERMES_KANBAN_TASK`); `delegate_task` children rechazan mutación de board | `allowed_tools` | ✅ |
| C-015 | `memory` (toolset `memory`; MEMORY.md/USER.md) | M1; `memory.memory_enabled`, `user_profile_enabled`, `write_approval`, `memory_char_limit`; M12 escaneo de inyección | `allowed_tools` (+ `allow_memory_writes`? no: `memory.write_approval` nativo basta — enforcement E-3 en §4) | ✅ con nota |
| C-016 | `session_search` (toolset `session_search`) | M1; datos de sesión aislados cross-session | `allowed_tools` + `session_retention_days` (P-27) | ⚠️ gap retención |
| C-017 | `skills_list`/`skill_view`/`skill_manage` (toolset `skills`) | M1; `skills.write_approval` (gate de escritura agent-side); Skills Guard (escaneo al instalar); `skills.external_dirs`, `hermes skills trust`; curator | `allowed_skills` (referenciada en spec 001 REQ-PRF-005 pero AUSENTE del catálogo — P-8) | ⚠️ gap catálogo |
| C-018 | `clarify`, `todo_list` (toolsets `clarify`/`todo`) | M1 | `allowed_tools` | ✅ |
| C-019 | `manage_connections` (toolset `connections`; conectores hosted Nous + MCP del catálogo, cuentas múltiples, rename) | M1; catálogo con aprobación Nous; tarjetas de aprobación en desktop | `allowed_connectors` + `allowed_connections` + `vault_allowed_credential_types` (existentes; coordinado con t_b16825d0) | ✅ |
| C-020 | Tools de plataforma: `discord`, `discord_admin`, `feishu_*`, `yb_*` (toolsets dedicados) | M1; gated por plataforma (`TOOLSET_SESSION_PLATFORMS` [SRC: toolsets.py L247]); `discord_admin` requiere permisos del bot | `allowed_tools` + `messaging_permissions` | ✅ |
| C-021 | Tools de superficie GUI: `desktop_ui`, `project`, `catalog`, `onboarding`, `bot_room` | M1; solo existen en sesiones desktop (`platforms=frozenset({"desktop"})` [SRC: toolsets.py L147-153]); `catalog` con tarjeta de aprobación | `allowed_tools` (deny-by-default las excluye) | ✅ |

### Dominio B — Messaging gateway (19 plataformas nativas + plugins)

| ID | Capacidad | Mecanismos nativos | Policy key | Estado |
|----|-----------|--------------------|------------|--------|
| C-022 | Gateway multiplataforma: telegram, discord, slack, whatsapp, signal, bluebubbles, email, sms, matrix, mattermost, dingtalk, feishu, weixin, qqbot, wecom, wecom-callback, yuanbao, webhook (+ plugins: a2a, buzz, google_chat, homeassistant, irc, line, ntfy, photon, raft, simplex, teams) | `gateway.platforms.<name>.enabled` [SRC: cli-config.yaml.example L1348]; plataformas plugin bundled auto-cargadas, user-installed opt-in (M14); bundles `hermes-<platform>` [SRC: toolsets.py L206-243] | `messaging_permissions` (map plataforma→modo read_only/reply/full, existente) | ✅ |
| C-023 | Autorización de usuarios del gateway (allowlists, allow-all, DM pairing) | M9: `*_ALLOWED_USERS`, `GATEWAY_ALLOWED_USERS`, `GATEWAY_ALLOW_ALL_USERS`, pairing codes OWASP/NIST, `unauthorized_dm_behavior` | — por diseño: en Trust Agent el gateway corre en el dispositivo del usuario enrolado; la identidad la fija el enrollment (device token + user de la org). Nota de amenaza en §4 E-3 | ✅ por diseño |
| C-024 | Webhooks entrantes (routes, HMAC `WEBHOOK_SECRET`, filters, `deliver_only`) | Plataforma `webhook`; toolset seguro restringido (`_HERMES_WEBHOOK_SAFE_TOOLS`: solo web_search/web_extract/vision_analyze/clarify [SRC: toolsets.py L42]); `toolsets` por route PUEDE reemplazar el toolset de plataforma; `approvals.unattended_mode: deny` | `messaging_permissions["webhook"]` + `allowed_tools`; **gap de enforcement**: el override `toolsets` por route debe quedar clampado por el plugin (§4 E-1) | ⚠️ enforcement |
| C-025 | API server OpenAI-compatible (`gateway.platforms.api_server`, `API_SERVER_KEY`, CORS allowlist, approval resolution `/v1/runs/{id}/approval`) | Plataforma con key propia; toolset `hermes-api-server` (sin clarify/tts/computer_use) | `messaging_permissions["api_server"]` + `allowed_tools` | ✅ |
| C-026 | ACP/editores (VS Code, Zed, JetBrains; toolset `hermes-acp`) | M1; `platform_toolsets.acp`; `agent.disabled_toolsets` resta en toda sesión ACP; doc advierte tratar ACP como automatización privilegiada | `allowed_tools` | ✅ |

### Dominio C — Modelos y providers

| ID | Capacidad | Mecanismos nativos | Policy key | Estado |
|----|-----------|--------------------|------------|--------|
| C-027 | Selección de modelo/provider (`model.default`, `hermes model`, catálogo remoto) | config + catálogo curado | `allowed_models` + `approved_models_only` (invariable) — enforcement vía gate LiteLLM + plugin | ✅ |
| C-028 | Provider routing (`provider_routing.sort/only/ignore/order/require_parameters/data_collection`) | whitelist/blacklist de providers; `data_collection: deny` | `allowed_providers` (P-26) — el routing/fallback NO puede saltar a un provider fuera de la lista | ⚠️ gap |
| C-029 | Fallback providers + credential pools (rotación de keys, `fallback_providers`, `credential_pool_strategies`) | Config por perfil; pools heredados por subagentes | `allowed_providers` (P-26) cubre el destino; pools de credenciales propias del tenant → vault (spec 006/024) | ⚠️ gap |
| C-030 | Modelos locales (`local_runtime`, llamacpp, Ollama) y MoA (mixture-of-agents presets) | Config; MoA presets aparecen como modelos | `allowed_models` + `allowed_providers` (P-26) | ⚠️ gap |
| C-031 | Auxiliary models (compresión, titling, vision, approval smart, goal_judge, curator) | `auxiliary.*` por tarea | `allowed_models` aplica (mismo catálogo) — nota de enforcement §4 E-1 | ✅ con nota |

### Dominio D — Automatización y recurrencia

| ID | Capacidad | Mecanismos nativos | Policy key | Estado |
|----|-----------|--------------------|------------|--------|
| C-032 | Cron jobs (delivery multiplataforma, skills attach, `no_agent` scripts, catch-up, retry) | `cron.*` (preflight, `allow_agent_scheduling` default false, `require_restart_safe_scope`); `approvals.cron_mode: deny`; scripts confinados a `~/.hermes/scripts/` | `allow_scheduled_tasks` (P-9) | ⚠️ gap |
| C-033 | Loops recurrentes en sesión (`/loop`, `loops.max_ticks` backstop) | Config de presupuesto de ticks | `allow_recurring_prompts` (P-10) | ⚠️ gap |
| C-034 | Session heartbeat (prompt recurrente en la sesión actual) | Feature opt-in; don't-invent-work guard | `allow_recurring_prompts` (P-10) | ⚠️ gap |
| C-035 | Goal mode (loop con juez LLM, `goals.max_turns`) | Config | `allow_goal_mode` (P-11) | ⚠️ gap |
| C-036 | Batch processing (`batch_runner.py`, cientos de prompts paralelos) | CLI del host; `--providers_allowed`; sandboxes por prompt | No aplica al agente de escritorio enrolado (feature de host/CLI del proveedor); cubierto por budget si se usa | ✅ por diseño |
| C-037 | Bot Mode (roster de bots = perfiles; `message_agent` bot-a-bot; group rooms; `bot_peers` gateway-a-gateway; `agent.bot_mode_protocol`) | `message_agent` solo en Bot Chats canónicos con marcador de install; peers requieren `API_SERVER_KEY` | `allow_bot_mode` (P-22) | ⚠️ gap |

### Dominio E — Extensión (código de terceros)

| ID | Capacidad | Mecanismos nativos | Policy key | Estado |
|----|-----------|--------------------|------------|--------|
| C-038 | Plugins generales (tools/hooks/memory providers/context engines/terminal backends/approval transports) | M14: `plugins.enabled` allowlist + `plugins.disabled` deny; catálogo con tiers y `dangerous` bloqueante; `plugins.isolation: host` | `allowed_plugins` (P-7) | ⚠️ gap |
| C-039 | MCP servers (stdio/HTTP; OAuth; `mcp-<server>` toolsets dinámicos) | M13: `trust` tiers, `tools.include/exclude`, `spend_limit`, env filtrado, `elicitation.enabled`, `sampling`; catálogo MCP con block-list curada | `allowed_mcp_servers` (P-6) + `allowed_tools` para las tools `mcp__*` (§4 E-1) | ⚠️ gap |
| C-040 | Gateway event hooks (`~/.hermes/hooks/<name>/HOOK.yaml+handler.py`) | **Trusted-by-placement**: se cargan sin allowlist ni prompt, in-process con privilegios del gateway; `HERMES_SAFE_MODE` no los salta | `allow_gateway_hooks` (P-23) + **residual risk**: el plugin no puede vetar la carga (ver §4 E-3) | ⚠️ gap + riesgo residual |
| C-041 | Shell hooks (`hooks:` en config.yaml) | Aprobación first-use o `hooks_auto_accept`; `pre_tool_call` shell puede bloquear | `allow_shell_hooks` (P-24) | ⚠️ gap |
| C-042 | Instalación de skills (hub/catálogo, `hermes skills trust`, import) | Skills Guard (escaneo de contenido sospechoso al instalar); `skills.external_dirs`; trust explícito por repo | `allowed_skills` (P-8) | ⚠️ gap |
| C-043 | Profile distributions (`hermes profile install <git-url>`, `distribution.yaml`, archivos distribution-owned incl. SOUL.md) | Reemplazo de archivos owned; SOUL.md de distribución SÍ se bloquea por scanner hit | `allow_profile_distributions` (P-25) | ⚠️ gap |
| C-044 | Import desde otros agentes (Claude Code, Codex, Cursor, OpenClaw: allowlists, MCP, skills, memorias) | Mapeo `permissions.allow→command_allowlist`, `permissions.deny→approvals.deny`; merge no-replace | `allow_profile_distributions` (P-25) cubre (mismo seam de importación) | ⚠️ gap |
| C-045 | Lazy installs de dependencias (`security.allow_lazy_installs`) y `hermes update` | Flag de seguridad; imagen Docker oficial los desactiva | `allow_self_updates` (P-29) | ⚠️ gap |

### Dominio F — Datos, memoria e identidad del agente

| ID | Capacidad | Mecanismos nativos | Policy key | Estado |
|----|-----------|--------------------|------------|--------|
| C-046 | Memoria persistente (MEMORY.md/USER.md en system prompt) | M12 escaneo; `memory.write_approval` | `allowed_tools`; `allow_custom_personality` no aplica aquí | ✅ |
| C-047 | Memory providers externos (Honcho, OpenViking, Mem0, Hindsight, Supermemory, RetainDB, ByteRover...) | `memory.provider` + plugins del catálogo (opt-in M14) | `allowed_memory_providers` (P-13) | ⚠️ gap |
| C-048 | Personalidad / SOUL.md (primera cosa del system prompt; `agent.personalities`, presets `/personality`) | SOUL.md propio del perfil no se bloquea por scanner (excepción documentada); distribuciones sí | `allow_custom_personality` (P-14) — la org puede fijar el persona; la sección de gobernanza del plugin va `after_memory` (spec 019) | ⚠️ gap |
| C-049 | Context files de proyecto (.hermes.md, AGENTS.md, CLAUDE.md, .cursorrules) | M12 escaneo anti-inyección; límites de tamaño | — cubierto por mecanismo nativo; el plugin añade la sección de gobernanza por encima | ✅ |
| C-050 | Sesiones (persistencia, resume, export con `--redact`, prune/archive) | Aislamiento cross-session; export redactado por default | `session_retention_days` (P-27) | ⚠️ gap |
| C-051 | Checkpoints y rollback (shadow git store, `/rollback`) | `checkpoints.enabled` (default false), caps de tamaño/retención | `require_checkpoints` (P-28) | ⚠️ gap |

### Dominio G — Secretos y credenciales

| ID | Capacidad | Mecanismos nativos | Policy key | Estado |
|----|-----------|--------------------|------------|--------|
| C-052 | Secret sources (Bitwarden, 1Password, command helper; plugins de secret source) | `secrets.sources`, `secrets.<src>.enabled`; env sanitizado en subprocesos | `allowed_secret_sources` (P-15) | ⚠️ gap |
| C-053 | Credential vault del browser (C-006) y fills de pago | Prompts que YOLO no salta; `vault.key_storage` | `allow_credential_vault` (P-16) + `allow_payment_autofill` (P-17) | ⚠️ gap |
| C-054 | Env passthrough a sandboxes (`terminal.env_passthrough`, `docker_forward_env`, `required_environment_variables`/`required_credential_files` de skills) | M10: default sellado; nombres de infraestructura de Hermes rechazados; mounts read-only | `allowed_env_passthrough` (P-18) | ⚠️ gap |
| C-055 | Adopción de logins externos (`auth.adopt_external_logins`) | Flag default true; rotación de refresh tokens | `allow_credential_vault` (P-16) cubre (mismo seam de credenciales) | ⚠️ gap |

### Dominio H — Red y egress

| ID | Capacidad | Mecanismos nativos | Policy key | Estado |
|----|-----------|--------------------|------------|--------|
| C-056 | Acceso web del agente (dominios) | M11 `security.website_blocklist` (dominios + shared_files) | `web_domain_policy` (P-19, map mode/domains) | ⚠️ gap |
| C-057 | Acceso a redes privadas/metadata (SSRF opt-out) | `security.allow_private_urls` (default false), `fake_ip_ranges` | `allow_private_network_access` (P-20) | ⚠️ gap |
| C-058 | Egress isolation / iron-proxy (segmentación Docker, allowlist Squid, inyección de credenciales) | M18 — despliegue del operador, no config del agente | `allowed_terminal_backends` (P-2) + deployment del proveedor (bundle distribuido pre-configurado) | ✅ por diseño |

### Dominio I — Voz y media entrante

| ID | Capacidad | Mecanismos nativos | Policy key | Estado |
|----|-----------|--------------------|------------|--------|
| C-059 | Voice mode (STT+TTS encadenado o `gpt-live` full-duplex; Discord voice) | `voice.*`, `stt.provider`, `tts.provider`; wake word `wake_word.enabled` | `allow_voice_mode` (P-21) | ⚠️ gap |

### Dominio J — Superficies de administración y config

| ID | Capacidad | Mecanismos nativos | Policy key | Estado |
|----|-----------|--------------------|------------|--------|
| C-060 | Web dashboard (admin panel: config, API keys, MCP, pairing, webhooks, gateway, credenciales, sesiones, logs, cron, skills) | Bind loopback o auth obligatoria (`--insecure` deprecado no-op); `--isolated` por perfil | `messaging_permissions["dashboard"]`? No: el dashboard es local del dispositivo. Riesgo residual documentado (§4 E-3): quien controla el dispositivo controla config.yaml — la policy se aplica POR ENCIMA vía plugin + managed scope (M16) | ✅ por diseño |
| C-061 | Multi-profile gateway (`gateway.multiplex_profiles`) y perfiles múltiples | M17 secret scope fail-closed; blocker de credenciales duplicadas | `max_daily_sessions` (existente) + enrollment 1 dispositivo=1 perfil gobernado (spec 019) | ✅ |
| C-062 | Deliverable mode (artefactos nativos en chat), document extraction, LSP, skins/temas, pets, language packs, Spotify, subscription proxy, tool gateway Nous | Features de superficie; tool gateway = `web.backend: nous` etc. | `artifact_sharing` (existente) para deliverables; `allowed_providers` (P-26) para tool gateway/subscription proxy | ⚠️ gap providers |

---

## 3. Policy keys NUEVAS requeridas (P-1..P-29)

Catálogo tipado (mismo mecanismo que REQ-GOV-020..024 / migración 0011): todas con no-relajación (un nivel inferior no puede ampliar más allá del superior; deny-overrides). `*` = token comodín donde aplica.

| P# | Key | Tipo | Default (proveedor) | Combina como | Governa (capability) |
|----|-----|------|---------------------|--------------|----------------------|
| P-1 | `approval_mode` | string enum `off\|smart\|manual` | `"smart"` | STRING_RANKS `manual`(2) > `smart`(1) > `off`(0); nivel inferior no puede bajar el rank | C-003; veta `approvals.mode: off`, `--yolo`, `/yolo`, `HERMES_YOLO_MODE` |
| P-2 | `allowed_terminal_backends` | list (`local`,`ssh`,`docker`,`modal`,`daytona`,`vercel_sandbox`,`singularity`,`*`) | `["docker"]` | intersección | C-003/C-058 `terminal.backend` |
| P-3 | `denied_command_patterns` | list (globs fnmatch) | `[]` | unión (deny-overrides) | C-003 `approvals.deny` |
| P-4 | `allowed_command_patterns` | list | `[]` | intersección (techo del `command_allowlist`) | C-003 |
| P-5 | `require_write_sandbox` | boolean | `true` | OR (true gana) | C-004 `HERMES_WRITE_SAFE_ROOT` |
| P-6 | `allowed_mcp_servers` | list (nombres de servidor, `*`) | `[]` | intersección | C-039 |
| P-7 | `allowed_plugins` | list (nombres, `*`) | `[]` | intersección | C-038 `plugins.enabled` |
| P-8 | `allowed_skills` | list (ids de skill, `*`) | `["*"]` | intersección | C-017/C-042 (ya referenciada en spec 001 REQ-PRF-005 — falta en catálogo) |
| P-9 | `allow_scheduled_tasks` | boolean | `false` | AND (false gana) | C-013/C-032 `cronjob_manage` + `cron.allow_agent_scheduling` |
| P-10 | `allow_recurring_prompts` | boolean | `false` | AND | C-033/C-034 `/loop` + `/heartbeat` |
| P-11 | `allow_goal_mode` | boolean | `false` | AND | C-035 |
| P-12 | `max_delegation_depth` | integer | `1` (piso 0) | mínimum | C-012 `max_spawn_depth` |
| P-13 | `allowed_memory_providers` | list (`builtin` + nombres, `*`) | `["builtin"]` | intersección | C-047 |
| P-14 | `allow_custom_personality` | boolean | `false` | AND | C-048 SOUL.md/personalities |
| P-15 | `allowed_secret_sources` | list (`env` + fuentes, `*`) | `["env"]` | intersección | C-052 |
| P-16 | `allow_credential_vault` | boolean | `false` | AND | C-006/C-053/C-055 |
| P-17 | `allow_payment_autofill` | boolean | `false` | AND | C-053 fills de tarjeta |
| P-18 | `allowed_env_passthrough` | list (nombres de vars, `*`) | `[]` | intersección | C-011/C-054 |
| P-19 | `web_domain_policy` | map `{mode: "open"\|"allowlist"\|"blocklist", domains: []}` | `{"mode":"open","domains":[]}` | deny-overrides por modo (blocklist de cualquier nivel se aplica; allowlist = intersección) | C-001/C-005/C-056 `security.website_blocklist` |
| P-20 | `allow_private_network_access` | boolean | `false` | AND | C-057 `security.allow_private_urls` |
| P-21 | `allow_voice_mode` | boolean | `false` | AND | C-059 voice + wake word |
| P-22 | `allow_bot_mode` | boolean | `false` | AND | C-037 `bot_mode_protocol`, `bot_peers`, `message_agent` |
| P-23 | `allow_gateway_hooks` | boolean | `false` | AND | C-040 `~/.hermes/hooks/` (con riesgo residual E-3) |
| P-24 | `allow_shell_hooks` | boolean | `false` | AND | C-041 `hooks:` |
| P-25 | `allow_profile_distributions` | boolean | `false` | AND | C-043/C-044 profile install + import-agent |
| P-26 | `allowed_providers` | list (`litellm_gateway` + providers, `*`) | `["litellm_gateway"]` | intersección | C-028/C-029/C-030/C-062 routing, fallback, locales, tool gateway |
| P-27 | `session_retention_days` | integer | `30` (piso 1) | mínimum | C-016/C-050 retención de sesiones |
| P-28 | `require_checkpoints` | boolean | `true` | OR | C-051 `checkpoints.enabled` |
| P-29 | `allow_self_updates` | boolean | `false` | AND | C-045 `hermes update` + `security.allow_lazy_installs` + installs de catálogo |

**Nota de coherencia con lanes paralelas**: `allowed_connections`/`allowed_connectors`/`vault_allowed_credential_types` ya cubren conectores y conexiones personalizadas (t_b16825d0) — esta matriz NO duplica esas keys; la fila C-019 las referencia.

---

## 4. Gaps de ENFORCEMENT sobre keys existentes (E-1..E-3)

El catálogo no basta: el plugin `trust-agent-governance` (spec 019) debe hacer cumplir las keys en las superficies derivadas. Hallazgos con cita:

- **E-1 — `allowed_tools` debe alcanzar las tools dinámicas y derivadas**: (a) tools de MCP (`mcp__<server>__*`) se registran como toolsets dinámicos [DOC: toolsets-reference.md §Dynamic Toolsets]; (b) `tool_search` carga tools diferidas — cada carga pasa por hooks/aprobación [DOC: tool-search.md L42], así que el veto `pre_tool_call` del plugin las cubre SI el hook corre en cada carga diferida (verificar en implementación); (c) los subagentes `delegate_task` heredan los toolsets del padre [DOC: delegation.md L374] — el veto debe propagarse a children; (d) las rutas webhook pueden REEMPLAZAR el toolset de plataforma con `toolsets:` por route [DOC: webhooks.md L85] — el plugin debe clampar ese override contra `allowed_tools`; (e) `allowed_models` debe cubrir auxiliary models (compresión/titling/vision) [DOC: configuration.md §auxiliary].
- **E-2 — `allowed_tools` con `computer_use` activa exige modo bounded**: YOLO/unrestricted desactiva los prompts de Cua [DOC: computer-use.md L124-126]; con `approval_mode` ≥ smart el plugin debe rechazar `permission_mode: unrestricted`.
- **E-3 — Riesgos residuales que el plugin NO puede vetar (documentarlos en el threat model, ADR-018)**: (a) gateway hooks trusted-by-placement se cargan sin allowlist [DOC: security.md §Trusted-by-placement] — mitigación: `allow_gateway_hooks=false` + managed scope que vigile `~/.hermes/hooks/` + attestation; (b) quien controla el dispositivo controla `config.yaml`/dashboard local — la gobernanza se aplica POR ENCIMA (bundle firmado + plugin + gates remotos LiteLLM/vault), no impidiendo la edición local; (c) `memory.write_approval` y `skills.write_approval` son config local — el plugin debe tratarlos como pisos y registrar en attestation si están por debajo de lo que la policy exige.

---

## 5. Tareas dev derivadas (creadas y linkadas al gate t_e8a28fce)

| Tarea | Alcance |
|-------|---------|
| T1 — Catálogo tipado | Migración Alembic + paridad `db/schema.sql` con las 29 keys de §3 (tipos, defaults, invariable flags, `STRING_RANKS`/`INTEGER_FLOORS` en el combinator) + tests de no-relajación |
| T2 — Enforcement batch 1 (config-level) | Plugin: `approval_mode`/veto YOLO, `allowed_terminal_backends`, `require_write_sandbox`, `allowed_env_passthrough`, `web_domain_policy`, `allow_private_network_access`, `allowed_providers`, `allow_self_updates` |
| T3 — Enforcement batch 2 (capability-level) | Plugin: `allowed_mcp_servers`, `allowed_plugins`, `allowed_skills`, `allowed_memory_providers`, `allowed_secret_sources`, `allow_credential_vault`/`allow_payment_autofill`, `allow_custom_personality`, `allow_voice_mode`, `allow_bot_mode`, `allow_gateway_hooks`/`allow_shell_hooks`, `allow_scheduled_tasks`/`allow_recurring_prompts`/`allow_goal_mode`, `max_delegation_depth`, `session_retention_days`, `require_checkpoints`, `allow_profile_distributions` + E-1/E-2/E-3 |
| T4 — Bundle JWS extensible | `policy_version` + schema versionado: keys desconocidas no rompen la verificación (forward-compat), unknown-key policy = ignorar-pero-nunca-relajar, todo veto auditado en attestation |

(Ids reales en el comentario de completion de t_d8ed9362.)

---

## 6. Cómo verificar / regenerar

Ver skill `/root/trust-agent/skills/hermes/capability-surface/SKILL.md` — procedimiento reproducible contra `llms-full.txt` + `/usr/local/lib/hermes-agent`. Regenerar cuando: cambie `toolsets.py`, se añada una plataforma en `plugins/platforms/`, o el índice `/docs/assets/files/llms-*.txt` liste páginas nuevas.
