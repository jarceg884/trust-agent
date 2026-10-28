# Spec 029 — Catálogo de policies v2: 29 keys tipadas de la matriz de capacidades (mandato Miller)

> Estado: **specify** · Tarea kanban: t_fb62560f · Fecha: 2026-10-27
> Idioma: español (docs) / inglés (código y UI).
> Fuente de requisitos: `docs/hermes-capability-matrix.md` §3 (P-1..P-29, t_d8ed9362) —
> cada key cierra el gap de una o más capacidades citadas (C-001..C-062).

## 1. Problema y mandato

La matriz de capacidades (t_d8ed9362) inventarió 62 capacidades de Hermes Agent contra
los 20 mecanismos nativos de limitación y las 18 policy keys existentes del catálogo
tipado `catalog.policy_keys`. Bajo el principio **deny-by-default** (toda capacidad de
Hermes sin policy key es un gap), quedaron identificadas **29 keys NUEVAS requeridas**
(P-1..P-29). Esta spec las incorpora al catálogo tipado con la MISMA maquinaria de
REQ-GOV-020..024 (migración 0011): catálogo tipado + combinator deny-overrides +
no-relajación entre niveles (REQ-POL-007) + validación 422.

El enforcement de estas keys en el plugin/bundle es objeto de las lanes paralelas
(t_7e2852f8 batch 1, t_a868974a batch 2, t_150e8931 bundle JWS extensible); esta spec
es SOLO catálogo + semántica de combinación + validación de upsert.

## 2. Requisitos (EARS)

- **REQ-029-001** (catálogo): el SISTEMA DEBE incorporar a `catalog.policy_keys` las
  29 keys de la §4 con tipo, default de proveedor e `invariable = false`, vía migración
  Alembic creada con `migration_guard.py` (ADR 0001) y en paridad exacta con
  `db/schema.sql` (AC-DB-040).
- **REQ-029-002** (tipos y defaults): los tipos y defaults DEBEN ser exactamente los de
  la §4 (tabla P-1..P-29 de la matriz). En particular: `allowed_skills` default
  `["*"]` (corrige su ausencia del catálogo pese a estar referenciada por spec 001
  REQ-PRF-005) y `allowed_providers` default `["litellm_gateway"]`.
- **REQ-029-003** (enum por ranks): `approval_mode` DEBE validarse como string enum
  `off|smart|manual` y combinarse por `STRING_RANKS` `manual(2) > smart(1) > off(0)`
  (el nivel más restrictivo gana; un nivel inferior no puede bajar el rank).
- **REQ-029-004** (pisos enteros): `max_delegation_depth` DEBE tener piso 0 y
  `session_retention_days` piso 1 (upsert con valor inferior → 422), además de la
  combinación por mínimo (el valor efectivo es el más bajo de los niveles).
- **REQ-029-005** (web_domain_policy): la key DEBE ser un map `{mode:
  open|allowlist|blocklist, domains: [strings]}` validado estructuralmente (422 si el
  modo o domains son inválidos) y combinado por **deny-overrides por modo**: si algún
  nivel fija `allowlist`, el modo efectivo es `allowlist` con la INTERSECCIÓN de las
  allowlists (menos los dominios bloqueados por cualquier nivel); si no hay allowlist
  pero algún nivel fija `blocklist`, el modo efectivo es `blocklist` con la UNIÓN de
  las blocklists; solo si todos los niveles están en `open` el modo efectivo es `open`.
- **REQ-029-006** (booleanos direccionales): los booleanos DEBEN combinarse según la
  dirección de restrictividad: keys `allow_*` (false = restrictivo) con AND lógico
  (false gana); keys `require_*` (`require_write_sandbox`, `require_checkpoints`,
  true = restrictivo) con OR lógico (true gana).
- **REQ-029-007** (lista deny por unión): `denied_command_patterns` DEBE combinarse
  por UNIÓN (más patrones denegados = más restrictivo); el resto de las listas
  (`allowed_*`, `allowed_env_passthrough`) por INTERSECCIÓN como hoy.
- **REQ-029-008** (no-relajación): la regla REQ-POL-007 aplica a las 29 keys sin
  cambios de maquinaria: un valor de depto/usuario que relaje el valor vigente del
  nivel superior DEBE responder 422.
- **REQ-029-009** (validación): key fuera del catálogo → 422 (REQ-POL-003); tipo
  incorrecto → 422; valor fuera del enum (`approval_mode`, `web_domain_policy.mode`)
  → 422; entero por debajo del piso → 422.
- **REQ-029-010** (bundle JWS): las 29 keys DEBEN fluir en la policy efectiva
  (`effective_policy` es genérica sobre el catálogo) y el bundle JWS (spec 019
  REQ-GOH-002) las incorpora como claims ADITIVOS sin romper los obligatorios §5.2 —
  schema versionado que tolera keys desconocidas (coordinado con t_150e8931 y
  t_f99e12ab; el verificador PyJWT ignora claims desconocidos).
- **REQ-029-011** (no duplicación): esta spec NO añade keys de conectores
  (`allowed_connectors`, `allowed_connections`, `vault_allowed_credential_types` ya
  existen — t_b16825d0/spec 024).

## 3. Decisiones de diseño

- **D-29-01 — combinator key-aware**: la dirección de restrictividad deja de ser solo
  por tipo y pasa a ser por (tipo, key): `TRUE_WINS_KEYS` (OR) para los `require_*`
  nuevos, `DENY_UNION_KEYS` para `denied_command_patterns`, y un combinador de map
  específico para `web_domain_policy` (el map genérico sigue siendo el de
  `messaging_permissions`). Las keys existentes NO cambian de semántica.
