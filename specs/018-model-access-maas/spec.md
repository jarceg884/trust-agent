# Spec 018 — Model Access sobre la documentación oficial de Huawei MaaS + policy

| Campo | Valor |
|---|---|
| **Padre** | specs/005-governance (REQ-MDL/REQ-POL, D-GOV-11) · specs/009-admin-console (REQ-AC-015) · feedback Miller #6 |
| **Alcance** | Rehacer el catálogo global de modelos sobre los modelos REALES de Huawei Cloud MaaS (endpoint internacional), integrar la aprobación de modelos con la policy de gobernanza, y alinear el `model_list` de LiteLLM a los deployment names reales. |
| **Fuente** | Doc oficial MaaS intl: `support.huaweicloud.com/intl/en-us/model-list-maas/model_list_0001.html` (actualizada 2026-10-22), `…/price-maas/price-maas-0002.html` (actualizada 2026-10-26), `…/price-modelarts/price-modelarts-0047.html` (Qwen3-32B). Verificación en vivo: `GET {MAAS_ENDPOINT}/models` con la key "Inforum" (HTTP 200, 6 modelos). |
| **Reglas** | Nada simulado: cada precio/límite citado con su fuente oficial. USD siempre. SDD completo; paridad schema.sql ↔ migración (AC-DB-040). |

## Problema (feedback #6)

El catálogo `catalog.model_catalog` contenía `qwen2.5-72b-instruct` y `deepseek-v3` — nombres genéricos que NO existen como deployments del endpoint MaaS internacional actual. Un tenant que los aprobara no podría ejercer el acceso contra el gateway real. Además, la aprobación de modelos vivía aislada de la historia de policy en la UI y en `/api/policies`.

## 1. Catálogo real MaaS (D-018-01)

**Decisión**: el catálogo global se reconstruye con los modelos desplegados en el endpoint MaaS intl de la cuenta (`https://api-ap-southeast-1.modelarts-maas.com/openai/v1`, verificado en vivo con la key "Inforum"): `glm-5.3`, `glm-5.2`, `glm-5.1`, `deepseek-v4.1-flash`, `deepseek-v4-flash`, `qwen3-32b`. Los dos nombres genéricos se eliminan (con sus filas de `model_access`; el histórico de `spend_ledger` referencia nombres como texto y se conserva).

**Precios USD por millón de tokens** (columnas existentes en centavos; se migran a `numeric(12,3)` para soportar precios fraccionales oficiales). Convención: **peor caso** — Periodo 1 (08:00–20:59 GMT+8) cuando hay pricing diferencial, tier ≥32K cuando hay pricing por longitud, y patrón reasoning cuando hay pricing por patrón. Misma convención que `litellm-real/config.yaml` (t_c4766c4d).

| model_name (deployment) | display_name oficial | Contexto | Máx. output | Input ¢/M | Output ¢/M | Fuente precio |
|---|---|---|---|---|---|---|
| `glm-5.3` | GLM-5.3 | 1M | 128K | 140 | 440 | price-maas-0002 (sin diferencial) |
| `glm-5.2` | GLM-5.2 | 1M | 128K | 140 | 440 | price-maas-0002 P1 |
| `glm-5.1` | GLM-5.1 | 198K | 128K | 107.8 | 377.4 | price-maas-0002 ≥32K P1 |
| `deepseek-v4.1-flash` | DeepSeek-V4.1-Flash | 1M | 384K | 30 | 120 | price-maas-0002 P1 |
| `deepseek-v4-flash` | DeepSeek-V4-Flash | 1M | 384K | 13.5 | 27 | price-maas-0002 (sin diferencial) |
| `qwen3-32b` | Qwen3-32B | NULL* | NULL* | 27 | 269.5 | price-modelarts-0047 (reasoning) |

\* `qwen3-32b` está desplegado y cobra (pricing oficial ModelArts Studio), pero NO figura en la página oficial "Model List" intl — sin límite de contexto documentado, las columnas quedan NULL y la UI renderiza "—". Notación de contexto según doc oficial: K=1,000, M=1,000,000.

