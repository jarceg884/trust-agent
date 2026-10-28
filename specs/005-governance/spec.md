# Spec 005 — Gobernanza: presupuestos jerárquicos, políticas, model access y agentes compartidos

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-23 |
| **Autor** | dev (tarea t_3b4fb364) |
| **Padre** | specs/001-platform/spec.md (REQ-BUD/POL/MDL/AGT), docs/arquitectura.md §5.3/§5.7/ADR-014 (contratos NORMATIVOS), specs/002-data-layer/spec.md (schema físico), specs/003-control-plane/spec.md (base transversal) |
| **Idioma** | Español (docs) / inglés (código, UI copy) / USD centavos (montos) |

---

## 1. Alcance

Esta spec implementa el **dominio de gobernanza** sobre el núcleo de spec 003: presupuestos jerárquicos con corte y notificación, API de políticas en 3 niveles, model access con integración LiteLLM (virtual keys por usuario) y agentes compartidos con grants explícitos.

**Dentro de alcance**:

- **Presupuestos jerárquicos** org→depto→usuario: upsert con validación de suma (REQ-BUD-002/003), overview con remanente no asignado (REQ-BUD-004), reducción bajo gasto con `force` (REQ-BUD-013), estado propio (`GET /api/budgets/me`).
- **Agregación de spend desde LiteLLM** (REQ-BUD-005): worker `spend-aggregator` que ingiere `/spend/logs/v2` con cursor incremental (`ops.spend_cursor`), escribe `spend_ledger` (idempotente por `request_id`) + `budget_counters` por nivel en la misma transacción, con margen MaaS por tenant (REQ-MDL-003) y modo central/BYOK (REQ-BUD-011).
- **Corte con notificación** (REQ-BUD-006/007/008): enforcement que bloquea/desbloquea virtual keys vía `/key/block`/`/key/unblock` (idempotente), emite `budget.cutoff` a audit y notificaciones de inbox; umbral 80 % (REQ-BUD-009) con notificación `budget_threshold_80` deduplicada por período; reset de período (REQ-BUD-010) por ventanas de `budget_counters` + desbloqueo.
- **API de políticas** (REQ-POL-001…007): upsert por nivel con claves del catálogo tipado, rechazo de invariables (422), versionado + audit, validación de que depto/usuario no relajen el nivel superior (REQ-POL-007), política efectiva por usuario (ya expuesta en `GET /api/me/policies` por spec 003).
- **Model access** (REQ-MDL-001/002/005/006): aprobación/retiro por nivel con regla de subconjunto, lista efectiva por usuario, verificación con 403 `model_not_approved` + audit, emisión de virtual keys LiteLLM por usuario con `max_budget`/`budget_duration` vinculados al presupuesto y `models` = lista efectiva; modo central/BYOK por aprobación.
- **Agentes compartidos** (REQ-AGT-001…006): publicar sin grants por defecto, grants explícitos `invoke`/`view`, listado scoped por grants, acceso sin grant → 404, revocación inmediata auditada, invocación facturada al invocador.
- **Notificaciones (inbox)**: tabla `notifications` + `GET /api/me/notifications` — mecanismo real de "corte con notificación" (arq. §2.3 "audit + inbox").
- **Evolución de capa de datos** (spec 002): migración `0004_governance` (apilada sobre `0002_login_throttle` y `0003_devices_enrollment_token` de tareas hermanas — única cabeza) + sección 9 de `schema.sql` con `agents`, `agent_grants`, `notifications` (RLS + grants idénticos al patrón existente) y nuevas acciones de audit en `catalog.audit_actions`.

**Fuera de alcance** (los implementan specs hermanas):

- Aprovisionamiento de tenants/usuarios y orquestación de keys en el flujo de invitación (→ spec 004-tenant-onboarding); esta spec entrega las primitivas (emisión de key, corte, notificación).
- API del vault, instancias de conectores, mensajerías y almacenamiento de keys BYOK cifradas (→ spec 006-vault-connectors). El **modo** BYOK (aprobación `mode='byok'`, ledger con `mode='byok'`, sin margen) SÍ está en alcance; el guardado de la key del cliente en el vault llega con spec 006 (REQ-MDL-007).
- Job `pricing_sync` (REQ-MDL-004): el pricing vigente se lee de `catalog.model_catalog`; la sincronización automática desde MaaS es de spec 004/operación.
- Health del gateway (REQ-MDL-008, prioridad S): se expone `LiteLLMClient.health()` como primitiva, sin pantalla.
- Overview histórico por período (REQ-BUD-012, S): los datos viven en `spend_ledger`; la vista histórica es de la spec de reporting.

