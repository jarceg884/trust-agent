# Spec 020 — Perfiles de agente default del producto, gobernados por policy (feedback Miller #7)

| Campo | Valor |
|---|---|
| **Padre** | specs/005-governance/spec.md (REQ-GOV-040…044, D-GOV-13) · specs/001-platform (REQ-AGT-001…006) · specs/009-admin-console (REQ-AC-016) |
| **Alcance** | Perfiles de agente default (templates curados del producto) que el tenant ve en Shared Agents y puede adoptar; la adopción crea una instancia del agente para el tenant; el policy engine existente (deny-overrides) gobierna la invocación igual que a los agentes propios. |
| **Fuente** | Feedback Miller #7 (t_0ed22c94): "Share agents deberían ser perfiles default que Trust Agent debe incluir dentro de la política, pero no funciona tal cual". Hoy solo existe publicar agentes propios. |
| **Reglas** | SDD; evidencia real (tests contra PostgreSQL vivo + adopción desde la UI del stack local con login PKCE+TOTP real); audit al contrato spec 008 §5 (acción nueva sembrada en `catalog.audit_actions`); nada simulado. |

## 1. Problema

El flujo actual de Shared Agents exige que un usuario publique un agente propio desde cero. El producto no trae perfiles curados: un tenant nuevo entra a Shared Agents y no hay nada que adoptar. El feedback #7 pide que Trust Agent incluya **perfiles default del producto** y que queden **dentro de la política** del tenant (modelos aprobados, connectors permitidos, budget).

## 2. Decisiones de diseño

**D-020-01 — Catálogo de perfiles default = data versionada en código.**
Igual que las plantillas de perfil por departamento (D-ONB-06, `app/profiles/templates.py`), los perfiles de agente default viven en un registro Python versionado: `AGENT_PROFILE_TEMPLATES` en `app/governance/agent_profiles.py` (dataclass `AgentProfileTemplate`: key, name, description, scope, skills, suggested_connectors, suggested_models, base_policies). No hay tabla `agent_profile_templates`: el catálogo del producto evoluciona con el código y una tabla sería estado duplicado sin dueño de spec. Perfiles iniciales: `analyst`, `coder`, `ops`, `support`. Los `suggested_models`/`suggested_connectors`/`base_policies` del template son SUGERENCIAS curadas — JAMÁS se auto-aproban ni relajan la policy del tenant (el tenant decide; REQ-POL-007 se mantiene).

**D-020-02 — Adoptar = instancia del agente para el tenant (no global).**
`POST /api/agent-profiles/{key}/adopt` inserta una fila en `agents` del tenant con `profile_key` (columna nueva, migración `0009_default_agent_profiles`): owner = adoptante (requiere departamento, igual que publish), department = el del adoptante, name/description/scope = los del template. Índice único parcial `(tenant_id, profile_key) WHERE profile_key IS NOT NULL AND status = 'active'` → **una instancia viva por tenant y perfil**; una segunda adopción del mismo perfil en el mismo tenant → 422. La instancia es un agente normal: grants explícitos `invoke`/`view` como hoy, manage role-based (owner / dept_admin del depto / org_admin), revocación inmediata, 404 sin grant. Audit: acción nueva `agent.profile_adopted` (sembrada en `catalog.audit_actions`).

**D-020-03 — Gobernanza = el policy engine existente, sin duplicación (deny-overrides igual que a agentes propios).**
- **Modelos aprobados**: `POST /api/agents/{id}/invoke` acepta body opcional `{"model_name": ...}`; cuando viene, el gate aplica `assert_model_allowed` (intersección org∩dept∩user de `model_access` ∩ policy `allowed_models`) → 403 `model_not_approved` + audit `model.access_denied`. Aplica a TODOS los agentes (propios y adoptados): el mismo gate, la misma policy.
- **Budget**: el gate de invocación verifica `budgets.status_for_user(...).cutoff` → 403 `budget_cutoff` + audit `agent.invoke_denied` (payload `reason: "budget_cutoff"`). Defensa en profundidad: el corte primario sigue siendo la virtual key bloqueada en el gateway (D-GOV-07); el gate evita autorizar una invocación que el gateway rechazaría.
- **Connectors permitidos**: los conectores sugeridos del perfil NO se instancian ni aprueban solos. El governance de conectores existente aplica en tiempo de uso: catálogo filtrado por policy efectiva (`allowed_connectors` / `messaging_permissions`), sweep proactivo REQ-CON-005 (spec 013 D-013-02) y verificación por instancia.

