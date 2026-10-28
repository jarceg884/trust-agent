# Integración Hermes Agent — bundle de descarga white-label (specs 022/023)

**El desktop es Hermes Agent configurado, NO un fork parcheado** (constitution
IV, lección C6 de la auditoría AIDLC). Toda la integración usa mecanismos
oficiales, verificados contra `hermes_cli` instalado y las docs
(hermes-agent.nousresearch.com/docs — skill `hermes-integration/hermes-saas-governance`).

> Ubicación (t_b8769ecb): este árbol vivía en `platform/desktop-client/hermes/`
> y se reubicó en `platform/hermes/` al retirar el desktop-client demo (:9090,
> spec 010). El API lo empaqueta en el tarball de `/download/{slug}/bundle`
> (spec 023; el Dockerfile lo copia a `/opt/trust-agent/hermes-plugins`).

## Piezas

| Pieza | Mecanismo oficial | Archivo |
|---|---|---|
| Perfil por usuario | **Profiles**: home independiente por usuario (`~/.hermes/profiles/<user>/`) — config, .env, memorias, sesiones, skills. NUNCA dos procesos sobre el mismo home. | `profile-template/config.yaml` |
| Plantilla versionada del perfil | **Profile distributions**: publicar la plantilla como repo git → `hermes profile install <repo> --alias <user>`; `hermes profile update` actualiza SOUL/config/skills sin tocar credenciales/memorias. | `profile-template/` (el instalador coloca `soul-template.md` como `SOUL.md`) |
| Puente de gobernanza | **Plugin de directorio**: `~/.hermes/plugins/trust-agent-governance/` con `plugin.yaml` + `__init__.py::register(ctx)`. | `plugins/trust-agent-governance/` |

## Cableado (paso a paso)

1. **Enrolamiento** (spec 007): el usuario completa el flujo del desktop
   (`/enroll`) → su dispositivo queda `active` con un device token.
2. **Virtual key**: el control plane emite la virtual key LiteLLM del usuario
   (`POST /api/models/keys`, entrega one-time). El instalador del desktop la
   escribe en el `.env` del perfil (`TRUST_USER_VIRTUAL_KEY`) — por máquina,
   jamás commiteada.
3. **Perfil**: `hermes profile install <repo-de-la-plantilla> --alias <user>`.
   El `config.yaml` resultante apunta `model.base_url` al proxy LiteLLM del
   tenant con esa key: **el gate real de modelos aprobados y presupuesto es
   LiteLLM** (D-GOV-15 — la key rechaza modelos no aprobados y aplica
   `max_budget`). Sin `fallback_model`: ningún tráfico evade el gateway.
4. **Plugin**: copiar `plugins/trust-agent-governance/` a
   `~/.hermes/plugins/` y habilitarlo (`plugins.enabled` en el config.yaml del
   perfil). Registra:
   - tool `governance_status` — presupuesto/políticas/modelos aprobados en
     vivo desde la API (`/api/me/budget`, `/api/me/policies`,
     `/api/models/allowed-for-me`) con el device token;
   - hook `pre_llm_call` — inyecta contexto de gobernanza en cada turno
     (verificado: `agent/turn_context.py::_collect_pre_llm_call_context`; es
     un hook de CONTEXTO, no de veto);
   - hook `pre_tool_call` — veto de defensa en profundidad (p. ej. delegación
     de agentes con presupuesto en corte; verificado:
     `_dispatch_pre_tool_call_hooks` consume `block_message`).
5. **Entorno del perfil**: `TRUST_AGENT_API` (control plane) y
   `TRUST_DEVICE_TOKEN` (renovable vía `POST /api/devices/{id}/token`, TTL
   15 min) en el `.env` del perfil.

## Por qué esto cumple "cero parches"

- No se toca ningún archivo de `hermes_cli/` ni de plugins core: todo va por
  `plugin.yaml` + `register(ctx)` y config por perfil (API verificada en
  `hermes_cli/plugins.py::PluginContext`).
- El hook `pre_llm_call` NO puede vetar modelos — y no lo necesita: el veto de
  modelos/presupuesto lo hace LiteLLM en el gateway con la virtual key
  (defensa por diseño, no por parche).
- `verify` de TLS siempre activo; secretos one-time; sin ROPC para usuarios
  finales (constitution IV).