## 2. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| D-GOV-01 | **Doble plano de presupuesto** (ADR-014): LiteLLM como mecanismo de corte nativo de primera línea (por key: `max_budget` + `budget_duration`) y el **ledger propio como fuente de verdad de gobernanza** (overview, corte de depto/org, notificaciones, margen, audit). El worker `spend-aggregator` (`python -m app.governance.spend_worker`) ingiere con cursor incremental y ejecuta el enforcement en el mismo ciclo. | ADR-014, REQ-BUD-005 |
| D-GOV-02 | **Bloqueo de keys por hash**: `/key/generate` devuelve la key en claro EXACTAMENTE una vez y su `token` (sha256 de la key); la plataforma guarda SOLO ese hash en `api_keys.litellm_key_hash` (schema §5.14) y bloquea/desbloquea con `/key/block`/`/key/unblock` pasando el **hash** — verificado contra docs/despliegues reales de LiteLLM (esos endpoints aceptan el hash como `key`). Sin hash almacenado no hay corte; sin key en claro no hay robo de credencial. | schema §5.14, docs LiteLLM virtual_keys, hallazgo verificado |
| D-GOV-03 | **Corte por nivel**: usuario = nativo LiteLLM (`max_budget` de su key) + replicación del worker (bloqueo idempotente + notificación usuario y `dept_admin`); depto y org = worker (bloquea las keys de los usuarios alcanzados, ≤ 60 s con polling corto, ADR-014 §7). Se desvía del detalle "depto = team nativo" de ADR-014: los teams de LiteLLM exigen persistir el `team_id` generado en un esquema sin columna para ello y su beneficio (0 s vs ≤ 60 s) no lo exige REQ-BUD-007; el corte por worker cumple el presupuesto de latencia de §7 para depto/org. El plano team queda documentado como evolución (añadir `departments.litellm_team_id`). | ADR-014, REQ-BUD-006/007/008, §7 |
| D-GOV-04 | **Idempotencia de extremo a extremo**: `spend_ledger.request_id` UNIQUE + `ON CONFLICT DO NOTHING` — solo las filas NUEVAS mueven `budget_counters`; el cursor (`ops.spend_cursor`) se actualiza tras el ingest; un crash entre ingest y cursor re-ingesta sin duplicar. Enforcement y notificaciones son transiciones de estado (solo actúan cuando `api_keys.status` o la deduplicación de notificación lo indican). | REQ-BUD-005, C2 |
| D-GOV-05 | **Mapeo log→tenant/usuario**: prioridad `metadata` del log (la plataforma estampa `spend_logs_metadata: {tenant_id, user_id}` al emitir la key); fallback = resolución del `user_api_key_hash` contra `api_keys` con una conexión `trust_migrator` de SOLO lectura (patrón D-CP-10: RLS oculta `api_keys` a `trust_app` cross-tenant). El depto se resuelve desde `users.department_id`. | D-CP-10, skill litellm-proxy-governance |
| D-GOV-06 | **Margen y BYOK en el ingest**: `cost_billed_cents = cost_base_cents × (10000 + maas_margin_bps)/10000` para `mode='central'`; para `mode='byok'` el gasto se registra igual (REQ-BUD-011) con `cost_billed = cost_base` (sin margen: el cobro directo es cliente↔proveedor). El modo se resuelve desde `model_access` (usuario→depto→org, default `central`). El histórico jamás se reescribe. | REQ-MDL-003, REQ-BUD-011, ADR-014 |
| D-GOV-07 | **Ventanas de período**: cada presupuesto define su `period` (`daily`/`weekly`/`monthly`); `budget_counters` lleva una fila por (nivel, entidad, `period_start`); el corte y el umbral se evalúan SOLO contra la ventana vigente → el reset de período (REQ-BUD-010) es estructural (ventana nueva = contador en 0) y el enforcement desbloquea keys bloqueadas cuyo dueño ya no está cortado. | REQ-BUD-010 |
| D-GOV-08 | **Notificaciones = inbox real**: tabla `notifications` (RLS, append + marcar leído) + `GET /api/me/notifications`. Corte de usuario → notifica al usuario y a los `dept_admin` de su depto; de depto → a los `dept_admin` y a los usuarios del depto; de org → a los `org_admin`. Umbral 80 % (`soft_threshold_pct`) → `budget_threshold_80` deduplicada por (usuario, tipo, scope, `period_start`). Cada corte/umbral además emite audit (`budget.cutoff` / `budget.threshold`). | REQ-BUD-006/008/009, arq §2.3 |
| D-GOV-09 | **Políticas**: upsert por nivel con clave del catálogo tipado (422 si la clave no existe o el tipo no coincide); `invariable=true` → 422 siempre (REQ-POL-004); versionado (`version` +1) y audit `policy.updated`; un valor de depto/usuario que RELAJE el valor vigente del nivel superior se rechaza con 422 (REQ-POL-007) usando la misma semántica de restrictividad del combinator deny-overrides (D-CP-15): el valor es aceptable ssi `combine(superior, nuevo) == nuevo`. | REQ-POL-001…007, D-CP-15 |
| D-GOV-10 | **SSO+MFA**: SSO es estructural (el API solo autentica vía OIDC/JWKS — no existe vía de password, D-CP-06); la parte configurable es `require_mfa` (invariable del proveedor, piso `true`) + `MfaGate` de spec 003. No se añade clave de política nueva: la exigencia "SSO obligatorio" se cumple por construcción y se documenta aquí. | D-CP-06/07, REQ-POL-004 |
| D-GOV-11 | **Model access por intersección**: modelos efectivos del usuario = aprobados en org ∩ (si el depto tiene filas —de CUALQUIER status, aprobadas o denegadas—: aprobados en depto) ∩ (si el usuario tiene filas —ídem—: aprobados en usuario) ∩ (política efectiva `allowed_models` si está DEFINIDA en algún nivel). Aprobar en depto/usuario un modelo no aprobado en org → 422 (regla de subconjunto, REQ-MDL-001/005). La política `allowed_models` solo puede **estrechar** (nunca ampliar fuera de lo aprobado — coherente con la invariable `approved_models_only`). Distinción deny-overrides (fix t_f0d3122d, migración 0019): clave AUSENTE en los 3 niveles (default de catálogo `null`) = sin restricción adicional; lista DEFINIDA aunque sea `[]` = deny-all explícito que ningún nivel puede relajar — el upsert ya acepta `[]` como valor más restrictivo (REQ-POL-007) y el enforcement DEBE aplicarlo. Un nivel de aprobación cuyas filas están todas denegadas deniega todo a ese nivel (revocar la última aprobación de un nivel jamás relaja al conjunto superior). | REQ-MDL-001/005/006, REQ-POL-003, t_f0d3122d |
| D-GOV-12 | **Virtual key por usuario** (REQ-MDL-002): `POST /api/models/keys` — para sí (cualquier rol con `desktop.use`) o para otro (permiso `budget.user.manage` con scope). La key se devuelve UNA vez en la respuesta (entrega one-time, constitution IV); en DB solo hash + metadatos. `models` = lista efectiva del usuario; `max_budget` = presupuesto del usuario en USD (`budget_duration` según su `period`: monthly→`30d`, weekly→`7d`, daily→`24h`); `metadata`/`spend_logs_metadata` = `{tenant_id, user_id}` (D-GOV-05). Revocación → `status='revoked'` + bloqueo en LiteLLM. | REQ-MDL-002, constitution IV |
| D-GOV-13 | **Agentes compartidos**: `agents` (dueño, depto, scope dept/org, status) + `agent_grants` (`invoke`/`view`, `revoked_at`). Publicar no crea NINGÚN grant (REQ-AGT-001). Gestionar grants (crear/revocar): dueño, `dept_admin` del depto del agente u `org_admin` (REQ-AGT-002 "manage"). Ver: dueño, grant `view`/`invoke` vigente, o admin del alcance; el resto → 404 (no revela existencia, REQ-AGT-003). Invocar: dueño o grant `invoke` (ver sin invocar → 403); el consumo se factura al invocador porque el desktop invoca con SU virtual key (REQ-AGT-005); la plataforma expone el gate `POST /api/agents/{id}/invoke` (autorización + audit `agent.invoked`/`agent.invoke_denied`). Revocación = `revoked_at` → efecto inmediato en la siguiente query (REQ-AGT-004). | REQ-AGT-001…006 |
| D-GOV-14 | **Evolución de la capa de datos sin bifurcar la verdad**: las tablas nuevas viven en la migración `0004_governance` (encadenada sobre las migraciones de las tareas hermanas para mantener una única cabeza) Y en la sección 9 de `db/schema.sql` (mismo SQL, mismo orden) — el test de paridad de spec 002 sigue exigiendo pg_dump idéntico; `0001_initial` queda congelado con el schema de spec 002. `db/tests/test_schema_parity.py` se actualiza para verificar contra el head de la cadena (no contra una revisión hardcodeada). | D-DB-12, constitution II |
| D-GOV-15 | **LiteLLM en tests = doble HTTP declarado y honesto**: el cliente `app/models/litellm.py` es código real contra la Admin API del proxy (httpx); la suite usa un servidor HTTP real (`tests/litellm_fake.py`) que implementa el contrato documentado de la Admin API (`/key/generate|block|unblock|info`, `/spend/logs/v2`, `/chat/completions` con corte nativo por `max_budget` → 429 y key bloqueada → 401, models de la key restringidos). Es el mismo patrón del IdP mock de spec 003 (HTTP real, semántica documentada, declarado como doble). Contra un proxy real solo cambia la URL (`TRUST_LITELLM_BASE_URL`). Sin URL configurada → 503 `service_unavailable` (fail-closed, jamás fail-open). | constitution I/IV, skill litellm-proxy-governance |
| D-GOV-17 | **Precisión sub-centavo en ledger/counters** (gap B-2, t_8bd1fee8): con precios reales de MaaS una llamada chica cuesta fracciones de centavo (ej. 174 tokens deepseek-v4-flash = $0.000045 = 0.0045¢), y `round(usd×100)` a entero + columnas `bigint` truncaban 9/10 filas del ledger a 0¢ — los presupuestos sub-contaban gasto real y el corte llegaba tarde o nunca. Desde la migración `0029_sub_cent_spend_precision`: `spend_ledger.cost_base_cents`/`cost_billed_cents` y `budget_counters.spent_cents` son `numeric(12,3)` — **centavos con precisión de milli-centavo (0.001¢)**, misma convención que los precios del catálogo de spec 018 (`numeric(12,3)`). Los nombres `*_cents` se conservan (la unidad sigue siendo el centavo; solo se admite fracción). `budgets.amount_cents` y `budget_counters.budget_cents` siguen `bigint`: los presupuestos los fija un admin en centavos enteros. **Redondeo (vinculante)**: el costo se cuantiza a 0.001¢ con `ROUND_HALF_UP` en el ingest (nunca por debajo del costo real: conservador para el corte) y el margen MaaS se aplica con aritmética `Decimal` exacta sobre el valor ya cuantizado, re-cuantizando a 0.001¢ `ROUND_HALF_UP`. **Corte sin redondeo adicional**: `spent >= amount` se evalúa contra el acumulado exacto en milli-centavos — N llamadas de 0.004¢ cruzan un presupuesto de 1¢ exactamente cuando la suma alcanza 1.000¢, sin esperar a que una llamada individual redondee a ≥1¢. El umbral 80 % usa la misma comparación exacta. | gap B-2 (t_a296ae9b §BUG B-2), spec 018 §precios numeric(12,3), t_8bd1fee8 |