- **D-29-02 — defaults deny-by-default**: las keys de allowlist de capacidades
  extensivas (`allowed_mcp_servers`, `allowed_plugins`, `allowed_env_passthrough`,
  `allowed_command_patterns`, `denied_command_patterns`) nacen con `[]` (deny-all /
  sin denios extra según el sentido), y los flags de capacidades sensibles
  (`allow_*`) nacen en `false`: un tenant nuevo no puede usar esas capacidades hasta
  que su admin las habilite. `allowed_skills` nace `["*"]` por compatibilidad con
  spec 001 REQ-PRF-005.
- **D-29-03 — pisos en policies.py**: los pisos enteros viven en `INTEGER_FLOORS`
  (policies.py) junto a `offline_grace_minutes`/`attestation_overdue_hours`; el enum
  de `approval_mode` vive en `STRING_RANKS` (combinator.py), que ya sirve de fuente
  de enum válido para `_validate_value`.
- **D-29-04 — validación estructural del map**: `web_domain_policy` se valida en
  `_validate_value` (modo enum + domains lista de strings) porque el tipo `map`
  genérico no alcanza.
- **D-29-05 — migración 0022**: una sola migración `0022_policy_catalog_v2` con
  INSERT ... ON CONFLICT (key) DO NOTHING de las 29 keys y downgrade DELETE exacto,
  creada bajo lock con `migration_guard.py` (head actual: `0021_hermes_download`).

## 4. Las 29 keys (matriz §3, P-1..P-29)

| P# | Key | Tipo | Default | Combina como |
|----|-----|------|---------|--------------|
| P-1 | `approval_mode` | string enum `off\|smart\|manual` | `"smart"` | STRING_RANKS manual(2)>smart(1)>off(0) |
| P-2 | `allowed_terminal_backends` | list | `["docker"]` | intersección |
| P-3 | `denied_command_patterns` | list (globs fnmatch) | `[]` | unión (deny-overrides) |
| P-4 | `allowed_command_patterns` | list | `[]` | intersección |
| P-5 | `require_write_sandbox` | boolean | `true` | OR (true gana) |
| P-6 | `allowed_mcp_servers` | list | `[]` | intersección |
| P-7 | `allowed_plugins` | list | `[]` | intersección |
| P-8 | `allowed_skills` | list | `["*"]` | intersección |
| P-9 | `allow_scheduled_tasks` | boolean | `false` | AND |
| P-10 | `allow_recurring_prompts` | boolean | `false` | AND |
| P-11 | `allow_goal_mode` | boolean | `false` | AND |
| P-12 | `max_delegation_depth` | integer | `1` (piso 0) | mínimo |
| P-13 | `allowed_memory_providers` | list | `["builtin"]` | intersección |
| P-14 | `allow_custom_personality` | boolean | `false` | AND |
| P-15 | `allowed_secret_sources` | list | `["env"]` | intersección |
| P-16 | `allow_credential_vault` | boolean | `false` | AND |
| P-17 | `allow_payment_autofill` | boolean | `false` | AND |
| P-18 | `allowed_env_passthrough` | list | `[]` | intersección |
| P-19 | `web_domain_policy` | map `{mode, domains}` | `{"mode":"open","domains":[]}` | deny-overrides por modo (REQ-029-005) |
| P-20 | `allow_private_network_access` | boolean | `false` | AND |
| P-21 | `allow_voice_mode` | boolean | `false` | AND |
| P-22 | `allow_bot_mode` | boolean | `false` | AND |
| P-23 | `allow_gateway_hooks` | boolean | `false` | AND |
| P-24 | `allow_shell_hooks` | boolean | `false` | AND |
| P-25 | `allow_profile_distributions` | boolean | `false` | AND |
| P-26 | `allowed_providers` | list | `["litellm_gateway"]` | intersección |
| P-27 | `session_retention_days` | integer | `30` (piso 1) | mínimo |
| P-28 | `require_checkpoints` | boolean | `true` | OR (true gana) |
| P-29 | `allow_self_updates` | boolean | `false` | AND |

## 5. Criterios de aceptación

- **AC-029-1** (upsert válido): PUT de cada tipo de key nuevo (string enum, list,
  boolean require/allow, integer, map) en org responde 200 y versiona.
- **AC-029-2** (no-relajación): un PUT de depto/usuario que relaje el nivel superior
  (bajar rank de `approval_mode`, quitar un deny de `denied_command_patterns`,
  apagar un `require_*` del org, ensanchar una allowlist, subir un mínimo) → 422.
- **AC-029-3** (token/key desconocido): key fuera de catálogo → 422; valor fuera del
  enum (`approval_mode: "yolo"`, `web_domain_policy.mode: "custom"`) → 422; entero
  bajo piso → 422.
- **AC-029-4** (deny-overrides 3 niveles): la policy efectiva de un usuario con los
  tres niveles fijados refleja la combinación más restrictiva por key: intersección
  de listas, unión de denies, AND/OR booleanos, mínimo de enteros, rank máximo de
  `approval_mode` y deny-overrides por modo de `web_domain_policy`.
- **AC-029-5** (paridad): `db/tests/test_schema_parity.py` pasa (schema.sql ↔
  migración producen el mismo esquema) y `migration_guard.py check` OK con única
  cabeza.
- **AC-029-6** (bundle): el bundle JWS sigue verificando con el verificador del
  plugin (claims §5.2 intactos); la inclusión aditiva de las keys nuevas queda
  registrada para t_150e8931/t_f99e12ab.