Columnas nuevas en `catalog.model_catalog`: `context_tokens bigint NULL`, `max_output_tokens bigint NULL`.

> **AC-018-01**: tras la migración, `GET /api/models` devuelve exactamente los 6 deployments reales con display_name oficial, contexto, máx. output y precios USD/M oficiales (glm-5.2 = 140/440); `qwen2.5-72b-instruct` y `deepseek-v3` ya no existen; los nombres coinciden 1:1 con `GET {MAAS_ENDPOINT}/models` en vivo.
>
> _Enmienda 2026-10-27 (§5, D-018-07): el conjunto vivo cambió — `qwen3-32b` retirado (404 upstream), `deepseek-v4.1-flash-1017` dado de alta. El invariante 1:1 con `GET /models` vivo se mantiene; ahora lo custodia el refresh central, no solo las migraciones._

## 2. Integración con policy (D-018-02)

**Decisión**: la aprobación de modelos ES policy de gobernanza. El conjunto efectivo ya es `org ∩ dept ∩ user ∩ policy.allowed_models` (deny-overrides, `effective_models_for_user`). Esta spec lo hace visible:

- `GET /api/models/access` incluye una sección `policy`: valor vigente de `allowed_models` a nivel org (null = sin restricción adicional) y el floor invariable `approved_models_only: true` leído de `catalog.policy_keys`.
- `GET /api/policies?level=…` incluye `model_approvals`: los modelos aprobados a ese nivel/scope (nombre, modo, estado) — la matriz de aprobación queda expuesta como parte de la policy del nivel, no como un menú aparte.
- UI Model Access: columna Context, y bloque "Governance policy" que muestra `approved_models_only` + `allowed_models` vigente y explica la intersección deny-overrides (org → dept → user → policy).

> **AC-018-02**: `GET /api/policies?level=org` devuelve `model_approvals` con los modelos aprobados a nivel org del tenant; `GET /api/models/access` devuelve `policy.allowed_models` (null si no hay policy) y `policy.approved_models_only === true`; la UI muestra ambos.

### 2.1 Enforcement de `allowed_models` alineado con el combinator (D-018-05, fix t_f0d3122d)

**Decisión**: el gate `effective_models_for_user` aplica la MISMA semántica deny-overrides que el combinator y el upsert — el fallo original (QA P4e/P4e1, `models/access.py` `or []` + truthiness) colapsaba el deny-all explícito a "sin restricción":

- `allowed_models` DEFINIDA en cualquier nivel (org/depto/usuario) → intersección con el conjunto de aprobaciones; `[]` = **deny-all explícito** (el valor más restrictivo que el upsert acepta, REQ-POL-007). `GET /api/models/allowed-for-me` = `[]` y `POST /api/models/verify` → 403 `model_not_approved` + audit `model.access_denied`.
- `allowed_models` AUSENTE en los 3 niveles → default de catálogo `null` (migración 0019; antes `[]`, indistinguible del deny-all) = sin restricción adicional; el usuario hereda la intersección de aprobaciones. La policy efectiva (`GET /api/me/policies`) muestra `null`, la misma convención de esta spec para `GET /api/models/access` ("null = sin restricción adicional").
- Niveles de aprobación (depto/usuario) con filas en `model_access` de CUALQUIER status participan en la intersección con su conjunto aprobado: revocar la última aprobación de un nivel deja el nivel en deny-all (una revocación jamás relaja).

> **AC-018-05** (= AC-GOV-036 de spec 005): org aprueba `glm-5.2` con policy org `["glm-5.2"]` y policy de usuario `[]` → allowed-for-me del usuario = `[]`, verify → 403; policy org `[]` → deny-all del tenant; sin filas de la clave → `allowed_models: null` en la efectiva y herencia de aprobaciones; depto con única aprobación revocada → miembros en deny-all.

