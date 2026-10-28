# Spec 032 — Discovery dinámico de modelos: toda API conectada aporta su catálogo (mandato Miller)

| Campo | Valor |
|---|---|
| **Padre** | specs/018-model-access-maas · specs/024-custom-connections · specs/005-governance (REQ-MDL) · mandato Miller (t_99526286) |
| **Alcance** | Toda conexión personalizada de tipo API/LLM (OpenAI-compatible) permitida por policy aporta sus modelos al catálogo vía `GET {base_url}/v1/models`; los admins los aprueban/restringen por nivel en Model Access; los modelos byok se registran en el gateway LiteLLM con la key de la conexión y su spend descuenta al budget del invocador. |
| **Fuente** | Verificación en vivo contra el proxy LiteLLM REAL v1.106.3 (127.0.0.1:4000): `POST /model/new` → `{model_id, model_name, …}`, `POST /model/delete {id:<uuid>}` (requiere el `model_id` devuelto por /model/new, NO el nombre), `GET /model/info` (resuelve name→id, `db_model`), `GET /v1/models`. Requiere `general_settings.store_model_in_db: true` (proxy_server.py:7022) — habilitado en `litellm-real/config.yaml`. |
| **Reglas** | Nada simulado: el fetch es contra APIs reales (MaaS intl como conexión byok de prueba). Keys jamás en plaintext en respuestas/logs (vault envelope). USD. Paridad schema.sql ↔ Alembic (AC-DB-040). SDD completo. |

## Problema (mandato Miller)

`catalog.model_catalog` se alimenta SOLO del endpoint MaaS central (spec 018, seeds por migración). Las conexiones personalizadas BYOK (spec 024: BDs, API keys en vault, policy `allowed_connections`) NO aportan modelos: un tenant con su propia cuenta de OpenRouter/OpenAI/MaaS no puede gobernarla. Además el catálogo no tiene ciclo de vida: cuando un modelo desaparece de la API upstream (patrón `qwen3-32b` detectado por QA) sigue figurando como disponible.

## 1. Conexiones de tipo LLM y discovery por conexión (D-032-01)

**Decisión**: nueva `kind='llm'` en `vault_connections` (mismo envelope crypto, misma policy `allowed_connections`). Metadata NO-secreta requerida: `base_url` (http/https, el endpoint OpenAI-compatible). Opcional: `input_price_cents_per_m` / `output_price_cents_per_m` (números ≥ 0, default 0) — **el pricing BYOK lo declara el admin dueño de la key** (el control plane no puede conocer el pricing de una API arbitraria; 0 = coste desconocido → spend 0, el corte de budget sigue operando por los montos que LiteLLM computa).

Cuando la conexión está `active`, el control plane ejecuta `GET {base_url}/models` (normalización: si `base_url` termina en `/v1` se usa tal cual; si no, se apende `/v1`) con `Authorization: Bearer <key>` — key descifrada del vault **en memoria, por uso, jamás en respuestas ni logs** (mismo contrato REQ-024-007). Cada modelo descubierto:

- `model_name` = `byok:{connection_id}:{upstream_id}` — namespacing con el UUID de la conexión (globalmente único) hace imposible la colisión con modelos centrales o con byok de otro tenant/otra conexión.
- `source='byok'`, `tenant_id`, `connection_id`, `provider` = nombre de la conexión, `display_name` = upstream id, precios = metadata declarada.
- Se registra en el gateway LiteLLM: `POST /model/new {model_name, litellm_params: {model: "openai/{upstream_id}", api_base, api_key: key de la conexión, input_cost_per_token, output_cost_per_token}, model_info: {input_cost_per_token, output_cost_per_token}}` (¢/M → USD/token). El `model_id` devuelto se persiste (`gateway_model_id`) para el delete. **Enmienda v1.1 (verificación live)**: el pricing va en `litellm_params` (per-deployment override, la vía recomendada de la doc oficial) Y en `model_info` (passthrough metadata) — contra el proxy REAL v1.106.3 el pricing solo en `model_info` NO se aplica al billing (spend=0 observado en vivo); solo en `litellm_params` sí.

> **AC-032-01**: con una conexión `llm` real (MaaS intl + key de prueba), `POST /api/models/catalog/refresh {"connection_id": …}` (org_admin) descubre los modelos del endpoint en `GET /api/models` marcados `source=byok`, `provider=<nombre conexión>`, y ninguna respuesta de la API contiene la key (higiene verificada).

## 2. Ciclo de vida: stale/removed (D-032-02)

**Decisión**: `catalog.model_catalog.status` admite `stale` y `removed` además de `available`/`deprecated`:

- Un modelo byok que desaparece del `GET /models` en un refresh → `stale` (puede reaparecer: un refresh posterior que lo vuelve a ver lo restaura a `available`).
- Una conexión revocada → todos sus modelos pasan a `stale` y se eliminan del gateway (`POST /model/delete {id: gateway_model_id}`) — el modelo deja de ser invocable inmediatamente también en el data plane (las vkeys viejas que aún lo listan no encuentran routing).
- `removed` = estado terminal reservado (eliminación física futura); el CHECK lo admite desde ya.

Los modelos `stale`/`removed` NUNCA son invocables (ver §4) y la UI los muestra con badge.

> **AC-032-02**: refresh con el endpoint devolviendo un subconjunto → los ausentes quedan `stale` y `allowed-for-me` los excluye; revocar la conexión → sus modelos `stale`, eliminados del gateway, y `POST /api/models/verify` → 403 `model_not_approved` + audit.

## 3. Refresh: acción admin + job periódico (D-032-03)

**Decisión**:

