# Revisión E2E Pilar 2 — Gobernanza: budgets jerárquicos, policies y model access sobre MaaS oficial

**Tarea**: t_a296ae9b (hija de t_1c65ff67, revisión maestra t_ef306f32) · **Perfil**: qa · **Fecha**: 2026-10-26/27
**Alcance**: puntos 3, 4 y 5 del alcance E2E del console (los otros pilares no se tocaron).

## Entorno y método (nada simulado)

- **Infra viva**: API `:8000`, Admin Console `:3100`, Keycloak 26 `:8080`, **proxy LiteLLM REAL `:4000`** (→ Huawei Cloud MaaS internacional `https://api-ap-southeast-1.modelarts-maas.com/openai/v1`), PostgreSQL `:55432`, spend-worker real. El control plane vivo usa `TRUST_LITELLM_BASE_URL=http://127.0.0.1:4000` (proxy real, no el doble de tests).
- **Tenant fresco dedicado**: `qa-gov-e2e` (QA Governance E2E Corp), aprovisionado real vía `POST /api/admin/tenants` (201, realm KC real, 4 deptos seed, budget org $100, plan `standard`). Admin + 2 workers (`w1` en IT Operations, `w2` en Cloud Operations) invitados por `POST /api/users` (jobs reales) y logueados con **PKCE S256 + enrolamiento/verificación TOTP real (acr=2)**.
- **Console por navegador real**: login PKCE+TOTP por la UI (`/login` → org code → Keycloak → TOTP), flujos de edición/creación/borrado en Budgets, Organization y Model Access.
- **Scripts reproducibles**: `docs/evidencia/pilar2-governance/` (`gov_setup.py`, `gov_a_budgets_models.py`, `gov_b_cascada.py`, `gov_c_policies.py`, `maas_directo.py`). Estado de la corrida: `gov_state.json` en el workspace de la tarea.
- **Llamadas REALES contra MaaS oficial**: 11 chat completions facturadas a través del gateway de la plataforma + 1 llamada directa al endpoint MaaS oficial con la key del proveedor + `GET {MAAS}/models` en vivo.

---

## Punto 3 — Budgets jerárquicos → **GAP PARCIAL**

El núcleo (creación desde el console, jerarquía, propagación y aplicación efectiva de límites con corte real) **funciona con evidencia en vivo**; hay 1 bug de borde en `/api/budgets/me` y 1 limitación de granularidad de costos que debilita la aplicación efectiva de límites con precios reales de MaaS.

### Evidencia VERIFICADA

**Creación y edición desde el console (UI real, navegador)**
- `/budgets` renderiza la jerarquía completa: Organization $100.00 (used $0.01) → Cloud Operations $20 (w2 $10) → IT Operations $20 (used $0.01, w1 $10), con remanente no asignado por depto ("$10.00 of the department budget remains unallocated").
- Edición desde la UI: IT Operations $20 → $25 → persistido en DB (`budgets.amount_cents = 2500`), toast "IT Operations budget updated." (restaurado a $20 después).
- **El padre restringe a los hijos visible en la UI**: intentar IT=$90 muestra inline el 422 real: `Department budgets would exceed the organization budget by 1000 cents (Σ departments = 11000 > org = 10000).`

**Jerarquía y validaciones (API)**
- `PUT /api/budgets/dept/{A}` $20 → 200; `PUT dept A $90` → **422** (Σ $110 > org $100, detalle del exceso).
- `PUT /api/budgets/user/{w1}` $10 → 200; `PUT user w1 $25` → **422** (`User budgets of the department would exceed the department budget by 500 cents.`).
- `GET /api/budgets/overview` → `unassigned_cents = 6000` (org − Σ deptos, verificado aritméticamente).
- Reducción bajo gasto sin `force` → 422; con `force` → 200 + audit `budget.forced_below_spend`.

