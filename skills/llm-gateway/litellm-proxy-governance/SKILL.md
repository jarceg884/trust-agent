---
name: litellm-proxy-governance
description: "LiteLLM proxy como plano de control de modelos: virtual keys, budgets (max_budget/budget_duration), teams, spend endpoints, pricing update. Use when gobernar acceso a modelos, presupuestos con corte o tracking de gasto."
version: 1.1.0
author: trust-agent dev
metadata:
  hermes:
    tags: [litellm, proxy, virtual-keys, budgets, teams, spend, pricing, llm-gateway, governance]
---

# LiteLLM Proxy — Virtual keys, budgets, teams, spend y pricing

Investigado contra docs oficiales https://docs.litellm.ai/docs/proxy/* (oct-2026)
y **VERIFICADO EN VIVO contra un proxy real v1.106.3 con upstream Huawei MaaS**
(re-validación t_c4766c4d, 2026-10-24 — sección 7).
Requisito duro: **presupuestos requieren base de datos PostgreSQL** — en un
despliegue sin DB los budgets *fallan abiertos* (se loguea un warning al arranque
y nada bloquea en request time).

## 1. Setup base

```bash
export DATABASE_URL=postgresql://<user>:<pass>@<host>:5432/<dbname>
export LITELLM_MASTER_KEY=sk-...   # DEBE empezar con sk- ; clave admin del proxy
```

```yaml
# config.yaml
model_list:
  - model_name: qwen-plus
    litellm_params:
      model: openai/qwen-plus          # Huawei MaaS es OpenAI-compatible
      api_base: ${MAAS_ENDPOINT}
      api_key: os.environ/MAAS_API_KEY
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
```

## 2. Virtual keys — `/key/generate`

```bash
curl -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H 'Content-Type: application/json' \
  -d '{
    "key_alias": "depto-ti-user42",
    "user_id": "user-42",              # dueño; spend se acumula en LiteLLM_UserTable
    "team_id": "team-ti",              # si pertenece a un team, SOLO se aplican budgets del team
    "models": ["glm-5.2", "deepseek-v4-flash"],  # deployments reales MaaS (spec 018)
    "max_budget": 10,                  # USD; null por default (no se chequea)
    "budget_duration": "30d",          # reset del spend al fin del periodo
    "rpm_limit": 60, "tpm_limit": 100000,
    "expires": "2026-12-31T00:00:00Z",
    "metadata": {"tags": ["tenant:acme"], "spend_logs_metadata": {"tenant_id": "acme", "user_id": "u42"}},
    "permissions": {"get_spend_routes": true}
  }'
```

**Gobernanza sin guardar la key en claro (verificado en despliegues reales,
integración Waldur + docs):** la respuesta devuelve `key` (plaintext, EXACTAMENTE
una vez) y `token` (el sha256 de la key). `/key/block`, `/key/unblock`,
`/key/update` y `/key/info` aceptan ese **hash** como parámetro `key`, y las
filas de `/spend/logs` llevan el hash de la key (`user_api_key_hash`). Por eso
una plataforma puede guardar SOLO `sha256(key)` y aun así bloquear/desbloquear
y mapear spend a usuarios — el patrón de `api_keys.litellm_key_hash` del
Trust Agent (spec 005, D-GOV-02). Un hash desconocido devuelve 404
`Key not found.` (tratarlo como "no existe", no como fallo). `blocked` vuelve
`null` (no `false`) para keys nunca bloqueadas — leer truthy/falsy.
`spend_logs_metadata` viaja en cada fila de spend: estampar
`{tenant_id, user_id}` al emitir la key hace el mapeo log→usuario trivial.


**Reglas de herencia (docs virtual_keys):**
- El dueño de una key es su `user_id`. `/key/generate` estampa el `user_id` del
  caller solo si NO es proxy admin; un admin debe pasar `user_id` explícito
  (una key de admin sin `user_id` no hereda nada).
- Si la key tiene `team_id`: **solo se aplican los budgets del team y del miembro
  en el team** (`max_budget_in_team`); el budget personal del dueño NO aplica
  (comportamiento restaurado en v1.95.0).
- Model access se evalúa contra la fila de la key; rutas de gestión contra el rol
  del dueño (`proxy_admin` salta restricciones).

**`budget_duration` y ventanas de reset (docs users):**

| budget_duration | Resetea |
|---|---|
| `1h` | cada hora |
| `24h` | diario a medianoche UTC |
| `7d` | cada lunes a medianoche UTC |
| `30d` | día 1 de cada mes a medianoche UTC |
| `1s/1m/1d` | segundos / minutos / días |

