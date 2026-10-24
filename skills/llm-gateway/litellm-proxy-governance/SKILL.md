---
name: litellm-proxy-governance
description: "LiteLLM proxy como plano de control de modelos: virtual keys, budgets (max_budget/budget_duration), teams, spend endpoints, pricing update. Use when gobernar acceso a modelos, presupuestos con corte o tracking de gasto."
version: 1.0.0
author: trust-agent dev
metadata:
  hermes:
    tags: [litellm, proxy, virtual-keys, budgets, teams, spend, pricing, llm-gateway, governance]
---

# LiteLLM Proxy — Virtual keys, budgets, teams, spend y pricing

Investigado contra docs oficiales https://docs.litellm.ai/docs/proxy/* (oct-2026).
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
    "models": ["qwen-plus", "deepseek-v3"],
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

## Fuentes

- https://docs.litellm.ai/docs/proxy/virtual_keys
- https://docs.litellm.ai/docs/proxy/users (Budgets, Rate Limits)
- https://docs.litellm.ai/docs/proxy/team_budgets
- https://docs.litellm.ai/docs/proxy/cost_tracking (Spend Tracking)
- https://docs.litellm.ai/docs/proxy/sync_models_github (pricing update)
- https://docs.litellm.ai/docs/proxy/custom_model_cost_map
- https://docs.litellm.ai/docs/proxy/custom_pricing
- https://docs.litellm.ai/docs/proxy/error_reference