## 3. Requisitos (formato EARS)

Convenciones: plantilla EARS **Cuando** `<trigger>` **el sistema** `<respuesta>`; prioridad MoSCoW; cada REQ con AC verificable con evidencia real. Trazabilidad: `[001-REQ-…]` = spec maestra, `[ARQ-…]` = arquitectura, `[003-…]` = spec control plane.

### 3.1 Presupuestos jerárquicos

**REQ-GOV-001** (M) [001-REQ-BUD-002] — Cuando un `org_admin` asigna el presupuesto de un departamento, el sistema valida que la suma de presupuestos activos de departamentos ≤ presupuesto de organización; si excede, rechaza con 422 y el detalle del exceso; sin presupuesto de organización, rechaza con 422.
> **AC-GOV-001**: PUT depto que excede la suma → 422 con mensaje del exceso; dentro de la suma → 200; sin presupuesto org → 422.

**REQ-GOV-002** (M) [001-REQ-BUD-003] — Cuando un admin asigna presupuesto a un usuario, el sistema valida que el usuario pertenece al departamento del scope y que la suma de presupuestos de usuarios del departamento ≤ presupuesto del departamento; si excede, 422 con detalle.
> **AC-GOV-002**: PUT usuario de otro depto → 422/404; suma excedida → 422 con detalle; dentro → 200.