Múltiples ventanas por key: `budget_limits: [{budget_duration: "24h", max_budget: 5}, {budget_duration: "30d", max_budget: 100}]`.

Al superar `max_budget` la request falla (422 por defecto; para 429:
`litellm_settings.budget_exceeded_status_code: 429`). Headers de error exponen
`x-litellm-key-max-budget` y `x-litellm-key-spend`.

## 3. Teams

```bash
# Crear team con budget
curl -X POST http://localhost:4000/team/new -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{"team_alias": "Depto TI", "max_budget": 500, "budget_duration": "30d",
       "models": ["qwen-plus"], "rpm_limit": 600}'

# Añadir miembro con budget DENTRO del team
curl -X POST http://localhost:4000/team/member_add -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{"team_id": "<uuid>", "max_budget_in_team": 50,
       "member": {"role": "user", "user_id": "user-42"}}'

# Info + spend del team
curl http://localhost:4000/team/info?team_id=<uuid> -H "Authorization: Bearer $LITELLM_MASTER_KEY"
```

Jerarquía Trust Agent (org→depto→usuario) se mapea: **org = team padre o budget
global, depto = team, usuario = user con `max_budget_in_team`**, key por
dispositivo/usuario con `team_id` del depto. Defaults para teams auto-creados por
SSO: `litellm_settings.default_team_params` (max_budget, budget_duration, models).

Roles (docs virtual_keys): `proxy_admin`, `proxy_admin_viewer`, `org_admin`
(admin de una org: crea teams/users solo dentro de su org), `internal_user`,
`internal_user_viewer`, `customer`. **`org_admin` existe nativamente** — relevante
para multi-tenant.

## 4. Spend endpoints

| Endpoint | Qué da |
|---|---|
| `GET /key/info?key=sk-...` | spend de una key (+ `model_max_budget_usage`) |
| `GET /user/info?user_id=...` | spend total del usuario + sus keys |
| `GET /team/info?team_id=...` | spend del team |
| `GET /global/spend/report?start_date=...&end_date=...` | reporte por rango (admin) |
| `GET /spend/keys`, `GET /spend/users` | listas ordenadas por spend; **no-admins scoped a sus propios datos** (un internal_user que pide `?user_id=` de otro recibe 403) |
| `GET /spend/logs/v2?start_date=...&page=1&page_size=100` | logs individuales paginados (usa esta, no la legacy `/spend/logs` que trunca a 10k) |
| `GET /user/daily/activity?start_date=...&end_date=...` | desglose diario por modelo/provider/key |
| `POST /global/spend/reset` | reset spend de TODAS las keys/teams (solo LITELLM_MASTER_KEY; logs se conservan) |

Permitir spend routes a no-admins: crear key con `"permissions": {"get_spend_routes": true}`.

## 5. Pricing update (mantener costos al día)

LiteLLM precia cada request desde su **model cost map**
(`model_prices_and_context_window.json`). Por defecto el proxy la refresca de
GitHub al arranque — pricing de modelos nuevos sin actualizar LiteLLM.

```bash
# Sync manual en caliente (re-registra routing de providers también)
curl -X POST http://localhost:4000/reload/model_cost_map -H "Authorization: Bearer $ADMIN"

# Sync automático cada 6h
curl -X POST "http://localhost:4000/schedule/model_cost_map_reload?hours=6" -H "Authorization: Bearer $ADMIN"
curl -X DELETE "http://localhost:4000/schedule/model_cost_map_reload"      # cancelar
curl -X GET  "http://localhost:4000/schedule/model_cost_map_reload/status" # estado
curl -X GET  "http://localhost:4000/model/cost_map/source"                 # qué mapa cargó y revisión
```

Mapa propio: `LITELLM_MODEL_COST_MAP_URL=https://.../model_prices.json` (o `s3://`);
mapa embebido en imagen: `LITELLM_LOCAL_MODEL_COST_MAP=True` (¡entonces el reload
no alcanza el archivo embebido!). Override por deployment en config:

```yaml
model_list:
  - model_name: qwen-plus
    litellm_params: {model: openai/qwen-plus, ...}
    model_info:
      input_cost_per_token: 0.0000004    # override directo
      output_cost_per_token: 0.0000012
```

Para Huawei MaaS: si el modelo no está en el cost map, setear `model_info` con
los precios MaaS o el spend se loguea a $0 (el proxy lo flaggea con WARNING
`pricing entry ... has no input_cost_per_token` y el contador Prometheus
`litellm_zero_cost_requests_total`).