### 2.2 Resolución server-side de `department_id` a nivel usuario (D-018-06, fix t_f75e6a18 / GAP M-1)

**Decisión**: el constraint `model_access_scope_check` de la DB exige para `level='user'` una fila con `department_id IS NOT NULL AND user_id IS NOT NULL` (y para `level='dept'`, `department_id IS NOT NULL`). El body del `PUT /api/models/access` NO es quien define el depto: el backend lo resuelve. Contrato:

- `level='user'`: `user_id` es obligatorio (ausente → 422). `department_id` en el body es **opcional e ignorado como fuente de verdad**: `approve()`/`revoke()` resuelven el departamento REAL del usuario desde `users` (mismo tenant; un `user_id` de otro tenant → 404, no un FK 500). Si el body trae un `department_id` que NO coincide con el depto real del usuario → 422 (una aprobación anclada a un depto equivocado sería silenciosamente inefectiva: la intersección de `effective_models_for_user` consulta por el depto del principal).
- Usuario SIN departamento (`users.department_id IS NULL`, p.ej. el admin recién provisionado): user-level approval/deny no es representable en `model_access` (el constraint exige depto) → 422 con mensaje explícito. La restricción por usuario sin depto queda vía policy `allowed_models` a nivel usuario.
- `level='dept'` sin `department_id` → 422 (antes: CheckViolation → 500). `level='org'` con `department_id` o `user_id` en el body → 422 (antes: 500): el nivel org es global por definición.
- La UI (`models-page.tsx`) manda `department_id: null` para nivel user — queda VÁLIDA sin cambios: el backend resuelve.

> **AC-018-06**: contra la DB real (constraint vivo): `PUT /api/models/access {level: user, user_id}` sin `department_id` → 201 y la fila queda con el depto real del usuario; revoke simétrico → 200; `department_id` mismatch → 422; usuario sin depto → 422; `level=dept` sin depto → 422; `level=org` con scope → 422; `user_id` cross-tenant → 404. Ningún camino de approve/revoke produce 500 por `model_access_scope_check`.

## 3. LiteLLM alineado (D-018-03)

**Decisión**: `litellm-real/config.yaml` declara los 6 deployment names reales con pricing real por token (USD, peor caso P1) y `max_tokens` = contexto documentado (`qwen3-32b` sin `max_tokens`: no documentado). LiteLLM sigue siendo el único gateway; el catálogo de la plataforma y el `model_list` del proxy comparten fuente (doc oficial MaaS).

> **AC-018-03**: el proxy LiteLLM real arranca con el `model_list` nuevo; `GET /v1/models` del proxy lista los 6 deployments; una invocación `chat/completions` de `glm-5.2` a través del proxy responde desde MaaS real (evidencia en vivo).

## 4. Skill reutilizable (D-018-04)

Hallazgo capturado como skill: `skills/llm-gateway/huawei-maas-models/SKILL.md` — catálogo, precios USD, límites, endpoint intl, pricing diferencial por horario, y cómo verificar en vivo contra `/models`. Regla 3 de CONTEXTO-TAREAS.md.

> **AC-018-04**: el skill existe con los 6 modelos, precios con fuente, límites y comando de verificación en vivo.

## 5. Sincronía catálogo ↔ `/models` vivo (D-018-07, fix GAP M-2 / t_bf7eff20)

**Contexto (verificación en vivo 2026-10-27T20:55Z)**: `GET {MAAS_ENDPOINT}/models` (key del proveedor) → HTTP 200 con 6 modelos: `['deepseek-v4-flash', 'deepseek-v4.1-flash', 'deepseek-v4.1-flash-1017', 'glm-5.1', 'glm-5.2', 'glm-5.3']` — SIN `qwen3-32b` (llamada real → 404 `ModelArts.81009 "Invalid model."`). La deriva es BIDIRECCIONAL: el mismo día en que qwen3-32b desapareció apareció el deployment nuevo `deepseek-v4.1-flash-1017`. Un catálogo mantenido solo por migraciones contra la doc deriva en cuanto MaaS re-despliega; el GAP M-2 lo probó (un tenant que aprobaba qwen3-32b recibía 404 en cada uso).