**D-020-04 — Contrato de API.**
- `GET /api/agent-profiles` (cualquier usuario autenticado con MFA) → `{profiles: [{key, name, description, scope, skills, suggested_connectors, suggested_models, adopted_agent_id | null, adopted_by | null}], effective_policy: {allowed_models: [..], allowed_connectors: [..], budget_cutoff: bool}}`. `effective_policy` es la policy EFECTIVA del CALLER (deny-overrides de 3 niveles + intersección de model access + cutoff de budget) — la UI la muestra tal cual ("ver policy efectiva").
- `POST /api/agent-profiles/{key}/adopt` → 201 `{agent_id, profile_key, name, scope, owner_user_id, department_id}`; key desconocida → 404; ya adoptado en el tenant → 422; sin departamento → 422.

**D-020-05 — UI (console, sección "Default profiles" en Shared Agents).**
En `/agents`, encima de la tabla de agentes compartidos: card "Default profiles" con tabla (perfil, descripción, modelos/conectores sugeridos, estado adoptado/no adoptado), botón **Adopt** por perfil y dialog **Effective policy** (modelos aprobados efectivos, conectores permitidos, estado de cutoff del caller). Tras adoptar, la instancia aparece en la tabla de shared agents (access: own) y es gestionable con los grants de hoy. Además, un dialog **Invoke** (por agente) que llama al gate `POST /api/agents/{id}/invoke` con `model_name` seleccionable y muestra el veredicto real: 200 (autorizado, billed_to) o el error 403 de policy (gobernanza visible desde la UI).

## 3. Requisitos y criterios de aceptación

**REQ-020-001** (M) — El producto trae perfiles de agente default curados; todo usuario autenticado los ve al entrar a Shared Agents junto con SU policy efectiva.
> **AC-020-01**: `GET /api/agent-profiles` devuelve los perfiles del catálogo con `effective_policy` del caller (modelos efectivos = intersección real de `model_access` + policy; conectores = policy efectiva; cutoff = estado real de budget). Un usuario de otro tenant ve otra policy (aislamiento).

**REQ-020-002** (M) — Adoptar un perfil default crea una instancia del agente para el tenant, editable/gestionable por el admin con grants explícitos como hoy.
> **AC-020-02**: adopt → 201; existe fila `agents` con `profile_key` y owner = adoptante; aparece en `GET /api/agents` con access `own`; audit `agent.profile_adopted`; segunda adopción del mismo perfil en el tenant (aunque la intente OTRO usuario) → 422; key desconocida → 404.
> **AC-020-03**: aislamiento multi-tenant: el tenant B adopta el mismo perfil → instancia DISTINTA (el índice único es por tenant; nada global se comparte).
> **AC-020-04**: grants como hoy: el owner otorga `invoke` a otro usuario → ese usuario invoca 200; un tercero sin grant → 404 (sin oráculo de existencia).

**REQ-020-003** (M) — La policy del tenant gobierna los perfiles default igual que a los agentes propios (deny-overrides).
> **AC-020-05**: invocar la instancia adoptada con `model_name` aprobado para el invocador → 200 (`billed_to_user_id` = invocador, audit `agent.invoked`); con `model_name` NO aprobado → 403 `model_not_approved` + audit `model.access_denied`; sin `model_name` → 200 (compatibilidad con el contrato actual).
> **AC-020-06**: con el invocador cortado por budget (`cutoff: true`) → invoke → 403 `budget_cutoff` + audit `agent.invoke_denied`.

**REQ-020-004** (M) — UI: sección "Default profiles" en Shared Agents con adoptar y ver policy efectiva.
> **AC-020-07**: evidencia real desde el navegador contra el stack local (login PKCE S256 + TOTP real): la sección lista los perfiles, "Effective policy" muestra la policy efectiva real del usuario, "Adopt" crea la instancia (visible en la tabla de shared agents) y "Invoke" con modelo aprobado → 200 y con modelo no aprobado → el error 403 de policy se muestra en la UI.

## 4. Evolución de capa de datos (spec 002)

Migración `0009_default_agent_profiles` (única cabeza sobre `0008_model_catalog_glm52`) + espejo en `db/schema.sql`:
- `ALTER TABLE agents ADD COLUMN profile_key text NULL` (paridad: sección 9.2).
- Índice único parcial `agents_tenant_profile_uidx ON agents (tenant_id, profile_key) WHERE profile_key IS NOT NULL AND status = 'active'`.
- Seed `catalog.audit_actions`: `('agent.profile_adopted', 'Default agent profile adopted (tenant instance).')` (contrato spec 008 §5; el FK de `audit_log.action` lo exige).

## 5. Fuera de alcance

- Editar name/description de un agente publicado (no existe para agentes propios tampoco; paridad).
- Auto-aplicar los `base_policies` sugeridos del template como policy del tenant (el tenant gobierna; jamás se relaja nada por defecto).
- Instanciar conectores automáticamente al adoptar (el usuario los configura bajo la policy vigente).
- Desktop: la sección vive en la console (el desktop ya lista agentes vía `GET /api/agents`, que incluye las instancias adoptadas sin cambios).