## 6. Pitfalls (verificados en docs)

1. **Budgets sin DB fallan abiertos** — sin `DATABASE_URL` no se resuelven virtual
   keys y el budget global se salta. Siempre correr con PostgreSQL.
2. **Key con team_id ignora el budget personal del dueño** — el corte es el del
   team y el `max_budget_in_team`. Diseñar la jerarquía con esto en mente.
3. **El parámetro `user` del body es auto-declarado** — un end-user puede
   desviar su spend declarando otro `user`. Mitigación oficial:
   `litellm_settings.overwrite_user_with_key_hash: true` (fuerza `user` = hash
   sha256 de la key) y SIEMPRE setear `user_id` al emitir keys.
4. **`/spend/logs` legacy trunca a 10.000 filas** (header
   `x-litellm-spend-logs-truncated: true`); usar `/spend/logs/v2` y ventanas de
   tiempo chicas para exports grandes.
5. **`temp_budget_expiry` es datetime ISO** (no duration string) en
   `/key/update` con `temp_budget_increase`.
6. **`max_budget`/`budget_limits` en `/key/update`** requiere proxy admin,
    team admin u org admin (403 para el resto) salvo
    `general_settings.self_serve_budget_policy`. `/key/update` trata un `null`
    explícito como *clear*, no como *ignore*.
7. **Bloqueo por hash, no por key**: si tu plataforma no guarda la key en claro
    (como debe ser), bloquea con `/key/block {"key": "<sha256>"}` — verificado;
    NO hace falta guardar la key para gobernarla (sección 2).
8. **Rotación automática de keys**: `auto_rotate: true` +
    `rotation_interval: "30d"`, requiere `LITELLM_KEY_ROTATION_ENABLED=true` y DB;
    `LITELLM_KEY_ROTATION_GRACE_PERIOD` para corte sin downtime.
9. `default_key_generate_params` en `litellm_settings` aplica defaults
   (max_budget, models, team_id, allowed_routes) a keys nuevas elegibles.
10. **Pricing de modelos DB (`POST /model/new`) va en `litellm_params`** —
    verificado en vivo contra v1.106.3 (spec 032, t_99526286): con el pricing
    SOLO en `model_info` el proxy registra el modelo pero factura $0
    (`spend: 0.0`, `cost_breakdown` en ceros, sin WARNING). El override de
    billing es el "per-deployment pricing override" de `litellm_params`
    (`input_cost_per_token`/`output_cost_per_token`); `model_info` queda
    como passthrough de metadata. Enviar en AMBOS es seguro. Esto difiere
    del modelo por config (§5), donde `model_info` sí aplica.

## Fuentes

- https://docs.litellm.ai/docs/proxy/virtual_keys
- https://docs.litellm.ai/docs/proxy/users (Budgets, Rate Limits)
- https://docs.litellm.ai/docs/proxy/team_budgets
- https://docs.litellm.ai/docs/proxy/cost_tracking (Spend Tracking)
- https://docs.litellm.ai/docs/proxy/sync_models_github (pricing update)
- https://docs.litellm.ai/docs/proxy/custom_model_cost_map
- https://docs.litellm.ai/docs/proxy/custom_pricing
- https://docs.litellm.ai/docs/proxy/error_reference

## 7. Hallazgos verificados contra un proxy REAL (t_c4766c4d, v1.106.3)

Evidencia en vivo contra LiteLLM 1.106.3 + PostgreSQL dedicado + upstream
Huawei MaaS `glm-5.2` (script `platform/scripts/revalidate_litellm_real.py`,
docs/validacion-final.md §9). Todo lo anterior de docs se cumplió; estos son
los deltas y confirmaciones:

### 7.1 Formato REAL de `/spend/logs/v2` (difiere de lo que uno asume)

- El hash de la key viaja en **`api_key`** — NO existe `user_api_key_hash`.
- El nombre PÚBLICO del modelo viaja en **`model_group`**; `model` lleva el
  interno de `litellm_params` (p. ej. `openai/glm-5.2`). Para requests
  fallidos de modelos no ruteados `model_group` es `""` (usar fallback a
  `model`).
- Tokens: `prompt_tokens` / `completion_tokens` (no hay campo `tokens`).
- `metadata` es un dict con `usage_object` (con `prompt_tokens_details.
  cached_tokens`), `cost_breakdown` (input_cost/output_cost desglosados) y
  `user_api_key`.