**Aplicación efectiva con spend REAL de MaaS (propagación org→depto→usuario)**
- 11 llamadas reales vía proxy `:4000` → MaaS oficial (200 con contenido y usage reales; ej. glm-5.2: `usage {'completion_tokens': 98, 'reasoning_tokens': 96}`).
- Spend worker real ingiere: `spend_ledger` con filas por request (modo `central`), `budget_counters` de los 3 niveles descontados (user w1 1¢, dept IT 1¢, org 1¢), `overview.spent_cents = 1`, `/api/budgets/me` refleja el gasto.
- **Cascada de cortes con keys REALES bloqueadas en el gateway real**:
  - **user**: budget w1 → $0 (force) → worker → key w1 `blocked` → **401 real** del gateway (`Key is blocked. Update via /key/unblock…`); key w2 sigue 200; notificación `budget_cutoff` a w1; audit `budget.cutoff` + `model.key_blocked`.
  - **dept**: depto IT → $0 (force) → key w1 bloqueada por el corte de su DEPTO; **w2 (Cloud Operations) sigue invocando 200** (aislamiento entre hermanos).
  - **org**: org → $0 (force) → keys w1 Y w2 bloqueadas; notificación `budget_cutoff` al org_admin; 401 real para w2.
  - **recuperación**: subir presupuesto → worker → keys `active` de nuevo → llamadas reales 200. Audit final: `budget.cutoff` ×4, `budget.forced_below_spend` ×4, `model.key_blocked` ×4, `model.key_unblocked` ×4.

### GAPS (bugs reportados)

**BUG B-1 — `GET /api/budgets/me` reports `cutoff: false` while the user is cut off (budget = $0)**
- **Steps**: (1) usuario con spend real ≥ 1¢; (2) `PUT /api/budgets/user/{id} {"amount_cents": 0, "force": true}`; (3) correr spend worker (key bloqueada, notificación enviada, audit emitido); (4) `GET /api/budgets/me`.
- **Expected**: `cutoff: true, cutoff_level: "user"` (AC-GOV-012).
- **Actual**: `"user": {"budget_cents": 0, "spent_cents": 1, "remaining_cents": -1, "cutoff": false}, "cutoff": false, "cutoff_level": null` — capturado en vivo.
- **Root cause**: `app/governance/budgets.py` `_level_status` usa `budget["amount_cents"] > 0 and spent >= budget["amount_cents"]`; el guard `> 0` (pensado para budget ausente) excluye el 0 explícito. El enforcement (`spend.py`) no tiene ese guard → corte sí, estado no. Solo manifiesta con `amount = 0`.

**BUG B-2 (limitación) — sub-cent real spend is truncated to 0¢ in ledger/counters**
- **Steps**: llamadas reales pequeñas vía el gateway (ej. 174 tokens deepseek-v4-flash = $0.000045) → correr spend worker → consultar `spend_ledger`.
- **Expected**: el gasto real se acumula (aunque sea fraccionario) y descuenta de los presupuestos.
- **Actual**: 9 de 10 filas del ledger con `cost_base_cents = 0`; w2 hizo 4 llamadas reales y sus counters user/depto quedaron en 0.0. Solo la llamada ≥ medio centavo (1¢) movió counters.
- **Root cause**: `app/governance/spend_worker.py:87` `cost_base_cents = round(log.spend_usd * 100)` + columnas `bigint` en `spend_ledger` (schema §439-440). Con precios reales de MaaS (fracciones de centavo por llamada), el presupuesto sub-conta gasto real → el corte llega tarde o nunca para uso de requests chicos. La spec 018 migró los PRECIOS del catálogo a `numeric(12,3)` pero el ledger/counters siguen en centavos enteros.

**Observación menor**: los requests denegados por el gateway (403 modelo no aprobado) también ingresan al ledger con costo 0 (fila `qwen3-32b` tokens 0) — auditable pero ruido en el ledger.

---

## Punto 4 — Policies → **VERIFICADO OK** (1 bug menor de contrato API en DELETE user)

### Evidencia VERIFICADA