**REQ-GOV-003** (M) [001-REQ-BUD-013] — Cuando un admin reduce un presupuesto por debajo del gasto ya incurrido en la ventana vigente, el sistema exige `force=true` (y audita `budget.forced_below_spend`); sin confirmación, 422.
> **AC-GOV-003**: reducción bajo gasto sin force → 422; con force → 200 + evento audit `budget.forced_below_spend`.

**REQ-GOV-004** (M) [001-REQ-BUD-004] — Cuando un admin consulta el overview, el sistema devuelve por nivel: presupuesto, gastado en la ventana vigente y remanente; a nivel org además el desglose por departamento y el **remanente no asignado** (org − suma de deptos).
> **AC-GOV-004**: GET overview con deptos sembrados → remanente no asignado = org − Σ deptos (verificado aritméticamente en el test).

**REQ-GOV-005** (M) [001-REQ-BUD-005, ARQ-ADR-014] — Cuando el worker `spend-aggregator` corre, el sistema ingiere los logs de spend de LiteLLM (`/spend/logs/v2`) desde el cursor incremental, escribe una fila por request en `spend_ledger` (idempotente por `request_id`) y actualiza `budget_counters` de los 3 niveles en la misma transacción; el cursor avanza solo tras el ingest.
> **AC-GOV-005**: tras invocaciones reales contra el gateway (doble HTTP), run_once → filas de ledger == requests; segunda pasada → 0 filas nuevas, counters idénticos; el corte de proceso entre ingest y cursor no duplica (re-ingesta idempotente).

**REQ-GOV-006** (M) [001-REQ-BUD-006] — Cuando el gasto de un usuario en la ventana vigente alcanza su presupuesto, el sistema corta: bloquea sus virtual keys (idempotente), audita `budget.cutoff` (nivel user) y notifica al usuario y a los `dept_admin` de su depto; el gateway rechaza sus requests siguientes.
> **AC-GOV-006**: gastar ≥ presupuesto → run_once → key `blocked` en el gateway; invocación siguiente → 401/429 del gateway; notificación `budget_cutoff` al usuario; evento audit `budget.cutoff`.

