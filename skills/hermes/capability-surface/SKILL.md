---
name: capability-surface
description: Use when regenerating or updating the Hermes capability matrix (docs/hermes-capability-matrix.md), auditing which Hermes capabilities exist and how each can be limited, or after a Hermes upgrade changes the toolset/platform surface. Produces a cited capability × limitation × policy-key matrix.
version: 1.0.0
category: hermes
---

# Superficie de capacidades de Hermes → matriz de control de policy

## Cuándo usar

- Tras una actualización de Hermes Agent (nuevos toolsets, plataformas, tools o config keys).
- Cuando la revisión maestra o el gate final piden verificar que las policies cubren TODA la superficie del agente (mandato Miller).
- Antes de diseñar una policy key nueva: para no duplicar keys ni dejar capacidades sin dueño.

## Fuentes de verdad (en este orden; NADA inventado)

1. **Índice curado**: `https://hermes-agent.nousresearch.com/docs/assets/files/llms-d8572be0e192ab98a8bd158467f1e822.txt` (~17KB) — lista TODAS las páginas de doc. Si el hash del nombre de archivo cambió, la doc se movió: re-descubrir desde `/docs/llms-full.txt`.
2. **Doc completa**: `https://hermes-agent.nousresearch.com/docs/llms-full.txt` (concatenación de todas las páginas, ~6MB). Cada página empieza con `<!-- source: website/docs/<ruta> -->` — ese comentario es la cita canónica.
3. **Código fuente**: `/usr/local/lib/hermes-agent/`:
   - `toolsets.py` → `TOOLSETS` (lista REAL de toolsets y tools), `TOOLSET_SESSION_PLATFORMS`, `_HERMES_CORE_TOOLS`, `_HERMES_WEBHOOK_SAFE_TOOLS`.
   - `plugins/platforms/` → plataformas del gateway (directorios = plataformas).
   - `tools/connectors/`, `agent/secret_scope.py`, `tools/approval.py` (UNRECOVERABLE_BLOCKLIST), `cli-config.yaml.example` (superficie de config: bloques de primer nivel).
4. **Trust Agent**: `db/schema.sql` (`catalog.policy_keys` = catálogo tipado vigente), `platform/app/governance/policies.py` (combinator: STRING_RANKS, INTEGER_FLOORS, no-relajación), `specs/019-governance-over-hermes/spec.md` (mecanismo bundle+plugin).

## Procedimiento

1. **Descargar y indexar**:
   ```bash
   curl -fsSL https://hermes-agent.nousresearch.com/docs/llms-full.txt -o llms-full.txt
   grep -n "<!-- source:" llms-full.txt > page-index.txt   # mapa página→línea
   ```
2. **Extraer la lista real de toolsets y tools** desde `toolsets.py` (leer el archivo completo; no fiarse de la doc sola — la doc describe, el código decide). Anotar: toolsets core/composite/platform, tools por toolset, gating por plataforma.
3. **Extraer plataformas**: `ls /usr/local/lib/hermes-agent/plugins/platforms/` + bundles `hermes-*` de `toolsets.py`.
4. **Por cada dominio de la doc** (security, tools, toolsets-reference, mcp-config-reference, cron, delegation, memory, skills, personality, plugins, hooks, voice, webhooks, bot-mode, egress, managed-scope, credential-vault, computer-use, profiles, secrets, provider-routing, sessions, code-execution, configuration): grep de mecanismos (`disable|enabl|allow|block|deny|approv|trust|limit|restrict|sandbox|isolat|filter|guard`) y de claves yaml/env. Citar página (`<!-- source: -->`) o archivo:línea.
5. **Cruzar contra `catalog.policy_keys`**: cada capacidad debe tener UNA policy key. Deny-by-default: sin key = gap. Coordinar nombres con lanes paralelas (conectores: `allowed_connectors`/`allowed_connections` ya existen).
6. **Actualizar** `docs/hermes-capability-matrix.md`: nuevas filas C-XXX, keys P-XXX (tipo, default, cómo combina: intersección/unión/AND/OR/min), gaps de enforcement E-XXX, y tareas dev si hay gaps nuevos.

## Reglas de oro

- **Cada fila cita su fuente** (página de doc o archivo de código). Sin cita, no va a la matriz.
- **La doc describe, el código decide**: si doc y `toolsets.py` discrepan, gana el código y se anota la discrepancia.
- **Nada simulado**: los defaults y enums se copian del código/doc, no de memoria.
- **Riesgos residuales se documentan, no se ocultan**: hay superficies que un plugin NO puede vetar (gateway hooks trusted-by-placement, config.yaml local del dispositivo) — van al threat model con su mitigación (managed scope, attestation, gates remotos).
- **No parchear el core** (constitution IV): toda limitación nueva se implementa como policy key + enforcement en el plugin `trust-agent-governance` (hook `pre_tool_call`, spec 019).

## Lecciones (2026-10-26, primera generación)

- `web_search` NO está en el toolset `browser` a propósito: `disabled_toolsets: [browser]` no puede desactivar la búsqueda web (issue #64503 en toolsets.py L108-111) — una policy que quiera "sin navegador" necesita `allowed_tools` sin tools de browser, no solo disable del toolset.
- El toolset `kanban` no se activa con `all`/`*` (opt-in explícito) y las tools webhook están limitadas por diseño a 4 tools seguras — el diseño ya es deny-by-default en varios puntos; la policy lo hace tenant-controlable.
- YOLO (`/yolo`, `--yolo`, `HERMES_YOLO_MODE`, `approvals.mode: off`) es el bypass central que una policy de org debe vetar → key `approval_mode` con STRING_RANKS.
- Los subagentes heredan los toolsets del padre y no pueden escalar: el veto del plugin se propaga solo si corre también en los children (verificar en implementación).