- `POST /api/models/catalog/refresh` (permiso `connections.manage`, org_admin) con body opcional `{"connection_id": …}`: sin id → refresh **global** (central + todas las conexiones `llm` activas del tenant); con id → solo esa conexión (404/410 si no existe/revocada).
- **Central**: el refresh central sincroniza contra `GET {gateway}/v1/models` (master key) — la vía de acceso operativa del modo central; los modelos centrales que el gateway ya no enruta → `stale`. El catálogo central sigue manteniéndose por migraciones contra la doc oficial (spec 018); el refresh detecta drift catálogo↔gateway.
- **Job periódico** en el jobs-worker existente: job type `model.catalog_refresh`; el loop del worker encola cada `TRUST_CATALOG_REFRESH_HOURS` (default 6) un job por tenant con conexiones `llm` activas, con `idempotency_key` por ventana (reinicio del worker nunca duplica). El handler ejecuta el refresh global del tenant.
- Audit `model.catalog_refreshed` con payload `{scope, connection_id?, discovered, updated, stale, central_stale}` — sin keys.

> **AC-032-03**: el refresh global audita `model.catalog_refreshed`; el job `model.catalog_refresh` aparece en la cola (`jobs`) y su ejecución refresca el catálogo del tenant (evidencia con `--once` del worker).

## 4. Governance compuesta: conexión ∧ aprobación (D-032-04)

**Decisión**: el conjunto efectivo existente (org ∩ dept ∩ user ∩ `allowed_models`, deny-overrides) se estrecha para modelos byok: un modelo `source=byok` es efectivo SOLO si (a) SU conexión está `active`, (b) su `connection_id` ∈ `allowed_connections` efectivo del usuario, y (c) su status de catálogo es `available`. Conexión revocada, conexión fuera de policy o modelo `stale` → no efectivo (deny-overrides compuesto: una negación en cualquiera de las dos historias — aprobación de modelos o policy de conexiones — gana). `assert_model_allowed`, `allowed-for-me`, `verify` y la emisión de vkeys (`issue_key` usa el conjunto efectivo) heredan el gate sin cambios adicionales.

> **AC-032-04**: modelo byok aprobado a nivel org + conexión en `allowed_connections` → `allowed-for-me` lo incluye y `verify` 200; quitar la conexión de la policy (o revocarla) → el modelo desaparece del conjunto efectivo y `verify` → 403 + audit `model.access_denied`, aunque la aprobación org siga existiendo.

## 5. LiteLLM + spend (D-032-05)

**Decisión**: los modelos byok se registran/eliminan en el gateway en el mismo ciclo de discovery (§1/§2). La invocación es la estándar: el usuario usa su vkey (emitida con `models=[…nombres namespaced…]`) contra `{gateway}/v1/chat/completions`; el gateway enruta al upstream con la key de la conexión y computa spend con el pricing declarado; el spend-worker ingesta el ledger igual que central y `resolve_mode` estampa `mode='byok'`. El budget del invocador se descuenta por el enforcement existente (LiteLLM `max_budget` + corte del spend-worker).

> **AC-032-05**: invocación real del modelo byok vía gateway con la vkey del usuario → 200 con contenido del upstream real; el spend de esa invocación aparece en `spend_ledger` del usuario con `mode='byok'` y descuenta su budget.

## 6. Model Access por fuente (D-032-06)

**Decisión**: `GET /api/models/access` devuelve por modelo `source`, `connection_id`, `connection_name`, `last_seen_at` y `catalog_status`; la UI agrupa: sección "Central MaaS" + una sección por conexión BYOK (nombre, status de la conexión, botón "Refresh"), botón "Refresh catalog" global en el header, badges de status por modelo. El flujo de aprobación por nivel (org→dept→user, subset rule, revoke) aplica IGUAL a modelos byok — `mode='byok'` en `model_access`.

> **AC-032-06**: la matriz muestra los grupos por fuente y permite aprobar a nivel org un modelo byok; `GET /api/policies?level=org` lo lista en `model_approvals` con `mode=byok`.

## 7. Datos y aislamiento (D-032-07)

Migración `0024_dynamic_model_discovery` (guard, head 0023): columnas `tenant_id`, `connection_id` (FK → vault_connections), `source`, `last_seen_at`, `gateway_model_id` en `catalog.model_catalog`; status CHECK ampliado; UNIQUE(model_name) → parcial por fuente (central: model_name; byok: (connection_id, model_name)); `kind='llm'` en vault_connections; **RLS en catalog.model_catalog** (trust_app ve `tenant_id IS NULL` —centrales— o propio tenant; FORCE, mismo patrón §14.6) + grants de escritura para byok. Paridad schema.sql.

> **AC-032-07**: tenant B no ve los modelos byok de tenant D (API y SQL como trust_app bajo RLS); los centrales siguen visibles para todos.

## 8. E2E real (D-032-08)

`platform/scripts/evidence_dynamic_discovery.py` contra el stack vivo (api :8000, gateway real :4000, MaaS intl como upstream byok con key de prueba del archivo `litellm-real/.env`, jamás impresa): crear conexión llm → policy org permite la conexión → refresh → modelos en catálogo byok → aprobar org → vkey → invocar vía gateway → spend en ledger + budget descontado → revocar conexión → modelos stale + no invocables (verify 403, gateway sin el modelo).

> **AC-032-08**: el script ejecuta el flujo completo contra APIs reales y emite evidencia (HTTP codes, catálogo, ledger, budget antes/después) sin exponer secrets.

## Out of scope

- Pricing automático de APIs BYOK arbitrarias (lo declara el admin; 0 = desconocido).
- Discovery de APIs no OpenAI-compatible (el mandato cubre GET /v1/models).
- `removed` como proceso automático (estado reservado, ver §2).
- Modelos de embeddings/vision (solo chat, como spec 018).