**REQ-GOV-007** (M) [001-REQ-BUD-007] — Cuando el gasto de un departamento alcanza su presupuesto, el sistema bloquea las keys de TODOS los usuarios del departamento sin afectar a otros departamentos con remanente.
> **AC-GOV-007**: depto A1 agotado → keys de usuarios de A1 bloqueadas; usuario de A2 (con remanente) sigue invocando OK.

**REQ-GOV-008** (M) [001-REQ-BUD-008] — Cuando el gasto de la organización alcanza su presupuesto, el sistema bloquea todas las keys del tenant y notifica a los `org_admin`; otros tenants no se afectan.
> **AC-GOV-008**: org A agotada → todas las keys de A bloqueadas y notificación a org_admin; key de tenant B sigue activa.

**REQ-GOV-009** (M) [001-REQ-BUD-009] — Cuando el gasto cruza el umbral `soft_threshold_pct` (default 80) de cualquier nivel, el sistema notifica al afectado y a su admin (`budget_threshold_80`) y audita `budget.threshold`, sin duplicar la notificación dentro de la misma ventana.
> **AC-GOV-009**: cruce del 80 % → exactamente 1 notificación por (usuario, nivel, ventana) aunque el worker corra N veces; evento audit `budget.threshold` presente.

**REQ-GOV-010** (M) [001-REQ-BUD-010] — Cuando comienza una nueva ventana de período, el sistema reinicia los contadores (ventana nueva = `spent_cents` desde 0) y desbloquea las keys cuyo dueño ya no está cortado.
> **AC-GOV-010**: con gasto en ventana nueva por debajo del presupuesto, run_once → key bloqueada pasa a activa (`model.key_unblocked` auditado) y el gateway acepta de nuevo.

**REQ-GOV-011** (M) [001-REQ-BUD-011, REQ-MDL-003] — Cuando se ingiere gasto, el sistema aplica el margen MaaS del tenant al costo facturado en modo `central` y registra el gasto BYOK sin margen; ambos descuentan de los presupuestos.
> **AC-GOV-011**: entrada central con `maas_margin_bps=1000` → `cost_billed = cost_base × 1.1`; entrada byok → `cost_billed = cost_base`; ambas mueven los counters.

**REQ-GOV-012** (M) [001-REQ-DTK-005/006] — Cuando un usuario consulta su estado, el sistema devuelve presupuesto/gastado/remanente y estado de corte por nivel (user/dept/org) desde los counters vigentes.
> **AC-GOV-012**: `GET /api/budgets/me` refleja el gasto ingestado y `cutoff=true` con el nivel tras el corte.
> **AC-GOV-012a (borde amount=0, gap B-1 t_ac2d2611)**: un presupuesto **explícito de 0 centavos** (`PUT /api/budgets/user/{id} {"amount_cents": 0, "force": true}`) es un presupuesto vigente, no un presupuesto ausente: la ruta de lectura (`_level_status`) aplica la MISMA regla que el enforcement (`spend.py`: `spent >= amount`, sin guard `> 0`). Con spend ≥ 1¢ (o incluso 0¢) y budget 0, `GET /api/budgets/me` DEBE reportar `cutoff: true, cutoff_level: "user"` — nunca el estado contradictorio `remaining_cents: -1, cutoff: false`. Un presupuesto **ausente** (sin fila activa en `budgets`) sigue sin corte: `budget_cents: null, cutoff: false`. La distinción es la existencia de la fila, no el monto.

**REQ-GOV-016** (M) [gap B-2 t_8bd1fee8, D-GOV-17, spec 018 §precios] — Cuando el worker ingiere logs de spend con costo fraccionario (llamadas reales pequeñas de MaaS, ej. $0.000045), el sistema acumula el gasto real en `spend_ledger` y `budget_counters` con precisión de milli-centavo (0.001¢, `numeric(12,3)`); el corte y el umbral se evalúan contra el acumulado exacto, de modo que N llamadas sub-centavo descuentan de los presupuestos y cruzan el corte cuando la SUMA alcanza el monto.
> **AC-GOV-016 (regresión del truncamiento)**: N invocaciones reales pequeñas vía el gateway → run_once → N filas de ledger con `cost_base_cents` > 0 (fraccionario, ej. 0.005) y counters user/depto/org acumulando la suma fraccionaria — JAMÁS 9/10 filas en 0¢ como en el repro del gap B-2.
> **AC-GOV-016a (redondeo)**: el costo se cuantiza a 0.001¢ `ROUND_HALF_UP` (0.0045¢ → 0.005¢, jamás 0¢); con `maas_margin_bps=1000`, `cost_billed = cost_base × 1.1` en aritmética Decimal exacta re-cuantizada a 0.001¢ (0.005¢ → 0.006¢); byok → `cost_billed = cost_base` sin margen.
> **AC-GOV-016b (corte por suma, no por llamada)**: presupuesto de usuario de 1¢ + M llamadas de 0.004¢ cada una (ninguna ≥ 1¢ individualmente) → cuando la suma acumulada alcanza 1.000¢, run_once → key `blocked` + notificación `budget_cutoff` + audit `budget.cutoff`; el corte NO espera a que una llamada individual redondee a ≥ 1¢.
> **AC-GOV-016c (paridad y compat)**: la migración `0029_sub_cent_spend_precision` deja pg_dump idéntico al `db/schema.sql` actualizado (AC-DB-040); los valores enteros existentes se conservan exactos (`USING col::numeric(12,3)`); la API expone los montos como números JSON (los enteros siguen viéndose numéricamente idénticos) y el export CSV de requests muestra USD con precisión sub-centavo visible.
>
> Observación menor asociada (decidida): los requests **denegados** por el gateway (403/429 antes de despachar) ingresan al ledger con costo 0 **por diseño** — son evidencia de auditoría del intento y del enforcement (cuenta de requests, costo 0). No se filtran: el gasto real y el intento denegado son hechos distintos y ambos auditables.