**Decisión**: la fuente autoritativa del CONJUNTO de modelos centrales es el `GET {MAAS_ENDPOINT}/models` VIVO (no la doc estática ni la tabla de routing del gateway); la doc oficial sigue siendo la fuente autoritativa de metadatos (precios/límites). El refresh central (spec 032 §3: endpoint admin `POST /api/models/catalog/refresh` + job periódico `model.catalog_refresh`) reconcilia el catálogo central contra el upstream en cada corrida:

- **Retiro**: modelo central `available` ausente del upstream → `status='stale'` (fail-closed: fuera de `allowed-for-me`, `verify` → 403 `model_not_approved`, plan `standard` deja de aprobarlo — el provisioning resuelve `WHERE status='available'`). Las filas de `model_access` existentes se CONSERVAN (historia de aprobaciones; el gate las vuelve inefectivas). Si el modelo re-aparece upstream, un refresh posterior lo restaura a `available`.
- **Alta**: modelo del upstream ausente del catálogo → INSERT `source='central'`, `status='available'`, `display_name` = upstream id, precios/límites NULL (desconocidos hasta que una migración los selle contra la doc oficial — mismo contrato que BYOK declarado-0: coste desconocido ⇒ spend 0) y registro inmediato en el gateway vía `POST /model/new` (api_base = endpoint MaaS, key del proveedor de env) para que sea enrutable desde el primer refresh. El payload de audit `model.catalog_refreshed` reporta `{scope: central, stale, restored, added: [...]}` — la lista `added` es el disparador operacional de la migración de sellado de precios.
- **Chequeo de routing del gateway** (spec 032, se mantiene): modelo central que el gateway ya no enruta → `stale`.
- El chequeo upstream requiere `TRUST_MAAS_ENDPOINT` + `TRUST_MAAS_API_KEY` (env del control plane; la key jamás en respuestas/logs/audit). Sin esa config, el refresh central degrada al chequeo de routing existente (nunca falla abierto: no marca `available` nada que no haya visto).
- El job periódico `model.catalog_refresh` se encola para TODOS los tenants activos (antes: solo los con conexiones `llm` activas) — la deriva central afecta a todos.

**Retiro inmediato (migración `retire_qwen3_32b`)**: `qwen3-32b` → `stale` (verificado 404 en vivo; conservando sus `model_access`), y alta de `deepseek-v4.1-flash-1017` con metadatos oficiales ya documentados: display `DeepSeek-V4.1-Flash-1017`, contexto 1M, máx. output 384K (Model List intl, actualizado 2026-10-27 GMT+8), precios peor caso P1 30/120 ¢/M (price-maas-0002, actualizado 2026-10-27 GMT+8). `litellm-real/config.yaml` pierde el bloque `qwen3-32b` y gana el de `deepseek-v4.1-flash-1017` (AC-018-03: model_list = deployments vivos).

> **AC-018-07**: (a) `GET {MAAS_ENDPOINT}/models` en vivo citado con fecha coincide 1:1 con los modelos centrales `available` de `catalog.model_catalog` (DB real); (b) `qwen3-32b` está `stale` y el plan `standard` de un tenant nuevo NO lo aprueba; (c) para CADA modelo central `available` del catálogo, una llamada real `chat/completions` vía gateway responde 200 o 403 por policy — nunca 404 de modelo inexistente; (d) un refresh con el upstream devolviendo un subconjunto marca los ausentes `stale` y reporta `added` para los nuevos (test + evidencia en vivo).

## Out of scope

- BYOK por modelo contra MaaS (el modo ya existe en `model_access.mode`).
- Pricing por período/tier en tiempo de billing: el costo del ledger lo calcula LiteLLM con su cost map (peor caso); el diferencial horario se documenta en el skill.
- Modelos de image understanding / embeddings (MaaS los ofrece; el producto hoy solo gobierna chat).
