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
- Keycloak 26: flujo VERIFY_PROFILE roto (documentado en la auditoría); el enrolamiento MFA por scraping de HTML del prototipo (C5) es frágil — usar Admin API oficial.
- Mensajerías: un bot/token pertenece a UN solo perfil; dos gateways con el mismo token pelean el long-poll. Al clonar perfiles, las channels NO se clonan por defecto.
- mem0 u otros servicios que usen LITELLM_MASTER_KEY: su gasto evade virtual keys y presupuestos — darles su propia key con budget.
- Imágenes no fijadas (`:latest`) + `pip install` en arranque: no reproducible; fijar versiones.

## 4. Referencia cruzada

- Spec maestra con requisitos EARS: `/root/trust-agent/specs/001-platform/spec.md` (REQ-PRF, REQ-SEC-008, REQ-DTK-008 son los que citan estos mecanismos).
- Docs Hermes: `/docs/user-guide/features/plugins`, `/docs/user-guide/profiles`, índice completo `/docs/llms.txt`.