### 3.2 Políticas

**REQ-GOV-020** (M) [001-REQ-POL-001/003] — Cuando un admin define una política en cualquier nivel, el sistema solo acepta claves del catálogo tipado con valor del tipo correcto; clave inexistente o tipo incorrecto → 422.
> **AC-GOV-020**: clave desconocida → 422; `session_timeout_minutes` con string → 422; valor válido → 200 versionado.

**REQ-GOV-021** (M) [001-REQ-POL-004] — Cuando cualquier nivel intenta definir una política `invariable=true` (`require_mfa`, `approved_models_only`, `explicit_shared_agent_grants`, `scoped_credentials`), el sistema la rechaza con 422.
> **AC-GOV-021**: PUT `require_mfa` en org/depto/usuario → 422 en los tres casos.

**REQ-GOV-022** (M) [001-REQ-POL-005] — Cuando una política cambia, el sistema incrementa la versión, registra `policy.updated` en audit y la nueva versión aplica de inmediato (la efectiva se calcula por request).
> **AC-GOV-022**: dos PUT sucesivos → version 1, 2; evento audit con key/level/version; `GET /api/me/policies` refleja el nuevo valor.

**REQ-GOV-023** (M) [001-REQ-POL-007] — Cuando un `dept_admin` (o admin) define una política de depto/usuario, el sistema rechaza con 422 todo valor que relaje el valor vigente del nivel superior (misma semántica del combinator).
> **AC-GOV-023**: org `allowed_connectors=[vmware,email]`, depto intenta `[vmware,email,slack]` → 422; `[vmware]` → 200.

**REQ-GOV-024** (M) [001-REQ-POL-002/006, 003-D-CP-15] — Cuando se consulta la política efectiva de un usuario, el sistema devuelve la combinación deny-overrides de los 3 niveles con las invariables al piso del proveedor (ya implementado en spec 003; esta spec añade la vía de escritura).
> **AC-GOV-024**: `GET /api/me/policies` tras upserts en 3 niveles = salida del combinator (probado con casos por tipo).

**REQ-GOV-025** (M) [gap B-3 t_fdc728d2, revisión E2E Pilar 2 §"BUG B-3"] — Simetría de contrato PUT/DELETE en policies de usuario: cuando se desactiva una política de nivel usuario vía `DELETE /api/policies/user/{key}?user_id=…` SIN `department_id`, el sistema resuelve el departamento del usuario desde `users.department_id` — la MISMA resolución que aplica `PUT /api/policies/user/{user_id}` al escribir la fila. El identificador de una policy de usuario es el `user_id` (único por fila); exigir repetir el `department_id` en el DELETE hacía inalcanzable la fila escrita por el PUT (404 `Policy not found at this level.` por el filtro `(department_id IS NULL) = :dept_null`) — un admin no puede necesitar un dato que el propio upsert resuelve solo. Comportamiento vinculante:
>
> - `DELETE /api/policies/user/{key}?user_id=…` (sin `department_id`) → 204 si existe fila activa para ese (key, user) — el depto se resuelve como en el PUT; usuario inexistente → 404 `User not found.` (coherente con el PUT).
> - `DELETE …?user_id=…&department_id=…` explícito SIGUE soportado (compatibilidad hacia atrás); un `department_id` que no coincide con el depto real del usuario no matchea la fila (404).
> - Nivel depto SIN `department_id` sigue sin resolución (a diferencia del nivel usuario, no hay entidad única que lo resuelva): 404. Nivel org no lleva scope.
> - La resolución aplica TAMBIÉN al audit del `deactivate` y al barrido de conectores (`_sweep_connectors`), que reciben el depto resuelto — mismo dato que el upsert audita.
>
> **AC-GOV-025 (regresión de la asimetría)**: `PUT /api/policies/user/{user_id} {"key": K, "value": V}` → 200; a continuación `DELETE /api/policies/user/K?user_id={user_id}` SIN `department_id` → **204** (antes del fix: 404); un segundo DELETE idéntico → 404 (la fila quedó desactivada, no es reachable dos veces); la política efectiva del usuario vuelve al valor del nivel superior/default del catálogo.