**CRUD desde el console (UI real, navegador)**
- `/organization` renderiza la tabla de policies con versiones: `allowed_models v1`, `require_mfa v2`, invariables `approved_models_only/explicit_shared_agent_grants/scoped_credentials v1`.
- **Crear desde la UI**: `New policy` → key `allowed_connectors`, tipo JSON, valor `["vmware","email"]` → guardado v4, toast `Policy "allowed_connectors" saved.`, fila visible en la tabla.
- **Borrar desde la UI**: `Remove` → confirmación ("This action is audited.") → toast `Policy "allowed_connectors" removed.` → la fila desaparece de la tabla.

**Validaciones y versionado (API)**
- Clave fuera de catálogo → 422 (`Unknown policy key: 'no_such_key' (typed catalog only, REQ-POL-003)`); tipo incorrecto (`session_timeout_minutes` = string) → 422 (`expects an integer`).
- **No-relajación**: org `[vmware,email]`, depto intenta `[vmware,email,slack]` → 422 (`Department policy would relax the organization policy…`); depto estrecha a `[vmware]` → 200.
- **Invariables**: `approved_models_only` (dept) y `explicit_shared_agent_grants` (user) → 422 (`provider invariable (floor)`). NOTA: `require_mfa` **ya NO es invariable** — spec 015 D-AUTH-07 lo hizo variable (`catalog.policy_keys.invariable = f`, default `false`); `PUT require_mfa=false` org → 200 es comportamiento correcto actual (verificado en el catálogo y en el flujo de login: con `require_mfa=true` el BFF pide `acr_values=2`).
- **Versionado**: dos PUT sucesivos → `version 1`, `version 2`; audit `policy.updated` ×14 en el tenant.

**Aplicación efectiva sobre usuarios (deny-overrides)**
- `GET /api/me/policies` w1 → `allowed_connectors: ["vmware"]` (org ∩ depto).
- `GET /api/connectors/available` w1 → **solo vmware** (efecto real); w2 (depto sin policy propia) hereda org → vmware + email.
- Policy de usuario deny-all: `PUT user w1 allowed_connectors []` → 200; w1 ve **0 conectores**; w2 no afectado. Delete de la policy de usuario → w1 vuelve a `[vmware]`. Delete de la policy org → la efectiva cae al default manteniendo el piso del depto.

**Aplicación efectiva sobre agentes**
- w1 publica agente (`POST /api/agents` → 201) → `invoke` con `model_name: qwen3-32b` (no efectivo) → **403 `model_not_approved`**; con `glm-5.2` → 200 `billed_to_user_id` = w1. El gate de modelos del invoke aplica la policy engine.

**Exposición como gobernanza (spec 018)**
- `GET /api/policies?level=org` incluye `model_approvals` (lista de aprobaciones org con modo/estado); `GET /api/models/access` expone `policy.allowed_models` vigente + `approved_models_only: true`.

### GAP menor

**BUG B-3 — `DELETE /api/policies/user/{key}` returns 404 unless `department_id` is passed explicitly**
- **Steps**: `PUT /api/policies/user/{user_id} {"key": "allowed_models", "value": ["glm-5.2"]}` → 200 (la fila se crea con el `department_id` del usuario resuelto por el upsert); luego `DELETE /api/policies/user/allowed_models?user_id={user_id}` → **404** `Policy not found at this level.`
- **Expected**: 204 (el PUT resuelve el depto solo; el DELETE debería hacer lo mismo).
- **Actual**: 404. **Root cause**: `app/governance/policies.py` `deactivate` filtra `(department_id IS NULL) = :dept_null`; sin `department_id` en la query, `dept_null=true` no matchea filas user (que tienen depto). Workaround verificado: `DELETE ...?user_id=…&department_id=…` → 204. La UI del console solo borra policies org → no expuesto por UI, pero es un defecto de contrato API (asimetría PUT/DELETE).

---

## Punto 5 — Model access sobre MaaS oficial → **GAP PARCIAL**