- **`spend_logs_metadata` NO se estampa** (llega `"spend_logs_metadata":
  null` aunque se pasó al emitir la key): la resolución log→tenant/usuario
  debe hacerse POR HASH de la key (`api_key` → tabla local de hashes).
- El gateway también loguea los INTENTOS FALLIDOS (403 modelo no permitido,
  422 budget exceeded) con `spend: 0.0` y `status: "failure"` — el pipeline
  de ingesta debe tolerarlos (telemetría honesta, costo cero).

### 7.2 Semántica de corte confirmada en vivo

- Budget nativo excedido → **HTTP 422** `{"error": {"message": "Budget has
  been exceeded! Key=<alias> (sk-...) Current cost: X, Max budget: Y",
  "type": "budget_exceeded"}}` (422 por defecto, no 429).
- Key bloqueada → **HTTP 401** `"Authentication Error, Key is blocked.
  Update via /key/unblock if you're an admin."`.
- Modelo fuera de la lista de la key → **HTTP 403** `"The requested model
  'X' is not available for this API key, or the model name is invalid."` —
  el check de modelos ocurre ANTES de rutear (403 aunque el modelo no
  exista en el proxy).
- `/key/generate` devuelve `key` (plaintext una vez) y `token` (= sha256 de
  la key) — confirmado; `/key/info?key=<hash>` responde `{"key": "<hash>",
  "info": {...}}` (la info anida bajo `info`).
- **Pitfall nuevo — max_budget fosilizado en la key**: al SUBIR el
  presupuesto de un usuario, desbloquear la key no basta: el gateway sigue
  cortando con el `max_budget` nativo estampado al emitir la key hasta el
  reset de `budget_duration`. El plano de control debe re-sincronizar con
  `POST /key/update {"key": "<hash>", "max_budget": <nuevo>}` en cada ciclo
  de enforcement cuando el budget difiere del estampado (implementado en
  `app/governance/spend.py`, D-GOV-16). Jamás enviar `max_budget: null` en
  /key/update — un null explícito es CLEAR (pitfall 6).

### 7.3 Pricing verificado con precios reales MaaS

- `model_info` con `input_cost_per_token` / `output_cost_per_token` /
  `cache_read_input_cost` en config.yaml produce costos EXACTOS contra la
  fórmula del proveedor (verificado ±0% contra GLM-5.2 MaaS: $1.40/M in,
  $4.40/M out, $0.26/M cache-hit, Period 1).
- `POST /reload/model_cost_map` → 200 ("4260 models updated") y NO pisa los
  overrides de `model_info` del config — seguro de llamar en caliente.
- OJO Huawei MaaS: pricing diferencial por horario (Period 1/2, GMT+8) y
  cache-hit; el cost map es estático → fijar Period 1 (peor caso) y
  documentar que el costo real puede ser MENOR.

### 7.4 Gotchas de instalación (venv propio)

- `pip install 'litellm[proxy]'` NO alcanza para correr con DB: falta
  Prisma. Instalar `litellm[extra_proxy]` + `prisma` y correr
  `PATH=./venv/bin:$PATH prisma generate --schema=$(find venv -name
  schema.prisma)` una vez (sin eso: "Unable to find Prisma binaries").
- Sin DB el proxy ARRANCA igualmente (solo un warning) — presupuestos
  fallan abiertos. Verificar `LiteLLM_BudgetTable` en la DB antes de
  confiar.

## 8. Seguridad / supply chain (contexto del operador, 2026-10-24)

- **2026-10-21: v1.107.0 publicada con MALWARE** (robo de credenciales +
  RCE) en PyPI/Docker 11:39-14:20 UTC; release retirado; versiones previas
  seguras. Razón de más para gateway propio, pineado y auditado.
- **CVE-2026-114612** (CVSS 9.9, escalada a proxy admin via salt key):
  fixed en 1.100.4/1.101.3/1.102.2/1.103.1/1.104.0-rc.2.
- **CVE-2026-49468** (auth bypass por Host header): fixed en 1.84.0.
- **PINNEADO: litellm 1.106.3** (PyPI 2026-10-24 09:14 UTC, post-containment;
  cubre ambos CVEs, no es la versión comprometida). JAMÁS `latest` ni tags
  flotantes de Docker; ver `litellm-real/requirements.txt`.
- **PENDIENTE 2026-10-27**: disclosure EMBARGOADA — re-check obligatorio de
  docs.litellm.ai/blog/tags/incident-report + advisories antes de cualquier
  upgrade futuro.
- El gateway debe escuchar SOLO en 127.0.0.1 en local (sin exposición a
  red); la master key jamás en el repo (env fuera de git).