### 3.3 Model access + LiteLLM

**REQ-GOV-030** (M) [001-REQ-MDL-001/005] — Cuando un admin aprueba/retira un modelo para un nivel, el sistema valida la regla de subconjunto (depto/usuario ⊆ org) y audita `model.approved`/`model.revoked`; el retiro de org deja al modelo no efectivo para todos (intersección).
> **AC-GOV-030**: aprobar en depto un modelo no aprobado en org → 422; aprobar subset → 200 + audit; retirar org → el modelo desaparece de la lista efectiva del usuario.

**REQ-GOV-031** (M) [001-REQ-MDL-001] — Cuando un usuario verifica o usa un modelo no efectivo para él, el sistema responde 403 `reason=model_not_approved` y audita `model.access_denied`; los modelos efectivos se listan en `GET /api/models/allowed-for-me`.
> **AC-GOV-031**: verify de modelo no aprobado → 403 `model_not_approved` + audit; aprobado → 200; la lista efectiva = intersección de niveles ∩ política.

**REQ-GOV-032** (M) [001-REQ-MDL-002, D-GOV-12] — Cuando se emite una virtual key para un usuario, el sistema la crea en LiteLLM restringida a los modelos efectivos con `max_budget`/`budget_duration` del presupuesto del usuario, guarda SOLO el hash y devuelve la key una única vez.
> **AC-GOV-032**: POST keys → 201 con key en claro UNA vez; DB solo hash (búsqueda del valor en claro = 0); el gateway rechaza con la key un modelo fuera de su lista; re-emisión → nueva key, hash distinto.

**REQ-GOV-033** (M) [001-REQ-MDL-006] — Cuando un admin gestiona el Model Access, el sistema ofrece el catálogo global con estado por tenant (aprobado/pendiente/denegado por nivel), modo (central/BYOK) y pricing vigente.
> **AC-GOV-033**: GET access devuelve el catálogo completo con el estado por nivel del tenant y pricing en centavos USD/millón.

**REQ-GOV-034** (M) [001-REQ-MDL-003] — Cuando se aprueba un modelo, el sistema registra el modo `central` o `byok`; el ingest resuelve el modo (usuario→depto→org) y lo persiste en el ledger.
> **AC-GOV-034**: aprobación byok a nivel usuario → entrada de ledger con `mode='byok'` y sin margen (junto con AC-GOV-011).

**REQ-GOV-035** (M) [constitution IV] — Cuando la plataforma responde cualquier consulta posterior de keys, el sistema jamás incluye la key en claro ni su hash en el cuerpo.
> **AC-GOV-035**: GET keys/me → campos de metadatos sin `key`/hash; grep de la key en claro sobre la respuesta = 0.

**REQ-GOV-036** (M) [D-GOV-11, 003-D-CP-15, fix t_f0d3122d] — Cuando se calcula el conjunto efectivo de modelos de un usuario (`effective_models_for_user`, usado por `GET /api/models/allowed-for-me`, `POST /api/models/verify`, la emisión de keys, el bundle del desktop y la invocación de agentes), el sistema distingue "clave ausente" de "lista vacía": `allowed_models` definida en cualquier nivel (aunque sea `[]`) se intersecta con las aprobaciones — `[]` = deny-all explícito; la clave ausente en los 3 niveles (default de catálogo `null`, migración 0019) no añade restricción. Igualmente, un nivel de aprobación (depto/usuario) que tiene filas en `model_access` —de cualquier status— participa en la intersección con su conjunto APROBADO: todas las filas denegadas = deny-all a ese nivel (revocar la última aprobación de un nivel jamás relaja al conjunto superior).
> **AC-GOV-036**: (a) org aprueba `glm-5.2` + policy org `allowed_models=["glm-5.2"]` + policy de usuario `allowed_models=[]` (aceptada, valor más restrictivo) → `GET /api/models/allowed-for-me` del usuario = `[]` y `POST /api/models/verify` de `glm-5.2` → 403 `model_not_approved` + audit `model.access_denied`; `GET /api/me/policies` muestra `allowed_models: []`. (b) policy org `allowed_models=[]` → deny-all para TODOS los usuarios del tenant (verify → 403). (c) sin ninguna fila de `allowed_models` → `GET /api/me/policies` muestra `allowed_models: null` y el usuario hereda la intersección de aprobaciones (allowed-for-me no vacío). (d) depto con su única aprobación revocada (fila `denied`) → allowed-for-me de sus miembros = `[]`; re-aprobar restaura.

### 3.4 Agentes compartidos

**REQ-GOV-040** (M) [001-REQ-AGT-001] — Cuando un usuario publica un agente compartido, el sistema lo registra con dueño, depto, scope y SIN ningún grant; se audita `agent.shared`.
> **AC-GOV-040**: POST agents → 201; listado de otro usuario no lo incluye; audit `agent.shared`.