El flujo central está **verificado con llamadas reales contra el MaaS oficial** (catálogo con precios oficiales, aprobaciones org/depto con regla de subconjunto, keys virtuales reales, invocaciones 200/403 reales, spend real ingestado). Dos gaps reales: la aprobación a nivel **usuario** está rota (500) y el catálogo tiene **deriva contra el MaaS vivo** (qwen3-32b ya no existe).

### Evidencia VERIFICADA

**Catálogo = modelos y precios oficiales**
- `GET /api/models` → 6 deployments con display names, contexto y precios oficiales: `glm-5.2` = $1.40/M in, $4.40/M out (price-maas-0002 P1), `deepseek-v4-flash` = $0.135/$0.27, etc. — coincide con la doc oficial citada en spec 018.
- `GET {MAAS}/models` **en vivo** con la key del proveedor → HTTP 200: `['deepseek-v4-flash', 'deepseek-v4.1-flash', 'glm-5.1', 'glm-5.2', 'glm-5.3']` (5 modelos — ver GAP M-2).
- **Llamada directa al MaaS oficial** (sin gateway): `glm-5.2` → **200**, content `'OK'`, 72 tokens. El upstream es real y sirve los modelos del catálogo.

**Aprobaciones por nivel con regla de subconjunto**
- Provisioning (plan `standard`) aprueba los 6 modelos a nivel **org** (verificado en `model_access`).
- Aprobar depto ⊆ org → 201; **depto aprueba modelo retirado de org → 422** (`Subset rule violated: 'qwen3-32b' is not approved at organization level (REQ-MDL-005)`).
- **Retiro org → fuera de la lista efectiva**: tras revocar qwen3-32b en org, `allowed-for-me` de w1 la excluye (intersección).
- Lista efectiva por usuario: `allowed-for-me` w1 = org ∩ depto ∩ usuario; `POST /api/models/verify` aprobado → 200 `allowed: true`; no aprobado → **403 `model_not_approved`** + audit `model.access_denied`.
- Policy `allowed_models` estrecha la intersección (deny-overrides): policy user `["glm-5.2"]` → `allowed-for-me` = `[glm-5.2]` y verify de deepseek-v4-flash → 403 aunque esté aprobado; delete de la policy → restaurado.

**Keys virtuales REALES y llamadas REALES contra MaaS**
- `POST /api/models/keys` → 201 con **key real del proxy** (`sk-ft33…`, no fake), `max_budget_usd: 10.0`, `budget_duration: 30d`, `allowed_models` = lista efectiva; DB guarda SOLO `sha256(key)` (verificado).
- **Invocaciones reales**: `POST :4000/v1/chat/completions` con la key de w1 + `glm-5.2` → **200** con contenido real (`'OK'`) y usage real; modelo fuera de la lista de la key → **403** del gateway (`The requested model 'qwen3-32b' is not available for this API key…`).
- Spend real ingestado con mapeo correcto usuario/depto/org (el proxy real NO estampa `spend_logs_metadata` — null observado; el fallback por hash de key de D-GOV-05 funciona: filas del ledger con el `user_id` correcto).

**Console (UI real)**
- `/models` renderiza el catálogo con precios, contexto, badges de aprobación por nivel (org/dept) y el bloque "Governance policy" (`allowed_models (org): glm-5.2, deepseek-v4-flash`, `approved_models_only — enforced`).
- **Approve desde la UI**: qwen3-32b a nivel org → toast `Qwen3-32B approved at org level.`, badge `org` visible. **Revoke desde la UI**: confirmación → toast `Org-level approval for Qwen3-32B revoked.`, badge fuera.

### GAPS

