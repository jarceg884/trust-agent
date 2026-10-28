# Plan — spec 018

## Decisiones técnicas

1. **Migración `0009_model_catalog_maas`** (Alembic, espejo en `db/schema.sql` §3.3 por AC-DB-040):
   - `ALTER TABLE catalog.model_catalog ADD COLUMN context_tokens bigint, ADD COLUMN max_output_tokens bigint` (NULLables: qwen3-32b sin límite documentado).
   - `ALTER COLUMN input_price_cents_per_m TYPE numeric(12,3)`, ídem output (precios fraccionales oficiales: 107.8, 377.4, 13.5, 269.5).
   - `DELETE` de `model_access` las filas de los modelos retirados (FK) y `DELETE` de `catalog.model_catalog` `qwen2.5-72b-instruct` y `deepseek-v3` (no existen upstream).
   - `INSERT … ON CONFLICT (model_name) DO UPDATE` de los 6 modelos reales con pricing oficial peor caso.
2. **Backend** (`app/models/router.py`, `app/models/access.py`, `app/governance/policies.py`):
   - `GET /api/models` y `access_overview` exponen `context_tokens`/`max_output_tokens`.
   - `access_overview` agrega `policy`: `allowed_models` vigente a nivel org (última versión activa; null sin policy) + `approved_models_only` desde `catalog.policy_keys` (floor invariable).
   - `list_level` (policies) agrega `model_approvals` del nivel/scope: join `model_access` ∩ `catalog.model_catalog`, `status='approved'`.
3. **Frontend** (`console`): `types.ts` + `models-page.tsx` — columna Context (formateo K/M), bloque "Governance policy" con `approved_models_only`/`allowed_models` y copy de deny-overrides.
4. **LiteLLM** (`litellm-real/config.yaml`): 6 deployments con `input_cost_per_token`/`output_cost_per_token`/`cache_read_input_cost` reales (USD/token, peor caso P1) y `max_tokens` = contexto documentado.
5. **Tests**: reemplazar referencias a los modelos retirados por reales (`glm-5.2`, `glm-5.3`) en `platform/tests` (conftest, litellm_fake, model_access, catalogs, budget_history, budget_rollover) y `db/tests` (conftest, constraints, isolation); aserciones de precio a los valores oficiales.
6. **Skill**: `skills/llm-gateway/huawei-maas-models/SKILL.md`.

## Evidencia

- `alembic upgrade head` en DB efímera (platform suite + db suite, incl. paridad AC-DB-040).
- Migración aplicada a la DB de dev (`trust-agent-db`).
- En vivo: `GET {MAAS_ENDPOINT}/models` (key Inforum) ↔ catálogo; proxy LiteLLM real `GET /v1/models` + `chat/completions` glm-5.2 → respuesta MaaS real; `GET /api/models/access` con sección `policy`; `GET /api/policies?level=org` con `model_approvals`.
- Build de la consola en verde.