**REQ-GOV-041** (M) [001-REQ-AGT-002] — Cuando se otorga acceso, el sistema solo acepta grants explícitos `invoke`/`view` creados por el dueño, el `dept_admin` del depto del agente o un `org_admin`; grant al dueño o permiso inválido → 422.
> **AC-GOV-041**: grant por terceros sin derecho → 403; por dueño → 201 + audit `agent.granted`; permiso `manage` → 422.

**REQ-GOV-042** (M) [001-REQ-AGT-003/006] — Cuando un usuario sin grant vigente accede a un agente compartido, el sistema responde 404 (sin revelar existencia) y el agente no aparece en su listado; con grant `view` sin `invoke`, invocar → 403.
> **AC-GOV-042**: GET/invoke sin grant → 404; con `view` → GET 200, invoke 403 + audit `agent.invoke_denied`; listado scoped solo con grants.

**REQ-GOV-043** (M) [001-REQ-AGT-004] — Cuando un grant se revoca, el sistema impide nuevas invocaciones de inmediato (siguiente request → 404/403) y audita `agent.revoked`.
> **AC-GOV-043**: revoke → invoke siguiente → 404; audit presente.

**REQ-GOV-044** (M) [001-REQ-AGT-005] — Cuando un usuario invoca un agente compartido, el sistema autoriza contra su grant y audita `agent.invoked` con el invocador como actor; el consumo de modelos se factura al invocador (su virtual key).
> **AC-GOV-044**: invoke con grant → 200 con `billed_to` = invocador + audit `agent.invoked`.

### 3.5 Notificaciones

**REQ-GOV-050** (M) [001-REQ-BUD-006/008/009, D-GOV-08] — Cuando ocurre un corte o umbral, el sistema escribe notificaciones en el inbox de los afectados; un usuario lista y marca leídas SOLO las suyas.
> **AC-GOV-050**: corte → notificaciones para destinatarios esperados; GET notifications de otro usuario no las devuelve (RLS + filtro); marcar leída → `read_at` seteado.

### 3.6 Aislamiento y RBAC (transversal)

**REQ-GOV-060** (M) [001-REQ-TEN-002/003, 003-D-CP-08/09] — Cuando cualquier endpoint de gobernanza recibe IDs forjados de otro tenant, responde 404; `dept_admin` opera solo dentro de su depto; `user` no administra presupuestos/políticas/keys ajenas.
> **AC-GOV-060**: budget/policy/agent/key con ID cross-tenant → 404; dept_admin fuera de su depto → 404/403; user → 403 en rutas de administración.

## 4. Trazabilidad requisitos ↔ componentes

| REQ | Componente | Tests |
|---|---|---|
| REQ-GOV-001…004, 012 | `app/governance/budgets.py`, `governance/router.py` | `test_budgets.py` |
| REQ-GOV-005, 010, 011, 016 | `app/governance/spend.py`, `spend_worker.py`, `models/litellm.py` | `test_spend_cutoff.py`, `test_spend_subcent.py` |
| REQ-GOV-006…009 | `app/governance/spend.py` (enforcement) + `notifications.py` | `test_spend_cutoff.py` |
| REQ-GOV-020…025 | `app/governance/policies.py`, `governance/router.py` | `test_policies_api.py` |
| REQ-GOV-030…035 | `app/models/access.py`, `models/litellm.py`, `models/router.py` | `test_model_access.py` |
| REQ-GOV-040…044 | `app/governance/agents.py`, `governance/router.py` | `test_agents.py` |
| REQ-GOV-050 | `app/governance/notifications.py`, `auth/router.py` | `test_spend_cutoff.py` |
| REQ-GOV-060 | todos los routers (dependencias RBAC + RLS) | todos los archivos |
| D-GOV-14 | `db/migrations/versions/20261023_0002_governance.py`, `db/schema.sql` §9 | `db/tests/test_schema_parity.py` |

## Evolución spec 013 (cierre de gaps de spec 001, oct-2026)

Tres extensiones implementadas y verificadas — detalle y ACs en `specs/013-spec001-gaps/spec.md`:
- **AC-BUD-010 (rollover)**: seam de reloj único `budgets.today()` (UTC, consistente con `occurred_at`) usado por budgets.py y spend.py; test con reloj simulado `tests/test_budget_rollover.py` (D-013-01).
- **REQ-BUD-012 (history)**: `GET /api/budgets/history` — ventanas cerradas, presupuesto vs usado por nivel + desglose por modelo desde el ledger; org_admin completo, dept_admin con scope (D-013-03).
- **REQ-CON-005 (desconexión proactiva)**: barrido síncrono en la MISMA transacción del cambio de `allowed_connectors`/`messaging_permissions` (org/dept/user) vía `sweep_after_policy_change` — corta instancias activas, notifica al dueño y audita `connector.disconnected` (D-013-02). Además expone y corrige un bug latente: `policies.upsert` ahora REACTIVA la fila tras `deactivate` (el unique `policies_scope_uidx` no es parcial).