**BUG M-1 (bloqueante para el nivel usuario) — Approving a model at USER level returns HTTP 500**
- **Steps**: `PUT /api/models/access {"model_name": "deepseek-v4-flash", "level": "user", "user_id": "<uuid>"}` (también desde la UI del console: Models → Approve → Level=User → UUID → Approve model).
- **Expected**: 201 (REQ-MDL-001/005, nivel user de la intersección).
- **Actual**: **HTTP 500** `internal_error` — desde la API y desde la UI (`An unexpected error occurred.`). Traceback del API: `psycopg.errors.CheckViolation: new row for relation "model_access" violates check constraint "model_access_scope_check"`.
- **Root cause**: el check de DB exige para `level='user'` `department_id IS NOT NULL AND user_id IS NOT NULL`, pero `app/models/access.py` `approve()` inserta el `department_id` del body (NULL cuando se aprueba por `user_id`); nunca resuelve el depto del usuario. La UI (`models-page.tsx`) también manda `department_id: null` para nivel user.
- **Impacto**: el nivel **usuario** de la matriz de aprobaciones no es operativo (no se puede aprobar NI denegar explícitamente por usuario vía model_access; el modo BYOK a nivel usuario tampoco). La restricción por usuario solo se logra vía policy `allowed_models`. Nota: la suite pytest pasa porque el doble de tests no reproduce el constraint igual que el schema vivo — el 500 solo se ve contra la DB real.

**BUG M-2 (deriva de catálogo) — `qwen3-32b` ya no existe en el MaaS oficial pero el catálogo lo sigue ofreciendo**
- **Steps**: `GET {MAAS_ENDPOINT}/models` en vivo → 5 modelos (sin `qwen3-32b`); llamada real `chat/completions` con `qwen3-32b` (directa y vía proxy) → **404** `ModelArts.81009 "Invalid model."`.
- **Expected**: catálogo 1:1 con los deployments vivos (AC-018-01, verificado 2026-10-26 con 6 modelos).
- **Actual**: `catalog.model_catalog` y el provisioning (plan `standard` aprueba los 6, incluida qwen3-32b) siguen ofreciendo un modelo que MaaS ya no sirve. Un tenant que la apruebe recibe 404 en cada uso. Requiere re-verificación en vivo del catálogo (y retiro/reemplazo de qwen3-32b) — la fuente oficial ya no la lista ni la sirve.

**Observaciones**
- `spend_logs_metadata` no viaja en el proxy real (null) — documentado y compensado por el fallback de hash (D-GOV-05); funciona.
- El margen MaaS (`maas_margin_bps`) de este tenant es 0 → `cost_billed == cost_base`; la multiplicación de margen no se ejercitó con margen ≠ 0 en esta corrida (sí está cubierta por la suite según validaciones previas).
- La UI de aprobación a nivel usuario pide "paste the user's UUID" a mano ("the user directory API is pending") — UX pendiente, coherente con spec 009/012.

---

## Resumen ejecutivo

| Punto | Estado | GAPS |
|---|---|---|
| 3. Budgets jerárquicos | **GAP PARCIAL** | B-1 (`/api/budgets/me` cutoff=false con budget $0), B-2 (spend sub-centavo truncado a 0¢ → límites sub-contan) |
| 4. Policies | **VERIFICADO OK** | B-3 menor (DELETE policy user sin `department_id` → 404, asimetría PUT/DELETE) |
| 5. Model access MaaS oficial | **GAP PARCIAL** | M-1 (aprobación nivel usuario → 500, bloqueante), M-2 (qwen3-32b ya no existe en MaaS, catálogo desalineado) |

**Verificado con llamadas reales contra MaaS oficial**: 11 invocaciones facturadas vía el gateway de la plataforma (200 con contenido/usage reales), 1 llamada directa al endpoint oficial, `GET /models` oficial en vivo, keys virtuales reales emitidas/bloqueadas/desbloqueadas contra el proxy real, y spend real ingestado en ledger/counters/overview. Nada simulado.

**No se crearon tareas ni se modificó el board** (según la orden de la tarea): los gaps quedan reportados aquí con repro para que la tarea de consolidación (t_9c6d892b) ejecute el protocolo de gaps.
